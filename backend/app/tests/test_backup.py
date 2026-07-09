"""Unit tests for scheduled-backup file management (pruning + listing). The
pg_dump/subprocess path itself isn't covered here (needs a real Postgres or a
mocked subprocess) — this exercises the pure filesystem logic that runs after.
Run inside the container: python -m unittest discover -s app/tests -v"""
import os
import tempfile
import time
import unittest

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


if __name__ == "__main__":
    unittest.main()
