"""Unit tests for llm_assist: prompt-value truncation caps, model-size limit
profiles, the shared single-flight slot, reply parsing (JSON, simple, think-block
stripping), and the review/explain flows with a stubbed _generate."""
import unittest
from unittest.mock import AsyncMock, patch

from app.services import llm_assist
from app.services.llm_assist import (
    CAP_CANDIDATE, CAP_CONTEXT, CAP_DET_SUMMARY, CAP_ITEM, CAP_RELEASE,
    _limits, _parse_simple, _parse_pack_matches, _strip_think, _truncate,
    build_explain_prompt, build_review_prompt, build_pack_prompt,
    PACK_MATCH_TYPES, review_pack_files,
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
        # plus the static scaffold text (reply-format instruction + the fixed
        # judging-guidance block added in v0.20.0).
        # Scaffold grew in v0.30.0 (junk-strip / anime / agree-default / no-think).
        budget = CAP_RELEASE + CAP_CANDIDATE + CAP_CONTEXT + CAP_DET_SUMMARY + 1600
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
        # Reply schema is verdict-only; "reason" may still appear in judging guidance.
        self.assertTrue(p.rstrip().endswith('{"agrees": true|false}'))

    def test_classified_json_asks_shift_not_float(self):
        p = build_review_prompt("", "r", "c", "x", "d", confidence_style="classified")
        self.assertIn("confidence_shift", p)
        self.assertNotIn("confidence_adjustment", p)

    def test_simple_format_asks_pipe_line(self):
        p = build_review_prompt("", "r", "c", "x", "d", reply_format="simple")
        self.assertIn("agree or disagree", p)
        self.assertIn("Reply with ONLY one line", p)

    def test_explain_minimal_asks_one_word(self):
        p = build_explain_prompt("", "item", verbosity="minimal")
        self.assertIn("KEEP or DELETE", p)

    def test_review_defaults_to_agree_and_strips_junk(self):
        p = build_review_prompt("", "r", "c", "x", "d")
        self.assertIn("Default to AGREE", p)
        self.assertIn("Strip from the release name", p)
        self.assertIn("Anime & foreign titles", p)
        self.assertIn("Do NOT write chain-of-thought", p)
        self.assertIn("bullet reasons", p)

    def test_forbid_thinking_can_be_disabled(self):
        p = build_review_prompt("", "r", "c", "x", "d", forbid_thinking=False)
        self.assertNotIn("Do NOT write chain-of-thought", p)

    def test_pack_prompt_uses_folder_and_strip_guidance(self):
        p = build_pack_prompt("", "rel", "cand", "a.mkv", "ctx", folder_name="Show.S01-GRP")
        self.assertIn("Download folder name: Show.S01-GRP", p)
        self.assertIn("Strip quality/codec/uploader", p)
        self.assertIn("folder name, AND each filename", p)


class TestCompactDetSummary(unittest.TestCase):
    def test_includes_heuristic_and_tokens(self):
        from app.services.llm_assist import compact_det_summary
        out = compact_det_summary(
            "episode title similarity 0.9; season+episode numbers match; capped title-only",
            0.88, pack_label="S02")
        self.assertIn("heuristic=0.88", out)
        self.assertIn("pack=S02", out)
        self.assertIn("title", out)
        self.assertIn("numeric", out)
        self.assertIn("capped", out)


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


def _stubbed_pack_review(reply, **kwargs):
    """review_pack_files with _generate stubbed to return a canned reply."""
    with patch.object(llm_assist, "_generate", new=AsyncMock(return_value=reply)):
        import asyncio
        return asyncio.run(llm_assist.review_pack_files(
            "h", "m", "rel", "cand", ["a.mkv", "b.mkv"], **kwargs))


class TestPackMatchPrompt(unittest.TestCase):
    def test_prompt_lists_all_match_types(self):
        p = build_pack_prompt("", "rel", "cand", "a.mkv, b.mkv", "ctx")
        for t in PACK_MATCH_TYPES:
            self.assertIn(t, p)

    def test_minimal_still_asks_match_type(self):
        p = build_pack_prompt("", "rel", "cand", "a.mkv", "ctx", verbosity="minimal")
        self.assertIn("match_type", p)
        self.assertNotIn('"reason"', p)


class TestParsePackMatches(unittest.TestCase):
    def test_proper_array_parsed(self):
        out = _parse_pack_matches('[{"file": "a.mkv", "season": 1, "episode": 1}]')
        self.assertEqual(out, [{"file": "a.mkv", "season": 1, "episode": 1}])

    def test_single_object_salvaged_as_one_item_list(self):
        # A weaker model on a large pack sometimes collapses to a single object
        # (answering only the first file) despite the "for each file" instruction.
        out = _parse_pack_matches('{"file": "a.mkv", "season": 1, "episode": 1}')
        self.assertEqual(out, [{"file": "a.mkv", "season": 1, "episode": 1}])

    def test_object_without_file_key_not_salvaged(self):
        self.assertIsNone(_parse_pack_matches('{"season": 1, "episode": 1}'))

    def test_array_embedded_in_prose_extracted(self):
        out = _parse_pack_matches('Sure, here you go:\n[{"file": "a.mkv", "season": 1, "episode": 2}]\nHope that helps!')
        self.assertEqual(out, [{"file": "a.mkv", "season": 1, "episode": 2}])

    def test_object_embedded_in_prose_extracted(self):
        out = _parse_pack_matches('Here is the match:\n{"file": "a.mkv", "season": 1, "episode": 2}')
        self.assertEqual(out, [{"file": "a.mkv", "season": 1, "episode": 2}])

    def test_garbage_returns_none(self):
        self.assertIsNone(_parse_pack_matches("no idea what this is"))
        self.assertIsNone(_parse_pack_matches(""))


class TestPackMatchValidation(unittest.TestCase):
    def test_recognized_match_type_normalized(self):
        out = _stubbed_pack_review(
            '[{"file": "a.mkv", "season": 1, "episode": 1, "match_type": "exact match", "confidence": "high"}]')
        self.assertEqual(out[0]["match_type"], "Exact Match")

    def test_unrecognized_match_type_falls_back_to_low_confidence(self):
        out = _stubbed_pack_review(
            '[{"file": "a.mkv", "season": 1, "episode": 1, "match_type": "Best Guess", "confidence": "low"}]')
        self.assertEqual(out[0]["match_type"], "Low Confidence")

    def test_missing_match_type_falls_back_to_low_confidence(self):
        out = _stubbed_pack_review(
            '[{"file": "a.mkv", "season": 1, "episode": 1, "confidence": "medium"}]')
        self.assertEqual(out[0]["match_type"], "Low Confidence")


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


class TestStreamVisible(unittest.TestCase):
    def test_plain_text_passes_through(self):
        self.assertEqual(llm_assist._stream_visible("a good candidate"), "a good candidate")

    def test_open_think_suppressed_until_closed(self):
        self.assertEqual(llm_assist._stream_visible("<think>hmm"), "")
        self.assertEqual(llm_assist._stream_visible("<think>hmm</think>DELETE"), "DELETE")

    def test_partial_tag_held_back(self):
        # "<thi" could become "<think>" on the next chunk — must not be emitted.
        self.assertEqual(llm_assist._stream_visible("answer <thi"), "answer ")
        self.assertEqual(llm_assist._stream_visible("answer <"), "answer ")
        # A "<" that turns out to be ordinary text is released once disambiguated.
        self.assertEqual(llm_assist._stream_visible("answer <b>"), "answer <b>")

    def test_monotonic_growth_across_chunks(self):
        acc = ""
        emitted = ""
        for chunk in ["Keep it: ", "<thi", "nk>secret", "</th", "ink>", " watched 5x"]:
            acc += chunk
            vis = llm_assist._stream_visible(acc)
            self.assertTrue(vis.startswith(emitted))  # never retracts shown text
            emitted = vis
        self.assertEqual(emitted, "Keep it:  watched 5x")


class TestExplainStream(unittest.TestCase):
    def _run(self, chunks, **kwargs):
        import asyncio

        async def fake_stream(*a, **k):
            for c in chunks:
                yield c

        async def collect():
            out = []
            with patch.object(llm_assist, "_generate_stream", new=fake_stream):
                async for piece in llm_assist.explain_deletion_stream("h", "m", "item", **kwargs):
                    out.append(piece)
            return out

        return asyncio.run(collect())

    def test_streams_visible_text_only(self):
        pieces = self._run(["<think>reasoning ", "here</think>", "Solid delete", " candidate"],
                           verbosity="verbose")
        self.assertEqual("".join(pieces), "Solid delete candidate")

    def test_brief_stops_at_first_line(self):
        pieces = self._run(["One short sentence.\nSecond line never shows"])
        self.assertEqual("".join(pieces), "One short sentence.")

    def test_all_think_streams_nothing(self):
        self.assertEqual(self._run(["<think>never closes, never answers"]), [])


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


class TestBlendConfidence(unittest.TestCase):
    def test_default_weight_matches_legacy_blend(self):
        from app.services.import_matcher import blend_confidence
        self.assertEqual(blend_confidence(0.8, 0.6, 0.3), round(0.7 * 0.8 + 0.3 * 0.6, 3))

    def test_zero_weight_is_pure_deterministic(self):
        from app.services.import_matcher import blend_confidence
        self.assertEqual(blend_confidence(0.8, 0.1, 0.0), 0.8)

    def test_weight_clamped_and_result_capped(self):
        from app.services.import_matcher import blend_confidence
        self.assertEqual(blend_confidence(0.5, 0.9, 5.0), 0.9)   # weight clamps to 1
        self.assertEqual(blend_confidence(0.5, 0.9, -1.0), 0.5)  # weight clamps to 0
        self.assertEqual(blend_confidence(1.0, 1.0, 0.5), 1.0)   # never exceeds 1


class TestCircuitBreaker(unittest.TestCase):
    """Breaker/stats logic (v0.27.0, Approved Queue #7). record_result/breaker_open
    take an explicit `now` so these tests never sleep."""

    def setUp(self):
        llm_assist._stats = llm_assist._fresh_stats()
        llm_assist.set_breaker_config(3, 10)

    def tearDown(self):
        llm_assist._stats = llm_assist._fresh_stats()
        llm_assist.set_breaker_config(5, 10)

    def test_opens_after_threshold_consecutive_failures(self):
        for _ in range(2):
            llm_assist.record_result(False, 100, "boom", now=1000.0)
        self.assertFalse(llm_assist.breaker_open(1000.0))
        llm_assist.record_result(False, 100, "boom", now=1000.0)
        self.assertTrue(llm_assist.breaker_open(1000.0))
        self.assertEqual(llm_assist._stats["breaker_trips"], 1)

    def test_cooldown_expiry_closes_it(self):
        for _ in range(3):
            llm_assist.record_result(False, 100, "boom", now=1000.0)
        self.assertTrue(llm_assist.breaker_open(1000.0 + 599))
        self.assertFalse(llm_assist.breaker_open(1000.0 + 601))

    def test_success_resets_streak_and_closes(self):
        for _ in range(3):
            llm_assist.record_result(False, 100, "boom", now=1000.0)
        self.assertTrue(llm_assist.breaker_open(1000.0))
        llm_assist.record_result(True, 250, now=1000.0)
        self.assertFalse(llm_assist.breaker_open(1000.0))
        self.assertEqual(llm_assist._stats["consecutive_failures"], 0)

    def test_interleaved_failures_never_open(self):
        for _ in range(10):
            llm_assist.record_result(False, 100, "boom", now=1000.0)
            llm_assist.record_result(False, 100, "boom", now=1000.0)
            llm_assist.record_result(True, 250, now=1000.0)
        self.assertFalse(llm_assist.breaker_open(1000.0))
        self.assertEqual(llm_assist._stats["breaker_trips"], 0)

    def test_threshold_zero_disables_breaker(self):
        llm_assist.set_breaker_config(0, 10)
        for _ in range(50):
            llm_assist.record_result(False, 100, "boom", now=1000.0)
        self.assertFalse(llm_assist.breaker_open(1000.0))

    def test_manual_reset_closes_and_clears_streak(self):
        for _ in range(3):
            llm_assist.record_result(False, 100, "boom", now=1000.0)
        llm_assist.reset_breaker()
        self.assertFalse(llm_assist.breaker_open(1000.0))
        self.assertEqual(llm_assist._stats["consecutive_failures"], 0)
        # Cumulative counters survive a manual reset.
        self.assertEqual(llm_assist._stats["failures"], 3)

    def test_stats_readout_shape_and_latency_avg(self):
        llm_assist.record_result(True, 200, now=1000.0)
        llm_assist.record_result(True, 400, now=1000.0)
        llm_assist.record_result(False, 20000, "timeout", now=1000.0)
        s = llm_assist.get_stats()
        self.assertEqual(s["calls"], 3)
        self.assertEqual(s["successes"], 2)
        self.assertEqual(s["failures"], 1)
        # Failure latency (usually just the timeout) never skews the average.
        self.assertEqual(s["avg_latency_ms"], 300)
        self.assertEqual(s["last_error"], "timeout")
        self.assertFalse(s["breaker_open"])

    def test_generate_short_circuits_while_open(self):
        import asyncio
        for _ in range(3):
            llm_assist.record_result(False, 100, "boom")
        calls = llm_assist._stats["calls"]
        out = asyncio.run(llm_assist._generate("10.0.0.1:11434", "m", "prompt"))
        self.assertIsNone(out)
        # A short-circuited call is not recorded — it never reached the host.
        self.assertEqual(llm_assist._stats["calls"], calls)


class TestPerTaskLlmSettings(unittest.TestCase):
    """OllamaSettings.model_for/task_enabled (v0.27.0, Approved Queue #10)."""

    def _cfg(self, **kw):
        from app.schemas.settings import OllamaSettings
        base = dict(enabled=True, host="10.0.0.1:11434", model="default-model")
        base.update(kw)
        return OllamaSettings(**base)

    def test_blank_overrides_fall_back_to_shared_model(self):
        cfg = self._cfg()
        self.assertEqual(cfg.model_for("match"), "default-model")
        self.assertEqual(cfg.model_for("explain"), "default-model")
        self.assertTrue(cfg.task_enabled("match"))
        self.assertTrue(cfg.task_enabled("explain"))

    def test_per_task_model_overrides(self):
        cfg = self._cfg(match_model="fast-model", explain_model="  ")
        self.assertEqual(cfg.model_for("match"), "fast-model")
        self.assertEqual(cfg.model_for("explain"), "default-model")  # whitespace = blank

    def test_per_task_toggles_narrow_master_switch(self):
        cfg = self._cfg(match_enabled=False)
        self.assertFalse(cfg.task_enabled("match"))
        self.assertTrue(cfg.task_enabled("explain"))

    def test_master_switch_off_disables_everything(self):
        cfg = self._cfg(enabled=False, match_enabled=True, explain_enabled=True)
        self.assertFalse(cfg.task_enabled("match"))
        self.assertFalse(cfg.task_enabled("explain"))

    def test_no_usable_model_disables_task(self):
        cfg = self._cfg(model="", match_model="", explain_model="rat-model")
        self.assertFalse(cfg.task_enabled("match"))
        self.assertTrue(cfg.task_enabled("explain"))

    def test_pre_upgrade_config_defaults_keep_behavior(self):
        # A saved pre-v0.27.0 payload has none of the new keys — everything
        # defaults on, so an upgrade changes nothing.
        from app.schemas.settings import OllamaSettings
        cfg = OllamaSettings(**{"enabled": True, "host": "h", "model": "m"})
        self.assertTrue(cfg.task_enabled("match"))
        self.assertTrue(cfg.task_enabled("explain"))
        self.assertEqual(cfg.breaker_threshold, 5)

    def test_rationale_key_uses_effective_explain_model(self):
        # Same key when no override (pre-upgrade caches stay valid); new key
        # when an explain-specific model is set.
        from app.services.media_llm import rationale_key

        class _Item:
            score = 55.0
            watch_count = 0
            last_watched_at = None
            file_size = 1024

        base = self._cfg()
        self.assertEqual(rationale_key(base, _Item()),
                         rationale_key(self._cfg(explain_model=""), _Item()))
        self.assertNotEqual(rationale_key(base, _Item()),
                            rationale_key(self._cfg(explain_model="other"), _Item()))
        # A match-side override never touches rationale caching.
        self.assertEqual(rationale_key(base, _Item()),
                         rationale_key(self._cfg(match_model="other"), _Item()))


if __name__ == "__main__":
    unittest.main()
