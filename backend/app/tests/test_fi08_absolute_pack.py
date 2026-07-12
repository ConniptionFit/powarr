"""Unit tests for FI-08: anime absolute-numbering pack coverage.

Opt-in (ImportMatchingSettings.anime_absolute_pack_coverage, off by default)
follow-on to FI-02 — for a seriesType=anime batch pack with no season marker
at all ("001-100"), scopes the coverage denominator to that absolute-episode
range instead of the whole aired show, so a genuinely complete pack doesn't
read stuck at a tiny fraction forever."""
import unittest
from datetime import datetime, timedelta

from app.schemas.settings import ImportMatchingSettings
from app.services.import_matcher import _parse_release_numbers, score_pack_match, _pack_coverage

CFG = ImportMatchingSettings()


class ParseAbsoluteRangeTests(unittest.TestCase):
    def test_plain_absolute_range(self):
        p = _parse_release_numbers("[Group] Big Anime - 001-100 [1080p][Batch]")
        self.assertEqual(p["absolute_range"], (1, 100))
        self.assertIsNone(p["pack_seasons"])

    def test_no_range_when_season_marker_present(self):
        p = _parse_release_numbers("Show.Name.S01-S03.1080p.WEB-DL")
        self.assertIsNone(p["absolute_range"])

    def test_no_range_when_complete_marker_present(self):
        p = _parse_release_numbers("Show Name COMPLETE 001-100 1080p")
        self.assertIsNone(p["absolute_range"])

    def test_year_pair_is_not_mistaken_for_a_range(self):
        p = _parse_release_numbers("Anime Collection 2011-2015 BluRay")
        self.assertIsNone(p["absolute_range"])

    def test_implausible_span_rejected(self):
        p = _parse_release_numbers("Weird Release 100-5000 1080p")
        self.assertIsNone(p["absolute_range"])

    def test_single_episode_has_no_range(self):
        p = _parse_release_numbers("[SubsPlease] Anime Show - 1047 (1080p)")
        self.assertIsNone(p["absolute_range"])


class ScorePackMatchAbsoluteRangeTests(unittest.TestCase):
    def test_label_and_suggestion_for_absolute_range(self):
        score, has_num, parts, label = score_pack_match(
            0.9, None, False, [], mapped_episodes=95, total_episodes=100, cfg=CFG,
            absolute_range=(1, 100))
        self.assertEqual(label, "1-100 (absolute)")
        self.assertTrue(any("full absolute-range import suggested" in p for p in parts))
        self.assertTrue(has_num)
        self.assertGreaterEqual(score, 0.9)

    def test_near_half_caveat_suppressed_for_absolute_range(self):
        """FI-02's double-count caveat doesn't apply here — absolute-range
        scoping is exactly the fix for that class of denominator problem, so
        a near-50% ratio isn't the same ambiguous signal it is for a season
        pack scored against the whole aired show."""
        score, has_num, parts, label = score_pack_match(
            0.85, None, False, [], mapped_episodes=10, total_episodes=20, cfg=CFG,
            absolute_range=(1, 20))
        self.assertFalse(any("double-count aired episodes" in p for p in parts))

    def test_absolute_pack_detected_rationale(self):
        _, _, parts, _ = score_pack_match(
            0.9, None, False, [], mapped_episodes=50, total_episodes=100, cfg=CFG,
            absolute_range=(1, 100))
        self.assertTrue(any("absolute-numbered pack detected" in p for p in parts))


def _episode(id_, *, absolute=None, season=1, aired_days_ago=10):
    return {
        "id": id_,
        "seasonNumber": season,
        "absoluteEpisodeNumber": absolute,
        "airDateUtc": (datetime.utcnow() - timedelta(days=aired_days_ago)).isoformat() + "Z",
    }


class _FakeSonarrClient:
    def __init__(self, episodes, manual_import_episode_ids=()):
        self._episodes = episodes
        self._manual_import_episode_ids = set(manual_import_episode_ids)

    async def get_episodes(self, series_id):
        return self._episodes

    async def get_manual_import(self, download_id, folder=None):
        return [{"episodes": [{"id": eid} for eid in self._manual_import_episode_ids]}]


class PackCoverageAbsoluteRangeTests(unittest.IsolatedAsyncioTestCase):
    async def test_absolute_range_scopes_total_to_range_not_whole_show(self):
        # 1000-episode long-running show; pack only claims to cover 1-100.
        episodes = [_episode(i, absolute=i) for i in range(1, 1001)]
        client = _FakeSonarrClient(episodes, manual_import_episode_ids=range(1, 96))
        mapped, total = await _pack_coverage(
            client, "dl1", 42, target_seasons=None, absolute_range=(1, 100))
        self.assertEqual(total, 100)  # not 1000
        self.assertEqual(mapped, 95)

    async def test_season_based_scoping_unaffected_when_absolute_range_not_given(self):
        episodes = [_episode(i, absolute=i, season=1) for i in range(1, 21)]
        episodes += [_episode(i, absolute=i, season=2) for i in range(21, 41)]
        client = _FakeSonarrClient(episodes, manual_import_episode_ids=range(1, 21))
        mapped, total = await _pack_coverage(
            client, "dl1", 42, target_seasons={1})
        self.assertEqual(total, 20)
        self.assertEqual(mapped, 20)

    async def test_episodes_without_absolute_number_excluded_from_absolute_scope(self):
        episodes = [_episode(1, absolute=1), _episode(2, absolute=None), _episode(3, absolute=3)]
        client = _FakeSonarrClient(episodes, manual_import_episode_ids=[1, 3])
        mapped, total = await _pack_coverage(
            client, "dl1", 42, target_seasons=None, absolute_range=(1, 3))
        self.assertEqual(total, 2)  # episode 2 excluded — no absolute number to compare
        self.assertEqual(mapped, 2)


if __name__ == "__main__":
    unittest.main()
