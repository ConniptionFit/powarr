"""still_in_queue tracking + reopen (v0.35.0)."""
import unittest
from unittest.mock import MagicMock

from app.services.import_matcher import _close_stale_rows
from app.api.v1 import imports as imports_api


class _Row:
    def __init__(self, **kw):
        self.source_app = kw.get("source_app", "sonarr")
        self.status = kw.get("status", "suggested")
        self.download_id = kw.get("download_id")
        self.queue_item_id = kw.get("queue_item_id")
        self.still_in_queue = kw.get("still_in_queue")
        self.message = kw.get("message")
        self.resolved_at = kw.get("resolved_at")
        self.updated_at = None
        self.id = kw.get("id", 1)


class TestCloseStaleStillInQueue(unittest.TestCase):
    def test_marks_presence_and_closes_suggested(self):
        suggested_gone = _Row(status="suggested", download_id="aaa", still_in_queue=True)
        accepted_still = _Row(status="accepted", download_id="bbb", still_in_queue=False)
        rejected_gone = _Row(status="rejected", download_id="ccc", still_in_queue=True)

        # First query: tracked (still_in_queue True OR suggested)
        # Second query: download_ids in queue with still_in_queue false/null
        # Third query: queue_item_id path (empty)
        tracked = [suggested_gone, rejected_gone]
        to_mark = [accepted_still]

        db = MagicMock()
        call_n = {"n": 0}

        def query_side_effect(*_a, **_k):
            q = MagicMock()
            q.filter.return_value = q

            def all_side():
                call_n["n"] += 1
                if call_n["n"] == 1:
                    return tracked
                if call_n["n"] == 2:
                    return to_mark
                return []

            q.all.side_effect = all_side
            return q

        db.query.side_effect = query_side_effect

        summary = {"closed_external": 0}
        queue = [{"id": 1, "downloadId": "bbb"}]
        _close_stale_rows(db, "sonarr", queue, summary)

        self.assertEqual(suggested_gone.status, "closed_external")
        self.assertFalse(suggested_gone.still_in_queue)
        self.assertTrue(accepted_still.still_in_queue)
        self.assertFalse(rejected_gone.still_in_queue)
        self.assertEqual(summary["closed_external"], 1)
        db.commit.assert_called()


class TestReopen(unittest.TestCase):
    def test_reopen_accepted(self):
        item = _Row(status="accepted", id=42, message="pushed")
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = item
        out = imports_api._reopen(42, db)
        self.assertEqual(out["status"], "suggested")
        self.assertEqual(out["previous_status"], "accepted")
        self.assertIsNone(item.resolved_at)
        self.assertIn("Reopened from accepted", item.message)

    def test_reopen_already_triage_noop(self):
        item = _Row(status="suggested", id=7)
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = item
        out = imports_api._reopen(7, db)
        self.assertEqual(out["status"], "suggested")
        self.assertEqual(out.get("message"), "Already in triage")


if __name__ == "__main__":
    unittest.main()
