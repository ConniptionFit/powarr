"""Unit tests for SP-11: playlist-naming diversity within one generation run."""
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.smart_playlist import SmartPlaylist
from app.schemas.settings import SmartPlaylistSettings
from app.services.playlist_generator import (
    _norm_title, _playlist_title, _upsert_playlist_group,
)


class NormTitleTests(unittest.TestCase):
    def test_case_and_whitespace_insensitive(self):
        self.assertEqual(_norm_title("  Midnight   Drive "), _norm_title("midnight drive"))

    def test_empty_string(self):
        self.assertEqual(_norm_title(""), "")

    def test_none_safe(self):
        self.assertEqual(_norm_title(None), "")


class PlaylistTitleCollisionTests(unittest.IsolatedAsyncioTestCase):
    def _cfg(self, **over):
        base = dict(llm_playlist_names=True)
        base.update(over)
        return SmartPlaylistSettings(**base)

    async def test_llm_disabled_always_returns_fallback_ignoring_used_names(self):
        cfg = self._cfg(llm_playlist_names=False)
        title = await _playlist_title(None, cfg, "Rock", [], used_names=set())
        self.assertEqual(title, "Powarr · Rock")

    async def test_returns_llm_name_when_no_collision(self):
        cfg = self._cfg()
        with patch("app.services.playlist_generator.suggest_playlist_name_for",
                  new=AsyncMock(return_value="Midnight Drive")):
            title = await _playlist_title(None, cfg, "Rock", [], used_names=set())
        self.assertEqual(title, "Midnight Drive")

    async def test_retries_once_on_collision_and_returns_second_name(self):
        cfg = self._cfg()
        mock = AsyncMock(side_effect=["Kitchen Disco", "Late Night Groove"])
        with patch("app.services.playlist_generator.suggest_playlist_name_for", new=mock):
            title = await _playlist_title(
                None, cfg, "Electronic", [], used_names={"kitchen disco"})
        self.assertEqual(title, "Late Night Groove")
        self.assertEqual(mock.call_count, 2)

    async def test_falls_back_to_deterministic_name_after_two_collisions(self):
        cfg = self._cfg()
        mock = AsyncMock(return_value="Kitchen Disco")  # collides every time
        with patch("app.services.playlist_generator.suggest_playlist_name_for", new=mock):
            title = await _playlist_title(
                None, cfg, "Electronic", [], used_names={"kitchen disco"})
        self.assertEqual(title, "Powarr · Electronic")
        self.assertEqual(mock.call_count, 2)  # exactly one retry, not an infinite loop

    async def test_falls_back_when_llm_returns_nothing(self):
        cfg = self._cfg()
        with patch("app.services.playlist_generator.suggest_playlist_name_for",
                  new=AsyncMock(return_value=None)):
            title = await _playlist_title(None, cfg, "Jazz", [], used_names=set())
        self.assertEqual(title, "Powarr · Jazz")

    async def test_avoid_list_passed_to_naming_call(self):
        cfg = self._cfg()
        mock = AsyncMock(return_value="Fresh Name")
        with patch("app.services.playlist_generator.suggest_playlist_name_for", new=mock):
            await _playlist_title(None, cfg, "Pop", [], used_names={"used one", "used two"})
        _, kwargs = mock.call_args
        self.assertEqual(sorted(kwargs["avoid"]), ["used one", "used two"])

    async def test_default_used_names_is_empty_set_when_omitted(self):
        cfg = self._cfg()
        with patch("app.services.playlist_generator.suggest_playlist_name_for",
                  new=AsyncMock(return_value="Any Name")):
            title = await _playlist_title(None, cfg, "Rock", [])
        self.assertEqual(title, "Any Name")


class UpsertPlaylistGroupUsedNamesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    async def test_new_playlist_title_added_to_used_names(self):
        cfg = SmartPlaylistSettings(llm_playlist_names=True)
        used_names: set[str] = set()
        with patch("app.services.playlist_generator._playlist_title",
                  new=AsyncMock(return_value="Brand New Mix")):
            await _upsert_playlist_group(
                self.db, cfg, None, "Rock", [{"artist_name": "Band A"}],
                used_names=used_names)
        self.assertIn("brand new mix", used_names)

    async def test_used_names_none_does_not_error(self):
        cfg = SmartPlaylistSettings(llm_playlist_names=True)
        with patch("app.services.playlist_generator._playlist_title",
                  new=AsyncMock(return_value="Some Mix")):
            created, *_rest = await _upsert_playlist_group(
                self.db, cfg, None, "Rock", [{"artist_name": "Band A"}])
        self.assertTrue(created)


class GenerateCandidatesDiversityIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end: two genres in one run, LLM naming mock returns the SAME
    name for both — the second playlist must not end up with a duplicate
    title, exercising the real used_names threading through generate_candidates()."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

    async def test_two_genres_same_llm_name_produce_distinct_titles(self):
        from app.services.playlist_generator import generate_candidates

        class _FakeQdrant:
            def __init__(self, points):
                self._points = points

            async def scroll_monitored_artists(self, *, limit=256, offset=None, year_min=None, year_max=None):
                return self._points, None

        def _point(artist, genres):
            return {"payload": {"artist_name": artist, "genres": genres, "musicbrainz_id": None}}

        points = [_point("Band A", ["Rock"]), _point("Band B", ["Electronic"])]
        cfg = SmartPlaylistSettings(enabled=True, min_artists_per_genre=1, llm_playlist_names=True)

        # Every LLM call returns the same name — realistic worst case (weak
        # local model with low variety on a generic prompt).
        naming_mock = AsyncMock(return_value="Genre Mix")
        with patch("app.services.playlist_generator.SessionLocal", self.SessionLocal), \
             patch("app.services.playlist_generator.load_settings", return_value=cfg), \
             patch("app.services.qdrant_config.client", return_value=_FakeQdrant(points)), \
             patch("app.services.playlist_generator._plex_client", return_value=None), \
             patch("app.services.playlist_generator.suggest_playlist_name_for", new=naming_mock):
            result = await generate_candidates()

        self.assertTrue(result["ok"])
        db = self.SessionLocal()
        try:
            titles = [pl.title for pl in db.query(SmartPlaylist).all()]
            self.assertEqual(len(titles), 2)
            self.assertEqual(len(set(t.lower() for t in titles)), 2,
                             f"expected distinct titles, got {titles}")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
