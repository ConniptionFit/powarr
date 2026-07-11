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


class MusicBrainzWikidataTests(unittest.TestCase):
    """v0.41.0 — most MB artists only carry a wikidata rel (direct wikipedia rels
    are deprecated), so the enrichment chain resolves the Q-id instead (AD-04)."""

    def test_finds_wikidata_qid(self):
        data = {"relations": [
            {"url": {"resource": "https://www.discogs.com/artist/123"}},
            {"url": {"resource": "https://www.wikidata.org/wiki/Q6693441"}},
        ]}
        self.assertEqual(musicbrainz.wikidata_qid(data), "Q6693441")

    def test_no_wikidata_relation_returns_none(self):
        self.assertIsNone(musicbrainz.wikidata_qid(
            {"relations": [{"url": {"resource": "https://en.wikipedia.org/wiki/X"}}]}))
        self.assertIsNone(musicbrainz.wikidata_qid({}))


class MusicBrainzDeezerTests(unittest.TestCase):
    def test_extracts_deezer_artist_id(self):
        data = {"relations": [
            {"url": {"resource": "https://www.deezer.com/artist/74877"}},
        ]}
        self.assertEqual(musicbrainz.deezer_artist_id(data), "74877")

    def test_handles_language_prefixed_deezer_url(self):
        data = {"relations": [{"url": {"resource": "https://www.deezer.com/en/artist/74877"}}]}
        self.assertEqual(musicbrainz.deezer_artist_id(data), "74877")

    def test_no_deezer_relation_returns_none(self):
        self.assertIsNone(musicbrainz.deezer_artist_id({}))


class WikidataSitelinkTests(unittest.TestCase):
    def test_prefers_enwiki(self):
        from app.services.wikipedia import _sitelink_title
        links = {"dewiki": {"title": "Band DE"}, "enwiki": {"title": "Band EN"}}
        self.assertEqual(_sitelink_title(links), ("en", "Band EN"))

    def test_falls_back_to_any_language_wiki(self):
        from app.services.wikipedia import _sitelink_title
        self.assertEqual(_sitelink_title({"dewiki": {"title": "Band DE"}}), ("de", "Band DE"))

    def test_ignores_commonswiki_and_empty(self):
        from app.services.wikipedia import _sitelink_title
        self.assertIsNone(_sitelink_title({"commonswiki": {"title": "Category:Band"}}))
        self.assertIsNone(_sitelink_title({}))


class CleanTagsTests(unittest.TestCase):
    """AD-06 — placeholder 'Unknown' chips are filtered at creation and at the
    API serializer (covers legacy rows)."""

    def test_drops_placeholder_values_case_insensitive(self):
        from app.services.artist_discovery import clean_tags
        self.assertEqual(clean_tags(["rock", "Unknown", "N/A", "none", "", "metal"]),
                         ["rock", "metal"])

    def test_dedupes_and_strips(self):
        from app.services.artist_discovery import clean_tags
        self.assertEqual(clean_tags([" rock ", "rock"]), ["rock"])

    def test_none_input_is_safe(self):
        from app.services.artist_discovery import clean_tags
        self.assertEqual(clean_tags(None), [])

    def test_clean_era(self):
        from app.services.artist_discovery import clean_era
        self.assertIsNone(clean_era("Unknown"))
        self.assertIsNone(clean_era(None))
        self.assertIsNone(clean_era("  "))
        self.assertEqual(clean_era("2000s"), "2000s")


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


class RecentConnectionCountTests(unittest.TestCase):
    """AD-07 — dual-threshold connection counting against recently-listened seeds."""

    def test_counts_only_recent_keys(self):
        from app.services.artist_discovery import _recent_connection_count
        seeds = ["mbid-a", "mbid-b", "Some Artist"]
        recent = {"mbid-a", "some artist"}
        self.assertEqual(_recent_connection_count(seeds, recent), 2)

    def test_empty_recent_falls_back_to_full_list(self):
        from app.services.artist_discovery import _recent_connection_count
        seeds = ["a", "b", "c"]
        self.assertEqual(_recent_connection_count(seeds, set()), 3)

    def test_effective_auto_add_zero_disables(self):
        from app.schemas.settings import ArtistDiscoverySettings
        from app.services.artist_discovery import _effective_auto_add_threshold
        cfg = ArtistDiscoverySettings(auto_add_connection_threshold=0, auto_promote=False)
        self.assertEqual(_effective_auto_add_threshold(cfg), 0)

    def test_legacy_auto_promote_uses_suggest(self):
        from app.schemas.settings import ArtistDiscoverySettings
        from app.services.artist_discovery import _effective_auto_add_threshold
        cfg = ArtistDiscoverySettings(
            suggest_connection_threshold=3, auto_add_connection_threshold=0, auto_promote=True)
        self.assertEqual(_effective_auto_add_threshold(cfg), 3)


class AlreadyOwnedFilterTests(unittest.TestCase):
    """Mirrors run_graph_sync's already_owned check: a related artist already in
    Lidarr (monitored or not, matched by mbid or normalized name) or already in
    the locally-synced Plex library is excluded before any candidate is created."""

    @staticmethod
    def _already_owned(lidarr_by_mbid, lidarr_by_name, plex_names, rmbid, rname):
        rname_norm = _norm_artist(rname)
        return bool(lidarr_by_mbid.get(rmbid) or lidarr_by_name.get(rname_norm)) \
            or rname_norm in plex_names

    def test_unmonitored_lidarr_artist_is_still_excluded(self):
        # The whole point of in_lidarr vs is_monitored_lidarr: an unmonitored
        # Lidarr artist must still be excluded from suggestions.
        by_mbid = {"mbid-1": {"foreignArtistId": "mbid-1", "monitored": False}}
        owned = self._already_owned(by_mbid, {}, set(), "mbid-1", "Some Artist")
        self.assertTrue(owned)

    def test_lidarr_name_match_excludes_when_no_mbid(self):
        by_name = {"some artist": {"artistName": "Some Artist"}}
        owned = self._already_owned({}, by_name, set(), None, "Some Artist")
        self.assertTrue(owned)

    def test_plex_only_artist_is_excluded(self):
        owned = self._already_owned({}, {}, {"some artist"}, None, "Some Artist")
        self.assertTrue(owned)

    def test_artist_in_neither_is_not_excluded(self):
        owned = self._already_owned({}, {}, {"someone else"}, "other-mbid", "New Artist")
        self.assertFalse(owned)


class BlacklistTests(unittest.TestCase):
    def test_blacklist_match_is_normalized(self):
        from app.schemas.settings import SmartPlaylistSettings
        from app.services.playlist_generator import _blacklist_set, _is_blacklisted
        cfg = SmartPlaylistSettings(blacklisted_artists=["Guns N' Roses", "  The Band  "])
        blocked = _blacklist_set(cfg)
        self.assertTrue(_is_blacklisted("guns n roses", blocked))
        self.assertTrue(_is_blacklisted("The Band", blocked))
        self.assertFalse(_is_blacklisted("Someone Else", blocked))


if __name__ == "__main__":
    unittest.main()
