"""Unit tests for the pure parts of llm_assist: prompt-value truncation caps,
model-size limit profiles, and the shared single-flight slot."""
import unittest

from app.services import llm_assist
from app.services.llm_assist import (
    CAP_CANDIDATE, CAP_CONTEXT, CAP_DET_SUMMARY, CAP_ITEM, CAP_RELEASE,
    _limits, _truncate, build_explain_prompt, build_review_prompt,
)


class TestTruncation(unittest.TestCase):
    def test_short_values_pass_through_unchanged(self):
        self.assertEqual(_truncate("abc", 10), "abc")
        self.assertEqual(_truncate("", 10), "")
        self.assertEqual(_truncate(None, 10), "")

    def test_long_values_capped_with_ellipsis(self):
        out = _truncate("x" * 500, 300)
        self.assertEqual(len(out), 300)
        self.assertTrue(out.endswith("…"))

    def test_review_prompt_caps_every_placeholder(self):
        huge = "R" * 5000
        prompt = build_review_prompt("", huge, huge, huge, huge)
        # Even with every field pathological, the prompt stays bounded by the caps
        # plus the static scaffold text.
        budget = CAP_RELEASE + CAP_CANDIDATE + CAP_CONTEXT + CAP_DET_SUMMARY + 600
        self.assertLess(len(prompt), budget)
        # No single substituted run may exceed the largest per-field cap.
        self.assertNotIn("R" * (CAP_DET_SUMMARY + 1), prompt)

    def test_review_prompt_normal_values_untouched(self):
        prompt = build_review_prompt("", "Show.S01E01.1080p", "Show", "ctx", "det")
        self.assertIn("Show.S01E01.1080p", prompt)
        self.assertNotIn("…", prompt)

    def test_explain_prompt_caps_item(self):
        prompt = build_explain_prompt("", "I" * 5000)
        self.assertNotIn("I" * (CAP_ITEM + 1), prompt)
        prompt = build_explain_prompt("", "Movie (2020), 4.2 GB")
        self.assertIn("Movie (2020), 4.2 GB", prompt)


class TestModelSizeLimits(unittest.TestCase):
    def test_small_is_capped_even_in_verbose(self):
        self.assertEqual(_limits("small", False), (96, 15))
        self.assertEqual(_limits("small", True), (96, 15))

    def test_medium_matches_legacy_behavior(self):
        self.assertEqual(_limits("medium", False), (160, 20))
        self.assertEqual(_limits("medium", True), (400, 45))

    def test_large_gets_verbose_headroom_only(self):
        self.assertEqual(_limits("large", False), (160, 20))
        self.assertEqual(_limits("large", True), (600, 60))

    def test_unknown_size_falls_back_to_medium(self):
        self.assertEqual(_limits("weird", True), (400, 45))


class TestSingleFlightSlot(unittest.TestCase):
    def tearDown(self):
        llm_assist.release_slot()

    def test_acquire_release_cycle(self):
        self.assertFalse(llm_assist.slot_active())
        self.assertTrue(llm_assist.acquire_slot())
        self.assertTrue(llm_assist.slot_active())
        self.assertFalse(llm_assist.acquire_slot())  # second caller is refused
        llm_assist.release_slot()
        self.assertFalse(llm_assist.slot_active())
        self.assertTrue(llm_assist.acquire_slot())


if __name__ == "__main__":
    unittest.main()
