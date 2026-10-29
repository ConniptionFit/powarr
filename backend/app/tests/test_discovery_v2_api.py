"""Discovery v2 HTTP surface (shelves, taste, listening, scorecard, feedback) and the
Plex/Tautulli collector selection."""
import unittest
from datetime import datetime
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1 import artist_discovery as api
from app.database import Base, get_db
from app.models.artist_discovery import DiscoveredArtist
from app.schemas.settings import ArtistDiscoverySettings
from app.services import listening as L


class ApiTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        app = FastAPI()
        app.include_router(api.router, prefix="/api/v1")

        def _db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = _db
        self.c = TestClient(app)

    def test_empty_state_endpoints_work(self):
        self.assertEqual(self.c.get("/api/v1/artist-discovery/shelves").json()["shelves"], [])
        self.assertEqual(self.c.get("/api/v1/artist-discovery/taste").json()["log_events"], 0)
        st = self.c.get("/api/v1/artist-discovery/listening/status").json()
        self.assertEqual({s["source"] for s in st["sources"]}, {"lastfm", "plex", "listenbrainz"})
        self.assertEqual(self.c.get("/api/v1/artist-discovery/listening/preview?days=7").json()["raw_reports"], 0)
        self.assertEqual(self.c.get("/api/v1/artist-discovery/scorecard").json()["totals"]["suggested"], 0)

    def test_settings_roundtrip_includes_v2_fields(self):
        body = ArtistDiscoverySettings(listening_users=["John"], seed_exclude_tags=["kids"],
                                       auto_add_buckets=["close"], lidarr_tag="mine").model_dump()
        r = self.c.put("/api/v1/artist-discovery/settings", json=body)
        self.assertEqual(r.status_code, 200)
        got = self.c.get("/api/v1/artist-discovery/settings").json()
        self.assertEqual(got["listening_users"], ["John"])
        self.assertEqual(got["auto_add_buckets"], ["close"])
        self.assertEqual(got["lidarr_tag"], "mine")

    def test_feedback_endpoint(self):
        db = self.Session()
        row = DiscoveredArtist(artist_name="X", status="pending", source="direct", rank_score=1.0, bucket="close")
        db.add(row)
        db.commit()
        cid = row.id
        db.close()
        ok = self.c.post(f"/api/v1/artist-discovery/candidates/{cid}/feedback", json={"kind": "more_like_this"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.c.post(f"/api/v1/artist-discovery/candidates/{cid}/feedback", json={"kind": "no"}).status_code, 400)
        self.assertEqual(self.c.post("/api/v1/artist-discovery/candidates/999/feedback", json={"kind": "known"}).status_code, 404)
        shelves = self.c.get("/api/v1/artist-discovery/shelves?adventure=0.5").json()["shelves"]
        self.assertEqual(shelves[0]["items"][0]["artist_name"], "X")


class CollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_plex_direct_is_default_and_user_filter_applies(self):
        class FakePlex:
            async def get_track_history(self, since_ts):
                return [{"id": "1", "user": "John", "artist": "A", "track": "T", "album": "", "ts": 1_760_000_000},
                        {"id": "2", "user": "Guest", "artist": "B", "track": "U", "album": "", "ts": 1_760_000_100}]

        cfg = ArtistDiscoverySettings(listening_users=["john"])
        with patch("app.services.listening._integration", side_effect=lambda db, n: FakePlex() if n == "plex" else None):
            got = await L._collect_plex(None, cfg, datetime(2025, 1, 1))
        self.assertEqual([p.artist for p in got], ["A"])
        self.assertEqual(got[0].source, "plex")

    async def test_tautulli_used_when_selected_or_plex_missing(self):
        class FakeTaut:
            async def get_music_history(self, days):
                return [{"id": "9", "user": "John", "artist": "A", "track": "T", "album": "", "ts": 1_760_000_000,
                         "played_s": 200}]

        for via in ("tautulli", "plex"):
            cfg = ArtistDiscoverySettings(plex_history_via=via)
            with patch("app.services.listening._integration", side_effect=lambda db, n: FakeTaut() if n == "tautulli" else None):
                got = await L._collect_plex(None, cfg, datetime(2025, 1, 1))
            self.assertEqual([p.source for p in got], ["tautulli"])


if __name__ == "__main__":
    unittest.main()
