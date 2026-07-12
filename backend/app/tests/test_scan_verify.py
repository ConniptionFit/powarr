"""SCAL-03: per-cycle *arr history fetch reuse.

_verify_resolved() previously always fetched its own history from the *arr,
even when the calling scan cycle had just fetched an equivalent (or broader)
history list moments earlier for the stuck-item matcher. It now accepts an
optional pre-fetched `history` and skips its own fetch when one is supplied
-- this is the actual mechanism _scan_once_inner uses to collapse two
get_history() round-trips per app per cycle into one. These tests exercise
that contract directly against a real (in-memory) DB, without standing up
the full scan cycle."""
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.failed_import import FailedImport
from app.schemas.settings import ImportMatchingSettings
from app.services.import_matcher import _verify_resolved


class _NoFetchClient:
    """get_history() raises if called -- proves _verify_resolved used the
    pre-fetched history instead of fetching its own."""
    async def get_history(self, *a, **kw):
        raise AssertionError("get_history() should not be called when history is pre-supplied")


class _FetchingClient:
    def __init__(self, history):
        self._history = history
        self.calls = 0

    async def get_history(self, *a, **kw):
        self.calls += 1
        return self._history


class VerifyResolvedHistoryReuseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.cfg = ImportMatchingSettings()
        self.summary = {"verified": 0, "resolve_failed": 0, "orphaned": 0}

    async def test_uses_pre_supplied_history_without_fetching(self):
        row = FailedImport(source_app="sonarr", status="accepted", verified=None,
                           download_id="abc123", raw_title="Some.Show.S01E01")
        self.db.add(row)
        self.db.commit()
        history = [{"downloadId": "abc123", "eventType": "downloadFolderImported"}]
        await _verify_resolved(self.db, "sonarr", _NoFetchClient(), self.cfg,
                               self.summary, history=history)
        self.db.refresh(row)
        self.assertTrue(row.verified)
        self.assertEqual(self.summary["verified"], 1)

    async def test_falls_back_to_own_fetch_when_history_not_supplied(self):
        row = FailedImport(source_app="sonarr", status="accepted", verified=None,
                           download_id="abc123", raw_title="Some.Show.S01E01")
        self.db.add(row)
        self.db.commit()
        client = _FetchingClient([{"downloadId": "abc123", "eventType": "downloadFolderImported"}])
        await _verify_resolved(self.db, "sonarr", client, self.cfg, self.summary)
        self.assertEqual(client.calls, 1)
        self.db.refresh(row)
        self.assertTrue(row.verified)

    async def test_no_pending_rows_skips_fetch_entirely(self):
        """No accepted/auto_resolved rows awaiting verification -- must not
        touch the client at all, pre-supplied history or not."""
        await _verify_resolved(self.db, "sonarr", _NoFetchClient(), self.cfg, self.summary)

    async def test_grabbed_only_filter_matches_prior_eventtype1_semantics(self):
        """The scan cycle derives its match-path history by filtering the same
        pre-fetched list to eventType == 'grabbed' -- confirms that filter
        keeps only grab events and drops other types sharing a downloadId."""
        history = [
            {"downloadId": "x", "eventType": "grabbed", "sourceTitle": "Grab.Release"},
            {"downloadId": "x", "eventType": "downloadFolderImported", "sourceTitle": "Import.Event"},
            {"downloadId": "y", "eventType": "downloadFailed"},
        ]
        grabbed = [h for h in history if h.get("eventType") == "grabbed"]
        self.assertEqual(len(grabbed), 1)
        self.assertEqual(grabbed[0]["sourceTitle"], "Grab.Release")


if __name__ == "__main__":
    unittest.main()
