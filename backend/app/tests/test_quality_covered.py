"""Unit tests for equal-or-better library coverage helpers (v0.29.0)."""
import unittest

from app.services.import_matcher import is_quality_downgrade, queue_looks_like_quality_covered
from app.services.llm_assist import resolve_inference


class TestLidarrQualityCovered(unittest.TestCase):
    def test_album_already_imported_all_files(self):
        cands = [
            {"rejections": [{"reason": "Album already imported at 01/09/2025 00:02:38"}]},
            {"rejections": [{"reason": "Not an upgrade for existing album file(s)"}]},
        ]
        self.assertTrue(is_quality_downgrade(cands))

    def test_queue_message_fallback(self):
        self.assertTrue(queue_looks_like_quality_covered(
            "Not an upgrade for existing album file(s); 1 Curtains Up.flac"))


class TestResolveInference(unittest.TestCase):
    def test_zeros_keep_profile(self):
        mt, to, temp = resolve_inference("medium", False, temperature=0.2, max_tokens=0, timeout_seconds=0)
        self.assertEqual(mt, 160)
        self.assertEqual(to, 20)
        self.assertEqual(temp, 0.2)

    def test_overrides(self):
        mt, to, temp = resolve_inference("small", True, temperature=0.5, max_tokens=32, timeout_seconds=9)
        self.assertEqual((mt, to, temp), (32, 9, 0.5))


if __name__ == "__main__":
    unittest.main()
