"""Unit tests for llm_assist: prompt-value truncation caps, model-size limit
profiles, the shared single-flight slot, reply parsing (JSON, simple, think-block
stripping), and the review/explain flows with a stubbed _generate."""
import unittest
from unittest.mock import AsyncMock, patch

from app.services import llm_assist
from app.services.llm_assist import (
    CAP_CANDIDATE, CAP_CONTEXT, CAP_DET_SUMMARY, CAP_ITEM, CAP_RELEASE,
    _limits, _parse_simple, _strip_think, _truncate,
    build_explain_prompt, build_review_prompt,
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


class TestThinkStripping(unittest.TestCase):
    def test_closed_block_stripped(self):
        self.assertEqual(_strip_think("<think>secret</think>answer"), "answer")

    def test_unclosed_block_stripped_to_end(self):
        # Token cap cut generation before </think> — everything from <think> on
        # is chain-of-thought and must never leak (seen live with lfm2.5).
        self.assertEqual(_strip_think("answer <think>ramble ramble"), "answer")
        self.assertEqual(_strip_think("<think>only ramble, no reply"), "")


class TestParseSimple(unittest.TestCase):
    def test_full_numeric_form(self):
        out = _parse_simple("agree | 0.2 | title and season match")
        self.assertEqual(out, {"agrees": True, "confidence_adjustment": 0.2,
                               "reason": "title and season match"})

    def test_classified_form(self):
        out = _parse_simple("disagree | less | year differs")
        self.assertEqual(out, {"agrees": False, "confidence_shift": "less",
                               "reason": "year differs"})

    def test_bare_verdict_minimal(self):
        self.assertEqual(_parse_simple("agree"), {"agrees": True})
        self.assertEqual(_parse_simple("Disagree."), {"agrees": False})

    def test_disagree_checked_before_agree_substring(self):
        self.assertFalse(_parse_simple("disagrees | -0.1 | nope")["agrees"])

    def test_prose_verdict_tolerated(self):
        out = _parse_simple("I agree with this match | +0.15 | solid")
        self.assertTrue(out["agrees"])
        self.assertEqual(out["confidence_adjustment"], 0.15)

    def test_second_segment_neither_number_nor_shift_becomes_reason(self):
        out = _parse_simple("agree | looks right to me")
        self.assertEqual(out["reason"], "looks right to me")
        self.assertNotIn("confidence_adjustment", out)

    def test_garbage_returns_none(self):
        self.assertIsNone(_parse_simple("the weather is nice"))
        self.assertIsNone(_parse_simple(""))
        self.assertIsNone(_parse_simple("<think>unfinished"))


class TestPromptShapes(unittest.TestCase):
    def test_minimal_json_asks_verdict_only(self):
        p = build_review_prompt("", "r", "c", "x", "d", verbosity="minimal")
        self.assertIn('{"agrees": true|false}', p)
        self.assertNotIn("confidence_adjustment", p)
        self.assertNotIn("reason", p)

    def test_classified_json_asks_shift_not_float(self):
        p = build_review_prompt("", "r", "c", "x", "d", confidence_style="classified")
        self.assertIn("confidence_shift", p)
        self.assertNotIn("confidence_adjustment", p)

    def test_simple_format_has_no_json_ask(self):
        p = build_review_prompt("", "r", "c", "x", "d", reply_format="simple")
        self.assertNotIn("JSON", p)
        self.assertIn("agree or disagree", p)

    def test_explain_minimal_asks_one_word(self):
        p = build_explain_prompt("", "item", verbosity="minimal")
        self.assertIn("KEEP or DELETE", p)


def _stubbed_review(reply, **kwargs):
    """review_match with _generate stubbed to return a canned reply."""
    with patch.object(llm_assist, "_generate", new=AsyncMock(return_value=reply)):
        import asyncio
        return asyncio.run(llm_assist.review_match("h", "m", "rel", "cand", "det", **kwargs))


class TestReviewMatchParsing(unittest.TestCase):
    def test_json_reply_parsed(self):
        out = _stubbed_review('{"agrees": true, "confidence_adjustment": 0.5, "reason": "ok"}')
        self.assertTrue(out["agrees"])
        self.assertEqual(out["confidence_adjustment"], 0.3)  # clamped

    def test_json_mode_falls_back_to_simple_parse(self):
        out = _stubbed_review("agree | 0.1 | close enough")
        self.assertEqual(out, {"agrees": True, "confidence_adjustment": 0.1,
                               "rationale": "close enough"})

    def test_simple_mode_falls_back_to_json_parse(self):
        out = _stubbed_review('{"agrees": false, "reason": "nah"}', reply_format="simple")
        self.assertFalse(out["agrees"])

    def test_minimal_ignores_stray_extras(self):
        out = _stubbed_review('{"agrees": true, "confidence_adjustment": 0.3, "reason": "x"}',
                              verbosity="minimal")
        self.assertEqual(out, {"agrees": True, "confidence_adjustment": 0.0, "rationale": ""})

    def test_classified_shift_maps_to_fixed_steps(self):
        out = _stubbed_review('{"agrees": true, "confidence_shift": "more", "reason": "x"}',
                              confidence_style="classified")
        self.assertEqual(out["confidence_adjustment"], 0.15)

    def test_disagree_with_positive_shift_clamped(self):
        out = _stubbed_review("disagree | more | confused model",
                              reply_format="simple", confidence_style="classified")
        self.assertEqual(out["confidence_adjustment"], 0.0)

    def test_unparseable_returns_none(self):
        self.assertIsNone(_stubbed_review("no idea what this is"))
        self.assertIsNone(_stubbed_review(None))


class TestExplainMinimal(unittest.TestCase):
    def _explain(self, reply, **kwargs):
        with patch.object(llm_assist, "_generate", new=AsyncMock(return_value=reply)):
            import asyncio
            return asyncio.run(llm_assist.explain_deletion("h", "m", "item", **kwargs))

    def test_minimal_extracts_verdict_word(self):
        self.assertEqual(self._explain("DELETE", verbosity="minimal"), "DELETE")
        self.assertEqual(self._explain("I would keep this one.", verbosity="minimal"), "KEEP")

    def test_unclosed_think_yields_none_not_leak(self):
        self.assertIsNone(self._explain("<think>rambling that never closes"))

    def test_minimal_salvages_verdict_from_truncated_think(self):
        # Live lfm2.5 failure mode: whole reply is an unclosed <think> that states
        # the verdict but never closes the block. Minimal salvages the last verdict
        # word; the CoT itself is never returned.
        raw = "<think>score is low... thus we should DELETE. We must output only"
        self.assertEqual(self._explain(raw, verbosity="minimal"), "DELETE")
        self.assertIsNone(self._explain(raw, verbosity="brief"))  # non-minimal never salvages

    def test_minimal_salvage_takes_last_verdict(self):
        raw = "<think>maybe keep? no — watched 0x, thus delete"
        self.assertEqual(self._explain(raw, verbosity="minimal"), "DELETE")


class TestReviewMinimalSalvage(unittest.TestCase):
    def test_salvages_agree_from_truncated_think(self):
        out = _stubbed_review("<think>title matches, season matches, so I agree with",
                              verbosity="minimal")
        self.assertEqual(out, {"agrees": True, "confidence_adjustment": 0.0, "rationale": ""})

    def test_disagree_inner_agree_not_miscounted(self):
        out = _stubbed_review("<think>year differs; I disagree with this", verbosity="minimal")
        self.assertFalse(out["agrees"])

    def test_non_minimal_does_not_salvage(self):
        self.assertIsNone(_stubbed_review("<think>I agree but never finish", verbosity="brief"))


class TestRationaleKey(unittest.TestCase):
    """media_llm.rationale_key — the cache must miss when the prompt config or the
    item's scoring-relevant fields change, and hit otherwise."""

    class _Item:
        def __init__(self, **kw):
            self.score = kw.get("score", 55.0)
            self.watch_count = kw.get("watch_count", 0)
            self.last_watched_at = kw.get("last_watched_at")
            self.file_size = kw.get("file_size", 10 ** 9)

    def _key(self, item=None, **ollama_overrides):
        from app.schemas.settings import OllamaSettings
        from app.services.media_llm import rationale_key
        ollama = OllamaSettings(**{"model": "m1", **ollama_overrides})
        return rationale_key(ollama, item or self._Item())

    def test_stable_for_same_inputs(self):
        self.assertEqual(self._key(), self._key())

    def test_changes_with_prompt_model_verbosity_and_score(self):
        base = self._key()
        self.assertNotEqual(base, self._key(explain_prompt="custom {item}"))
        self.assertNotEqual(base, self._key(model="m2"))
        self.assertNotEqual(base, self._key(verbosity="minimal"))
        self.assertNotEqual(base, self._key(item=self._Item(score=70.0)))

    def test_ignores_unrelated_settings(self):
        # keep_alive/batch pacing don't affect the generated text — same key.
        self.assertEqual(self._key(), self._key(keep_alive_minutes=99, batch_delay_ms=500))


if __name__ == "__main__":
    unittest.main()
