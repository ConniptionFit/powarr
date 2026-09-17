"""Unit tests for Artist Discovery suggestion insights (get_candidate_insights)
and Deezer top tracks integration.
"""
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.integrations import deezer
from app.models.artist_discovery import DiscoveredArtist
from app.models.media import MediaItem
from app.schemas.settings import ArtistDiscoverySettings
from app.services.artist_discovery import get_candidate_insights, save_settings


def _db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class TestCandidateInsights(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        deezer.clear_top_tracks_cache()

    async def test_candidate_not_found(self):
        db = _db()
        insights = await get_candidate_insights(db, 9999)
        self.assertIsNone(insights)

    async def test_centroid_candidate_insights(self):
        db = _db()
        # Seed media item
        seed_track = MediaItem(
            plex_rating_key="track-1",
            title="Screaming",
            parent_title="Loathe",
            media_type="track",
            watch_count=42,
        )
        db.add(seed_track)

        cand = DiscoveredArtist(
            artist_name="Moodring",
            musicbrainz_id="b0e216a9-b8ab-4882-9c07-c54d8f684cf0",
            source="centroid",
            similarity_score=0.7504,
            genres=json.dumps(["alternative metal", "shoegaze", "serbian"]),
            mood_tags=json.dumps(["intense"]),
            seed_artist_names=json.dumps(["Loathe", "Static Dress"]),
            associated_seed_mbids=json.dumps(["m-loathe", "m-static"]),
            status="pending",
        )
        db.add(cand)
        db.commit()

        with patch("app.integrations.deezer.get_artist_top_tracks", new_callable=AsyncMock) as mock_top:
            mock_top.return_value = [
                {"id": 1, "title": "Cannibal", "duration": 181, "preview": "https://preview.mp3"}
            ]
            insights = await get_candidate_insights(db, cand.id)

        self.assertIsNotNone(insights)
        self.assertEqual(insights["artist_name"], "Moodring")
        self.assertEqual(insights["gate"]["lane"], "centroid")
        self.assertEqual(insights["gate"]["similarity_percent"], 75)
        self.assertEqual(insights["gate"]["connection_count"], 2)
        self.assertFalse(insights["is_low_metadata"])
        self.assertIsNone(insights["warning"])

        # Check seeds
        seeds = {s["name"]: s for s in insights["seeds"]}
        self.assertIn("Loathe", seeds)
        self.assertEqual(seeds["Loathe"]["plays_in_library"], 42)

        # Top tracks
        self.assertEqual(len(insights["top_tracks"]), 1)
        self.assertEqual(insights["top_tracks"][0]["title"], "Cannibal")

    async def test_low_metadata_noise_detection(self):
        db = _db()
        cand = DiscoveredArtist(
            artist_name="PyroLIVE",
            musicbrainz_id=None,
            source="ingest",
            similarity_score=None,
            genres=json.dumps([]),
            seed_artist_names=json.dumps(["DougDoug", "penguinz0"]),
            associated_seed_mbids=json.dumps(["DougDoug", "penguinz0"]),
            status="pending",
        )
        db.add(cand)
        db.commit()

        with patch("app.integrations.deezer.get_artist_top_tracks", new_callable=AsyncMock) as mock_top:
            mock_top.return_value = []
            insights = await get_candidate_insights(db, cand.id)

        self.assertIsNotNone(insights)
        self.assertTrue(insights["is_low_metadata"])
        self.assertIsNotNone(insights["warning"])
        self.assertIn("Missing MusicBrainz ID", insights["warning"])
        self.assertEqual(insights["gate"]["lane"], "ingest")
        self.assertIsNone(insights["gate"]["similarity_percent"])

    async def test_recent_and_all_time_connections_distinction(self):
        db = _db()
        save_settings(db, ArtistDiscoverySettings(auto_add_connection_threshold=3, scrobble_lookback_days=30))
        cand = DiscoveredArtist(
            artist_name="Texas in July",
            musicbrainz_id="b10bbbfc-cf9e-42e0-be17-e2c3e1d52350",
            source="graph",
            similarity_score=None,
            genres=json.dumps(["metalcore"]),
            seed_artist_names=json.dumps(["For the Fallen Dreams", "No Bragging Rights", "Within the Ruins"]),
            associated_seed_mbids=json.dumps(["m1", "m2", "m3"]),
            status="pending",
        )
        db.add(cand)
        db.commit()

        # Mock get_cached_recent_keys_and_status to return 1 recent match out of 3
        with patch("app.services.artist_discovery.get_cached_recent_keys_and_status", new_callable=AsyncMock) as mock_recent, \
             patch("app.integrations.deezer.get_artist_top_tracks", new_callable=AsyncMock) as mock_top:
            mock_recent.return_value = ({"within the ruins"}, True)
            mock_top.return_value = []
            insights = await get_candidate_insights(db, cand.id)

        self.assertIsNotNone(insights)
        gate = insights["gate"]
        self.assertEqual(gate["all_time_connections"], 3)
        self.assertEqual(gate["recent_connections"], 1)
        self.assertFalse(gate["auto_add_eligible"])
        self.assertIn("Requires 3 recent connections", gate["auto_add_reason"])
        self.assertIn("1 recent (3 all-time)", gate["auto_add_reason"])

        # Check seeds have is_recent flag accurately set
        seeds = {s["name"]: s["is_recent"] for s in insights["seeds"]}
        self.assertTrue(seeds["Within the Ruins"])
        self.assertFalse(seeds["For the Fallen Dreams"])
        self.assertFalse(seeds["No Bragging Rights"])


class TestDeezerTopTracks(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        deezer.clear_top_tracks_cache()

    async def test_deezer_empty_name(self):
        tracks = await deezer.get_artist_top_tracks("")
        self.assertEqual(tracks, [])

    @patch("httpx.AsyncClient.get")
    async def test_deezer_exact_name_match(self, mock_get):
        # Mock search response
        mock_search_resp = MagicMock()
        mock_search_resp.status_code = 200
        mock_search_resp.json.return_value = {
            "data": [{"id": 111, "name": "Moodring"}]
        }

        # Mock top tracks response
        mock_top_resp = MagicMock()
        mock_top_resp.status_code = 200
        mock_top_resp.json.return_value = {
            "data": [
                {
                    "id": 101,
                    "title": "Cannibal",
                    "duration": 180,
                    "preview": "https://cdn.example/preview.mp3",
                    "album": {"title": "Cannibal", "cover_medium": "https://cdn.example/cover.jpg"},
                }
            ]
        }

        mock_get.side_effect = [mock_search_resp, mock_top_resp]

        tracks = await deezer.get_artist_top_tracks("Moodring", limit=3)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["title"], "Cannibal")
        self.assertEqual(tracks[0]["preview"], "https://cdn.example/preview.mp3")
        self.assertEqual(tracks[0]["album"], "Cannibal")

    @patch("httpx.AsyncClient.get")
    async def test_deezer_mismatched_name_rejected(self, mock_get):
        # Fuzzy match to different name should be rejected
        mock_search_resp = MagicMock()
        mock_search_resp.status_code = 200
        mock_search_resp.json.return_value = {
            "data": [{"id": 999, "name": "The Police"}]
        }
        mock_get.return_value = mock_search_resp

        tracks = await deezer.get_artist_top_tracks("PyroLIVE")
        self.assertEqual(tracks, [])
