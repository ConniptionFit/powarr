"""Pure-function coverage for Artist Discovery: Qdrant point-ID determinism, the
Lidarr match-resolution fallback chain, candidate dedupe, and enrichment helpers
(MusicBrainz life-span/genre/wikipedia parsing, Lidarr image selection). No real
network."""
import unittest

from app.integrations import musicbrainz
from app.integrations.qdrant import QdrantIntegration
from app.services.artist_discovery import _norm_artist
from app.services.artist_enrichment import _lidarr_image


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


class MusicBrainzLifeSpanTests(unittest.TestCase):
    def test_open_ended_reads_as_present(self):
        text = musicbrainz.life_span_text({"life-span": {"begin": "1990-01-01"}})
        self.assertEqual(text, "1990–present")

    def test_ended_with_end_date_shows_range(self):
        text = musicbrainz.life_span_text(
            {"life-span": {"begin": "1990-01-01", "end": "2005-06-01", "ended": True}})
        self.assertEqual(text, "1990–2005")

    def test_ended_without_end_date_shows_begin_only(self):
        text = musicbrainz.life_span_text({"life-span": {"begin": "1990-01-01", "ended": True}})
        self.assertEqual(text, "1990")

    def test_missing_begin_returns_none(self):
        self.assertIsNone(musicbrainz.life_span_text({"life-span": {}}))
        self.assertIsNone(musicbrainz.life_span_text({}))


class MusicBrainzGenresTests(unittest.TestCase):
    def test_extracts_genre_names_capped_at_eight(self):
        data = {"genres": [{"name": f"genre{i}"} for i in range(12)]}
        result = musicbrainz.genres(data)
        self.assertEqual(len(result), 8)
        self.assertEqual(result[0], "genre0")

    def test_missing_genres_returns_empty_list(self):
        self.assertEqual(musicbrainz.genres({}), [])


class MusicBrainzWikipediaTests(unittest.TestCase):
    def test_finds_wikipedia_relation(self):
        data = {"relations": [
            {"url": {"resource": "https://www.discogs.com/artist/123"}},
            {"url": {"resource": "https://en.wikipedia.org/wiki/Some_Artist"}},
        ]}
        result = musicbrainz.wikipedia_title(data)
        self.assertEqual(result, ("en", "Some_Artist"))

    def test_no_wikipedia_relation_returns_none(self):
        data = {"relations": [{"url": {"resource": "https://www.discogs.com/artist/123"}}]}
        self.assertIsNone(musicbrainz.wikipedia_title(data))

    def test_no_relations_returns_none(self):
        self.assertIsNone(musicbrainz.wikipedia_title({}))


class LidarrImageSelectionTests(unittest.TestCase):
    def test_prefers_poster_over_fanart_and_banner(self):
        images = [
            {"coverType": "Banner", "url": "banner.jpg"},
            {"coverType": "Fanart", "url": "fanart.jpg"},
            {"coverType": "Poster", "url": "poster.jpg"},
        ]
        self.assertEqual(_lidarr_image(images), "poster.jpg")

    def test_falls_back_to_fanart_when_no_poster(self):
        images = [{"coverType": "Banner", "url": "banner.jpg"}, {"coverType": "Fanart", "url": "fanart.jpg"}]
        self.assertEqual(_lidarr_image(images), "fanart.jpg")

    def test_no_images_returns_none(self):
        self.assertIsNone(_lidarr_image([]))
        self.assertIsNone(_lidarr_image(None))


if __name__ == "__main__":
    unittest.main()
