"""Unit tests for AD-28: require_musicbrainz_id setting and purge_artists_without_mbid.
"""
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.artist_discovery import DiscoveredArtist
from app.schemas.settings import ArtistDiscoverySettings
from app.services.artist_discovery import (
    _create_or_promote_candidate,
    purge_artists_without_mbid,
)


def _db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class TestRequireMusicBrainzId(unittest.IsolatedAsyncioTestCase):
    async def test_candidate_creation_skips_without_mbid_when_required(self):
        db = _db()
        qdrant = MagicMock()
        with patch("app.services.artist_discovery._enrich_candidate", new_callable=AsyncMock) as mock_enrich:
            mock_enrich.return_value = {
                "image_url": None, "bio": None, "genres": [], "years_active": None, "musicbrainz_id": None
            }
            res = await _create_or_promote_candidate(
                db, qdrant, mbid=None, name="PyroLIVE",
                seeds_list=["seed-1"], source_label="ingest",
                auto_add_n=0, recent_n=1, require_mbid=True
            )
            self.assertEqual(res, {"created": 0, "promoted": 0})
            rows = db.query(DiscoveredArtist).all()
            self.assertEqual(len(rows), 0)

    async def test_candidate_creation_allows_without_mbid_when_not_required(self):
        db = _db()
        qdrant = MagicMock()
        qdrant.point_id = MagicMock(return_value="mock-id")
        with patch("app.services.artist_discovery._enrich_candidate", new_callable=AsyncMock) as mock_enrich, \
             patch("app.services.artist_discovery._resolve_seed_names", new_callable=AsyncMock) as mock_resolve:
            mock_enrich.return_value = {
                "image_url": None, "bio": None, "genres": [], "years_active": None, "musicbrainz_id": None
            }
            mock_resolve.return_value = ["Seed Artist"]
            res = await _create_or_promote_candidate(
                db, qdrant, mbid=None, name="PyroLIVE",
                seeds_list=["seed-1"], source_label="ingest",
                auto_add_n=0, recent_n=1, require_mbid=False
            )
            self.assertEqual(res, {"created": 1, "promoted": 0})
            rows = db.query(DiscoveredArtist).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].artist_name, "PyroLIVE")
            self.assertIsNone(rows[0].musicbrainz_id)

    async def test_candidate_creation_allows_with_mbid_when_required(self):
        db = _db()
        qdrant = MagicMock()
        with patch("app.services.artist_discovery._enrich_candidate", new_callable=AsyncMock) as mock_enrich, \
             patch("app.services.artist_discovery._resolve_seed_names", new_callable=AsyncMock) as mock_resolve:
            mock_enrich.return_value = {
                "image_url": "https://img.jpg", "bio": "Bio", "genres": ["metal"], "years_active": "2020-present",
                "musicbrainz_id": "real-mbid-123"
            }
            mock_resolve.return_value = ["Seed Artist"]
            res = await _create_or_promote_candidate(
                db, qdrant, mbid="real-mbid-123", name="Real Band",
                seeds_list=["seed-1"], source_label="graph",
                auto_add_n=0, recent_n=1, require_mbid=True
            )
            self.assertEqual(res, {"created": 1, "promoted": 0})
            rows = db.query(DiscoveredArtist).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].musicbrainz_id, "real-mbid-123")

    async def test_candidate_creation_resolves_mbid_from_enrichment_when_empty_initially(self):
        db = _db()
        qdrant = MagicMock()
        with patch("app.services.artist_discovery._enrich_candidate", new_callable=AsyncMock) as mock_enrich, \
             patch("app.services.artist_discovery._resolve_seed_names", new_callable=AsyncMock) as mock_resolve:
            mock_enrich.return_value = {
                "image_url": "https://img.jpg", "bio": "Bio", "genres": ["metal"], "years_active": "2020-present",
                "musicbrainz_id": "discovered-from-lidarr"
            }
            mock_resolve.return_value = ["Seed Artist"]
            res = await _create_or_promote_candidate(
                db, qdrant, mbid=None, name="Bleed from Within",
                seeds_list=["seed-1"], source_label="graph",
                auto_add_n=0, recent_n=1, require_mbid=True
            )
            self.assertEqual(res, {"created": 1, "promoted": 0})
            rows = db.query(DiscoveredArtist).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].musicbrainz_id, "discovered-from-lidarr")

    async def test_purge_artists_without_mbid(self):
        db = _db()
        # 1. Invalid YouTuber pending candidate with no MBID
        pyro = DiscoveredArtist(
            artist_name="PyroLIVE", musicbrainz_id=None, status="pending", source="ingest"
        )
        # 2. Real band with MBID
        real = DiscoveredArtist(
            artist_name="Real Band", musicbrainz_id="valid-mbid", status="pending", source="graph"
        )
        # 3. Accepted real band that originally missed MBID, but Lidarr knows it
        accepted_band = DiscoveredArtist(
            artist_name="The Animal In Me", musicbrainz_id=None, status="accepted", source="graph"
        )
        db.add_all([pyro, real, accepted_band])
        db.commit()

        mock_lidarr = AsyncMock()
        mock_lidarr.lookup_artist.return_value = [{"artistName": "The Animal In Me", "foreignArtistId": "backfilled-mbid-456"}]

        mock_qdrant = AsyncMock()
        mock_qdrant.scroll.side_effect = [
            ([
                {"id": "pt-1", "payload": {"artist_name": "penguinz0", "musicbrainz_id": "", "in_lidarr": False}},
                {"id": "pt-2", "payload": {"artist_name": "Radiohead", "musicbrainz_id": "mbid-rh", "in_lidarr": True}},
            ], None)
        ]
        mock_qdrant.delete_points = AsyncMock(return_value=True)

        with patch("app.services.artist_discovery._lidarr_client", return_value=mock_lidarr), \
             patch("app.services.artist_discovery._qdrant", return_value=mock_qdrant):
            res = await purge_artists_without_mbid(db)

        self.assertTrue(res["ok"])
        self.assertEqual(res["purged_candidates"], 1)  # PyroLIVE purged
        self.assertEqual(res["backfilled_accepted"], 1)  # The Animal In Me backfilled
        self.assertEqual(res["purged_qdrant_points"], 1)  # penguinz0 point purged

        # Verify DB state
        remaining = db.query(DiscoveredArtist).all()
        names = {r.artist_name: r.musicbrainz_id for r in remaining}
        self.assertNotIn("PyroLIVE", names)
        self.assertIn("Real Band", names)
        self.assertEqual(names["Real Band"], "valid-mbid")
        self.assertIn("The Animal In Me", names)
        self.assertEqual(names["The Animal In Me"], "backfilled-mbid-456")

        # Verify Qdrant delete call
        mock_qdrant.delete_points.assert_called_once_with(["pt-1"])
