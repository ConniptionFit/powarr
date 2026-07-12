"""Unit tests for FI-06: root-cause classifier."""
import unittest
from types import SimpleNamespace

from app.services.root_cause import classify_root_cause


def _item(**over):
    base = dict(
        status="suggested", source_app="sonarr", raw_title="Some.Show.S01E01.1080p.WEB.x264-GRP",
        match_rationale=None, message=None, quality_downgrade=None, suspicious_files=None,
        partial_import=None, llm_agrees=None, confidence=0.9,
    )
    base.update(over)
    return SimpleNamespace(**base)


class ClassifyRootCauseTests(unittest.TestCase):
    def test_orphaned_status_is_missing_files(self):
        cause = classify_root_cause(_item(status="orphaned"))
        self.assertEqual(cause.code, "missing_files")

    def test_no_files_message_is_missing_files(self):
        cause = classify_root_cause(_item(message="Reason: no files present"))
        self.assertEqual(cause.code, "missing_files")

    def test_year_mismatch_rationale(self):
        cause = classify_root_cause(_item(
            match_rationale="year mismatch (release 2011 vs library 1982) — hard fail"))
        self.assertEqual(cause.code, "year_mismatch")

    def test_quality_downgrade_is_not_an_upgrade(self):
        cause = classify_root_cause(_item(quality_downgrade=True))
        self.assertEqual(cause.code, "not_an_upgrade")

    def test_suspicious_files(self):
        cause = classify_root_cause(_item(suspicious_files='["file.exe"]'))
        self.assertEqual(cause.code, "suspicious_file")

    def test_partial_import(self):
        cause = classify_root_cause(_item(partial_import=True))
        self.assertEqual(cause.code, "pack_partial")

    def test_no_library_match_with_real_title_is_no_match(self):
        cause = classify_root_cause(_item(
            raw_title="Some.Real.Show.Title.S01E01.1080p.WEB.x264-GRP",
            match_rationale="no library match found"))
        self.assertEqual(cause.code, "no_match")

    def test_no_library_match_with_junk_title_is_scene_junk(self):
        cause = classify_root_cause(_item(
            raw_title="1080p.WEB.x264-GRP.mkv",
            match_rationale="no library match found"))
        self.assertEqual(cause.code, "scene_name_junk")

    def test_llm_disagrees(self):
        cause = classify_root_cause(_item(llm_agrees=False))
        self.assertEqual(cause.code, "llm_disagrees")

    def test_weak_numeric_corroboration(self):
        cause = classify_root_cause(_item(
            match_rationale="no numeric corroboration — confidence capped at 0.45"))
        self.assertEqual(cause.code, "weak_numeric_match")

    def test_low_confidence_fallback(self):
        cause = classify_root_cause(_item(confidence=0.1))
        self.assertEqual(cause.code, "low_confidence")

    def test_unclassified_fallback(self):
        cause = classify_root_cause(_item(confidence=0.9))
        self.assertEqual(cause.code, "unclassified")

    def test_priority_order_quality_downgrade_beats_no_match(self):
        # Mirrors the existing badge priority in MatchReview.tsx — the two
        # must never disagree about which cause "wins" for the same row.
        cause = classify_root_cause(_item(
            quality_downgrade=True, match_rationale="no library match found"))
        self.assertEqual(cause.code, "not_an_upgrade")

    def test_every_cause_has_a_label_and_action(self):
        for item in (
            _item(status="orphaned"),
            _item(match_rationale="year mismatch (release 2011 vs library 1982) — hard fail"),
            _item(quality_downgrade=True),
            _item(suspicious_files='["a.exe"]'),
            _item(partial_import=True),
            _item(match_rationale="no library match found"),
            _item(llm_agrees=False),
            _item(match_rationale="no numeric corroboration"),
            _item(confidence=0.1),
            _item(confidence=0.9),
        ):
            cause = classify_root_cause(item)
            self.assertTrue(cause.label)
            self.assertTrue(cause.suggested_action)


if __name__ == "__main__":
    unittest.main()
