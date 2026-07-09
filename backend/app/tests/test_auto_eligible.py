"""Unit tests for Process N / auto-eligible import helpers (v0.28.0)."""
import unittest
from types import SimpleNamespace

from app.schemas.settings import ImportMatchingSettings
from app.services.auto_eligible import is_auto_eligible


def _item(**kwargs):
    defaults = dict(status="suggested", confidence=0.95, matched_id=1)
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestIsAutoEligible(unittest.TestCase):
    def test_off_when_auto_resolve_disabled(self):
        cfg = ImportMatchingSettings(auto_resolve_enabled=False, high_confidence_threshold=0.9)
        self.assertFalse(is_auto_eligible(_item(), cfg))

    def test_meets_threshold(self):
        cfg = ImportMatchingSettings(auto_resolve_enabled=True, high_confidence_threshold=0.9)
        self.assertTrue(is_auto_eligible(_item(confidence=0.9), cfg))
        self.assertTrue(is_auto_eligible(_item(confidence=0.99), cfg))

    def test_below_threshold(self):
        cfg = ImportMatchingSettings(auto_resolve_enabled=True, high_confidence_threshold=0.9)
        self.assertFalse(is_auto_eligible(_item(confidence=0.89), cfg))

    def test_resolve_failed_ok(self):
        cfg = ImportMatchingSettings(auto_resolve_enabled=True, high_confidence_threshold=0.9)
        self.assertTrue(is_auto_eligible(_item(status="resolve_failed"), cfg))

    def test_wrong_status(self):
        cfg = ImportMatchingSettings(auto_resolve_enabled=True, high_confidence_threshold=0.9)
        self.assertFalse(is_auto_eligible(_item(status="accepted"), cfg))
        self.assertFalse(is_auto_eligible(_item(status="orphan_pending"), cfg))

    def test_requires_matched_id(self):
        cfg = ImportMatchingSettings(auto_resolve_enabled=True, high_confidence_threshold=0.9)
        self.assertFalse(is_auto_eligible(_item(matched_id=None), cfg))


if __name__ == "__main__":
    unittest.main()
