"""Unit tests for the ingestion-time similar-artist expansion (n8n
ingestor_filter parity — closes the candidate-starvation gap where only
run_graph_sync, scoped to monitored-Lidarr artists, populated the
centroid-search candidate pool) and the unified connection-based
suggest/auto-add gating shared by every discovery lane (centroid, graph,
ingestion)."""
import json
import time
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.integrations.qdrant import QdrantIntegration
from app.models.artist_discovery import DiscoveredArtist
from app.schemas.settings import ArtistDiscoverySettings
from app.services.artist_discovery import (
    _gate_and_create_candidate, _gate_centroid_candidate, _upsert_related_point,
    ingest_scrobbles,
)

_ENRICHMENT = {"image_url": None, "bio": None, "genres": [], "years_active": None}


class _FakeQdrant:
    """In-memory stand-in for QdrantIntegration — enough of retrieve/upsert/
    set_payload/scroll for these tests. point_id reuses the real static
    method so IDs stay authentic (MD5-of-mbid-or-normalized-name)."""
    point_id = staticmethod(QdrantIntegration.point_id)

    def __init__(self, points=None):
        self._points = {p["id"]: p for p in (points or [])}

    async def retrieve_points(self, ids, with_vector=False):
        # with_vector mirrors the real QdrantIntegration signature — ingest passes
        # it (AD-24) so an existing vector can be carried through its upsert.
        return [self._points[i] for i in ids if i in self._points]

    async def upsert_points(self, points):
        for p in points:
            self._points[p["id"]] = {"id": p["id"], "payload": p["payload"], "vector": p.get("vector")}

    async def set_payload(self, ids, updates):
        for i in ids:
            if i in self._points:
                self._points[i]["payload"].update(updates)

    async def scroll(self, *, filter=None, limit=256, offset=None, with_vector=False):
        return list(self._points.values()), None


class _FakeLastfm:
    def __init__(self, top_artists=None, similar=None, recent_tracks=None):
        self._top = top_artists or []
        self._similar = similar or {}
        self._recent = recent_tracks or []

    async def get_top_artists(self, limit=200):
        return self._top

    async def get_similar_artists(self, artist, mbid=None, limit=15):
        return self._similar.get(artist, [])

    async def get_top_tags(self, artist, mbid=None):
        return []

    async def get_recent_tracks(self, from_ts=None, limit=200):
        return self._recent


class UpsertRelatedPointTests(unittest.IsolatedAsyncioTestCase):
    async def test_creates_new_point_with_seed(self):
        with patch("app.services.artist_discovery._embed_artist", return_value=[0.1, 0.2]):
            point = await _upsert_related_point(
                _FakeQdrant(), _FakeLastfm(), ArtistDiscoverySettings(), "seed-mbid", "New Artist", "new-mbid")
        self.assertIsNotNone(point)
        self.assertEqual(point["payload"]["associated_seed_mbids"], ["seed-mbid"])
        self.assertFalse(point["payload"]["is_discovered"])

    async def test_appends_new_seed_to_existing_point(self):
        pid = QdrantIntegration.point_id("new-mbid", "New Artist")
        qdrant = _FakeQdrant([{"id": pid, "payload": {
            "musicbrainz_id": "new-mbid", "artist_name": "New Artist",
            "associated_seed_mbids": ["seed-a"], "is_discovered": False}}])
        point = await _upsert_related_point(qdrant, _FakeLastfm(), ArtistDiscoverySettings(),
                                             "seed-b", "New Artist", "new-mbid")
        self.assertEqual(set(point["payload"]["associated_seed_mbids"]), {"seed-a", "seed-b"})

    async def test_duplicate_seed_not_appended_twice(self):
        pid = QdrantIntegration.point_id("new-mbid", "New Artist")
        qdrant = _FakeQdrant([{"id": pid, "payload": {
            "musicbrainz_id": "new-mbid", "artist_name": "New Artist",
            "associated_seed_mbids": ["seed-a"], "is_discovered": False}}])
        point = await _upsert_related_point(qdrant, _FakeLastfm(), ArtistDiscoverySettings(),
                                             "seed-a", "New Artist", "new-mbid")
        self.assertEqual(point["payload"]["associated_seed_mbids"], ["seed-a"])


class GateAndCreateCandidateTests(unittest.IsolatedAsyncioTestCase):
    """Graph/ingest lanes: connection count IS the suggest-worthiness floor."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.qdrant = _FakeQdrant()

    def tearDown(self):
        self.db.close()

    async def test_below_suggest_threshold_creates_nothing(self):
        cfg = ArtistDiscoverySettings(suggest_connection_threshold=3, auto_add_connection_threshold=0)
        with patch("app.services.artist_discovery._enrich_candidate", return_value=_ENRICHMENT):
            result = await _gate_and_create_candidate(
                self.db, self.qdrant, cfg, set(), mbid="m1", name="Artist",
                seeds_list=["seed-a"], source_label="ingest", seed_artist_name="Seed", payload={})
        self.assertEqual(result, {"created": 0, "promoted": 0})
        self.assertEqual(self.db.query(DiscoveredArtist).count(), 0)

    async def test_at_suggest_threshold_creates_pending_row(self):
        cfg = ArtistDiscoverySettings(suggest_connection_threshold=2, auto_add_connection_threshold=0)
        with patch("app.services.artist_discovery._enrich_candidate", return_value=_ENRICHMENT):
            result = await _gate_and_create_candidate(
                self.db, self.qdrant, cfg, set(), mbid="m1", name="Artist",
                seeds_list=["seed-a", "seed-b"], source_label="ingest", seed_artist_name="Seed A", payload={})
        self.assertEqual(result, {"created": 1, "promoted": 0})
        row = self.db.query(DiscoveredArtist).filter_by(musicbrainz_id="m1").first()
        self.assertEqual(row.status, "pending")
        self.assertEqual(row.source, "ingest")
        self.assertEqual(json.loads(row.associated_seed_mbids), ["seed-a", "seed-b"])

    async def test_at_auto_add_threshold_promotes_instead_of_queueing(self):
        cfg = ArtistDiscoverySettings(suggest_connection_threshold=2, auto_add_connection_threshold=3)
        with patch("app.services.artist_discovery._enrich_candidate", return_value=_ENRICHMENT), \
             patch("app.services.artist_discovery.add_to_lidarr",
                   return_value={"ok": True, "lidarr_artist_id": 99}) as mock_add:
            result = await _gate_and_create_candidate(
                self.db, self.qdrant, cfg, set(), mbid="m1", name="Artist",
                seeds_list=["seed-a", "seed-b", "seed-c"], source_label="graph",
                seed_artist_name="Seed A", payload={})
        self.assertEqual(result, {"created": 0, "promoted": 1})
        mock_add.assert_awaited_once()
        self.assertEqual(self.db.query(DiscoveredArtist).count(), 1)  # created then promoted, not queued

    async def test_existing_pending_row_refreshes_seeds_and_can_still_promote(self):
        row = DiscoveredArtist(musicbrainz_id="m1", artist_name="Artist", source="graph",
                                status="pending", associated_seed_mbids=json.dumps(["seed-a"]))
        self.db.add(row)
        self.db.commit()
        cfg = ArtistDiscoverySettings(suggest_connection_threshold=2, auto_add_connection_threshold=2)
        with patch("app.services.artist_discovery.add_to_lidarr",
                   return_value={"ok": True, "lidarr_artist_id": 5}) as mock_add:
            result = await _gate_and_create_candidate(
                self.db, self.qdrant, cfg, set(), mbid="m1", name="Artist",
                seeds_list=["seed-a", "seed-b"], source_label="graph",
                seed_artist_name="Seed B", payload={})
        self.assertEqual(result, {"created": 0, "promoted": 1})
        mock_add.assert_awaited_once()
        self.assertEqual(json.loads(row.associated_seed_mbids), ["seed-a", "seed-b"])

    async def test_rejected_row_blocks_recreation(self):
        self.db.add(DiscoveredArtist(musicbrainz_id="m1", artist_name="Artist",
                                      source="graph", status="rejected"))
        self.db.commit()
        cfg = ArtistDiscoverySettings(suggest_connection_threshold=1, auto_add_connection_threshold=0)
        result = await _gate_and_create_candidate(
            self.db, self.qdrant, cfg, set(), mbid="m1", name="Artist",
            seeds_list=["seed-a", "seed-b", "seed-c"], source_label="graph",
            seed_artist_name="Seed", payload={})
        self.assertEqual(result, {"created": 0, "promoted": 0})
        self.assertEqual(self.db.query(DiscoveredArtist).count(), 1)  # still just the rejected row


class GateCentroidCandidateTests(unittest.IsolatedAsyncioTestCase):
    """Centroid lane: cosine similarity (already checked by the caller via
    Qdrant's score_threshold) is the suggest-worthiness floor — connections
    only ever upgrade a hit to an immediate auto-add, never suppress it."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.qdrant = _FakeQdrant()

    def tearDown(self):
        self.db.close()

    async def test_creates_pending_row_regardless_of_connection_count(self):
        cfg = ArtistDiscoverySettings(suggest_connection_threshold=3, auto_add_connection_threshold=0)
        with patch("app.services.artist_discovery._enrich_candidate", return_value=_ENRICHMENT):
            result = await _gate_centroid_candidate(
                self.db, self.qdrant, cfg, set(), mbid="m1", name="Artist",
                source_label="centroid", similarity_score=0.81,
                payload={"associated_seed_mbids": ["seed-a"]})
        self.assertEqual(result, {"created": 1, "promoted": 0})
        row = self.db.query(DiscoveredArtist).filter_by(musicbrainz_id="m1").first()
        self.assertEqual(row.similarity_score, 0.81)
        self.assertEqual(json.loads(row.associated_seed_mbids), ["seed-a"])

    async def test_well_connected_centroid_hit_auto_adds(self):
        cfg = ArtistDiscoverySettings(auto_add_connection_threshold=2)
        with patch("app.services.artist_discovery._enrich_candidate", return_value=_ENRICHMENT), \
             patch("app.services.artist_discovery.add_to_lidarr",
                   return_value={"ok": True, "lidarr_artist_id": 1}) as mock_add:
            result = await _gate_centroid_candidate(
                self.db, self.qdrant, cfg, set(), mbid="m1", name="Artist",
                source_label="centroid", similarity_score=0.9,
                payload={"associated_seed_mbids": ["seed-a", "seed-b"]})
        self.assertEqual(result, {"created": 0, "promoted": 1})
        mock_add.assert_awaited_once()

    async def test_no_connections_still_creates_row(self):
        cfg = ArtistDiscoverySettings(suggest_connection_threshold=5)
        with patch("app.services.artist_discovery._enrich_candidate", return_value=_ENRICHMENT):
            result = await _gate_centroid_candidate(
                self.db, self.qdrant, cfg, set(), mbid="m1", name="Artist",
                source_label="centroid", similarity_score=0.76, payload={})
        self.assertEqual(result, {"created": 1, "promoted": 0})


class IngestScrobblesExpansionTests(unittest.IsolatedAsyncioTestCase):
    """ingest_scrobbles now expands every taste seed into new
    is_discovered=false candidates via Last.fm similar-artist lookup —
    previously only run_graph_sync (monitored-Lidarr seeds only) did this,
    starving the centroid-search candidate pool."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    async def _run(self, qdrant, lastfm, cfg=None, lidarr_index=({}, {})):
        cfg = cfg or ArtistDiscoverySettings(suggest_connection_threshold=1)
        with patch("app.services.artist_discovery._qdrant", return_value=qdrant), \
             patch("app.services.artist_discovery._lastfm_client", return_value=lastfm), \
             patch("app.services.artist_discovery._embed_artist", return_value=[0.1, 0.2]), \
             patch("app.services.artist_discovery._enrich_candidate", return_value=_ENRICHMENT), \
             patch("app.services.artist_discovery._lidarr_artist_index", return_value=lidarr_index), \
             patch("app.services.artist_discovery._plex_artist_names", return_value=set()):
            return await ingest_scrobbles(self.db, cfg)

    async def test_new_seed_gets_expanded_into_new_candidate(self):
        top = [{"name": "Seed Artist", "mbid": "seed-mbid", "playcount": "50"}]
        similar = {"Seed Artist": [{"name": "New Candidate", "mbid": "cand-mbid", "match": "0.9"}]}
        qdrant = _FakeQdrant()
        result = await self._run(qdrant, _FakeLastfm(top_artists=top, similar=similar))
        self.assertEqual(result["ingested"], 1)
        self.assertEqual(result["expanded"], 1)
        row = self.db.query(DiscoveredArtist).filter_by(musicbrainz_id="cand-mbid").first()
        self.assertIsNotNone(row)
        self.assertEqual(row.source, "ingest")
        self.assertEqual(json.loads(row.associated_seed_mbids), ["seed-mbid"])
        # The candidate point actually landed in Qdrant as is_discovered=false —
        # this is the pool centroid search draws from.
        pid = QdrantIntegration.point_id("cand-mbid", "New Candidate")
        self.assertFalse(qdrant._points[pid]["payload"]["is_discovered"])

    async def test_already_owned_related_artist_excluded(self):
        top = [{"name": "Seed Artist", "mbid": "seed-mbid", "playcount": "50"}]
        similar = {"Seed Artist": [{"name": "Owned Artist", "mbid": "owned-mbid", "match": "0.9"}]}
        result = await self._run(
            _FakeQdrant(), _FakeLastfm(top_artists=top, similar=similar),
            lidarr_index=({"owned-mbid": {"foreignArtistId": "owned-mbid"}}, {}))
        self.assertEqual(result["expanded"], 0)
        self.assertEqual(self.db.query(DiscoveredArtist).count(), 0)

    async def test_already_discovered_seed_still_gets_expanded(self):
        # A seed already tracked as is_discovered=true (from a prior run) is
        # still expanded this run if its scan is stale — expansion isn't
        # gated on "is this a brand-new seed."
        pid = QdrantIntegration.point_id("seed-mbid", "Seed Artist")
        qdrant = _FakeQdrant([{"id": pid, "payload": {
            "musicbrainz_id": "seed-mbid", "artist_name": "Seed Artist",
            "is_discovered": True, "last_related_scan_timestamp": 0}}])
        lastfm = _FakeLastfm(
            top_artists=[{"name": "Seed Artist", "mbid": "seed-mbid", "playcount": "50"}],
            similar={"Seed Artist": [{"name": "New Candidate", "mbid": "cand-mbid", "match": "0.9"}]})
        result = await self._run(qdrant, lastfm)
        self.assertEqual(result["ingested"], 0)  # already a seed, no re-embed
        self.assertEqual(result["expanded"], 1)

    async def test_fresh_scan_seed_not_re_expanded(self):
        pid = QdrantIntegration.point_id("seed-mbid", "Seed Artist")
        qdrant = _FakeQdrant([{"id": pid, "payload": {
            "musicbrainz_id": "seed-mbid", "artist_name": "Seed Artist",
            "is_discovered": True, "last_related_scan_timestamp": int(time.time())}}])
        lastfm = _FakeLastfm(
            top_artists=[{"name": "Seed Artist", "mbid": "seed-mbid", "playcount": "50"}],
            similar={"Seed Artist": [{"name": "New Candidate", "mbid": "cand-mbid", "match": "0.9"}]})
        cfg = ArtistDiscoverySettings(suggest_connection_threshold=1, related_artists_refresh_days=30)
        result = await self._run(qdrant, lastfm, cfg=cfg)
        self.assertEqual(result["expanded"], 0)


if __name__ == "__main__":
    unittest.main()
