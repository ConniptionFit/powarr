"""Unit tests for the ManualImport-command payload builders.

Imports must execute via POST /command {name: ManualImport} with flat ids —
the bare POST /manualimport route is the *arr reprocess endpoint: it never
imports, and Sonarr 404s on it when a candidate lacks a flat seriesId
(diagnosed live against Sonarr 4.0.19, 2026-07-05).
Run inside the container: python -m unittest discover -s app/tests -v"""
import unittest

from app.integrations.sonarr import build_manual_import_files as sonarr_files
from app.integrations.radarr import build_manual_import_files as radarr_files


class TestSonarrManualImportFiles(unittest.TestCase):
    CANDIDATE = {
        "path": "/downloads/One.Piece.S10V2/ep1.mkv",
        "folderName": "One.Piece.S10V2",
        "series": {"id": 1110, "title": "One Piece"},
        "episodes": [{"id": 501, "seasonNumber": 10}, {"id": 502, "seasonNumber": 10}],
        "quality": {"quality": {"id": 3, "name": "WEBDL-1080p"}},
        "languages": [{"id": 1, "name": "English"}],
        "releaseGroup": "KS",
    }

    def test_flat_ids_from_nested_objects(self):
        files = sonarr_files([self.CANDIDATE], None, "14E05525")
        self.assertEqual(len(files), 1)
        f = files[0]
        self.assertEqual(f["seriesId"], 1110)
        self.assertEqual(f["episodeIds"], [501, 502])
        self.assertEqual(f["downloadId"], "14E05525")
        # the nested objects the reprocess endpoint chokes on must not be sent
        self.assertNotIn("series", f)
        self.assertNotIn("episodes", f)

    def test_series_id_fallback_to_matched_id(self):
        cand = {**self.CANDIDATE, "series": None}
        files = sonarr_files([cand], 1110, "abc")
        self.assertEqual(files[0]["seriesId"], 1110)

    def test_unmapped_file_skipped(self):
        cand = {**self.CANDIDATE, "series": None}
        self.assertEqual(sonarr_files([cand], None, "abc"), [])

    def test_no_episodes_skipped(self):
        cand = {**self.CANDIDATE, "episodes": []}
        self.assertEqual(sonarr_files([cand], None, "abc"), [])

    def test_explicit_episode_ids_win(self):
        cand = {**self.CANDIDATE, "episodeIds": [900]}
        self.assertEqual(sonarr_files([cand], None, "abc")[0]["episodeIds"], [900])

    def test_missing_optionals_omitted_not_none(self):
        cand = {"path": "/d/x.mkv", "seriesId": 5, "episodeIds": [1]}
        f = sonarr_files([cand], None, "abc")[0]
        self.assertNotIn("releaseGroup", f)
        self.assertNotIn("folderName", f)
        self.assertEqual(f["indexerFlags"], 0)
        self.assertEqual(f["languages"], [])


class TestRadarrManualImportFiles(unittest.TestCase):
    def test_flat_movie_id(self):
        cand = {"path": "/d/m.mkv", "movie": {"id": 42}, "quality": {"quality": {"id": 3}}}
        f = radarr_files([cand], None, "abc")[0]
        self.assertEqual(f["movieId"], 42)
        self.assertNotIn("movie", f)
        self.assertEqual(f["downloadId"], "abc")

    def test_fallback_and_skip(self):
        self.assertEqual(radarr_files([{"path": "/d/m.mkv"}], None, "abc"), [])
        f = radarr_files([{"path": "/d/m.mkv"}], 42, "abc")[0]
        self.assertEqual(f["movieId"], 42)


if __name__ == "__main__":
    unittest.main()
