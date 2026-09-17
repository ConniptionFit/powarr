"""SP-14: genre fuzzy-merge for Smart Playlists.

- Case/punctuation/whitespace variants of the same genre label always merge
  automatically (merge_genre_groups / _normalize_genre_key).
- User-curated genre_aliases additionally merge genuinely different-looking
  labels the user considers equivalent (e.g. "Rap" -> "Hip-Hop").
- _artists_by_genre applies the merge before the min_artists_per_genre
  threshold, and excluded_genres / a requested `genre` compare against the
  merged form so behavior stays consistent.
"""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.schemas.settings import SmartPlaylistSettings
from app.services.playlist_generator import (
    _artists_by_genre, _normalize_genre_key, _resolve_genre_alias, merge_genre_groups,
)


class NormalizeGenreKeyTests(unittest.TestCase):
    def test_case_and_hyphen_variants_match(self):
        self.assertEqual(_normalize_genre_key("Hip-Hop"), _normalize_genre_key("hip hop"))
        self.assertEqual(_normalize_genre_key("Hip-Hop"), _normalize_genre_key("HIP_HOP"))

    def test_distinct_genres_do_not_match(self):
        self.assertNotEqual(_normalize_genre_key("Hip-Hop"), _normalize_genre_key("Pop"))

    def test_collapses_internal_whitespace(self):
        self.assertEqual(_normalize_genre_key("Hip   Hop"), "hip hop")


class ResolveGenreAliasTests(unittest.TestCase):
    def test_resolves_case_insensitively(self):
        self.assertEqual(_resolve_genre_alias("rap", {"Rap": "Hip-Hop"}), "Hip-Hop")

    def test_passthrough_when_no_alias(self):
        self.assertEqual(_resolve_genre_alias("Jazz", {"Rap": "Hip-Hop"}), "Jazz")

    def test_passthrough_when_no_aliases_configured(self):
        self.assertEqual(_resolve_genre_alias("Rap", None), "Rap")


class MergeGenreGroupsTests(unittest.TestCase):
    def test_merges_punctuation_variants(self):
        raw = {
            "Hip-Hop": [{"artist_name": "A"}, {"artist_name": "B"}],
            "Hip Hop": [{"artist_name": "C"}],
        }
        merged = merge_genre_groups(raw)
        self.assertEqual(len(merged), 1)
        label = next(iter(merged))
        self.assertEqual(len(merged[label]), 3)

    def test_display_label_is_most_common_spelling(self):
        raw = {
            "hip hop": [{"artist_name": "A"}],
            "Hip-Hop": [{"artist_name": "B"}, {"artist_name": "C"}],
        }
        merged = merge_genre_groups(raw)
        self.assertIn("Hip-Hop", merged)  # 2 artists beats 1

    def test_alias_merges_distinct_labels(self):
        raw = {
            "Rap": [{"artist_name": "A"}],
            "Hip-Hop": [{"artist_name": "B"}],
        }
        merged = merge_genre_groups(raw, aliases={"Rap": "Hip-Hop"})
        self.assertEqual(len(merged), 1)
        self.assertIn("Hip-Hop", merged)
        self.assertEqual(len(merged["Hip-Hop"]), 2)

    def test_unrelated_genres_stay_separate(self):
        raw = {
            "Hip-Hop": [{"artist_name": "A"}],
            "Jazz": [{"artist_name": "B"}],
        }
        merged = merge_genre_groups(raw)
        self.assertEqual(set(merged), {"Hip-Hop", "Jazz"})


def _point(artist, genres, mbid=None):
    return {"payload": {"artist_name": artist, "genres": genres, "musicbrainz_id": mbid}}


class _FakeQdrant:
    """One page of results, then a None offset to stop _artists_by_genre's loop."""

    def __init__(self, points):
        self._points = points

    async def scroll_monitored_artists(self, *, limit=256, offset=None, **kw):
        if offset is not None:
            return [], None
        return self._points, None


class ArtistsByGenreMergeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    async def _run(self, points, cfg=None, genre=None):
        cfg = cfg or SmartPlaylistSettings(min_artists_per_genre=1)
        client = _FakeQdrant(points)
        with patch("app.services.qdrant_config.client", return_value=client):
            return await _artists_by_genre(self.db, cfg, genre)

    async def test_punctuation_variants_merge_into_one_playlist_bucket(self):
        points = [
            _point("Artist A", ["Hip-Hop"]),
            _point("Artist B", ["Hip Hop"]),
            _point("Artist C", ["hip_hop"]),
        ]
        by_genre = await self._run(points)
        self.assertEqual(len(by_genre), 1)
        label = next(iter(by_genre))
        self.assertEqual(len(by_genre[label]), 3)

    async def test_alias_merges_rap_into_hip_hop(self):
        points = [
            _point("Artist A", ["Rap"]),
            _point("Artist B", ["Hip-Hop"]),
        ]
        cfg = SmartPlaylistSettings(min_artists_per_genre=1, genre_aliases={"Rap": "Hip-Hop"})
        by_genre = await self._run(points, cfg=cfg)
        self.assertEqual(len(by_genre), 1)
        self.assertIn("Hip-Hop", by_genre)
        self.assertEqual(len(by_genre["Hip-Hop"]), 2)

    async def test_excluded_genres_match_merged_form(self):
        points = [_point("Artist A", ["Hip Hop"])]  # spelled differently than excluded entry
        cfg = SmartPlaylistSettings(min_artists_per_genre=1, excluded_genres=["Hip-Hop"])
        by_genre = await self._run(points, cfg=cfg)
        self.assertEqual(by_genre, {})

    async def test_requested_genre_matches_merged_form(self):
        points = [_point("Artist A", ["Rap"])]
        cfg = SmartPlaylistSettings(min_artists_per_genre=1, genre_aliases={"Rap": "Hip-Hop"})
        by_genre = await self._run(points, cfg=cfg, genre="Hip-Hop")
        self.assertIn("Hip-Hop", by_genre)


if __name__ == "__main__":
    unittest.main()
