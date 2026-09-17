"""SP-02: Plex sonic-similarity track bias for Smart Playlists.

- PlexIntegration.sonically_similar_keys fails soft (empty list) on non-200,
  malformed body, or any network error — never raises.
- playlist_generator._sonic_bias reorders candidates toward the sonic-nearest
  set without dropping any, and only when a seed track already exists in the
  playlist's ledger.
- playlist_generator._add_artist_tracks_to_db only applies the bias when
  sonic_similarity_enabled is set and a plex client is supplied; existing
  insertion-order behavior is unchanged otherwise (regression guard).
"""
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.integrations.plex import PlexIntegration
from app.models.media import MediaItem
from app.models.smart_playlist import SmartPlaylist, SmartPlaylistTrack
from app.schemas.settings import SmartPlaylistSettings
from app.services.playlist_generator import _add_artist_tracks_to_db, _sonic_bias


class _Resp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


class SonicallySimilarKeysTests(unittest.IsolatedAsyncioTestCase):
    def _plex(self):
        return PlexIntegration("http://plex:32400", "token")

    async def test_returns_rating_keys_on_success(self):
        resp = _Resp(200, {"MediaContainer": {"Metadata": [
            {"ratingKey": "10", "distance": 0.1}, {"ratingKey": "11", "distance": 0.2}]}})
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=client):
            keys = await self._plex().sonically_similar_keys("1")
        self.assertEqual(keys, ["10", "11"])

    async def test_empty_rating_key_short_circuits(self):
        keys = await self._plex().sonically_similar_keys("")
        self.assertEqual(keys, [])

    async def test_non_200_fails_soft(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_Resp(404))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=client):
            keys = await self._plex().sonically_similar_keys("1")
        self.assertEqual(keys, [])

    async def test_network_error_fails_soft(self):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=RuntimeError("boom"))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=client):
            keys = await self._plex().sonically_similar_keys("1")
        self.assertEqual(keys, [])


class _FakePlex:
    def __init__(self, near: set[str] | None = None):
        self._near = near or set()

    async def sonically_similar_keys(self, rating_key, **kw):
        return list(self._near)


class SonicBiasTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.pl = SmartPlaylist(genre_tag="rock", title="Powarr · rock")
        self.db.add(self.pl)
        self.db.commit()
        self.tracks = [
            MediaItem(plex_rating_key=str(i), title=f"Track {i}", media_type="track",
                      parent_title="Some Artist")
            for i in range(1, 6)
        ]

    def _run(self, coro):
        import asyncio
        return asyncio.run(coro)

    def test_no_seed_returns_original_order(self):
        plex = _FakePlex(near={"3", "4"})
        out = self._run(_sonic_bias(self.db, plex, self.pl, list(self.tracks)))
        self.assertEqual([t.plex_rating_key for t in out], ["1", "2", "3", "4", "5"])

    def test_seed_present_reorders_without_dropping(self):
        self.db.add(SmartPlaylistTrack(playlist_id=self.pl.id, plex_key="0",
                                       artist_name="Some Artist"))
        self.db.commit()
        plex = _FakePlex(near={"3", "4"})
        out = self._run(_sonic_bias(self.db, plex, self.pl, list(self.tracks)))
        self.assertEqual({t.plex_rating_key for t in out}, {"1", "2", "3", "4", "5"})
        self.assertEqual(set(t.plex_rating_key for t in out[:2]), {"3", "4"})


class AddArtistTracksToDbTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.pl = SmartPlaylist(genre_tag="rock", title="Powarr · rock")
        self.db.add(self.pl)
        for i in range(1, 6):
            self.db.add(MediaItem(plex_rating_key=str(i), title=f"Track {i}",
                                  media_type="track", parent_title="Some Artist"))
        self.db.commit()

    def _run(self, coro):
        import asyncio
        return asyncio.run(coro)

    def test_disabled_by_default_keeps_arbitrary_order(self):
        cfg = SmartPlaylistSettings()
        self.assertFalse(cfg.sonic_similarity_enabled)
        plex = _FakePlex(near={"5"})
        out = self._run(_add_artist_tracks_to_db(
            self.db, self.pl, "Some Artist", 3, plex=plex, cfg=cfg))
        self.assertEqual([t.plex_rating_key for t in out], ["1", "2", "3"])

    def test_enabled_applies_bias_and_respects_max_tracks(self):
        self.db.add(SmartPlaylistTrack(playlist_id=self.pl.id, plex_key="0",
                                       artist_name="Some Artist"))
        self.db.commit()
        cfg = SmartPlaylistSettings(sonic_similarity_enabled=True)
        plex = _FakePlex(near={"5"})
        out = self._run(_add_artist_tracks_to_db(
            self.db, self.pl, "Some Artist", 2, plex=plex, cfg=cfg))
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].plex_rating_key, "5")

    def test_no_plex_client_keeps_arbitrary_order(self):
        cfg = SmartPlaylistSettings(sonic_similarity_enabled=True)
        out = self._run(_add_artist_tracks_to_db(
            self.db, self.pl, "Some Artist", 3, plex=None, cfg=cfg))
        self.assertEqual([t.plex_rating_key for t in out], ["1", "2", "3"])

    def test_regression_no_lidarr_column_on_mediaitem(self):
        """Bug fix 2026-07-11 (introduced v0.42.1/450fc7f): MediaItem has no
        is_monitored_lidarr column (that field only ever existed on Qdrant
        payloads) — filtering on it raised AttributeError on every call, so
        Smart Playlists could never actually add a track to Plex. This must
        keep working with zero setup beyond a plain MediaItem row."""
        out = self._run(_add_artist_tracks_to_db(
            self.db, self.pl, "Some Artist", 5, plex=None, cfg=None))
        self.assertEqual(len(out), 5)


if __name__ == "__main__":
    unittest.main()
