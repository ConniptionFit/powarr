"""Unit tests for the import-matcher heuristics (regression-prone string logic).
Run inside the container: python -m unittest discover -s app/tests -v"""
import unittest

from app.schemas.settings import ImportMatchingSettings
from app.services.import_matcher import (_normalize, title_similarity, _is_stuck, _within_grace,
                                         _parse_release_numbers, score_episode_match,
                                         score_pack_match, apply_pack_match_override)

CFG = ImportMatchingSettings()  # defaults: title 0.6 / number 0.4, anime numbering on


class TestNormalize(unittest.TestCase):
    def test_strips_quality_junk(self):
        self.assertEqual(_normalize("The.Fifth.Element.1997.2160p.UHD.BluRay.x265.10bit.HDR.DDP5.1-LAMA"),
                         "the fifth element uhd ddp5 1 lama")

    def test_strips_season_episode(self):
        self.assertNotIn("s01e05", _normalize("Show.Name.S01E05.720p.HDTV"))

    def test_empty(self):
        self.assertEqual(_normalize(""), "")
        self.assertEqual(_normalize(None), "")


class TestTitleSimilarity(unittest.TestCase):
    def test_exact_release_match(self):
        s = title_similarity("The.Fifth.Element.1997.2160p.UHD.BluRay.x265-LAMA", "The Fifth Element")
        self.assertGreaterEqual(s, 0.85)

    def test_substring_bonus(self):
        s = title_similarity("Parasite 2019 PROPER UHD BluRay 2160p x265-hallowed", "Parasite")
        self.assertGreaterEqual(s, 0.85)

    def test_unrelated_titles_low(self):
        s = title_similarity("Some.Completely.Different.Movie.2020.1080p", "The Fifth Element")
        self.assertLess(s, 0.5)

    def test_empty_zero(self):
        self.assertEqual(title_similarity("", "Anything"), 0.0)


class TestIsStuck(unittest.TestCase):
    def test_import_pending(self):
        self.assertTrue(_is_stuck({"trackedDownloadState": "importPending"}))

    def test_completed_warning(self):
        self.assertTrue(_is_stuck({"status": "completed", "trackedDownloadStatus": "warning"}))

    def test_downloading_not_stuck(self):
        self.assertFalse(_is_stuck({"status": "downloading", "trackedDownloadState": "downloading"}))

    def test_stalled_only_when_enabled(self):
        rec = {"status": "downloading", "errorMessage": "The download is stalled with no connections"}
        self.assertFalse(_is_stuck(rec, include_stalled=False))
        self.assertTrue(_is_stuck(rec, include_stalled=True))


class TestGracePeriod(unittest.TestCase):
    def test_no_added_field_not_in_grace(self):
        self.assertFalse(_within_grace({}, 10))

    def test_zero_grace_disabled(self):
        self.assertFalse(_within_grace({"added": "2020-01-01T00:00:00Z"}, 0))

    def test_old_item_not_in_grace(self):
        self.assertFalse(_within_grace({"added": "2020-01-01T00:00:00Z"}, 10))


class TestParseReleaseNumbers(unittest.TestCase):
    def test_standard_season_episode(self):
        p = _parse_release_numbers("Show.Name.S02E10.1080p.WEB-DL.x265")
        self.assertEqual((p["season"], p["episode"]), (2, 10))

    def test_anime_absolute(self):
        p = _parse_release_numbers("[SubsPlease] Anime Show - 1047 (1080p) [A1B2C3D4]")
        self.assertEqual(p["absolute"], 1047)
        self.assertIsNone(p["season"])

    def test_e_prefix_absolute(self):
        self.assertEqual(_parse_release_numbers("Anime Show E47 [720p]")["absolute"], 47)

    def test_year_and_junk_not_absolute(self):
        p = _parse_release_numbers("Show.Name.2019.Special.1080p.DDP5.1")
        self.assertIsNone(p["absolute"])

    def test_both_se_and_absolute(self):
        p = _parse_release_numbers("Anime Show S02E10 - 1047 [1080p]")
        self.assertEqual((p["season"], p["episode"], p["absolute"]), (2, 10, 1047))


class TestPackParsing(unittest.TestCase):
    def test_season_only_pack(self):
        p = _parse_release_numbers("Hey.Arnold!.1996.S05.PMP.WEB-DL.540p.AAC.2.0.H.264-tokar86a")
        self.assertEqual(p["pack_seasons"], {5})
        self.assertIsNone(p["episode"])
        self.assertFalse(p["complete"])

    def test_season_word_pack(self):
        self.assertEqual(_parse_release_numbers("Show Name Season 3 1080p")["pack_seasons"], {3})

    def test_season_range_pack(self):
        self.assertEqual(_parse_release_numbers("Show.Name.S01-S03.1080p.WEB-DL")["pack_seasons"],
                         {1, 2, 3})

    def test_complete_series_pack(self):
        p = _parse_release_numbers("Show Name COMPLETE 1080p BluRay")
        self.assertTrue(p["complete"])
        self.assertIsNone(p["pack_seasons"])

    def test_single_episode_is_not_pack(self):
        p = _parse_release_numbers("Show.Name.S03E05.1080p")
        self.assertIsNone(p["pack_seasons"])
        self.assertEqual((p["season"], p["episode"]), (3, 5))


class TestScorePackMatch(unittest.TestCase):
    def test_full_coverage_suggests_entire_season_import(self):
        score, has_num, parts, label = score_pack_match(
            0.85, {5}, False, [5] * 20, mapped_episodes=20, total_episodes=20, cfg=CFG)
        self.assertEqual(label, "S05")
        self.assertTrue(has_num)
        self.assertGreaterEqual(score, 0.9)
        self.assertTrue(any("entire-season import suggested" in p for p in parts))

    def test_complete_series_wording(self):
        score, has_num, parts, label = score_pack_match(
            0.9, None, True, [1, 1, 2, 2], mapped_episodes=48, total_episodes=48, cfg=CFG)
        self.assertEqual(label, "complete series")
        self.assertTrue(any("entire-series import suggested" in p for p in parts))

    def test_partial_coverage_downgraded(self):
        score, has_num, parts, label = score_pack_match(
            0.85, {5}, False, [5] * 12, mapped_episodes=12, total_episodes=20, cfg=CFG)
        self.assertTrue(has_num)
        self.assertLess(score, 0.9)
        self.assertTrue(any("partial pack coverage" in p for p in parts))

    def test_siblings_outside_season_hard_penalty(self):
        score, has_num, parts, label = score_pack_match(
            0.9, {5}, False, [5, 5, 4], mapped_episodes=None, total_episodes=None, cfg=CFG)
        self.assertTrue(has_num)
        self.assertLess(score, 0.7)
        self.assertTrue(any("outside S05" in p for p in parts))

    def test_no_corroboration_is_title_only(self):
        score, has_num, parts, label = score_pack_match(
            0.95, {5}, False, [], mapped_episodes=None, total_episodes=None, cfg=CFG)
        self.assertFalse(has_num)
        self.assertTrue(any("title-only" in p for p in parts))

    def test_siblings_without_coverage_partial_credit(self):
        score, has_num, parts, label = score_pack_match(
            0.85, {5}, False, [5] * 8, mapped_episodes=None, total_episodes=None, cfg=CFG)
        self.assertTrue(has_num)
        self.assertTrue(any("full coverage unverified" in p for p in parts))


class TestScoreEpisodeMatch(unittest.TestCase):
    def test_standard_title_and_numbers_match(self):
        ep = {"title": "The Winds of Winter", "seasonNumber": 6, "episodeNumber": 10}
        score, has_num, parts = score_episode_match(
            "Game.of.Thrones.S06E10.The.Winds.of.Winter.1080p", ep, "standard", CFG)
        self.assertTrue(has_num)
        self.assertGreaterEqual(score, 0.9)
        self.assertTrue(any("season/episode S06E10 matched" in p for p in parts))

    def test_season_mismatch_hard_penalty_not_disqualifying(self):
        ep = {"title": "Pilot", "seasonNumber": 1, "episodeNumber": 10}
        score, has_num, parts = score_episode_match("Show.Name.S02E10.720p", ep, "standard", CFG)
        self.assertTrue(has_num)
        self.assertGreater(score, 0.0)
        self.assertLess(score, 0.5)
        self.assertTrue(any("season mismatch" in p for p in parts))

    def test_strong_title_with_contradicting_number_stays_below_auto_resolve(self):
        ep = {"title": "The Winds of Winter", "seasonNumber": 6, "episodeNumber": 10}
        score, has_num, parts = score_episode_match(
            "Game.of.Thrones.S01E02.The.Winds.of.Winter.1080p", ep, "standard", CFG)
        self.assertTrue(has_num)
        self.assertLess(score, 0.90)  # title cannot override the numeric contradiction

    def test_anime_absolute_explains_se_mismatch(self):
        ep = {"title": "A Fierce Battle", "seasonNumber": 21, "episodeNumber": 23,
              "absoluteEpisodeNumber": 1047}
        score, has_num, parts = score_episode_match(
            "[Group] Anime Show S02E10 - 1047 [1080p]", ep, "anime", CFG)
        self.assertTrue(has_num)
        self.assertTrue(any("absolute episode #1047 matched" in p for p in parts))
        self.assertTrue(any("explained by anime absolute numbering" in p for p in parts))

    def test_anime_implausible_absolute_downweighted(self):
        ep = {"title": "Title", "seasonNumber": 2, "episodeNumber": 10, "absoluteEpisodeNumber": 10}
        score, has_num, parts = score_episode_match("[Group] Anime Show - 10 [1080p]", ep, "anime", CFG)
        self.assertTrue(has_num)
        self.assertTrue(any("implausible" in p for p in parts))

    def test_anime_missing_absolute_falls_back_to_se(self):
        ep = {"title": "Title", "seasonNumber": 1, "episodeNumber": 5, "absoluteEpisodeNumber": None}
        score, has_num, parts = score_episode_match("Anime.Show.S01E05.1080p", ep, "anime", CFG)
        self.assertTrue(has_num)
        self.assertTrue(any("fell back to season/episode" in p for p in parts))

    def test_title_only_reports_no_numeric_corroboration(self):
        ep = {"title": "The Winds of Winter", "seasonNumber": 6, "episodeNumber": 10}
        score, has_num, parts = score_episode_match(
            "Game of Thrones The Winds of Winter WEBRip", ep, "standard", CFG)
        self.assertFalse(has_num)
        self.assertTrue(any("title-only" in p for p in parts))

    def test_anime_toggle_off_ignores_absolute(self):
        cfg = ImportMatchingSettings(anime_absolute_numbering=False)
        ep = {"title": "Title", "seasonNumber": 21, "episodeNumber": 23, "absoluteEpisodeNumber": 1047}
        score, has_num, parts = score_episode_match("[Group] Anime Show - 1047 [1080p]", ep, "anime", cfg)
        self.assertFalse(has_num)  # S/E path finds nothing to corroborate
        self.assertFalse(any("absolute" in p for p in parts))


class TestApplyPackMatchOverride(unittest.TestCase):
    def matches(self):
        return [
            {"file": "ep588.mkv", "season": 16, "episode": 10, "confidence": "high", "reason": "absolute 588"},
            {"file": "ep589.mkv", "season": 16, "episode": 11, "confidence": "medium", "reason": "absolute 589"},
        ]

    def test_override_updates_entry_and_marks_user(self):
        out = apply_pack_match_override(self.matches(), "ep589.mkv", 10, 5)
        self.assertEqual(out[1]["season"], 10)
        self.assertEqual(out[1]["episode"], 5)
        self.assertEqual(out[1]["confidence"], "user")
        self.assertEqual(out[1]["reason"], "Manually adjusted")

    def test_other_entries_untouched(self):
        out = apply_pack_match_override(self.matches(), "ep589.mkv", 10, 5)
        self.assertEqual(out[0], self.matches()[0])

    def test_unknown_file_raises(self):
        with self.assertRaises(ValueError):
            apply_pack_match_override(self.matches(), "missing.mkv", 1, 1)

    def test_negative_numbers_raise(self):
        with self.assertRaises(ValueError):
            apply_pack_match_override(self.matches(), "ep588.mkv", -1, 5)


if __name__ == "__main__":
    unittest.main()
