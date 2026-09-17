"""Unit tests for SP-15's differential-sync side: mood_tags refreshed from
genres every cycle (pure, no I/O), era backfilled via a bounded, rate-limited
MusicBrainz lookup only for points that don't already have one."""
import json
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.app_setting import AppSetting
from app.services.artist_discovery import run_differential_sync


def _point(pid, mbid, name, genres=None, era=""):
    return {"id": pid, "payload": {
        "musicbrainz_id": mbid, "artist_name": name,
        "genres": genres or [], "era": era, "mood_tags": [],
        "total_plays_global": 0,
    }}


class _FakeQdrant:
    def __init__(self, points):
        self._points = points
        self.set_payload_calls: list[tuple[list, dict]] = []

    async def scroll(self, *, limit=256, offset=None):
        return self._points, None

    async def set_payload(self, ids, updates):
        self.set_payload_calls.append((ids, updates))

    def _update_for(self, pid):
        for ids, updates in self.set_payload_calls:
            if pid in ids:
                return updates
        return None


class DifferentialSyncMoodEraTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.db.add(AppSetting(key="artist_discovery", value=json.dumps({"enabled": True})))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    async def _run(self, points, mb_return=None):
        qdrant = _FakeQdrant(points)
        with patch("app.services.artist_discovery._qdrant", return_value=qdrant), \
             patch("app.services.artist_discovery._lidarr_artist_index", return_value=({}, {})), \
             patch("app.services.artist_discovery._plex_artist_names", return_value=set()), \
             patch("app.services.artist_discovery._lastfm_client", return_value=None), \
             patch("app.integrations.musicbrainz.get_artist", return_value=mb_return) as mb_mock:
            result = await run_differential_sync(self.db)
        return result, qdrant, mb_mock

    async def test_mood_tags_recomputed_from_genres_every_cycle(self):
        points = [_point("p1", "mbid-a", "Artist", genres=["Rock", "Chill"])]
        result, qdrant, _ = await self._run(points)
        self.assertTrue(result["ok"])
        updates = qdrant._update_for("p1")
        self.assertEqual(updates["mood_tags"], ["Chill"])

    async def test_era_backfilled_when_missing_and_mbid_present(self):
        points = [_point("p1", "mbid-a", "Artist", genres=[], era="")]
        mb_data = {"life-span": {"begin": "1994-01-01"}}
        result, qdrant, mb_mock = await self._run(points, mb_return=mb_data)
        self.assertTrue(result["ok"])
        updates = qdrant._update_for("p1")
        self.assertEqual(updates["era"], "1990s")
        mb_mock.assert_called_once_with("mbid-a")

    async def test_era_not_refetched_when_already_set(self):
        points = [_point("p1", "mbid-a", "Artist", genres=[], era="1980s")]
        result, qdrant, mb_mock = await self._run(points, mb_return={"life-span": {"begin": "2020-01-01"}})
        self.assertTrue(result["ok"])
        updates = qdrant._update_for("p1")
        self.assertNotIn("era", updates)  # untouched — existing value preserved
        mb_mock.assert_not_called()

    async def test_era_backfill_bounded_per_run(self):
        # More missing-era points than the cap — only the first N get a
        # MusicBrainz call this cycle, same bounded-backfill precedent as
        # re_enrich_missing(); the rest catch up on a later cycle.
        points = [_point(f"p{i}", f"mbid-{i}", f"Artist {i}", era="") for i in range(25)]
        result, qdrant, mb_mock = await self._run(points, mb_return={"life-span": {"begin": "2000-01-01"}})
        self.assertTrue(result["ok"])
        self.assertEqual(mb_mock.call_count, 20)  # _ERA_BACKFILL_CAP

    async def test_no_musicbrainz_id_skips_era_backfill(self):
        points = [_point("p1", None, "Artist", genres=[], era="")]
        result, qdrant, mb_mock = await self._run(points)
        self.assertTrue(result["ok"])
        mb_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
