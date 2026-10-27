"""Regression: candidates whose seeds are stored as MBIDs but scrobbled by name
must still reach the recent-connections auto-add bar, and eligible pending rows
are swept into Lidarr instead of waiting for a seed re-expansion."""
import json
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.artist_discovery import DiscoveredArtist
from app.schemas.settings import ArtistDiscoverySettings
from app.services import artist_discovery as svc

MBIDS = ["fd36f43e-5bd1-488e-b844-0e059250ef78",
         "fa01a34e-05f3-424c-8fd0-390840f51d74",
         "e6e63b7f-3819-4c48-a3f5-90e439bdea13"]
NAMES = ["glaive", "aldn", "ericdoa"]
RECENT = {n for n in NAMES} | {svc._norm_artist(n) for n in NAMES}  # name-only scrobbles


def _db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class _FakeQdrant:
    def point_id(self, mbid, name):
        return mbid

    async def retrieve_points(self, ids, **kw):
        by_id = dict(zip(MBIDS, NAMES))
        return [{"payload": {"musicbrainz_id": i, "artist_name": by_id[i]}} for i in ids]


class TestNameAwareRecentCount(unittest.IsolatedAsyncioTestCase):
    async def test_mbid_seeds_counted_via_resolved_names(self):
        n = await svc._recent_count_with_names(_FakeQdrant(), MBIDS, RECENT)
        self.assertEqual(n, 3)

    async def test_gate_auto_adds_pending_row_with_mbid_seeds(self):
        db = _db()
        row = DiscoveredArtist(musicbrainz_id="m1", artist_name="kurtains", status="pending",
                               associated_seed_mbids=json.dumps(MBIDS))
        db.add(row)
        db.commit()
        cfg = ArtistDiscoverySettings(suggest_connection_threshold=2, auto_add_connection_threshold=3)
        with patch.object(svc, "add_to_lidarr", new_callable=AsyncMock) as add, \
             patch.object(svc, "_resolve_seed_names", new_callable=AsyncMock, return_value=NAMES):
            add.return_value = {"ok": True}
            res = await svc._gate_and_create_candidate(
                db, _FakeQdrant(), cfg, RECENT, mbid="m1", name="kurtains", seeds_list=MBIDS,
                source_label="ingest", seed_artist_name="aldn", payload={})
        self.assertEqual(res["promoted"], 1)
        add.assert_called_once()


class TestPendingSweep(unittest.IsolatedAsyncioTestCase):
    async def _run(self, recent_keys, has_lastfm=True):
        db = _db()
        db.add(DiscoveredArtist(musicbrainz_id="m1", artist_name="kurtains", status="pending",
                                associated_seed_mbids=json.dumps(MBIDS),
                                seed_artist_names=json.dumps(NAMES)))
        db.commit()
        cfg = ArtistDiscoverySettings(auto_add_connection_threshold=3, auto_add_all_time_threshold=0)
        with patch.object(svc, "get_cached_recent_keys_and_status", new_callable=AsyncMock,
                          return_value=(recent_keys, has_lastfm)), \
             patch.object(svc, "add_to_lidarr", new_callable=AsyncMock) as add:
            add.return_value = {"ok": True}
            return await svc.promote_eligible_pending(db, cfg), add

    async def test_sweep_adds_eligible(self):
        n, add = await self._run(RECENT)
        self.assertEqual(n, 1)
        add.assert_called_once()

    async def test_sweep_skips_when_not_recent(self):
        n, add = await self._run({"someone else"})
        self.assertEqual(n, 0)
        add.assert_not_called()

    async def test_sweep_disabled_when_thresholds_zero(self):
        db = _db()
        cfg = ArtistDiscoverySettings(auto_add_connection_threshold=0, auto_add_all_time_threshold=0,
                                      auto_promote=False)
        self.assertEqual(await svc.promote_eligible_pending(db, cfg), 0)


if __name__ == "__main__":
    unittest.main()
