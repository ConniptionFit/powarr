"""FI-11 — unpublished ("TBA"/"TBD") episode titles and daily-series air dates.

A daily episode is routinely grabbed the same day it airs, before the metadata
source has published a title, so TVDB/Sonarr hand back the literal string
"TBA". Scored naively that reads as a ~0% title match and drags an otherwise
correct row under low_confidence_floor, where no triage row is created at all —
the release simply never appears. Absence of a title is not evidence of a wrong
match, so the title leaves the weighted average entirely and the remaining
metadata carries the score.

Daily release names carry a date where a season show carries SxxEyy, so the
air date is the corroborating signal for them — and the date's digits must not
be mistaken for episode numbers.

The load-bearing tests here are the negative ones: excluding the title must
never manufacture a match on its own.
"""
import unittest

from app.schemas.settings import ImportMatchingSettings
from app.services.import_matcher import (
    _parse_release_numbers,
    is_placeholder_episode_title,
    parse_release_date,
    score_episode_match,
)

DAILY_RELEASE = "The.Late.Show.with.Stephen.Colbert.2026.08.24.1080p.WEB.h264-GROUP"


def _ep(**over):
    base = {"title": "TBA", "seasonNumber": 2026, "episodeNumber": 165,
            "airDate": "2026-08-24", "airDateUtc": "2026-08-24T23:35:00Z"}
    base.update(over)
    return base


class PlaceholderTitleDetectionTests(unittest.TestCase):
    def test_known_placeholders(self):
        for t in ["TBA", "TBD", "TBC", "tba", "  tbd  ", "To Be Announced",
                  "to be determined", "Unknown", "Untitled", "No Title",
                  "Episode 12", "Episode #12", "Ep 4", "S03E07", "", "   ", None]:
            self.assertTrue(is_placeholder_episode_title(t), f"{t!r} should be a placeholder")

    def test_real_titles_are_not_placeholders(self):
        """Guards the substring trap — a real title may *start* with a
        placeholder word without being one."""
        for t in ["The Trial", "Episode 12: The Reckoning", "TBA Halloween Special",
                  "Unknown Soldier", "Untitled Goose Episode", "Part 2 of the Story"]:
            self.assertFalse(is_placeholder_episode_title(t), f"{t!r} is a real title")


class ReleaseDateParsingTests(unittest.TestCase):
    def test_daily_naming_variants(self):
        for name in ["Show.2026.08.24.1080p.WEB", "Show.2026-08-24.1080p.WEB",
                     "Show 2026 08 24 1080p", "Show.20260824.720p.HDTV"]:
            self.assertEqual(parse_release_date(name).isoformat(), "2026-08-24", name)

    def test_non_dates_rejected(self):
        # A season show, a year range, and an impossible month.
        self.assertIsNone(parse_release_date("Some.Show.S03E07.1080p.WEB"))
        self.assertIsNone(parse_release_date("Anime.Batch.2020.2021.Complete.1080p"))
        self.assertIsNone(parse_release_date("Show.2026.20.99.1080p"))

    def test_date_digits_are_not_episode_numbers(self):
        """The day of the month was being read as an absolute episode number,
        and the dashed form additionally produced an absolute_range."""
        for name in ["Show.2026.08.24.1080p.WEB", "Show.2026-08-24.1080p.WEB"]:
            parsed = _parse_release_numbers(name)
            self.assertIsNone(parsed["absolute"], name)
            self.assertIsNone(parsed["absolute_range"], name)

    def test_season_show_numbers_still_parse(self):
        parsed = _parse_release_numbers("Some.Show.S03E07.1080p.WEB")
        self.assertEqual((parsed["season"], parsed["episode"]), (3, 7))


class DailyPlaceholderScoringTests(unittest.TestCase):
    def setUp(self):
        self.cfg = ImportMatchingSettings()

    def test_tba_with_matching_air_date_matches(self):
        score, has_numeric, parts = score_episode_match(DAILY_RELEASE, _ep(), "daily", self.cfg)
        self.assertEqual(score, 1.0)
        self.assertTrue(has_numeric)
        self.assertTrue(any("placeholder" in p for p in parts))
        self.assertTrue(any("air date 2026-08-24 matched" in p for p in parts))

    def test_tba_row_clears_the_floor_it_used_to_fall_under(self):
        """The actual reported symptom: below low_confidence_floor no triage row
        is created, so the release is never offered for review at all."""
        score, _, _ = score_episode_match(DAILY_RELEASE, _ep(), "daily", self.cfg)
        self.assertGreater(score, self.cfg.low_confidence_floor)

    def test_wrong_air_date_still_fails(self):
        """Load-bearing: dropping the title must not manufacture a match."""
        score, _, parts = score_episode_match(
            DAILY_RELEASE, _ep(airDate="2026-08-19", airDateUtc="2026-08-19T23:35:00Z"),
            "daily", self.cfg)
        self.assertEqual(score, 0.0)
        self.assertTrue(any("air date mismatch" in p for p in parts))

    def test_placeholder_with_no_corroboration_at_all_cannot_match(self):
        score, has_numeric, parts = score_episode_match(
            DAILY_RELEASE, {"title": "TBA", "seasonNumber": 2026, "episodeNumber": 165},
            "daily", self.cfg)
        self.assertEqual(score, 0.0)
        self.assertFalse(has_numeric)
        self.assertTrue(any("cannot match" in p for p in parts))

    def test_utc_only_air_date_tolerates_the_day_rollover(self):
        """A show airing 23:35 local is already the next day in UTC, while the
        release is named for the local date."""
        score, _, parts = score_episode_match(
            DAILY_RELEASE,
            {"title": "TBA", "airDateUtc": "2026-08-25T03:35:00Z"}, "daily", self.cfg)
        self.assertEqual(score, 1.0)
        self.assertTrue(any("within a day" in p for p in parts))

    def test_local_air_date_is_not_given_the_rollover_tolerance(self):
        """The tolerance exists only for the UTC fallback — an explicit local
        airDate one day off is a genuine mismatch."""
        score, _, _ = score_episode_match(
            DAILY_RELEASE, _ep(airDate="2026-08-25"), "daily", self.cfg)
        self.assertEqual(score, 0.0)


class SeasonShowPlaceholderTests(unittest.TestCase):
    def setUp(self):
        self.cfg = ImportMatchingSettings()

    def test_tba_with_matching_se_matches(self):
        score, has_numeric, _ = score_episode_match(
            "Some.Show.S03E07.1080p.WEB-GROUP",
            {"title": "TBA", "seasonNumber": 3, "episodeNumber": 7}, "standard", self.cfg)
        self.assertEqual(score, 1.0)
        self.assertTrue(has_numeric)

    def test_tba_with_wrong_se_still_fails(self):
        score, _, _ = score_episode_match(
            "Some.Show.S03E07.1080p.WEB-GROUP",
            {"title": "TBA", "seasonNumber": 3, "episodeNumber": 9}, "standard", self.cfg)
        self.assertEqual(score, 0.0)

    def test_real_title_scoring_is_unchanged(self):
        """Regression guard: the ordinary path must not move."""
        score, has_numeric, parts = score_episode_match(
            "Some.Show.S03E07.The.Trial.1080p.WEB",
            {"title": "The Trial", "seasonNumber": 3, "episodeNumber": 7}, "standard", self.cfg)
        self.assertTrue(has_numeric)
        self.assertAlmostEqual(score, 0.91, places=2)
        self.assertTrue(any("episode title similarity" in p for p in parts))

    def test_published_title_still_scored_for_a_daily(self):
        """Once TVDB fills the title in, it is scored normally again — the
        exclusion is not sticky."""
        _, _, parts = score_episode_match(
            DAILY_RELEASE, _ep(title="Guest Interview With Someone"), "daily", self.cfg)
        self.assertTrue(any("episode title similarity" in p for p in parts))
        self.assertFalse(any("placeholder" in p for p in parts))


if __name__ == "__main__":
    unittest.main()
