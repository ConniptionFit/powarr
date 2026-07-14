"""Due-ness gate on scheduled Smart Playlists generation (v0.78.2).

_scheduled_playlist_generation was the only job in the maintenance loop with no
last-run interval check — generation ran on every 5-minute tick regardless of
SmartPlaylistSettings.schedule_interval_hours (observed live: a full 30-40s
generation every ~5.5 minutes against a configured 24h interval). These tests
pin the gate to the same AppSetting-timestamp pattern every sibling job uses.
"""
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.app_setting import AppSetting
from app.services.scheduler import _scheduled_playlist_generation

RUN_RESULT = {"ok": True, "message": "ok", "playlists": 1}


class PlaylistGenerationGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def _seed_settings(self, **overrides):
        value = {"enabled": True, "schedule_enabled": True,
                 "schedule_interval_hours": 24, **overrides}
        self.db.add(AppSetting(key="smart_playlists", value=json.dumps(value)))
        self.db.commit()

    def _last_run_row(self):
        return self.db.query(AppSetting).filter_by(key="last_playlist_generation").first()

    async def test_first_run_executes_and_stamps_last_run(self):
        self._seed_settings()
        with patch("app.services.playlist_generator.run_scheduled_generation",
                   new=AsyncMock(return_value=RUN_RESULT)) as mock_run:
            await _scheduled_playlist_generation(self.db)
        self.assertEqual(mock_run.await_count, 1)
        row = self._last_run_row()
        self.assertIsNotNone(row)
        self.assertIsNotNone(datetime.fromisoformat(row.value))

    async def test_second_tick_within_interval_is_skipped(self):
        self._seed_settings()
        with patch("app.services.playlist_generator.run_scheduled_generation",
                   new=AsyncMock(return_value=RUN_RESULT)) as mock_run:
            await _scheduled_playlist_generation(self.db)
            await _scheduled_playlist_generation(self.db)
        self.assertEqual(mock_run.await_count, 1)

    async def test_runs_again_once_interval_has_elapsed(self):
        self._seed_settings()
        stale = (datetime.utcnow() - timedelta(hours=25)).isoformat()
        self.db.add(AppSetting(key="last_playlist_generation", value=stale))
        self.db.commit()
        with patch("app.services.playlist_generator.run_scheduled_generation",
                   new=AsyncMock(return_value=RUN_RESULT)) as mock_run:
            await _scheduled_playlist_generation(self.db)
        self.assertEqual(mock_run.await_count, 1)
        self.assertGreater(datetime.fromisoformat(self._last_run_row().value),
                           datetime.fromisoformat(stale))

    async def test_unparseable_last_run_falls_through_to_running(self):
        self._seed_settings()
        self.db.add(AppSetting(key="last_playlist_generation", value="not-a-date"))
        self.db.commit()
        with patch("app.services.playlist_generator.run_scheduled_generation",
                   new=AsyncMock(return_value=RUN_RESULT)) as mock_run:
            await _scheduled_playlist_generation(self.db)
        self.assertEqual(mock_run.await_count, 1)

    async def test_disabled_schedule_never_runs_or_stamps(self):
        self._seed_settings(schedule_enabled=False)
        with patch("app.services.playlist_generator.run_scheduled_generation",
                   new=AsyncMock(return_value=RUN_RESULT)) as mock_run:
            await _scheduled_playlist_generation(self.db)
        self.assertEqual(mock_run.await_count, 0)
        self.assertIsNone(self._last_run_row())

    async def test_zero_interval_runs_every_tick(self):
        # interval 0 keeps the pre-v0.78.2 every-tick behavior as an escape
        # hatch, matching _scheduled_artist_discovery's timedelta semantics.
        self._seed_settings(schedule_interval_hours=0)
        with patch("app.services.playlist_generator.run_scheduled_generation",
                   new=AsyncMock(return_value=RUN_RESULT)) as mock_run:
            await _scheduled_playlist_generation(self.db)
            await _scheduled_playlist_generation(self.db)
        self.assertEqual(mock_run.await_count, 2)


if __name__ == "__main__":
    unittest.main()
