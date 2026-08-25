"""Unit tests for AD-24 — embeddings are optional.

The connection/graph lane is pure Qdrant payload bookkeeping and must keep
working with no Ollama configured or reachable: related-artist points get
written vector-less rather than dropped, so they still accumulate seed
connections and qualify for promotion. Only the taste-centroid lane, which is
cosine search by definition, stands down. `backfill_missing_vectors()` fills
those points in once embeddings come back.

The `"vector": {}` mechanism is Qdrant's own representation of "point with no
vector" (an empty named-vector map) — verified against live Qdrant: the field
cannot simply be omitted (the API rejects a point with a missing `vector`), and
such points scroll, retrieve, and accept set_payload normally while being
excluded from similarity search.
"""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.integrations.qdrant import QdrantIntegration
from app.schemas.settings import ArtistDiscoverySettings
from app.services.artist_discovery import (
    _upsert_related_point, _vector_field, backfill_missing_vectors,
    compute_taste_centroid, embeddings_available,
)


class _FakeQdrant:
    """Mirrors test_ad_ingest_expansion's stand-in, plus update_vectors and a
    vector-aware scroll so the backfill path can be exercised."""
    point_id = staticmethod(QdrantIntegration.point_id)

    def __init__(self, points=None):
        self._points = {p["id"]: p for p in (points or [])}
        self.vector_updates = []

    async def retrieve_points(self, ids, with_vector=False):
        return [self._points[i] for i in ids if i in self._points]

    async def upsert_points(self, points):
        for p in points:
            self._points[p["id"]] = {
                "id": p["id"], "payload": p["payload"], "vector": p.get("vector")}

    async def set_payload(self, ids, updates):
        for i in ids:
            if i in self._points:
                self._points[i].setdefault("payload", {}).update(updates)

    async def update_vectors(self, points):
        for p in points:
            self.vector_updates.append(p["id"])
            if p["id"] in self._points:
                self._points[p["id"]]["vector"] = p["vector"]

    async def scroll(self, *, filter=None, limit=256, offset=None, with_vector=False):
        return list(self._points.values()), None


class _FakeLastfm:
    async def get_top_tags(self, artist, mbid=None):
        return ["metalcore"]


class VectorFieldTests(unittest.TestCase):
    def test_real_vector_passes_through(self):
        self.assertEqual(_vector_field([0.1, 0.2]), [0.1, 0.2])

    def test_missing_vector_becomes_empty_map_not_omitted(self):
        # {} — not None, not a zero-filled vector. A zero vector would be a real
        # point in cosine space and would surface as a bogus nearest neighbour.
        self.assertEqual(_vector_field(None), {})
        self.assertEqual(_vector_field([]), {})


class EmbeddingsAvailableTests(unittest.TestCase):
    def test_requires_both_toggle_and_host(self):
        self.assertFalse(embeddings_available(ArtistDiscoverySettings()))
        self.assertFalse(embeddings_available(ArtistDiscoverySettings(
            embeddings_enabled=True, ollama_host="")))
        self.assertFalse(embeddings_available(ArtistDiscoverySettings(
            embeddings_enabled=False, ollama_host="http://h:11434")))
        self.assertTrue(embeddings_available(ArtistDiscoverySettings(
            embeddings_enabled=True, ollama_host="http://h:11434")))

    def test_whitespace_only_host_is_not_configured(self):
        self.assertFalse(embeddings_available(ArtistDiscoverySettings(
            embeddings_enabled=True, ollama_host="   ")))


class RelatedPointWithoutEmbeddingsTests(unittest.IsolatedAsyncioTestCase):
    """The regression this whole item exists to prevent: before AD-24 a missing
    vector returned None here, silently discarding the related artist entirely —
    no point, no connection, no candidate."""

    async def test_point_is_created_vectorless_not_dropped(self):
        qdrant = _FakeQdrant()
        cfg = ArtistDiscoverySettings()  # embeddings off
        point = await _upsert_related_point(
            qdrant, _FakeLastfm(), cfg, "seed-mbid", "New Artist", "new-mbid")
        self.assertIsNotNone(point, "related artist must survive with no embeddings")
        self.assertEqual(point["payload"]["associated_seed_mbids"], ["seed-mbid"])
        pid = QdrantIntegration.point_id("new-mbid", "New Artist")
        self.assertEqual(qdrant._points[pid]["vector"], {})

    async def test_vectorless_point_still_accumulates_connections(self):
        qdrant = _FakeQdrant()
        cfg = ArtistDiscoverySettings()
        await _upsert_related_point(qdrant, _FakeLastfm(), cfg, "seed-a", "New Artist", "new-mbid")
        point = await _upsert_related_point(
            qdrant, _FakeLastfm(), cfg, "seed-b", "New Artist", "new-mbid")
        # Connection counting is the graph lane's entire signal — it must keep
        # working on a point that has no vector.
        self.assertEqual(set(point["payload"]["associated_seed_mbids"]), {"seed-a", "seed-b"})

    async def test_embedding_used_when_available(self):
        qdrant = _FakeQdrant()
        cfg = ArtistDiscoverySettings(embeddings_enabled=True, ollama_host="http://h:11434")
        with patch("app.services.artist_discovery._embed_artist", return_value=[0.1, 0.2]):
            await _upsert_related_point(qdrant, _FakeLastfm(), cfg, "seed", "A", "m")
        pid = QdrantIntegration.point_id("m", "A")
        self.assertEqual(qdrant._points[pid]["vector"], [0.1, 0.2])


class CentroidWithVectorlessPointsTests(unittest.IsolatedAsyncioTestCase):
    """Vector-less points legitimately live in the pool now, so centroid
    computation must skip them rather than average nothing."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    async def test_top_played_vectorless_points_do_not_starve_the_centroid(self):
        # The two most-played artists have no vectors; a healthy one sits below
        # them. Pre-AD-24 the [:15] slice was taken before filtering, so a
        # window full of vector-less points averaged to None.
        points = [
            {"id": "a", "payload": {"total_plays_global": 500, "is_discovered": True}, "vector": None},
            {"id": "b", "payload": {"total_plays_global": 400, "is_discovered": True}, "vector": {}},
            {"id": "c", "payload": {"total_plays_global": 10, "is_discovered": True}, "vector": [1.0, 3.0]},
        ]
        qdrant = _FakeQdrant(points)
        with patch("app.services.artist_discovery._qdrant", return_value=qdrant):
            centroid = await compute_taste_centroid(self.db)
        self.assertEqual(centroid, [1.0, 3.0])

    async def test_no_vectors_at_all_yields_no_centroid(self):
        qdrant = _FakeQdrant([
            {"id": "a", "payload": {"total_plays_global": 5, "is_discovered": True}, "vector": {}}])
        with patch("app.services.artist_discovery._qdrant", return_value=qdrant):
            centroid = await compute_taste_centroid(self.db)
        self.assertIsNone(centroid)


class BackfillMissingVectorsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    async def test_noop_when_embeddings_disabled(self):
        qdrant = _FakeQdrant([{"id": "a", "payload": {"artist_name": "A"}, "vector": {}}])
        with patch("app.services.artist_discovery._qdrant", return_value=qdrant):
            result = await backfill_missing_vectors(self.db, ArtistDiscoverySettings())
        self.assertEqual(result["filled"], 0)
        self.assertEqual(qdrant.vector_updates, [])

    async def test_fills_only_vectorless_points(self):
        qdrant = _FakeQdrant([
            {"id": "a", "payload": {"artist_name": "A", "genres": ["x"]}, "vector": {}},
            {"id": "b", "payload": {"artist_name": "B", "genres": ["y"]}, "vector": [0.5, 0.5]},
        ])
        cfg = ArtistDiscoverySettings(embeddings_enabled=True, ollama_host="http://h:11434")
        with patch("app.services.artist_discovery._qdrant", return_value=qdrant), \
             patch("app.services.artist_discovery._lastfm_client", return_value=None), \
             patch("app.services.artist_discovery._embed_artist", return_value=[0.1, 0.2]):
            result = await backfill_missing_vectors(self.db, cfg)
        self.assertEqual(result["filled"], 1)
        self.assertEqual(qdrant.vector_updates, ["a"])
        # The already-vectored point is left completely alone.
        self.assertEqual(qdrant._points["b"]["vector"], [0.5, 0.5])

    async def test_backfill_preserves_accumulated_connections(self):
        # update_vectors (not a bare upsert) is what protects the payload — a
        # vector-only upsert would wipe associated_seed_mbids, which is the only
        # thing of value on a vector-less point.
        qdrant = _FakeQdrant([{"id": "a", "vector": {}, "payload": {
            "artist_name": "A", "genres": ["x"], "associated_seed_mbids": ["s1", "s2"]}}])
        cfg = ArtistDiscoverySettings(embeddings_enabled=True, ollama_host="http://h:11434")
        with patch("app.services.artist_discovery._qdrant", return_value=qdrant), \
             patch("app.services.artist_discovery._lastfm_client", return_value=None), \
             patch("app.services.artist_discovery._embed_artist", return_value=[0.1, 0.2]):
            await backfill_missing_vectors(self.db, cfg)
        self.assertEqual(qdrant._points["a"]["payload"]["associated_seed_mbids"], ["s1", "s2"])

    async def test_stops_early_when_host_configured_but_unreachable(self):
        qdrant = _FakeQdrant([
            {"id": "a", "payload": {"artist_name": "A", "genres": ["x"]}, "vector": {}},
            {"id": "b", "payload": {"artist_name": "B", "genres": ["y"]}, "vector": {}},
        ])
        cfg = ArtistDiscoverySettings(embeddings_enabled=True, ollama_host="http://down:11434")
        with patch("app.services.artist_discovery._qdrant", return_value=qdrant), \
             patch("app.services.artist_discovery._lastfm_client", return_value=None), \
             patch("app.services.artist_discovery._embed_artist", return_value=None):
            result = await backfill_missing_vectors(self.db, cfg)
        self.assertEqual(result["filled"], 0)
        self.assertEqual(qdrant.vector_updates, [])


class SettingsMigrationTests(unittest.TestCase):
    def test_existing_install_with_host_infers_embeddings_on(self):
        import json as _json

        from app.models.app_setting import AppSetting
        from app.services.artist_discovery import load_settings

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        try:
            db.add(AppSetting(key="artist_discovery", value=_json.dumps({
                "enabled": True, "ollama_host": "http://10.1.1.4:11434",
                "suggest_connection_threshold": 3})))
            db.commit()
            cfg = load_settings(db)
            # Upgrading must not silently disable a centroid lane that worked.
            self.assertTrue(cfg.embeddings_enabled)
        finally:
            db.close()

    def test_existing_install_without_host_stays_off(self):
        import json as _json

        from app.models.app_setting import AppSetting
        from app.services.artist_discovery import load_settings

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        try:
            db.add(AppSetting(key="artist_discovery", value=_json.dumps({
                "enabled": True, "ollama_host": "", "suggest_connection_threshold": 3})))
            db.commit()
            self.assertFalse(load_settings(db).embeddings_enabled)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
