"""Pure-function coverage for Artist Discovery: Qdrant point-ID determinism, the
Lidarr match-resolution fallback chain, and candidate dedupe. No real network."""
import unittest

from app.integrations.qdrant import QdrantIntegration
from app.services.artist_discovery import _norm_artist


class PointIdDeterminismTests(unittest.TestCase):
    def test_same_mbid_yields_same_id(self):
        a = QdrantIntegration.point_id("abc-123", "Some Artist")
        b = QdrantIntegration.point_id("abc-123", "Different Name")
        self.assertEqual(a, b)  # MBID is the primary key when present

    def test_different_mbid_yields_different_id(self):
        a = QdrantIntegration.point_id("abc-123", "Some Artist")
        b = QdrantIntegration.point_id("xyz-789", "Some Artist")
        self.assertNotEqual(a, b)

    def test_blank_mbid_falls_back_to_normalized_name(self):
        a = QdrantIntegration.point_id(None, "The Artist")
        b = QdrantIntegration.point_id("", "the   ARTIST")
        self.assertEqual(a, b)  # normalized name is the fallback key

    def test_name_fallback_differs_by_artist(self):
        a = QdrantIntegration.point_id(None, "Artist One")
        b = QdrantIntegration.point_id(None, "Artist Two")
        self.assertNotEqual(a, b)

    def test_id_is_a_valid_uuid_string(self):
        import uuid
        pid = QdrantIntegration.point_id("some-mbid", "Some Artist")
        uuid.UUID(pid)  # raises if malformed


class NormArtistTests(unittest.TestCase):
    def test_lowercases_and_collapses_whitespace(self):
        self.assertEqual(_norm_artist("  The   Artist  "), "the artist")

    def test_strips_punctuation(self):
        self.assertEqual(_norm_artist("Guns N' Roses!"), "guns n roses")

    def test_empty_and_none_are_safe(self):
        self.assertEqual(_norm_artist(""), "")
        self.assertEqual(_norm_artist(None), "")


class LidarrMatchResolutionTests(unittest.TestCase):
    """Mirrors the fallback chain in add_to_lidarr: exact MBID -> normalized-name
    match -> first result, matching n8n's Assemble Payload logic."""

    @staticmethod
    def _resolve(results, mbid, name):
        match = None
        if mbid:
            match = next((r for r in results if r.get("foreignArtistId") == mbid), None)
        if not match:
            target = _norm_artist(name)
            match = next((r for r in results if _norm_artist(r.get("artistName") or "") == target), None)
        if not match:
            match = results[0]
        return match

    def test_exact_mbid_match_wins(self):
        results = [
            {"foreignArtistId": "wrong-id", "artistName": "Some Artist"},
            {"foreignArtistId": "right-id", "artistName": "Totally Different Name"},
        ]
        match = self._resolve(results, "right-id", "Some Artist")
        self.assertEqual(match["foreignArtistId"], "right-id")

    def test_falls_back_to_normalized_name_when_no_mbid_match(self):
        results = [
            {"foreignArtistId": "id-1", "artistName": "Not It"},
            {"foreignArtistId": "id-2", "artistName": "The  Right Artist!"},
        ]
        match = self._resolve(results, "missing-id", "the right artist")
        self.assertEqual(match["foreignArtistId"], "id-2")

    def test_falls_back_to_first_result_as_last_resort(self):
        results = [
            {"foreignArtistId": "id-1", "artistName": "No Match Here"},
            {"foreignArtistId": "id-2", "artistName": "Also No Match"},
        ]
        match = self._resolve(results, None, "Completely Unrelated")
        self.assertEqual(match["foreignArtistId"], "id-1")


if __name__ == "__main__":
    unittest.main()
