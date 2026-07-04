"""Unit tests for the import-matcher heuristics (regression-prone string logic).
Run inside the container: python -m unittest discover -s app/tests -v"""
import unittest

from app.services.import_matcher import _normalize, title_similarity, _is_stuck, _within_grace


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


if __name__ == "__main__":
    unittest.main()
