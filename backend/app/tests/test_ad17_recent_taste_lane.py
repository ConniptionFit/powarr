"""Unit tests for AD-17: second discovery lane seeded from recently-listened
artists, alongside the existing all-time most-played centroid."""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.schemas.settings import ArtistDiscoverySettings
from app.services.artist_discovery import (
    compute_recent_taste_centroid, run_centroid_discovery,
)


def _point(mbid, name, vector, plays=0):
    return {"payload": {"musicbrainz_id": mbid, "artist_name": name,
                        "is_discovered": True, "total_plays_global": plays},
            "vector": vector}


class _FakeQdrant:
    def __init__(self, points, search_hits=None):
        self._points = points
        self._search_hits = search_hits or []

    async def scroll(self, *, filter=None, limit=256, offset=None, with_vector=False):
        return self._points, None

    async def search(self, vector, *, limit=10, score_threshold=None, must=None, must_not=None):
        return self._search_hits


class _FakeLastfm:
    def __init__(self, tracks):
        self._tracks = tracks

    async def get_recent_tracks(self, from_ts=None, limit=200):
        return self._tracks


def _track(mbid=None, name=None):
    artist = {"mbid": mbid or "", "#text": name or ""}
    return {"artist": artist}


class ComputeRecentTasteCentroidTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    async def test_averages_only_recently_listened_points(self):
        points = [
            _point("mbid-a", "Artist A", [1.0, 0.0], plays=5),
            _point("mbid-b", "Artist B", [0.0, 1.0], plays=100),  # most-played, but NOT recent
        ]
        qdrant = _FakeQdrant(points)
        lastfm = _FakeLastfm([_track(mbid="mbid-a")])
        with patch("app.services.artist_discovery._qdrant", return_value=qdrant), \
             patch("app.services.artist_discovery._lastfm_client", return_value=lastfm):
            centroid = await compute_recent_taste_centroid(self.db, lookback_days=30)
        self.assertEqual(centroid, [1.0, 0.0])  # only Artist A, not the all-time favorite

    async def test_no_lastfm_returns_none(self):
        with patch("app.services.artist_discovery._qdrant", return_value=_FakeQdrant([])), \
             patch("app.services.artist_discovery._lastfm_client", return_value=None):
            centroid = await compute_recent_taste_centroid(self.db, lookback_days=30)
        self.assertIsNone(centroid)

    async def test_no_recent_scrobbles_returns_none(self):
        points = [_point("mbid-a", "Artist A", [1.0, 0.0])]
        with patch("app.services.artist_discovery._qdrant", return_value=_FakeQdrant(points)), \
             patch("app.services.artist_discovery._lastfm_client", return_value=_FakeLastfm([])):
            centroid = await compute_recent_taste_centroid(self.db, lookback_days=30)
        self.assertIsNone(centroid)

    async def test_recent_artist_not_yet_in_taste_space_returns_none(self):
        # Recently listened to an artist that isn't a discovered/embedded point.
        points = [_point("mbid-a", "Artist A", [1.0, 0.0])]
        with patch("app.services.artist_discovery._qdrant", return_value=_FakeQdrant(points)), \
             patch("app.services.artist_discovery._lastfm_client",
                   return_value=_FakeLastfm([_track(name="Someone Else")])):
            centroid = await compute_recent_taste_centroid(self.db, lookback_days=30)
        self.assertIsNone(centroid)


class RunCentroidDiscoveryTwoLaneTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.cfg = ArtistDiscoverySettings(recent_taste_lane_enabled=True)

    def tearDown(self):
        self.db.close()

    async def _run(self, points, search_hits, lastfm_tracks):
        qdrant = _FakeQdrant(points, search_hits=search_hits)
        lastfm = _FakeLastfm(lastfm_tracks)
        with patch("app.services.artist_discovery._qdrant", return_value=qdrant), \
             patch("app.services.artist_discovery._lastfm_client", return_value=lastfm), \
             patch("app.services.artist_discovery._enrich_candidate",
                   return_value={"image_url": None, "bio": None, "genres": [], "years_active": None}):
            return await run_centroid_discovery(self.db, self.cfg)

    async def test_both_lanes_tagged_with_distinct_source(self):
        points = [_point("seed-mbid", "Seed Artist", [1.0, 0.0], plays=10)]
        hit = {"payload": {"artist_name": "New Artist", "musicbrainz_id": "new-mbid"}, "score": 0.9}
        result = await self._run(points, [hit], [_track(mbid="seed-mbid")])
        self.assertEqual(result["candidates"], 1)  # same candidate found by both lanes, created once
        from app.models.artist_discovery import DiscoveredArtist
        row = self.db.query(DiscoveredArtist).filter_by(musicbrainz_id="new-mbid").first()
        self.assertIsNotNone(row)
        # First lane to create it wins the source tag — either is valid, just not duplicated.
        self.assertIn(row.source, ("centroid", "centroid_recent"))
        self.assertEqual(self.db.query(DiscoveredArtist).count(), 1)  # no duplicate row

    async def test_recent_lane_disabled_only_runs_all_time(self):
        self.cfg = ArtistDiscoverySettings(recent_taste_lane_enabled=False)
        points = [_point("seed-mbid", "Seed Artist", [1.0, 0.0], plays=10)]
        hit = {"payload": {"artist_name": "New Artist", "musicbrainz_id": "new-mbid"}, "score": 0.9}
        result = await self._run(points, [hit], [_track(mbid="seed-mbid")])
        self.assertEqual(result["candidates"], 1)
        self.assertNotIn("recent-taste", result["message"])

    async def test_no_recent_scrobbles_still_returns_all_time_results(self):
        points = [_point("seed-mbid", "Seed Artist", [1.0, 0.0], plays=10)]
        hit = {"payload": {"artist_name": "New Artist", "musicbrainz_id": "new-mbid"}, "score": 0.9}
        result = await self._run(points, [hit], [])  # nothing recently listened
        self.assertEqual(result["candidates"], 1)  # all-time lane still works


if __name__ == "__main__":
    unittest.main()
