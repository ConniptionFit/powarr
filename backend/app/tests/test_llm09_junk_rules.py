"""Unit tests for LLM-09: user-authored regex junk-strip rules."""
import unittest

from app.services.import_matcher import apply_custom_junk_rules, _lidarr_readarr_match


class ApplyCustomJunkRulesTests(unittest.TestCase):
    def test_no_rules_is_noop(self):
        cleaned, applied = apply_custom_junk_rules("Show.Name.S01E01.GROUP", None)
        self.assertEqual(cleaned, "Show.Name.S01E01.GROUP")
        self.assertEqual(applied, [])

    def test_empty_title_is_noop(self):
        cleaned, applied = apply_custom_junk_rules("", [{"name": "x", "pattern": "x"}])
        self.assertEqual(cleaned, "")
        self.assertEqual(applied, [])

    def test_single_rule_strips_match(self):
        rules = [{"name": "TAG", "pattern": r"\bTAG\b", "enabled": True}]
        cleaned, applied = apply_custom_junk_rules("Show Name TAG Episode", rules)
        self.assertEqual(cleaned, "Show Name Episode")
        self.assertEqual(applied, ["TAG"])

    def test_disabled_rule_skipped(self):
        rules = [{"name": "TAG", "pattern": r"\bTAG\b", "enabled": False}]
        cleaned, applied = apply_custom_junk_rules("Show Name TAG Episode", rules)
        self.assertEqual(cleaned, "Show Name TAG Episode")
        self.assertEqual(applied, [])

    def test_enabled_defaults_true_when_omitted(self):
        rules = [{"name": "TAG", "pattern": r"\bTAG\b"}]
        cleaned, applied = apply_custom_junk_rules("Show Name TAG Episode", rules)
        self.assertEqual(cleaned, "Show Name Episode")
        self.assertEqual(applied, ["TAG"])

    def test_rules_applied_in_order(self):
        rules = [
            {"name": "first", "pattern": r"AAA"},
            {"name": "second", "pattern": r"BBB"},
        ]
        cleaned, applied = apply_custom_junk_rules("X AAA BBB Y", rules)
        self.assertEqual(cleaned, "X Y")
        self.assertEqual(applied, ["first", "second"])

    def test_rule_that_matches_nothing_not_reported_as_applied(self):
        rules = [{"name": "never", "pattern": r"ZZZNOPE"}]
        cleaned, applied = apply_custom_junk_rules("Show Name Episode", rules)
        self.assertEqual(cleaned, "Show Name Episode")
        self.assertEqual(applied, [])

    def test_invalid_regex_skipped_not_raised(self):
        # Unbalanced group — a real re.error, not a hypothetical.
        rules = [{"name": "bad", "pattern": r"(unclosed"}]
        cleaned, applied = apply_custom_junk_rules("Show Name Episode", rules)
        self.assertEqual(cleaned, "Show Name Episode")
        self.assertEqual(applied, [])

    def test_invalid_rule_does_not_block_later_valid_rules(self):
        rules = [
            {"name": "bad", "pattern": r"(unclosed"},
            {"name": "good", "pattern": r"TAG"},
        ]
        cleaned, applied = apply_custom_junk_rules("Show TAG Name", rules)
        self.assertEqual(cleaned, "Show Name")
        self.assertEqual(applied, ["good"])

    def test_empty_pattern_skipped(self):
        rules = [{"name": "blank", "pattern": ""}]
        cleaned, applied = apply_custom_junk_rules("Show Name", rules)
        self.assertEqual(cleaned, "Show Name")
        self.assertEqual(applied, [])

    def test_collapses_resulting_whitespace(self):
        rules = [{"name": "strip", "pattern": r"MID"}]
        cleaned, _ = apply_custom_junk_rules("A MID B", rules)
        self.assertEqual(cleaned, "A B")


class LidarrReadarrJunkRuleIntegrationTests(unittest.TestCase):
    def test_junk_rule_applied_before_matching(self):
        library = [{
            "id": 10, "title": "Awake", "artistId": 536,
            "artist": {"id": 536, "artistName": "Godsmack"},
        }]
        # A custom scene-group tag the built-in cleaner wouldn't recognize.
        rec = {"title": "Godsmack-Awake-MYCUSTOMTAG-FLAC", "artistId": 536, "downloadId": "abc"}
        hist = [{"downloadId": "abc", "artistId": 536, "albumId": 10}]
        rules = [{"name": "custom-tag", "pattern": r"MYCUSTOMTAG"}]
        mid, title, conf, parts = _lidarr_readarr_match("lidarr", rec, hist, library, rules)
        self.assertEqual(mid, 10)
        self.assertTrue(any("junk rules applied: custom-tag" in p for p in parts))

    def test_no_rules_param_behaves_as_before(self):
        # Default None must not change existing behavior — regression guard for
        # every pre-LLM-09 caller of this function.
        library = [{
            "id": 10, "title": "Awake", "artistId": 536,
            "artist": {"id": 536, "artistName": "Godsmack"},
        }]
        rec = {"title": "Godsmack-Awake-24BIT-WEB-FLAC", "artistId": 536, "downloadId": "abc"}
        hist = [{"downloadId": "abc", "artistId": 536, "albumId": 10}]
        mid, title, conf, parts = _lidarr_readarr_match("lidarr", rec, hist, library)
        self.assertEqual(mid, 10)
        self.assertFalse(any("junk rules applied" in p for p in parts))


if __name__ == "__main__":
    unittest.main()
