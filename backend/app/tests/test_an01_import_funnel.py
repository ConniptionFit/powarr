"""Unit tests for AN-01: import funnel analytics."""
import json
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.failed_import import FailedImport
from app.services.import_funnel import compute_import_funnel, _tracked_state


def _row(**over):
    base = dict(source_app="sonarr", raw_title="Title", status="suggested",
                verified=None, created_at=datetime.utcnow(), raw_metadata=None)
    base.update(over)
    return FailedImport(**base)


class TrackedStateTests(unittest.TestCase):
    def test_orphaned_status_wins_over_metadata(self):
        row = _row(status="orphaned", raw_metadata=json.dumps({"trackedDownloadState": "importFailed"}))
        self.assertEqual(_tracked_state(row), "orphaned")

    def test_reads_tracked_download_state_from_metadata(self):
        row = _row(status="rejected", raw_metadata=json.dumps({"trackedDownloadState": "importBlocked"}))
        self.assertEqual(_tracked_state(row), "importBlocked")

    def test_unknown_when_metadata_missing(self):
        row = _row(status="rejected", raw_metadata=None)
        self.assertEqual(_tracked_state(row), "unknown")

    def test_unknown_when_state_not_a_recognized_stuck_state(self):
        row = _row(status="rejected", raw_metadata=json.dumps({"trackedDownloadState": "downloading"}))
        self.assertEqual(_tracked_state(row), "unknown")

    def test_unknown_on_malformed_json(self):
        row = _row(status="rejected", raw_metadata="{not json")
        self.assertEqual(_tracked_state(row), "unknown")


class ComputeImportFunnelTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    def test_empty_returns_zero_totals(self):
        stats = compute_import_funnel(self.db)
        self.assertEqual(stats["overall"]["total"], 0)
        self.assertEqual(stats["by_app"], [])

    def test_counts_by_status(self):
        self.db.add_all([
            _row(status="suggested"),
            _row(status="accepted"),
            _row(status="auto_resolved"),
            _row(status="rejected"),
        ])
        self.db.commit()
        stats = compute_import_funnel(self.db)
        overall = stats["overall"]
        self.assertEqual(overall["total"], 4)
        self.assertEqual(overall["suggested"], 1)
        self.assertEqual(overall["accepted_or_auto"], 2)
        self.assertEqual(overall["failed"], 1)

    def test_verified_only_counts_successful_verified_rows(self):
        self.db.add_all([
            _row(status="accepted", verified=True),
            _row(status="accepted", verified=False),
            _row(status="auto_resolved", verified=True),
            _row(status="rejected", verified=True),  # verified irrelevant on a failure row
        ])
        self.db.commit()
        stats = compute_import_funnel(self.db)
        overall = stats["overall"]
        self.assertEqual(overall["accepted_or_auto"], 3)
        self.assertEqual(overall["verified"], 2)
        self.assertAlmostEqual(overall["verified_rate"], 2 / 3, places=3)

    def test_failed_rate_computed_correctly(self):
        self.db.add_all([
            _row(status="accepted"),
            _row(status="rejected"),
            _row(status="orphaned"),
            _row(status="resolve_failed"),
        ])
        self.db.commit()
        stats = compute_import_funnel(self.db)
        overall = stats["overall"]
        self.assertEqual(overall["failed"], 3)
        self.assertAlmostEqual(overall["failed_rate"], 0.75)

    def test_open_rows_are_neither_success_nor_failure(self):
        self.db.add_all([_row(status="suggested"), _row(status="orphan_pending")])
        self.db.commit()
        stats = compute_import_funnel(self.db)
        overall = stats["overall"]
        self.assertEqual(overall["accepted_or_auto"], 0)
        self.assertEqual(overall["failed"], 0)
        # total > 0 (two open rows), zero of them are failures yet — a real 0.0
        # rate, distinct from None (which means no rows exist at all / no
        # accepted-or-auto rows exist yet for the verified-rate denominator).
        self.assertEqual(overall["failed_rate"], 0.0)
        self.assertIsNone(overall["verified_rate"])

    def test_failure_reason_breakdown(self):
        self.db.add_all([
            _row(status="rejected", raw_metadata=json.dumps({"trackedDownloadState": "importFailed"})),
            _row(status="rejected", raw_metadata=json.dumps({"trackedDownloadState": "importFailed"})),
            _row(status="orphaned"),
        ])
        self.db.commit()
        stats = compute_import_funnel(self.db)
        breakdown = stats["overall"]["failure_reason_breakdown"]
        self.assertEqual(breakdown, {"importFailed": 2, "orphaned": 1})

    def test_grouped_by_app(self):
        self.db.add_all([
            _row(source_app="sonarr", status="accepted"),
            _row(source_app="sonarr", status="rejected"),
            _row(source_app="radarr", status="accepted"),
        ])
        self.db.commit()
        stats = compute_import_funnel(self.db)
        by_app = {g["app"]: g["total"] for g in stats["by_app"]}
        self.assertEqual(by_app, {"sonarr": 2, "radarr": 1})
        sonarr = next(g for g in stats["by_app"] if g["app"] == "sonarr")
        self.assertEqual(sonarr["failed"], 1)

    def test_by_app_sorted_alphabetically(self):
        self.db.add_all([_row(source_app="radarr"), _row(source_app="lidarr"), _row(source_app="sonarr")])
        self.db.commit()
        stats = compute_import_funnel(self.db)
        apps = [g["app"] for g in stats["by_app"]]
        self.assertEqual(apps, ["lidarr", "radarr", "sonarr"])

    def test_days_filter_excludes_old_rows(self):
        self.db.add_all([
            _row(created_at=datetime.utcnow()),
            _row(created_at=datetime.utcnow() - timedelta(days=30)),
        ])
        self.db.commit()
        stats = compute_import_funnel(self.db, days=7)
        self.assertEqual(stats["overall"]["total"], 1)

    def test_days_none_includes_all_rows(self):
        self.db.add_all([
            _row(created_at=datetime.utcnow()),
            _row(created_at=datetime.utcnow() - timedelta(days=365)),
        ])
        self.db.commit()
        stats = compute_import_funnel(self.db, days=None)
        self.assertEqual(stats["overall"]["total"], 2)


if __name__ == "__main__":
    unittest.main()
