"""Unit tests for SP-12: named playlist templates (union of several genres)."""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.smart_playlist import SmartPlaylist
from app.schemas.settings import SmartPlaylistSettings
from app.services.playlist_generator import _artists_for_template, generate_candidates


class ArtistsForTemplateTests(unittest.TestCase):
    def test_unions_artists_across_listed_genres(self):
        by_genre = {
            "Rock": [{"artist_name": "Band A", "musicbrainz_id": None}],
            "Electronic": [{"artist_name": "Band B", "musicbrainz_id": None}],
            "Jazz": [{"artist_name": "Band C", "musicbrainz_id": None}],
        }
        artists = _artists_for_template(by_genre, ["Rock", "Electronic"], {})
        names = {a["artist_name"] for a in artists}
        self.assertEqual(names, {"Band A", "Band B"})

    def test_dedupes_artist_appearing_in_multiple_listed_genres(self):
        by_genre = {
            "Rock": [{"artist_name": "Band A", "musicbrainz_id": None}],
            "Electronic": [{"artist_name": "Band A", "musicbrainz_id": None}],
        }
        artists = _artists_for_template(by_genre, ["Rock", "Electronic"], {})
        self.assertEqual(len(artists), 1)

    def test_genre_not_in_template_excluded(self):
        by_genre = {
            "Rock": [{"artist_name": "Band A", "musicbrainz_id": None}],
            "Jazz": [{"artist_name": "Band C", "musicbrainz_id": None}],
        }
        artists = _artists_for_template(by_genre, ["Rock"], {})
        names = {a["artist_name"] for a in artists}
        self.assertEqual(names, {"Band A"})

    def test_case_insensitive_genre_matching(self):
        by_genre = {"rock": [{"artist_name": "Band A", "musicbrainz_id": None}]}
        artists = _artists_for_template(by_genre, ["Rock"], {})
        self.assertEqual(len(artists), 1)


def _point(artist, genres):
    return {"payload": {"artist_name": artist, "genres": genres, "musicbrainz_id": None}}


class _FakeQdrant:
    def __init__(self, points):
        self._points = points

    async def scroll_monitored_artists(self, *, limit=256, offset=None, year_min=None, year_max=None):
        return self._points, None


class GenerateCandidatesTemplateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

    async def test_template_playlist_created_from_union(self):
        points = [
            _point("Rock Band", ["Rock"]),
            _point("Rock Band 2", ["Rock"]),
            _point("Electro Act", ["Electronic"]),
        ]
        cfg = SmartPlaylistSettings(
            enabled=True, min_artists_per_genre=1,
            playlist_templates={"Workout": ["Rock", "Electronic"]},
        )
        with patch("app.services.playlist_generator.SessionLocal", self.SessionLocal), \
             patch("app.services.playlist_generator.load_settings", return_value=cfg), \
             patch("app.services.qdrant_config.client", return_value=_FakeQdrant(points)), \
             patch("app.services.playlist_generator._plex_client", return_value=None), \
             patch("app.services.playlist_generator._playlist_title", return_value="Workout Mix"):
            result = await generate_candidates()

        self.assertTrue(result["ok"])
        db = self.SessionLocal()
        pl = db.query(SmartPlaylist).filter_by(genre_tag="Workout").first()
        self.assertIsNotNone(pl)
        self.assertTrue(pl.is_template)
        db.close()

    async def test_template_below_min_artists_not_created(self):
        points = [_point("Rock Band", ["Rock"])]
        cfg = SmartPlaylistSettings(
            enabled=True, min_artists_per_genre=5,  # union only has 1 artist
            playlist_templates={"Workout": ["Rock"]},
        )
        with patch("app.services.playlist_generator.SessionLocal", self.SessionLocal), \
             patch("app.services.playlist_generator.load_settings", return_value=cfg), \
             patch("app.services.qdrant_config.client", return_value=_FakeQdrant(points)), \
             patch("app.services.playlist_generator._plex_client", return_value=None), \
             patch("app.services.playlist_generator._playlist_title", return_value="Genre Mix"):
            await generate_candidates()

        db = self.SessionLocal()
        pl = db.query(SmartPlaylist).filter_by(genre_tag="Workout").first()
        self.assertIsNone(pl)
        db.close()

    async def test_real_genre_playlists_not_marked_as_template(self):
        points = [_point("Rock Band", ["Rock"])]
        cfg = SmartPlaylistSettings(enabled=True, min_artists_per_genre=1, playlist_templates={})
        with patch("app.services.playlist_generator.SessionLocal", self.SessionLocal), \
             patch("app.services.playlist_generator.load_settings", return_value=cfg), \
             patch("app.services.qdrant_config.client", return_value=_FakeQdrant(points)), \
             patch("app.services.playlist_generator._plex_client", return_value=None), \
             patch("app.services.playlist_generator._playlist_title", return_value="Rock Mix"):
            await generate_candidates()

        db = self.SessionLocal()
        pl = db.query(SmartPlaylist).filter_by(genre_tag="Rock").first()
        self.assertIsNotNone(pl)
        self.assertFalse(pl.is_template)
        db.close()


if __name__ == "__main__":
    unittest.main()
