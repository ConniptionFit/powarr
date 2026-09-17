"""Unit tests for AD-32: Auto-Add Proximity Sorting & All-Time Auto-Add Threshold.

Verifies:
1. Proximity-based "Best Match" sorting:
   - 8/10 all-time (80%) > 2/3 recent (66.7%) > 6/10 all-time (60%)
   - 0-recent candidate does not artificially outrank candidates closer to auto-adding.
2. Dual auto-add promotion pipeline:
   - Auto-adds if either recent >= recent_threshold OR all-time >= all-time_threshold.
3. Suggestion insights gate:
   - Exposes auto_add_recent_threshold, auto_add_all_time_threshold, auto_add_progress,
     and auto_add_eligible.
"""
import json
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.v1.artist_discovery import _candidate_best_match_key, CandidateOut
from app.database import Base
from app.models.artist_discovery import DiscoveredArtist
from app.schemas.settings import ArtistDiscoverySettings
from app.services.artist_discovery import get_candidate_insights, save_settings, _create_or_promote_candidate


def _db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class TestAutoAddProximitySort(unittest.TestCase):
    def test_user_scenario_dual_threshold_ranking(self):
        """User specification:
        Recent threshold: 3
        All-time threshold: 10
        Artist C: 8/10 all-time (80%) -> #1
        Artist B: 2/3 recent (66.7%) -> #2
        Artist A: 6/10 all-time (60%) -> #3
        Artist D: 0/3 recent, 3/10 all-time (30%) -> #4
        """
        cfg = ArtistDiscoverySettings(
            auto_add_connection_threshold=3,
            auto_add_all_time_threshold=10,
        )

        cand_a = CandidateOut(
            id=1, artist_name="Artist A", source="graph",
            recent_connections=0, all_time_connections=6,
            created_at=datetime.utcnow(),
        )
        cand_b = CandidateOut(
            id=2, artist_name="Artist B", source="graph",
            recent_connections=2, all_time_connections=2,
            created_at=datetime.utcnow(),
        )
        cand_c = CandidateOut(
            id=3, artist_name="Artist C", source="graph",
            recent_connections=0, all_time_connections=8,
            created_at=datetime.utcnow(),
        )
        cand_d = CandidateOut(
            id=4, artist_name="Artist D", source="graph",
            recent_connections=0, all_time_connections=3,
            created_at=datetime.utcnow(),
        )

        candidates = [cand_a, cand_b, cand_c, cand_d]
        candidates.sort(key=lambda c: _candidate_best_match_key(c, cfg), reverse=True)

        ranked_names = [c.artist_name for c in candidates]
        self.assertEqual(ranked_names, ["Artist C", "Artist B", "Artist A", "Artist D"])

    def test_recent_only_threshold_does_not_rank_zero_recent_at_top(self):
        """When only recent threshold is active (e.g. 3) and all-time is disabled (0),
        a candidate with 1 recent connection (33.3%) must rank above Texas in July
        which has 0 recent connections despite having 3 all-time connections.
        """
        cfg = ArtistDiscoverySettings(
            auto_add_connection_threshold=3,
            auto_add_all_time_threshold=0,
        )

        texas_in_july = CandidateOut(
            id=1, artist_name="Texas in July", source="graph",
            recent_connections=0, all_time_connections=3,
            created_at=datetime.utcnow(),
        )
        active_candidate = CandidateOut(
            id=2, artist_name="Active Candidate", source="graph",
            recent_connections=1, all_time_connections=1,
            created_at=datetime.utcnow(),
        )

        candidates = [texas_in_july, active_candidate]
        candidates.sort(key=lambda c: _candidate_best_match_key(c, cfg), reverse=True)

        self.assertEqual(candidates[0].artist_name, "Active Candidate")
        self.assertEqual(candidates[1].artist_name, "Texas in July")


class TestAllTimeAutoAddPromotion(unittest.IsolatedAsyncioTestCase):
    async def test_promotes_when_all_time_threshold_met(self):
        """When auto_add_all_time_threshold is set to 10, a candidate with 10 seeds
        auto-adds to Lidarr even if recent_connections is 0.
        """
        db = _db()
        seeds = [f"seed_{i}" for i in range(10)]

        with patch("app.services.artist_discovery._enrich_candidate", new_callable=AsyncMock) as mock_enrich, \
             patch("app.services.artist_discovery.add_to_lidarr", new_callable=AsyncMock) as mock_add:
            mock_enrich.return_value = {
                "image_url": None, "bio": "Test bio", "years_active": "2010s",
                "genres": ["metalcore"], "musicbrainz_id": "mbid-123",
            }
            mock_add.return_value = {"ok": True, "artist_name": "Prolific Band"}

            res = await _create_or_promote_candidate(
                db, None, mbid="mbid-123", name="Prolific Band",
                seeds_list=seeds, source_label="graph",
                auto_add_n=3, recent_n=0, auto_add_all_time_n=10,
                require_mbid=False, filter_youtube=False, require_seed_grounding=False,
            )

        self.assertEqual(res["created"], 0)
        self.assertEqual(res["promoted"], 1)
        mock_add.assert_called_once()

    async def test_insights_dual_auto_add_progress(self):
        """Verifies insights modal gate calculation with dual thresholds."""
        db = _db()
        save_settings(db, ArtistDiscoverySettings(
            auto_add_connection_threshold=3,
            auto_add_all_time_threshold=10,
            scrobble_lookback_days=30,
        ))

        cand = DiscoveredArtist(
            artist_name="Between The Buried And Me",
            musicbrainz_id="b51512f4-a447-4952-b883-9b870505ce3a",
            source="graph",
            similarity_score=None,
            genres=json.dumps(["progressive metal"]),
            seed_artist_names=json.dumps([f"Band {i}" for i in range(6)]),
            associated_seed_mbids=json.dumps([f"m{i}" for i in range(6)]),
            status="pending",
        )
        db.add(cand)
        db.commit()

        # 1 recent scrobble out of 6 seeds
        with patch("app.services.artist_discovery.get_cached_recent_keys_and_status", new_callable=AsyncMock) as mock_recent, \
             patch("app.integrations.deezer.get_artist_top_tracks", new_callable=AsyncMock) as mock_top:
            mock_recent.return_value = ({"band 0"}, True)
            mock_top.return_value = []
            insights = await get_candidate_insights(db, cand.id)

        self.assertIsNotNone(insights)
        gate = insights["gate"]
        self.assertEqual(gate["recent_connections"], 1)
        self.assertEqual(gate["all_time_connections"], 6)
        self.assertEqual(gate["auto_add_recent_threshold"], 3)
        self.assertEqual(gate["auto_add_all_time_threshold"], 10)
        # Progress is max(1/3 = 0.333, 6/10 = 0.60) = 0.6
        self.assertAlmostEqual(gate["auto_add_progress"], 0.60, places=2)
        self.assertFalse(gate["auto_add_eligible"])
        self.assertIn("Requires 3 recent connections in 30d", gate["auto_add_reason"])
        self.assertIn("10 all-time connections", gate["auto_add_reason"])
