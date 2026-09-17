"""Unit tests for AD-19: one discovery lane per user-configured mood tag,
sliced from SP-15's mood_tags now populated on discovered points."""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.schemas.settings import ArtistDiscoverySettings
from app.services.artist_discovery import (
    _normalize_mood_key, compute_mood_centroid, run_centroid_discovery,
)


def _point(mbid, name, vector, mood_tags=None, plays=0):
    return {"payload": {"musicbrainz_id": mbid, "artist_name": name,
                        "is_discovered": True, "total_plays_global": plays,
                        "mood_tags": mood_tags or []},
            "vector": vector}


class _FakeQdrant:
    def __init__(self, points, search_hits=None):
        self._points = points
        self._search_hits = search_hits or []

    async def scroll(self, *, filter=None, limit=256, offset=None, with_vector=False):
        return self._points, None

    async def search(self, vector, *, limit=10, score_threshold=None, must=None, must_not=None):
        return self._search_hits


class NormalizeMoodKeyTests(unittest.TestCase):
    def test_lowercases_and_underscores_spaces(self):
        self.assertEqual(_normalize_mood_key("Feel Good"), "feel_good")

    def test_strips_punctuation(self):
        self.assertEqual(_normalize_mood_key("chill!!"), "chill")

    def test_blank_falls_back_to_mood(self):
        self.assertEqual(_normalize_mood_key(""), "mood")
        self.assertEqual(_normalize_mood_key(None), "mood")


class ComputeMoodCentroidTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    async def test_averages_only_points_carrying_the_mood(self):
        points = [
            _point("mbid-a", "Chill Artist", [1.0, 0.0], mood_tags=["chill"]),
            _point("mbid-b", "Energetic Artist", [0.0, 1.0], mood_tags=["energetic"]),
        ]
        with patch("app.services.artist_discovery._qdrant", return_value=_FakeQdrant(points)):
            centroid = await compute_mood_centroid(self.db, "chill")
        self.assertEqual(centroid, [1.0, 0.0])

    async def test_match_is_case_insensitive(self):
        points = [_point("mbid-a", "Artist", [1.0, 0.0], mood_tags=["Chill"])]
        with patch("app.services.artist_discovery._qdrant", return_value=_FakeQdrant(points)):
            centroid = await compute_mood_centroid(self.db, "chill")
        self.assertEqual(centroid, [1.0, 0.0])

    async def test_no_points_carry_the_mood_returns_none(self):
        points = [_point("mbid-a", "Artist", [1.0, 0.0], mood_tags=["energetic"])]
        with patch("app.services.artist_discovery._qdrant", return_value=_FakeQdrant(points)):
            centroid = await compute_mood_centroid(self.db, "chill")
        self.assertIsNone(centroid)

    async def test_blank_mood_returns_none(self):
        with patch("app.services.artist_discovery._qdrant", return_value=_FakeQdrant([])):
            centroid = await compute_mood_centroid(self.db, "")
        self.assertIsNone(centroid)


class RunCentroidDiscoveryMoodLaneTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    async def _run(self, points, search_hits, cfg):
        qdrant = _FakeQdrant(points, search_hits=search_hits)
        with patch("app.services.artist_discovery._qdrant", return_value=qdrant), \
             patch("app.services.artist_discovery._lastfm_client", return_value=None), \
             patch("app.services.artist_discovery._enrich_candidate",
                   return_value={"image_url": None, "bio": None, "genres": [], "years_active": None}):
            return await run_centroid_discovery(self.db, cfg)

    async def test_mood_lane_creates_candidate_tagged_with_slugged_source(self):
        cfg = ArtistDiscoverySettings(recent_taste_lane_enabled=False, mood_discovery_lanes=["Feel Good"])
        points = [_point("seed-mbid", "Seed Artist", [1.0, 0.0], mood_tags=["feel good"], plays=10)]
        hit = {"payload": {"artist_name": "New Artist", "musicbrainz_id": "new-mbid"}, "score": 0.9}
        result = await self._run(points, [hit], cfg)
        self.assertEqual(result["candidates"], 1)
        from app.models.artist_discovery import DiscoveredArtist
        row = self.db.query(DiscoveredArtist).filter_by(musicbrainz_id="new-mbid").first()
        self.assertIsNotNone(row)
        self.assertIn(row.source, ("centroid", "centroid_mood_feel_good"))

    async def test_no_mood_lanes_configured_no_extra_work(self):
        cfg = ArtistDiscoverySettings(recent_taste_lane_enabled=False, mood_discovery_lanes=[])
        points = [_point("seed-mbid", "Seed Artist", [1.0, 0.0], plays=10)]
        hit = {"payload": {"artist_name": "New Artist", "musicbrainz_id": "new-mbid"}, "score": 0.9}
        result = await self._run(points, [hit], cfg)
        self.assertEqual(result["candidates"], 1)  # all-time lane only

    async def test_mood_with_no_matching_points_contributes_nothing(self):
        cfg = ArtistDiscoverySettings(recent_taste_lane_enabled=False, mood_discovery_lanes=["energetic"])
        points = [_point("seed-mbid", "Seed Artist", [1.0, 0.0], mood_tags=["chill"], plays=10)]
        hit = {"payload": {"artist_name": "New Artist", "musicbrainz_id": "new-mbid"}, "score": 0.9}
        result = await self._run(points, [hit], cfg)
        self.assertEqual(result["candidates"], 1)  # all-time lane still surfaces it
        self.assertNotIn("energetic", result["message"])


if __name__ == "__main__":
    unittest.main()
