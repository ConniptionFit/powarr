"""Unit tests for AD-29: YouTube Channel & Non-Music Detection, Prevention, and Removal."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.youtube_filter import (
    DEFAULT_BLOCKED_CHANNELS,
    has_music_genres,
    has_music_platform_links,
    has_non_music_tags,
    is_name_in_blocked_channels,
    is_non_music_channel,
    normalize_channel_name,
)
from app.models.artist_discovery import DiscoveredArtist
from app.schemas.settings import ArtistDiscoverySettings


class TestYouTubeChannelFilter(unittest.TestCase):

    def test_normalize_channel_name(self):
        self.assertEqual(normalize_channel_name("Funhaus"), "funhaus")
        self.assertEqual(normalize_channel_name("Brought You This Thing!"), "broughtyouthisthing")
        self.assertEqual(normalize_channel_name("PyroLIVE"), "pyrolive")

    def test_is_name_in_blocked_channels(self):
        blocked = ["Funhaus", "astrogoblin", "BroughtYouThisThing", "PyroLIVE"]
        self.assertTrue(is_name_in_blocked_channels("Funhaus", blocked))
        self.assertTrue(is_name_in_blocked_channels("funhaus", blocked))
        self.assertTrue(is_name_in_blocked_channels("BroughtYouThisThing", blocked))
        self.assertTrue(is_name_in_blocked_channels("brought you this thing", blocked))
        self.assertTrue(is_name_in_blocked_channels("PyroLIVE", blocked))
        self.assertFalse(is_name_in_blocked_channels("Deftones", blocked))
        self.assertFalse(is_name_in_blocked_channels("Mouth Culture", blocked))

    def test_has_music_platform_links(self):
        self.assertTrue(has_music_platform_links([{"name": "spotify"}, {"name": "youtube"}]))
        self.assertTrue(has_music_platform_links(["deezer", "facebook"]))
        self.assertTrue(has_music_platform_links(["bandcamp"]))
        self.assertTrue(has_music_platform_links([{"url": "https://open.spotify.com/artist/123"}]))
        self.assertFalse(has_music_platform_links([{"name": "facebook"}, {"name": "twitter"}]))
        self.assertFalse(has_music_platform_links([]))
        self.assertFalse(has_music_platform_links(None))

    def test_has_music_genres(self):
        self.assertTrue(has_music_genres(["indie rock", "post-punk"]))
        self.assertTrue(has_music_genres(["hip hop", "alternative"]))
        self.assertTrue(has_music_genres(["metalcore"]))
        self.assertFalse(has_music_genres(["youtube", "gaming", "podcast"]))
        self.assertFalse(has_music_genres([]))
        self.assertFalse(has_music_genres(None))

    def test_has_non_music_tags(self):
        self.assertTrue(has_non_music_tags(["youtube"]))
        self.assertTrue(has_non_music_tags(["podcast", "gaming"]))
        self.assertTrue(has_non_music_tags(["let's play"]))
        self.assertFalse(has_non_music_tags(["rock", "pop", "metal"]))

    def test_non_music_classification_blocked_channels(self):
        is_nm, reason = is_non_music_channel("Funhaus", blocked_channels=DEFAULT_BLOCKED_CHANNELS)
        self.assertTrue(is_nm)
        self.assertEqual(reason, "matches_blocked_channels")

        is_nm, reason = is_non_music_channel("astrogoblin", blocked_channels=DEFAULT_BLOCKED_CHANNELS)
        self.assertTrue(is_nm)
        self.assertEqual(reason, "matches_blocked_channels")

    def test_non_music_classification_tags(self):
        is_nm, reason = is_non_music_channel("SomeRandomCreator", tags=["youtube", "gaming"])
        self.assertTrue(is_nm)
        self.assertEqual(reason, "non_music_tags")

    def test_non_music_classification_bio(self):
        is_nm, reason = is_non_music_channel(
            "CreatorGroup",
            bio="CreatorGroup is an American comedy group and YouTube channel based in LA."
        )
        self.assertTrue(is_nm)
        self.assertEqual(reason, "bio_indicates_youtube_or_podcast")

    def test_musician_shield_with_youtube_presence(self):
        """Musicians who have YouTube channels must NOT be blocked."""
        # Joji: has music genres, streaming links, and YouTube link
        is_nm, reason = is_non_music_channel(
            "Joji",
            tags=["rnb", "hip-hop", "lo-fi"],
            lidarr_artist_data={
                "links": [{"name": "spotify"}, {"name": "apple"}, {"name": "youtube"}],
                "genres": ["r&b"],
            },
            blocked_channels=DEFAULT_BLOCKED_CHANNELS,
        )
        self.assertFalse(is_nm)
        self.assertEqual(reason, "verified_musician_links_and_genres")

        # Mouth Culture: has indie rock tags and spotify link
        is_nm, reason = is_non_music_channel(
            "Mouth Culture",
            tags=["alternative rock", "indie"],
            lidarr_artist_data={"links": [{"name": "spotify"}, {"name": "deezer"}]},
            blocked_channels=DEFAULT_BLOCKED_CHANNELS,
        )
        self.assertFalse(is_nm)
        self.assertEqual(reason, "verified_musician_links_and_genres")

    def test_library_presence_shield(self):
        """If user already has the artist in Lidarr/Plex, never flag as non-music."""
        is_nm, reason = is_non_music_channel(
            "Funhaus",
            in_library=True,
            blocked_channels=DEFAULT_BLOCKED_CHANNELS,
        )
        self.assertFalse(is_nm)
        self.assertEqual(reason, "owned_in_library")


class TestPurgeNonMusicArtists(unittest.IsolatedAsyncioTestCase):

    async def test_purge_non_music_candidates(self):
        from app.services.artist_discovery import purge_non_music_artists

        db = MagicMock()
        mock_cands = [
            # Candidate 1: Funhaus (blocked channel)
            DiscoveredArtist(
                id=1, artist_name="Funhaus", musicbrainz_id="e695eda1-6cd6-41c5-bdbf-25e2d7895809",
                status="pending", genres="[]", associated_seed_mbids='["astrogoblin", "BroughtYouThisThing"]'
            ),
            # Candidate 2: Genuine band (Mouth Culture)
            DiscoveredArtist(
                id=2, artist_name="Mouth Culture", musicbrainz_id="08d71fb5-af78-4e5b-b54b-0b5c7587d152",
                status="pending", genres='["indie rock"]', associated_seed_mbids='["seed1"]'
            ),
            # Candidate 3: Accepted library band
            DiscoveredArtist(
                id=3, artist_name="Bleed from Within", musicbrainz_id="mbid-bfw",
                status="accepted", lidarr_artist_id=10, genres='["metalcore"]'
            ),
        ]
        db.query.return_value.all.return_value = mock_cands

        mock_qdrant = MagicMock()
        mock_qdrant.scroll = AsyncMock(return_value=([
            {"id": "pt-funhaus", "payload": {"artist_name": "Funhaus", "in_lidarr": False}},
            {"id": "pt-mouth", "payload": {"artist_name": "Mouth Culture", "in_lidarr": False}},
        ], None))
        mock_qdrant.delete_points = AsyncMock(return_value=True)

        with patch("app.services.artist_discovery._qdrant", return_value=mock_qdrant), \
             patch("app.services.artist_discovery._lidarr_client", return_value=None), \
             patch("app.services.artist_discovery.load_settings", return_value=ArtistDiscoverySettings(
                 blocked_channels=["Funhaus", "astrogoblin", "BroughtYouThisThing"]
             )):
            res = await purge_non_music_artists(db)

        self.assertTrue(res["ok"])
        self.assertEqual(res["purged_candidates"], 1)  # Funhaus purged
        self.assertEqual(res["purged_qdrant_points"], 1)  # pt-funhaus deleted
        db.delete.assert_called_once_with(mock_cands[0])
        mock_qdrant.delete_points.assert_called_once_with(["pt-funhaus"])


if __name__ == "__main__":
    unittest.main()
