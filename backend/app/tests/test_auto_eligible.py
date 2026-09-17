"""Unit tests for Process N / auto-eligible import helpers (v0.28.0)
and the dual-signal auto-import gate (v0.44.0)."""
import unittest
from types import SimpleNamespace

from app.schemas.settings import ImportMatchingSettings
from app.services.auto_eligible import is_auto_eligible, passes_auto_thresholds, describe_auto_gate


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


class TestDescribeAutoGate(unittest.TestCase):
    """FI-07 — "why not auto?" inspector. Must never disagree with is_auto_eligible."""

    def test_would_auto_import_when_eligible(self):
        cfg = _cfg()
        gate = describe_auto_gate(_item(heuristic_confidence=0.95), cfg)
        self.assertTrue(gate["would_auto_import"])
        self.assertEqual(gate["reasons"], [])
        self.assertTrue(gate["algorithm"]["passes"])

    def test_auto_resolve_disabled_reason(self):
        cfg = _cfg(auto_resolve_enabled=False)
        gate = describe_auto_gate(_item(heuristic_confidence=0.95), cfg)
        self.assertFalse(gate["would_auto_import"])
        self.assertIn("Auto-resolve is turned off", gate["reasons"][0])

    def test_wrong_status_reason(self):
        cfg = _cfg()
        gate = describe_auto_gate(_item(status="accepted", heuristic_confidence=0.95), cfg)
        self.assertFalse(gate["would_auto_import"])
        self.assertTrue(any("Status 'accepted'" in r for r in gate["reasons"]))

    def test_no_match_reason(self):
        cfg = _cfg()
        gate = describe_auto_gate(_item(matched_id=None, heuristic_confidence=0.95), cfg)
        self.assertFalse(gate["would_auto_import"])
        self.assertTrue(any("No matched library item" in r for r in gate["reasons"]))

    def test_algorithm_mode_below_threshold_reason(self):
        cfg = _cfg(auto_import_mode="algorithm")
        gate = describe_auto_gate(_item(heuristic_confidence=0.7), cfg)
        self.assertFalse(gate["would_auto_import"])
        self.assertFalse(gate["algorithm"]["passes"])
        self.assertTrue(any("Algorithm confidence (0.70)" in r for r in gate["reasons"]))

    def test_both_mode_reports_each_failing_leg(self):
        cfg = _cfg(auto_import_mode="both")
        gate = describe_auto_gate(_item(heuristic_confidence=0.95, llm_confidence=0.5), cfg)
        self.assertFalse(gate["would_auto_import"])
        self.assertTrue(gate["algorithm"]["passes"])
        self.assertFalse(gate["llm"]["passes"])
        self.assertEqual(len(gate["reasons"]), 1)
        self.assertIn("LLM confidence (0.50)", gate["reasons"][0])

    def test_either_mode_neither_leg_passes(self):
        cfg = _cfg(auto_import_mode="either")
        gate = describe_auto_gate(_item(heuristic_confidence=0.5, llm_confidence=0.5), cfg)
        self.assertFalse(gate["would_auto_import"])
        self.assertIn("Neither leg passed", gate["reasons"][0])

    def test_missing_llm_score_worded_as_no_score(self):
        cfg = _cfg(auto_import_mode="llm")
        gate = describe_auto_gate(_item(heuristic_confidence=0.95, llm_confidence=None), cfg)
        self.assertFalse(gate["would_auto_import"])
        self.assertIn("no score", gate["reasons"][0])

    def test_agrees_with_is_auto_eligible(self):
        cases = [
            _item(heuristic_confidence=0.95),
            _item(status="rejected", heuristic_confidence=0.95),
            _item(matched_id=None, heuristic_confidence=0.95),
            _item(heuristic_confidence=0.5, llm_confidence=0.5),
            _item(heuristic_confidence=0.95, llm_confidence=0.95),
        ]
        for cfg in (_cfg(), _cfg(auto_import_mode="both"), _cfg(auto_import_mode="llm"),
                    _cfg(auto_resolve_enabled=False)):
            for item in cases:
                self.assertEqual(
                    is_auto_eligible(item, cfg),
                    describe_auto_gate(item, cfg)["would_auto_import"],
                    msg=f"mismatch for mode={cfg.auto_import_mode} item={item}",
                )


if __name__ == "__main__":
    unittest.main()
