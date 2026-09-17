"""run_differential_sync no-op write skip (v0.78.2).

The hourly differential sync used to set_payload every Qdrant point on every
cycle even when nothing changed (observed live: the same 285 writes per hour).
Points whose computed updates already match the stored payload are now counted
as unchanged and skipped; points with any drifted field are still written.
"""
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.schemas.settings import ArtistDiscoverySettings
from app.services.artist_discovery import run_differential_sync


def _point(pid, name, **payload):
    base = {
        "artist_name": name, "musicbrainz_id": None, "genres": [], "era": "1990s",
        "is_monitored_lidarr": False, "plex_fulfillment": "none",
        "in_lidarr": False, "in_plex": False, "mood_tags": [],
    }
    base.update(payload)
    return {"id": pid, "payload": base}


class DifferentialSyncNoopSkipTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    async def _run(self, points):
        qdrant = AsyncMock()
        qdrant.scroll = AsyncMock(return_value=(points, None))
        qdrant.set_payload = AsyncMock()
        cfg = ArtistDiscoverySettings(enabled=True)
        with patch("app.services.artist_discovery.load_settings", return_value=cfg), \
             patch("app.services.artist_discovery._qdrant", return_value=qdrant), \
             patch("app.services.artist_discovery._lidarr_artist_index",
                   new=AsyncMock(return_value=({}, {}))), \
             patch("app.services.artist_discovery._plex_artist_names",
                   return_value={"owned artist"}), \
             patch("app.services.artist_discovery._lastfm_client", return_value=None):
            result = await run_differential_sync(self.db)
        return result, qdrant

    async def test_unchanged_point_is_skipped(self):
        result, qdrant = await self._run([_point("p1", "Some Artist")])
        self.assertTrue(result["ok"])
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["unchanged"], 1)
        qdrant.set_payload.assert_not_awaited()
        self.assertIn("unchanged", result["message"])

    async def test_drifted_point_is_still_written(self):
        # Stored payload says not in Plex, but the artist now is — must write.
        result, qdrant = await self._run(
            [_point("p1", "Owned Artist", in_plex=False)])
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["unchanged"], 0)
        qdrant.set_payload.assert_awaited_once()
        args = qdrant.set_payload.await_args.args
        self.assertEqual(args[0], ["p1"])
        self.assertTrue(args[1]["in_plex"])

    async def test_mixed_batch_only_writes_drifted_points(self):
        result, qdrant = await self._run([
            _point("p1", "Some Artist"),
            _point("p2", "Owned Artist"),
        ])
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["unchanged"], 1)
        self.assertEqual(qdrant.set_payload.await_count, 1)


if __name__ == "__main__":
    unittest.main()
