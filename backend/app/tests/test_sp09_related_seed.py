"""Unit tests for SP-09: related-artist playlist generation axis.

Only the related-artist axis is implemented (see the module docstring in
playlist_generator.py::_artists_for_related_seed for why mood/era aren't —
their Qdrant payload fields exist but nothing in the pipeline ever
populates them with real data)."""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.smart_playlist import SmartPlaylist
from app.schemas.settings import SmartPlaylistSettings
from app.services.playlist_generator import _artists_for_related_seed, generate_candidates


class _FakeLastfm:
    def __init__(self, similar):
        self._similar = similar

    async def get_similar_artists(self, artist, mbid=None, limit=15):
        return self._similar


def _point(artist, genres=None):
    return {"payload": {"artist_name": artist, "genres": genres or [], "musicbrainz_id": None}}


class _FakeQdrant:
    def __init__(self, points):
        self._points = points

    async def scroll_monitored_artists(self, *, limit=256, offset=None, year_min=None, year_max=None):
        return self._points, None


class ArtistsForRelatedSeedTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.cfg = SmartPlaylistSettings()

    async def test_includes_seed_and_monitored_similar_artists(self):
        # Each artist is the SOLE member of their own genre bucket — below any
        # min_artists_per_genre threshold, proving this doesn't go through
        # the per-genre-filtered by_genre map.
        points = [_point("Radiohead", ["Rock"]), _point("Thom Yorke", ["Electronic"]),
                 _point("Unrelated Band", ["Jazz"])]
        lastfm = _FakeLastfm([{"name": "Thom Yorke"}, {"name": "Not In Library"}])
        with patch("app.services.qdrant_config.client", return_value=_FakeQdrant(points)), \
             patch("app.services.artist_discovery._lastfm_client", return_value=lastfm):
            artists = await _artists_for_related_seed(self.db, self.cfg, "Radiohead")
        names = {a["artist_name"] for a in artists}
        self.assertEqual(names, {"Radiohead", "Thom Yorke"})

    async def test_no_lastfm_returns_empty(self):
        points = [_point("Radiohead", ["Rock"])]
        with patch("app.services.qdrant_config.client", return_value=_FakeQdrant(points)), \
             patch("app.services.artist_discovery._lastfm_client", return_value=None):
            artists = await _artists_for_related_seed(self.db, self.cfg, "Radiohead")
        self.assertEqual(artists, [])

    async def test_seed_not_monitored_but_similar_artists_are(self):
        points = [_point("Thom Yorke", ["Electronic"])]
        lastfm = _FakeLastfm([{"name": "Thom Yorke"}])
        with patch("app.services.qdrant_config.client", return_value=_FakeQdrant(points)), \
             patch("app.services.artist_discovery._lastfm_client", return_value=lastfm):
            artists = await _artists_for_related_seed(self.db, self.cfg, "Radiohead")
        self.assertEqual([a["artist_name"] for a in artists], ["Thom Yorke"])

    async def test_no_seed_artist_returns_empty(self):
        artists = await _artists_for_related_seed(self.db, self.cfg, "")
        self.assertEqual(artists, [])

    async def test_lastfm_error_fails_soft(self):
        class _BrokenLastfm(_FakeLastfm):
            async def get_similar_artists(self, artist, mbid=None, limit=15):
                raise RuntimeError("unreachable")
        points = [_point("Radiohead", ["Rock"])]
        with patch("app.services.qdrant_config.client", return_value=_FakeQdrant(points)), \
             patch("app.services.artist_discovery._lastfm_client", return_value=_BrokenLastfm([])):
            artists = await _artists_for_related_seed(self.db, self.cfg, "Radiohead")
        self.assertEqual([a["artist_name"] for a in artists], ["Radiohead"])

    async def test_blacklisted_seed_excluded(self):
        points = [_point("Radiohead", ["Rock"])]
        cfg = SmartPlaylistSettings(blacklisted_artists=["Radiohead"])
        lastfm = _FakeLastfm([])
        with patch("app.services.qdrant_config.client", return_value=_FakeQdrant(points)), \
             patch("app.services.artist_discovery._lastfm_client", return_value=lastfm):
            artists = await _artists_for_related_seed(self.db, cfg, "Radiohead")
        self.assertEqual(artists, [])


class GenerateCandidatesRelatedSeedTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

    async def test_related_seed_playlist_created(self):
        # Radiohead and Thom Yorke each sole members of their own genre —
        # would be invisible via the per-genre-filtered path.
        points = [_point("Radiohead", ["Rock"]), _point("Thom Yorke", ["Electronic"])]
        cfg = SmartPlaylistSettings(
            enabled=True, min_artists_per_genre=2,
            related_artist_seeds={"Radiohead Adjacent": "Radiohead"},
        )
        lastfm = _FakeLastfm([{"name": "Thom Yorke"}])
        with patch("app.services.playlist_generator.SessionLocal", self.SessionLocal), \
             patch("app.services.playlist_generator.load_settings", return_value=cfg), \
             patch("app.services.qdrant_config.client", return_value=_FakeQdrant(points)), \
             patch("app.services.playlist_generator._plex_client", return_value=None), \
             patch("app.services.artist_discovery._lastfm_client", return_value=lastfm), \
             patch("app.services.playlist_generator._playlist_title", return_value="Radiohead Adjacent"):
            result = await generate_candidates()

        self.assertTrue(result["ok"])
        db = self.SessionLocal()
        pl = db.query(SmartPlaylist).filter_by(genre_tag="Radiohead Adjacent").first()
        self.assertIsNotNone(pl)
        self.assertTrue(pl.is_template)
        db.close()


if __name__ == "__main__":
    unittest.main()
