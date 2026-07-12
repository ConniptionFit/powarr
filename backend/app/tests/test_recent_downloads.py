"""Unit tests for FI-09: recent-downloads browse/search + force re-import."""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.integration import Integration
from app.services.recent_downloads import list_recent_downloads


class _FakeClient:
    def __init__(self, history=(), queue=(), library=()):
        self._history = list(history)
        self._queue = list(queue)
        self._library = list(library)

    async def get_history(self, event_type=1, max_records=100):
        return self._history

    async def get_queue(self):
        return self._queue

    async def get_series(self):
        return self._library

    async def get_movies(self):
        return self._library

    async def get_albums(self):
        return self._library

    async def get_books(self):
        return self._library


class ListRecentDownloadsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.db.add(Integration(name="sonarr", enabled=True, url="http://s"))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    async def test_lists_recent_grab_with_matched_title(self):
        client = _FakeClient(
            history=[{"downloadId": "dl1", "seriesId": 42, "sourceTitle": "Show.S01E01",
                     "date": "2026-07-12T10:00:00Z", "eventType": "grabbed"}],
            library=[{"id": 42, "title": "Show"}],
        )
        with patch("app.services.recent_downloads._get_client", return_value=client):
            rows = await list_recent_downloads(self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_app"], "sonarr")
        self.assertEqual(rows[0]["matched_title"], "Show")
        self.assertEqual(rows[0]["download_id"], "dl1")

    async def test_still_in_queue_flag_from_queue_download_ids(self):
        client = _FakeClient(
            history=[{"downloadId": "dl1", "seriesId": 42, "sourceTitle": "X", "date": "2026-07-12T10:00:00Z"}],
            queue=[{"downloadId": "dl1"}],
            library=[{"id": 42, "title": "Show"}],
        )
        with patch("app.services.recent_downloads._get_client", return_value=client):
            rows = await list_recent_downloads(self.db)
        self.assertTrue(rows[0]["still_in_queue"])

    async def test_dedupes_multiple_history_events_for_same_download_id(self):
        # Same pack grab produces multiple episode-level history rows sharing
        # one downloadId — should collapse to a single browse row.
        client = _FakeClient(history=[
            {"downloadId": "dl1", "seriesId": 42, "sourceTitle": "Pack", "date": "2026-07-12T10:00:00Z"},
            {"downloadId": "dl1", "seriesId": 42, "sourceTitle": "Pack", "date": "2026-07-12T10:00:00Z"},
        ], library=[{"id": 42, "title": "Show"}])
        with patch("app.services.recent_downloads._get_client", return_value=client):
            rows = await list_recent_downloads(self.db)
        self.assertEqual(len(rows), 1)

    async def test_search_filters_by_title(self):
        client = _FakeClient(history=[
            {"downloadId": "dl1", "seriesId": 1, "sourceTitle": "Alpha.Release", "date": "2026-07-12T10:00:00Z"},
            {"downloadId": "dl2", "seriesId": 2, "sourceTitle": "Beta.Release", "date": "2026-07-12T09:00:00Z"},
        ], library=[{"id": 1, "title": "Alpha"}, {"id": 2, "title": "Beta"}])
        with patch("app.services.recent_downloads._get_client", return_value=client):
            rows = await list_recent_downloads(self.db, search="alpha")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["download_id"], "dl1")

    async def test_no_download_id_skipped(self):
        client = _FakeClient(history=[
            {"downloadId": None, "seriesId": 1, "sourceTitle": "No id", "date": "2026-07-12T10:00:00Z"},
        ])
        with patch("app.services.recent_downloads._get_client", return_value=client):
            rows = await list_recent_downloads(self.db)
        self.assertEqual(rows, [])

    async def test_disabled_app_skipped(self):
        # No radarr Integration row at all — should not raise, just skip it.
        with patch("app.services.recent_downloads._get_client", return_value=_FakeClient()):
            rows = await list_recent_downloads(self.db, source_app="radarr")
        self.assertEqual(rows, [])

    async def test_history_fetch_failure_is_fail_soft(self):
        class _BrokenClient(_FakeClient):
            async def get_history(self, event_type=1, max_records=100):
                raise RuntimeError("unreachable")
        with patch("app.services.recent_downloads._get_client", return_value=_BrokenClient()):
            rows = await list_recent_downloads(self.db)
        self.assertEqual(rows, [])

    async def test_sorted_newest_first(self):
        client = _FakeClient(history=[
            {"downloadId": "dl1", "seriesId": 1, "sourceTitle": "Older", "date": "2026-07-10T10:00:00Z"},
            {"downloadId": "dl2", "seriesId": 1, "sourceTitle": "Newer", "date": "2026-07-12T10:00:00Z"},
        ], library=[{"id": 1, "title": "Show"}])
        with patch("app.services.recent_downloads._get_client", return_value=client):
            rows = await list_recent_downloads(self.db)
        self.assertEqual([r["download_id"] for r in rows], ["dl2", "dl1"])


if __name__ == "__main__":
    unittest.main()
