"""Unit tests for LLM-06: in-app LLM accuracy dashboard aggregation."""
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.llm_match_log import LlmMatchLog
from app.services.llm_match_log import compute_accuracy_stats


def _log(**over):
    base = dict(source_app="sonarr", model="qwen2.5:7b", scaffold_version="v3",
               parse_ok=True, agrees=True, enforced=False, latency_ms=1200,
               resolution=None, created_at=datetime.utcnow())
    base.update(over)
    return LlmMatchLog(**base)


class ComputeAccuracyStatsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    def test_empty_returns_zero_totals(self):
        stats = compute_accuracy_stats(self.db)
        self.assertEqual(stats["overall"]["total"], 0)
        self.assertIsNone(stats["overall"]["parse_ok_rate"])

    def test_parse_ok_and_agree_rates(self):
        self.db.add_all([
            _log(parse_ok=True, agrees=True),
            _log(parse_ok=True, agrees=True),
            _log(parse_ok=True, agrees=False),
            _log(parse_ok=False, agrees=None),
        ])
        self.db.commit()
        stats = compute_accuracy_stats(self.db)
        overall = stats["overall"]
        self.assertEqual(overall["total"], 4)
        self.assertAlmostEqual(overall["parse_ok_rate"], 0.75)
        self.assertAlmostEqual(overall["agree_rate"], 2 / 3, places=3)  # of the 3 parsed rows

    def test_enforced_rate(self):
        self.db.add_all([
            _log(parse_ok=True, enforced=True),
            _log(parse_ok=True, enforced=False),
        ])
        self.db.commit()
        stats = compute_accuracy_stats(self.db)
        self.assertAlmostEqual(stats["overall"]["enforced_rate"], 0.5)

    def test_avg_latency(self):
        self.db.add_all([_log(latency_ms=1000), _log(latency_ms=2000)])
        self.db.commit()
        stats = compute_accuracy_stats(self.db)
        self.assertAlmostEqual(stats["overall"]["avg_latency_ms"], 1500.0)

    def test_outcome_agreement_rate_correct_agree(self):
        # agrees=True and eventually accepted -> counted as correct.
        self.db.add_all([
            _log(agrees=True, resolution="accepted"),
            _log(agrees=True, resolution="rejected"),  # wrong call
        ])
        self.db.commit()
        stats = compute_accuracy_stats(self.db)
        self.assertEqual(stats["overall"]["outcome_sample_size"], 2)
        self.assertAlmostEqual(stats["overall"]["outcome_agreement_rate"], 0.5)

    def test_outcome_agreement_rate_correct_disagree(self):
        # agrees=False and eventually rejected -> also counted as correct.
        self.db.add_all([_log(agrees=False, resolution="rejected")])
        self.db.commit()
        stats = compute_accuracy_stats(self.db)
        self.assertAlmostEqual(stats["overall"]["outcome_agreement_rate"], 1.0)

    def test_open_rows_excluded_from_outcome_scoring(self):
        self.db.add_all([_log(agrees=True, resolution=None)])
        self.db.commit()
        stats = compute_accuracy_stats(self.db)
        self.assertEqual(stats["overall"]["outcome_sample_size"], 0)
        self.assertIsNone(stats["overall"]["outcome_agreement_rate"])

    def test_orphaned_resolution_excluded_from_outcome_scoring(self):
        # Regression test for a real finding from live testing: "orphaned"
        # means the download's files disappeared, which has nothing to do
        # with whether the LLM's match judgment was correct — scoring it as
        # "wrong" produced a misleadingly low accuracy number (8.5% on real
        # data that was actually a 100% agree rate with unrelated orphaning).
        self.db.add_all([
            _log(agrees=True, resolution="orphaned"),
            _log(agrees=True, resolution="orphaned"),
            _log(agrees=True, resolution="accepted"),
        ])
        self.db.commit()
        stats = compute_accuracy_stats(self.db)
        # Only the "accepted" row counts toward the outcome sample.
        self.assertEqual(stats["overall"]["outcome_sample_size"], 1)
        self.assertAlmostEqual(stats["overall"]["outcome_agreement_rate"], 1.0)
        # But orphaned rows still show up in the raw resolution breakdown.
        self.assertEqual(stats["overall"]["resolution_breakdown"]["orphaned"], 2)

    def test_resolution_breakdown(self):
        self.db.add_all([
            _log(resolution="accepted"), _log(resolution="accepted"), _log(resolution="rejected"),
        ])
        self.db.commit()
        stats = compute_accuracy_stats(self.db)
        self.assertEqual(stats["overall"]["resolution_breakdown"], {"accepted": 2, "rejected": 1})

    def test_grouped_by_source_app(self):
        self.db.add_all([
            _log(source_app="sonarr"), _log(source_app="sonarr"), _log(source_app="lidarr"),
        ])
        self.db.commit()
        stats = compute_accuracy_stats(self.db)
        by_app = {g["key"]: g["total"] for g in stats["by_source_app"]}
        self.assertEqual(by_app, {"sonarr": 2, "lidarr": 1})

    def test_grouped_by_model_and_scaffold_version(self):
        self.db.add_all([
            _log(model="qwen2.5:7b", scaffold_version="v3"),
            _log(model="qwen3:4b", scaffold_version="v3"),
        ])
        self.db.commit()
        stats = compute_accuracy_stats(self.db)
        models = {g["key"] for g in stats["by_model"]}
        self.assertEqual(models, {"qwen2.5:7b", "qwen3:4b"})
        versions = {g["key"] for g in stats["by_scaffold_version"]}
        self.assertEqual(versions, {"v3"})

    def test_days_filter_excludes_old_rows(self):
        self.db.add_all([
            _log(created_at=datetime.utcnow()),
            _log(created_at=datetime.utcnow() - timedelta(days=100)),
        ])
        self.db.commit()
        stats = compute_accuracy_stats(self.db, days=30)
        self.assertEqual(stats["overall"]["total"], 1)

    def test_null_group_key_bucketed_as_unknown(self):
        self.db.add_all([_log(source_app=None)])
        self.db.commit()
        stats = compute_accuracy_stats(self.db)
        keys = {g["key"] for g in stats["by_source_app"]}
        self.assertIn("(unknown)", keys)


if __name__ == "__main__":
    unittest.main()
