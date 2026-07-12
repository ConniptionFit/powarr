"""Unit tests for FI-10: nightly malformed-import audit."""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.integration import Integration
from app.models.malformed_import_flag import MalformedImportFlag
from app.services.malformed_audit import run_malformed_import_audit


def _episode(id_, *, season=1, has_file=True, aired_days_ago=10, absolute=None):
    return {
        "id": id_,
        "seasonNumber": season,
        "hasFile": has_file,
        "absoluteEpisodeNumber": absolute,
        "airDateUtc": (datetime.utcnow() - timedelta(days=aired_days_ago)).isoformat() + "Z",
    }


def _history_row(download_id, *, series_id=42, source_title="Show.S01.1080p.WEB-DL",
                 days_ago=1):
    return {
        "downloadId": download_id, "seriesId": series_id, "sourceTitle": source_title,
        "date": (datetime.utcnow() - timedelta(days=days_ago)).isoformat() + "Z",
    }


class _FakeSonarrClient:
    def __init__(self, history, episodes, queue=()):
        self._history = history
        self._episodes = episodes
        self._queue = list(queue)

    async def get_history(self, event_type=1, max_records=500):
        return self._history

    async def get_queue(self):
        return self._queue

    async def get_series(self):
        return [{"id": 42, "title": "Show"}]

    async def get_episodes(self, series_id):
        return self._episodes


class MalformedAuditTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.db.add(Integration(name="sonarr", enabled=True, url="http://s"))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    async def test_incomplete_pack_gets_flagged(self):
        history = [_history_row("dl1", source_title="Show.S01.1080p.WEB-DL")]
        episodes = [_episode(i, has_file=(i <= 10)) for i in range(1, 21)]  # 10/20 = 50%
        client = _FakeSonarrClient(history, episodes)
        with patch("app.services.malformed_audit._get_client", return_value=client):
            result = await run_malformed_import_audit(self.db, lookback_days=7, threshold=0.9)
        self.assertEqual(result["flagged"], 1)
        flag = self.db.query(MalformedImportFlag).first()
        self.assertEqual(flag.download_id, "dl1")
        self.assertEqual(flag.mapped_episodes, 10)
        self.assertEqual(flag.total_episodes, 20)
        self.assertAlmostEqual(flag.coverage_ratio, 0.5)
        self.assertEqual(flag.matched_title, "Show")

    async def test_complete_pack_not_flagged(self):
        history = [_history_row("dl1")]
        episodes = [_episode(i, has_file=True) for i in range(1, 21)]
        client = _FakeSonarrClient(history, episodes)
        with patch("app.services.malformed_audit._get_client", return_value=client):
            result = await run_malformed_import_audit(self.db, lookback_days=7, threshold=0.9)
        self.assertEqual(result["flagged"], 0)
        self.assertEqual(self.db.query(MalformedImportFlag).count(), 0)

    async def test_still_in_queue_skipped(self):
        history = [_history_row("dl1")]
        episodes = [_episode(i, has_file=(i <= 5)) for i in range(1, 21)]
        client = _FakeSonarrClient(history, episodes, queue=[{"downloadId": "dl1"}])
        with patch("app.services.malformed_audit._get_client", return_value=client):
            result = await run_malformed_import_audit(self.db, lookback_days=7, threshold=0.9)
        self.assertEqual(result["flagged"], 0)  # still active — not "settled"

    async def test_single_episode_grab_not_audited(self):
        history = [_history_row("dl1", source_title="Show.S01E01.1080p.WEB-DL")]
        episodes = [_episode(1)]
        client = _FakeSonarrClient(history, episodes)
        with patch("app.services.malformed_audit._get_client", return_value=client):
            result = await run_malformed_import_audit(self.db, lookback_days=7, threshold=0.9)
        self.assertEqual(result["checked"], 0)
        self.assertEqual(result["flagged"], 0)

    async def test_outside_lookback_window_skipped(self):
        history = [_history_row("dl1", days_ago=30)]
        episodes = [_episode(i, has_file=False) for i in range(1, 21)]
        client = _FakeSonarrClient(history, episodes)
        with patch("app.services.malformed_audit._get_client", return_value=client):
            result = await run_malformed_import_audit(self.db, lookback_days=7, threshold=0.9)
        self.assertEqual(result["checked"], 0)

    async def test_already_flagged_download_id_not_reflagged(self):
        self.db.add(MalformedImportFlag(
            source_app="sonarr", download_id="dl1", source_title="Show.S01.1080p.WEB-DL",
            mapped_episodes=5, total_episodes=20, coverage_ratio=0.25,
        ))
        self.db.commit()
        history = [_history_row("dl1")]
        episodes = [_episode(i, has_file=(i <= 5)) for i in range(1, 21)]
        client = _FakeSonarrClient(history, episodes)
        with patch("app.services.malformed_audit._get_client", return_value=client):
            result = await run_malformed_import_audit(self.db, lookback_days=7, threshold=0.9)
        self.assertEqual(result["flagged"], 0)
        self.assertEqual(self.db.query(MalformedImportFlag).count(), 1)  # unchanged

    async def test_absolute_range_pack_uses_absolute_scope(self):
        history = [_history_row("dl1", source_title="[Group] Anime - 001-100 [Batch]")]
        # 1000-episode long-runner; pack claims 1-100, only 50 have files.
        episodes = [_episode(i, absolute=i, has_file=(i <= 50)) for i in range(1, 1001)]
        client = _FakeSonarrClient(history, episodes)
        with patch("app.services.malformed_audit._get_client", return_value=client):
            result = await run_malformed_import_audit(self.db, lookback_days=7, threshold=0.9)
        self.assertEqual(result["flagged"], 1)
        flag = self.db.query(MalformedImportFlag).first()
        self.assertEqual(flag.total_episodes, 100)  # not 1000
        self.assertEqual(flag.mapped_episodes, 50)
        self.assertEqual(flag.pack_label, "1-100 (absolute)")

    async def test_disabled_sonarr_returns_empty(self):
        self.db.query(Integration).delete()
        self.db.commit()
        with patch("app.services.malformed_audit._get_client", return_value=_FakeSonarrClient([], [])):
            result = await run_malformed_import_audit(self.db, lookback_days=7, threshold=0.9)
        self.assertEqual(result, {"checked": 0, "flagged": 0, "new_flags": []})


if __name__ == "__main__":
    unittest.main()
