"""Unit tests for scheduled-backup file management (pruning + listing). The
pg_dump/subprocess path itself isn't covered here (needs a real Postgres or a
mocked subprocess) — this exercises the pure filesystem logic that runs after.
Run inside the container: python -m unittest discover -s app/tests -v"""
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.services import backup


class TestBackupFileManagement(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_data_dir = settings.data_dir
        settings.data_dir = self._tmp.name

    def tearDown(self):
        settings.data_dir = self._orig_data_dir
        self._tmp.cleanup()

    def _touch(self, name: str):
        d = backup.backup_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / name
        path.write_text("x")
        return path

    def test_list_backups_empty_when_no_dir(self):
        self.assertEqual(backup.list_backups(), [])

    def test_list_backups_newest_first(self):
        p1 = self._touch("powarr-20260101T000000Z.sql")
        time.sleep(0.01)
        p2 = self._touch("powarr-20260102T000000Z.sql")
        names = [b["name"] for b in backup.list_backups()]
        self.assertEqual(names, [p2.name, p1.name])

    def test_prune_keeps_only_retention_count(self):
        for i in range(5):
            self._touch(f"powarr-{i}.sql")
            time.sleep(0.01)
        deleted = backup.prune_backups(2)
        self.assertEqual(deleted, 3)
        self.assertEqual(len(backup.list_backups()), 2)

    def test_prune_zero_retention_means_unlimited(self):
        for i in range(3):
            self._touch(f"powarr-{i}.sql")
        deleted = backup.prune_backups(0)
        self.assertEqual(deleted, 0)
        self.assertEqual(len(backup.list_backups()), 3)

    def test_prune_ignores_non_backup_files(self):
        self._touch("powarr-1.sql")
        other = backup.backup_dir() / "unrelated.txt"
        other.write_text("keep me")
        backup.prune_backups(0)
        self.assertTrue(other.exists())


class TestBackupStatus(unittest.TestCase):
    """OPS-03 — staleness assessment. Same tmp-data-dir harness as above."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_data_dir = settings.data_dir
        settings.data_dir = self._tmp.name
        self.now = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        settings.data_dir = self._orig_data_dir
        self._tmp.cleanup()

    def _touch(self, name: str, age_hours: float = 0.0):
        d = backup.backup_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / name
        path.write_text("x")
        mtime = (self.now - timedelta(hours=age_hours)).timestamp()
        os.utime(path, (mtime, mtime))
        return path

    def _iso_ago(self, hours: float) -> str:
        # Same shape the scheduler writes: naive-UTC isoformat
        return (self.now - timedelta(hours=hours)).replace(tzinfo=None).isoformat()

    def test_disabled_is_never_stale(self):
        s = backup.backup_status(False, 24, None, now=self.now)
        self.assertFalse(s["stale"])
        self.assertFalse(s["enabled"])

    def test_zero_interval_counts_as_disabled(self):
        s = backup.backup_status(True, 0, self._iso_ago(999), now=self.now)
        self.assertFalse(s["enabled"])
        self.assertFalse(s["stale"])

    def test_enabled_but_never_ran_is_stale(self):
        s = backup.backup_status(True, 24, None, now=self.now)
        self.assertTrue(s["stale"])
        self.assertIn("ever completed", s["reason"])
        self.assertIsNone(s["last_backup"])

    def test_recent_backup_not_stale(self):
        s = backup.backup_status(True, 24, self._iso_ago(20), now=self.now)
        self.assertFalse(s["stale"])
        self.assertAlmostEqual(s["age_hours"], 20.0, places=1)

    def test_within_2x_grace_not_stale(self):
        # One missed tick (interval < age <= 2x interval) shouldn't alarm
        s = backup.backup_status(True, 24, self._iso_ago(40), now=self.now)
        self.assertFalse(s["stale"])

    def test_beyond_2x_interval_is_stale(self):
        s = backup.backup_status(True, 24, self._iso_ago(72), now=self.now)
        self.assertTrue(s["stale"])
        self.assertIn("72h", s["reason"])

    def test_newest_file_mtime_backfills_missing_setting(self):
        self._touch("powarr-a.sql", age_hours=10)
        s = backup.backup_status(True, 24, None, now=self.now)
        self.assertFalse(s["stale"])
        self.assertEqual(s["newest_file"], "powarr-a.sql")
        self.assertEqual(s["backup_count"], 1)
        self.assertAlmostEqual(s["age_hours"], 10.0, places=1)

    def test_garbage_timestamp_falls_back_to_files_then_never(self):
        s = backup.backup_status(True, 24, "not-a-date", now=self.now)
        self.assertTrue(s["stale"])
        self.assertIn("ever completed", s["reason"])


if __name__ == "__main__":
    unittest.main()
