"""Lidarr/Readarr album-level matching (v0.34.0, FI-03)."""
import unittest

from app.services.import_matcher import _lidarr_readarr_match, _album_display_title


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
