"""Active Processes tray helpers (v0.33.0 coalesce / bump_total)."""
import unittest

from app.services import tasks


class TestBumpTotal(unittest.TestCase):
    def setUp(self):
        tasks._tasks.clear()

    def tearDown(self):
        tasks._tasks.clear()

    def test_bump_grows_total_and_label(self):
        tid = tasks.create_task("import_batch", "Importing 1 item(s)", total=1)
        tasks.bump_total(tid, 2, label="Importing 3 item(s)", message="+2 queued")
        t = tasks.get_task(tid)
        self.assertEqual(t.total, 3)
        self.assertEqual(t.label, "Importing 3 item(s)")
        self.assertEqual(t.message, "+2 queued")

    def test_find_running(self):
        a = tasks.create_task("llm_run", "A", total=1)
        tasks.create_task("import_batch", "B", total=1)
        found = tasks.find_running("llm_run")
        self.assertIsNotNone(found)
        self.assertEqual(found.id, a)
        tasks.finish_task(a, "done")
        self.assertIsNone(tasks.find_running("llm_run"))


if __name__ == "__main__":
    unittest.main()
