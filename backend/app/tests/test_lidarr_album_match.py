"""Lidarr/Readarr album-level matching (v0.34.0, FI-03; containment shaping v0.37.0, FI-04)."""
import unittest

from app.services.import_matcher import (
    _lidarr_readarr_match, _album_display_title, _apply_music_checks,
    MUSIC_CHECK_FLOOR, MUSIC_CHECK_CAP_WRONG_ALBUM,
    MUSIC_CHECK_CAP_NO_ARTIST_LINKED, MUSIC_CHECK_CAP_JUNK,
)


class TestApplyMusicChecks(unittest.TestCase):
    """FI-04: containment checks shape Lidarr confidence without the LLM."""

    def test_both_strict_floors_confidence(self):
        parts = []
        conf = _apply_music_checks(
            "lidarr", "Fire_From_The_Gods-Soul_Revolution-CD-FLAC-2022-BOCKSCAR",
            "Fire From the Gods - Soul Revolution", 0.662, parts, linked=False)
        self.assertEqual(conf, MUSIC_CHECK_FLOOR)
        self.assertTrue(any("raised" in p for p in parts))

    def test_both_strict_never_lowers_a_higher_score(self):
        conf = _apply_music_checks(
            "lidarr", "Prof-Good_Time_Boy-SINGLE-WEB-2026-FATHEAD",
            "Prof - Good Time Boy", 0.955, [], linked=True)
        self.assertEqual(conf, 0.955)

    def test_wrong_album_capped(self):
        # Self-titled candidate vs a different-album release (the Gorillaz trap)
        conf = _apply_music_checks(
            "lidarr", "Gorillaz-Cracker_Island-24-44-WEB-FLAC-2023-OBZEN",
            "Gorillaz - Gorillaz", 0.637, [], linked=False)
        self.assertEqual(conf, MUSIC_CHECK_CAP_WRONG_ALBUM)

    def test_uploader_tag_junk_capped_hard(self):
        # Fuzzy-only match on the trailing group tag ('PERFECT')
        conf = _apply_music_checks(
            "lidarr", "The_Weeknd-Starboy-Deluxe_Edition-CD-FLAC-2016-PERFECT",
            "The Smashing Pumpkins - Perfect", 0.637, [], linked=False)
        self.assertEqual(conf, MUSIC_CHECK_CAP_JUNK)

    def test_single_word_album_junk_needs_link_to_survive(self):
        # 'Enter' appears as a word in 'Enter Shikari' — a fuzzy-only candidate
        # is still junk, but an id-linked artist-less release is capped gently.
        args = ("lidarr", "Enter_Shikari-Take_To_The_Skies-WEB-FLAC-2007-RUIDOS",
                "Cybotron - Enter", 0.637)
        self.assertEqual(_apply_music_checks(*args, [], linked=False), MUSIC_CHECK_CAP_JUNK)
        self.assertEqual(_apply_music_checks(*args, [], linked=True),
                         MUSIC_CHECK_CAP_NO_ARTIST_LINKED)

    def test_loose_grades_leave_confidence_unchanged(self):
        # Collapsed spelling (ACDC vs AC/DC) must never cap a valid match
        conf = _apply_music_checks(
            "lidarr", "ACDC-Back_In_Black-REMASTERED-FLAC-2003-GRP",
            "AC/DC - Back in Black", 0.72, [], linked=True)
        self.assertEqual(conf, 0.72)

    def test_non_lidarr_untouched(self):
        conf = _apply_music_checks(
            "readarr", "Some_Book-Retail-EPUB-GRP", "Author - Different Book",
            0.7, [], linked=False)
        self.assertEqual(conf, 0.7)


class TestCollectAutoEligible(unittest.TestCase):
    """v0.37.1: rescored rows meeting the auto-import bar get queued for accept."""

    def _row(self, **kw):
        from types import SimpleNamespace
        base = dict(id=1, status="suggested", matched_id=5, confidence=0.95)
        base.update(kw)
        return SimpleNamespace(**base)

    def _cfg(self, **kw):
        from app.schemas.settings import ImportMatchingSettings
        base = dict(auto_resolve_enabled=True, high_confidence_threshold=0.9)
        base.update(kw)
        return ImportMatchingSettings(**base)

    def test_eligible_rows_collected(self):
        from app.services.import_matcher import collect_auto_eligible
        rows = [self._row(id=1, confidence=0.95),
                self._row(id=2, confidence=0.89),          # below threshold
                self._row(id=3, status="orphaned"),        # wrong status
                self._row(id=4, matched_id=None),          # no match
                self._row(id=5, status="resolve_failed")]  # retry allowed
        self.assertEqual(collect_auto_eligible(rows, self._cfg()), [1, 5])

    def test_disabled_auto_resolve_collects_nothing(self):
        from app.services.import_matcher import collect_auto_eligible
        rows = [self._row()]
        self.assertEqual(
            collect_auto_eligible(rows, self._cfg(auto_resolve_enabled=False)), [])


class TestLidarrAlbumMatch(unittest.TestCase):
    def test_history_album_id(self):
        library = [{
            "id": 10, "title": "Awake", "artistId": 536,
            "artist": {"id": 536, "artistName": "Godsmack"},
        }]
        rec = {"title": "Godsmack-Awake-24BIT-WEB-FLAC", "artistId": 536, "downloadId": "abc"}
        hist = [{"downloadId": "abc", "artistId": 536, "albumId": 10}]
        mid, title, conf, parts = _lidarr_readarr_match("lidarr", rec, hist, library)
        self.assertEqual(mid, 10)
        self.assertIn("Awake", title)
        self.assertIn("Godsmack", title)
        self.assertGreaterEqual(conf, 0.7)

    def test_artist_scoped_fuzzy(self):
        library = [
            {"id": 1, "title": "Other", "artistId": 1, "artist": {"artistName": "X"}},
            {"id": 2, "title": "Tourist History", "artistId": 9,
             "artist": {"id": 9, "artistName": "Two Door Cinema Club"}},
        ]
        rec = {"title": "Two.Door.Cinema.Club.Tourist.History.DELUXE.FLAC",
               "artistId": 9, "downloadId": "d1"}
        mid, title, conf, parts = _lidarr_readarr_match("lidarr", rec, [], library)
        self.assertEqual(mid, 2)
        self.assertIn("Tourist History", title)
        self.assertGreaterEqual(conf, 0.7)

    def test_display_title(self):
        self.assertEqual(
            _album_display_title({"title": "Awake", "artist": {"artistName": "Godsmack"}}),
            "Godsmack - Awake")


if __name__ == "__main__":
    unittest.main()
