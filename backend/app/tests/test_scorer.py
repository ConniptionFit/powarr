"""Unit tests for cleanup scoring (v0.30.0): series-aware watch, sqrt size,
watch half-life, and per-library weight overlays."""
import unittest
from datetime import datetime, timedelta

from app.schemas.settings import ScoringWeights, ScoringProfiles
from app.services.scorer import score_item, score_breakdown, merge_weights, weights_for_library


def _base(**overrides) -> dict:
    now = datetime.utcnow()
    data = {
        "watch_count": 0,
        "last_watched_at": None,
        "series_watched": False,
        "series_last_watched_at": None,
        "file_size": 10 * 1024 ** 3,  # 10 GB
        "added_at": now - timedelta(days=365),
        "release_date": now - timedelta(days=365 * 5),
        "library_section": "TV",
    }
    data.update(overrides)
    return data


class TestSeriesWatch(unittest.TestCase):
    def test_never_watched_episode_gets_boost(self):
        w = ScoringWeights(file_size_weight=0, file_age_weight=0, release_date_weight=0)
        score = score_item(_base(), w)
        # never_watched_boost=2.0 capped at 1.0 → full watch factor → 100
        self.assertEqual(score, 100.0)

    def test_sibling_watched_suppresses_never_watched_boost(self):
        w = ScoringWeights(file_size_weight=0, file_age_weight=0, release_date_weight=0)
        recent = datetime.utcnow() - timedelta(days=7)
        score = score_item(_base(series_watched=True, series_last_watched_at=recent), w)
        # Engaged via series → exponential decay from 7 days << half-life → low score
        self.assertLess(score, 20.0)

    def test_own_watch_counts_even_without_series_flag(self):
        w = ScoringWeights(file_size_weight=0, file_age_weight=0, release_date_weight=0)
        recent = datetime.utcnow() - timedelta(days=3)
        score = score_item(_base(watch_count=1, last_watched_at=recent), w)
        self.assertLess(score, 10.0)


class TestSizeCurve(unittest.TestCase):
    def test_sqrt_curve_is_below_linear_for_mid_size(self):
        # 12.5 GB of 50 GB ref → linear 0.25, sqrt ≈ 0.5
        w = ScoringWeights(watch_history_weight=0, file_age_weight=0, release_date_weight=0,
                           max_size_gb_reference=50.0)
        score = score_item(_base(file_size=int(12.5 * 1024 ** 3)), w)
        self.assertAlmostEqual(score, 50.0, places=0)


class TestWatchHalfLife(unittest.TestCase):
    def test_shorter_half_life_raises_score_for_same_age(self):
        last = datetime.utcnow() - timedelta(days=180)
        item = _base(watch_count=1, last_watched_at=last)
        slow = ScoringWeights(file_size_weight=0, file_age_weight=0, release_date_weight=0,
                              watch_half_life_days=365.0)
        fast = ScoringWeights(file_size_weight=0, file_age_weight=0, release_date_weight=0,
                              watch_half_life_days=90.0)
        self.assertGreater(score_item(item, fast), score_item(item, slow))


class TestProfiles(unittest.TestCase):
    def test_merge_partial_overlay(self):
        base = ScoringWeights()
        merged = merge_weights(base, {"file_size_weight": 9.0, "watch_half_life_days": 90})
        self.assertEqual(merged.file_size_weight, 9.0)
        self.assertEqual(merged.watch_half_life_days, 90)
        self.assertEqual(merged.watch_history_weight, base.watch_history_weight)

    def test_weights_for_library_applies_overlay(self):
        base = ScoringWeights()
        profiles = ScoringProfiles(by_library={"Anime": {"never_watched_boost": 1.0}})
        eff = weights_for_library(base, profiles, "Anime")
        self.assertEqual(eff.never_watched_boost, 1.0)
        self.assertEqual(weights_for_library(base, profiles, "TV").never_watched_boost,
                         base.never_watched_boost)


class TestScoreBreakdown(unittest.TestCase):
    def test_breakdown_matches_score_item(self):
        w = ScoringWeights()
        item = _base()
        bd = score_breakdown(item, w)
        self.assertEqual(bd["score"], score_item(item, w))
        self.assertIn("watch", bd["factors"])
        self.assertIn("size", bd["factors"])

    def test_series_watched_flag_surfaced(self):
        w = ScoringWeights(file_size_weight=0, file_age_weight=0, release_date_weight=0)
        bd = score_breakdown(_base(series_watched=True, watch_count=0,
                                   series_last_watched_at=datetime.utcnow()), w)
        self.assertTrue(bd["series_watched"])
        self.assertLess(bd["factors"]["watch"], 1.0)


if __name__ == "__main__":
    unittest.main()
