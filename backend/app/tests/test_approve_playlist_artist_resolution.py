"""Regression tests for a real bug found via live use (2026-07-13): approving
an SP-12 template or SP-09 related-seed playlist synced 0 tracks — Plex
playlist created, always empty — because approve_playlist() only ever
resolved artists via the plain-genre lookup, and a template/seed playlist's
genre_tag is a name, not a real genre _artists_by_genre() would key on."""
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.smart_playlist import SmartPlaylist
from app.schemas.settings import SmartPlaylistSettings
from app.services.playlist_generator import approve_playlist


class _FakeQdrant:
    def __init__(self, points):
        self._points = points

    async def scroll_monitored_artists(self, *, limit=256, offset=None, year_min=None, year_max=None):
        return self._points, None


class _FakePlex:
    def __init__(self, playlist_id="999"):
        self._playlist_id = playlist_id

    async def create_playlist(self, title, playlist_type="audio"):
        return self._playlist_id


class _FakeLastfm:
    def __init__(self, similar):
        self._similar = similar

    async def get_similar_artists(self, artist, mbid=None, limit=15):
        return self._similar


def _point(artist, genres):
    return {"payload": {"artist_name": artist, "genres": genres, "musicbrainz_id": None}}


class ApprovePlaylistArtistResolutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

    async def test_plain_genre_playlist_resolves_artists(self):
        db = self.SessionLocal()
        db.add(SmartPlaylist(genre_tag="Rock", title="Rock", enabled=True, is_template=False))
        db.commit()
        db.close()

        points = [_point("Band A", ["Rock"]), _point("Band B", ["Rock"])]
        cfg = SmartPlaylistSettings(enabled=True, min_artists_per_genre=1)
        sync_mock = AsyncMock(return_value=2)
        with patch("app.services.playlist_generator.SessionLocal", self.SessionLocal), \
             patch("app.services.playlist_generator.load_settings", return_value=cfg), \
             patch("app.services.playlist_generator._plex_client", return_value=_FakePlex()), \
             patch("app.services.qdrant_config.client", return_value=_FakeQdrant(points)), \
             patch("app.services.playlist_generator._sync_tracks_to_plex", new=sync_mock):
            result = await approve_playlist(1)

        self.assertTrue(result["ok"])
        synced_artists = sync_mock.call_args[0][3]
        self.assertEqual({a["artist_name"] for a in synced_artists}, {"Band A", "Band B"})

    async def test_template_playlist_resolves_union_of_genres(self):
        """The exact bug: before the fix, this synced 0 artists."""
        db = self.SessionLocal()
        db.add(SmartPlaylist(genre_tag="Epic Meltdown Madness", title="Epic Meltdown Madness",
                             enabled=True, is_template=True))
        db.commit()
        db.close()

        points = [_point("Band A", ["Metal"]), _point("Band B", ["Metalcore"]),
                  _point("Band C", ["Pop"])]  # not in the template — must be excluded
        cfg = SmartPlaylistSettings(
            enabled=True, min_artists_per_genre=1,
            playlist_templates={"Epic Meltdown Madness": ["Metal", "Metalcore"]},
        )
        sync_mock = AsyncMock(return_value=2)
        with patch("app.services.playlist_generator.SessionLocal", self.SessionLocal), \
             patch("app.services.playlist_generator.load_settings", return_value=cfg), \
             patch("app.services.playlist_generator._plex_client", return_value=_FakePlex()), \
             patch("app.services.qdrant_config.client", return_value=_FakeQdrant(points)), \
             patch("app.services.playlist_generator._sync_tracks_to_plex", new=sync_mock):
            result = await approve_playlist(1)

        self.assertTrue(result["ok"])
        self.assertEqual(result["artists"], 2)
        synced_artists = sync_mock.call_args[0][3]
        self.assertEqual({a["artist_name"] for a in synced_artists}, {"Band A", "Band B"})

    async def test_related_seed_playlist_resolves_seed_and_similar_artists(self):
        db = self.SessionLocal()
        db.add(SmartPlaylist(genre_tag="Godsmack Radio", title="Godsmack Radio",
                             enabled=True, is_template=True))
        db.commit()
        db.close()

        points = [_point("Godsmack", ["Rock"]), _point("Disturbed", ["Rock"])]
        cfg = SmartPlaylistSettings(
            enabled=True, min_artists_per_genre=1,
            related_artist_seeds={"Godsmack Radio": "Godsmack"},
        )
        sync_mock = AsyncMock(return_value=1)
        with patch("app.services.playlist_generator.SessionLocal", self.SessionLocal), \
             patch("app.services.playlist_generator.load_settings", return_value=cfg), \
             patch("app.services.playlist_generator._plex_client", return_value=_FakePlex()), \
             patch("app.services.qdrant_config.client", return_value=_FakeQdrant(points)), \
             patch("app.services.artist_discovery._lastfm_client",
                  return_value=_FakeLastfm([{"name": "Disturbed"}])), \
             patch("app.services.playlist_generator._sync_tracks_to_plex", new=sync_mock):
            result = await approve_playlist(1)

        self.assertTrue(result["ok"])
        synced_artists = sync_mock.call_args[0][3]
        self.assertEqual({a["artist_name"] for a in synced_artists}, {"Godsmack", "Disturbed"})

    async def test_template_config_removed_since_creation_fails_soft_to_empty(self):
        db = self.SessionLocal()
        db.add(SmartPlaylist(genre_tag="Deleted Template", title="Deleted Template",
                             enabled=True, is_template=True))
        db.commit()
        db.close()

        cfg = SmartPlaylistSettings(enabled=True)  # no playlist_templates/related_artist_seeds entry
        sync_mock = AsyncMock(return_value=0)
        with patch("app.services.playlist_generator.SessionLocal", self.SessionLocal), \
             patch("app.services.playlist_generator.load_settings", return_value=cfg), \
             patch("app.services.playlist_generator._plex_client", return_value=_FakePlex()), \
             patch("app.services.playlist_generator._sync_tracks_to_plex", new=sync_mock):
            result = await approve_playlist(1)

        self.assertTrue(result["ok"])
        self.assertEqual(result["artists"], 0)
        synced_artists = sync_mock.call_args[0][3]
        self.assertEqual(synced_artists, [])


if __name__ == "__main__":
    unittest.main()
