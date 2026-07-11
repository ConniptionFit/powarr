"""Unit tests for Process N / auto-eligible import helpers (v0.28.0)
and the dual-signal auto-import gate (v0.44.0)."""
import unittest
from types import SimpleNamespace

from app.schemas.settings import ImportMatchingSettings
from app.services.auto_eligible import is_auto_eligible, passes_auto_thresholds


def _item(**kwargs):
    defaults = dict(status="suggested", confidence=0.95, matched_id=1,
                    heuristic_confidence=None, llm_confidence=None)
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _cfg(**kwargs):
    defaults = dict(auto_resolve_enabled=True, high_confidence_threshold=0.9,
                    llm_auto_threshold=0.8)
    defaults.update(kwargs)
    return ImportMatchingSettings(**defaults)


class TestIsAutoEligible(unittest.TestCase):
    def test_off_when_auto_resolve_disabled(self):
        cfg = _cfg(auto_resolve_enabled=False)
        self.assertFalse(is_auto_eligible(_item(), cfg))

    def test_meets_threshold(self):
        cfg = _cfg()
        self.assertTrue(is_auto_eligible(_item(confidence=0.9), cfg))
        self.assertTrue(is_auto_eligible(_item(confidence=0.99), cfg))

    def test_below_threshold(self):
        cfg = _cfg()
        self.assertFalse(is_auto_eligible(_item(confidence=0.89), cfg))

    def test_resolve_failed_ok(self):
        cfg = _cfg()
        self.assertTrue(is_auto_eligible(_item(status="resolve_failed"), cfg))

    def test_wrong_status(self):
        cfg = _cfg()
        self.assertFalse(is_auto_eligible(_item(status="accepted"), cfg))
        self.assertFalse(is_auto_eligible(_item(status="orphan_pending"), cfg))

    def test_requires_matched_id(self):
        cfg = _cfg()
        self.assertFalse(is_auto_eligible(_item(matched_id=None), cfg))

    def test_prefers_stored_heuristic_over_blend(self):
        # heuristic below the algorithm bar, blended above it → not eligible
        # (the gate reads the raw signals, never the blend — v0.44.0)
        cfg = _cfg(auto_import_mode="algorithm")
        self.assertFalse(is_auto_eligible(
            _item(confidence=0.95, heuristic_confidence=0.7), cfg))
        self.assertTrue(is_auto_eligible(
            _item(confidence=0.5, heuristic_confidence=0.95), cfg))

    def test_llm_leg_qualifies_in_either_mode(self):
        cfg = _cfg(auto_import_mode="either")
        self.assertTrue(is_auto_eligible(
            _item(confidence=0.6, heuristic_confidence=0.6, llm_confidence=0.95), cfg))


class TestPassesAutoThresholds(unittest.TestCase):
    """User-specified semantics (2026-07-11): algorithm bar 0.90, LLM bar 0.80."""

    def test_either_user_example(self):
        # LLM 95% with algorithm 50%: Either imports, Both fails.
        cfg = _cfg(auto_import_mode="either")
        self.assertTrue(passes_auto_thresholds(0.50, 0.95, cfg))
        cfg = _cfg(auto_import_mode="both")
        self.assertFalse(passes_auto_thresholds(0.50, 0.95, cfg))

    def test_both_requires_both(self):
        cfg = _cfg(auto_import_mode="both")
        self.assertTrue(passes_auto_thresholds(0.92, 0.85, cfg))
        self.assertFalse(passes_auto_thresholds(0.92, 0.79, cfg))
        self.assertFalse(passes_auto_thresholds(0.89, 0.85, cfg))

    def test_llm_only(self):
        cfg = _cfg(auto_import_mode="llm")
        self.assertTrue(passes_auto_thresholds(0.1, 0.8, cfg))
        self.assertFalse(passes_auto_thresholds(0.99, 0.79, cfg))

    def test_algorithm_only(self):
        cfg = _cfg(auto_import_mode="algorithm")
        self.assertTrue(passes_auto_thresholds(0.9, None, cfg))
        self.assertFalse(passes_auto_thresholds(0.89, 1.0, cfg))

    def test_missing_llm_score_fails_llm_leg(self):
        # No LLM score: either → algorithm-only; llm/both → never pass.
        self.assertTrue(passes_auto_thresholds(0.95, None, _cfg(auto_import_mode="either")))
        self.assertFalse(passes_auto_thresholds(0.5, None, _cfg(auto_import_mode="either")))
        self.assertFalse(passes_auto_thresholds(0.95, None, _cfg(auto_import_mode="llm")))
        self.assertFalse(passes_auto_thresholds(0.95, None, _cfg(auto_import_mode="both")))

    def test_missing_heuristic_fails_algorithm_leg(self):
        self.assertFalse(passes_auto_thresholds(None, 0.5, _cfg(auto_import_mode="either")))
        self.assertTrue(passes_auto_thresholds(None, 0.85, _cfg(auto_import_mode="either")))

    def test_unknown_mode_falls_back_to_either(self):
        cfg = _cfg(auto_import_mode="banana")
        self.assertTrue(passes_auto_thresholds(0.95, None, cfg))
        self.assertTrue(passes_auto_thresholds(0.1, 0.85, cfg))
        self.assertFalse(passes_auto_thresholds(0.1, 0.1, cfg))

    def test_default_mode_is_either(self):
        self.assertEqual(ImportMatchingSettings().auto_import_mode, "either")
        self.assertAlmostEqual(ImportMatchingSettings().llm_auto_threshold, 0.80)


if __name__ == "__main__":
    unittest.main()
