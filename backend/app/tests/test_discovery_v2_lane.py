"""Direct lane v2: listen-log seeds, seed hygiene, evidence ranking, auto-add scope,
shelves, feedback and the scorecard."""
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.artist_discovery import ArtistDiscoveryDecision, DiscoveredArtist
from app.models.discovery_v2 import ListenEvent
from app.schemas.settings import ArtistDiscoverySettings
from app.services import discovery_engine as de
from app.services import discovery_views as dv
from app.services import listening as L

_ENR = {"image_url": None, "bio": None, "genres": [], "years_active": None}

TAGS = {"Seed A": ["trip hop", "electronic"], "Seed B": ["trip hop", "downtempo"], "Kids": ["childrens"],
        "Close One": ["trip hop", "electronic"], "Far One": ["polka"], "Mid One": ["electronic", "jazz"]}
INFO = {"Close One": {"listeners": 20_000, "tags": TAGS["Close One"]},
        "Far One": {"listeners": 900_000, "tags": TAGS["Far One"]},
        "Mid One": {"listeners": 40_000, "tags": TAGS["Mid One"]}}


class _Lastfm:
    def __init__(self, top):
        self._top = top

    async def get_top_artists(self, period="overall", limit=200):
        return self._top

    async def get_similar_artists(self, artist, mbid=None, limit=15):
        if artist in ("Seed A", "Seed B"):
            return [{"name": "Close One", "match": "0.9", "mbid": "m-close"},
                    {"name": "Far One", "match": "0.8", "mbid": "m-far"},
                    {"name": "Mid One", "match": "0.7", "mbid": "m-mid"}]
        return []

    async def get_top_tags(self, artist, mbid=None):
        return TAGS.get(artist, [])

    async def get_artist_info(self, artist, mbid=None):
        return dict(INFO.get(artist, {}))


class _Lidarr:
    async def lookup_artist(self, term):
        return [{"artistName": term, "foreignArtistId": f"mbid-{term}"}]


TOP = [{"name": "Seed A", "playcount": "100"}, {"name": "Seed B", "playcount": "80"},
       {"name": "Kids", "playcount": "500"}]


class LaneV2Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    def _cfg(self, **kw):
        base = dict(enabled=True, aggressiveness="custom", min_seed_plays=3, suggest_score=0.3,
                    auto_add_score=0.5, auto_add_min_connections=1, max_adds_per_run=5, max_adds_per_day=0,
                    max_queue_per_run=10, min_match=0.4, filter_youtube_channels=False,
                    use_deezer_related=False, use_musicbrainz_releases=False, listening_enabled=False)
        base.update(kw)
        return ArtistDiscoverySettings(**base)

    async def _run(self, cfg, dry_run=False, top=TOP, deezer=None):
        added = []

        async def fake_add(db, cid, auto=False):
            row = db.query(DiscoveredArtist).filter_by(id=cid).first()
            row.status = "accepted"
            added.append((row.artist_name, auto))
            return {"ok": True}

        async def fake_deezer(db, key, name, ttl, sem):
            return (deezer or {}).get(name, [])

        with patch("app.services.artist_discovery._lastfm_client", return_value=_Lastfm(top)), \
             patch("app.services.artist_discovery._lidarr_client", return_value=_Lidarr()), \
             patch("app.services.artist_discovery._lidarr_artist_index", return_value=({}, {})), \
             patch("app.services.artist_discovery._plex_artist_names", return_value=set()), \
             patch("app.services.artist_discovery._enrich_candidate", return_value=_ENR), \
             patch("app.services.discovery_ranker.deezer_related", side_effect=fake_deezer), \
             patch("app.services.artist_discovery.add_to_lidarr", side_effect=fake_add):
            res = await de.run_direct_discovery(self.db, cfg, dry_run=dry_run)
        res["_added"] = added
        return res

    def _dec(self, res, name):
        return next(d for d in res["decisions"] if d["artist_name"] == name)

    async def test_ranking_output_buckets_tags_and_reason_are_stored(self):
        res = await self._run(self._cfg(excluded_genres=[], seed_exclude_artists=["Kids"]))
        row = self.db.query(DiscoveredArtist).filter_by(artist_name="Close One").one()
        self.assertEqual(row.bucket, "close")
        self.assertEqual(row.listeners, 20_000)
        self.assertIn("trip hop", json.loads(row.tags))
        self.assertIn("Seed A", row.reason)
        self.assertIn("_sources", json.loads(row.breakdown))
        far = self.db.query(DiscoveredArtist).filter_by(artist_name="Far One").one()
        self.assertEqual(far.bucket, "wildcard")
        self.assertEqual(res["seed_source"], "Last.fm")

    async def test_seed_hygiene_by_artist_and_tag(self):
        res = await self._run(self._cfg(seed_exclude_artists=["kids"]))
        self.assertEqual(res["seeds"], 2)
        res = await self._run(self._cfg(seed_exclude_tags=["Childrens"]), dry_run=True)
        self.assertEqual(res["seeds"], 2)
        self.assertEqual(res["skip_counts"].get("seed excluded by tag"), 1)

    async def test_auto_add_limited_to_allowed_buckets(self):
        cfg = self._cfg(auto_add_enabled=True, seed_exclude_artists=["Kids"], auto_add_buckets=["close"])
        res = await self._run(cfg)
        self.assertEqual([n for n, _a in res["_added"]], ["Close One"])
        self.assertTrue(all(auto for _n, auto in res["_added"]))  # the Lidarr tag applies to auto-adds
        far = self._dec(res, "Far One")
        self.assertEqual(far["action"], "queued")
        self.assertIn("'wildcard' pick", far["reason"])

    async def test_auto_add_genre_lists_and_weekly_cap(self):
        cfg = self._cfg(auto_add_enabled=True, seed_exclude_artists=["Kids"], auto_add_buckets=[],
                        auto_add_genres=["jazz"])
        res = await self._run(cfg)
        self.assertEqual([n for n, _ in res["_added"]], ["Mid One"])

    async def test_weekly_cap_counts_earlier_adds(self):
        for i in range(2):
            self.db.add(ArtistDiscoveryDecision(artist_name=f"x{i}", action="added"))
        self.db.commit()
        res = await self._run(self._cfg(auto_add_enabled=True, seed_exclude_artists=["Kids"], auto_add_buckets=[],
                                        max_adds_per_week=2), dry_run=True)
        self.assertEqual(res["added"], 0)
        self.assertIn("weekly add cap", self._dec(res, "Close One")["reason"])

    async def test_popularity_ceiling_filters_big_artists(self):
        res = await self._run(self._cfg(seed_exclude_artists=["Kids"], max_listeners=100_000), dry_run=True)
        names = {d["artist_name"] for d in res["decisions"]}
        self.assertNotIn("Far One", names)
        self.assertEqual(res["skip_counts"].get("above the maximum listeners"), 1)

    async def test_min_sources_blocks_single_source_auto_add(self):
        cfg = self._cfg(auto_add_enabled=True, seed_exclude_artists=["Kids"], auto_add_buckets=[],
                        auto_add_min_sources=2, use_deezer_related=True)
        res = await self._run(cfg)
        self.assertEqual(res["added"], 0)
        self.assertIn("auto-add needs 2", self._dec(res, "Close One")["reason"])
        res = await self._run(cfg, deezer={"Seed A": [{"name": "Close One"}], "Seed B": [{"name": "Close One"}]})
        self.assertIn("Close One", [n for n, _ in res["_added"]])  # a second source agrees

    async def test_external_source_can_introduce_a_candidate(self):
        res = await self._run(self._cfg(seed_exclude_artists=["Kids"], use_deezer_related=True), dry_run=True,
                              deezer={"Seed A": [{"name": "Deezer Only"}]})
        d = self._dec(res, "Deezer Only")
        self.assertEqual(d["sources"], ["deezer"])

    async def test_more_like_this_feedback_changes_scores_and_not_for_me_rejects(self):
        cfg = self._cfg(seed_exclude_artists=["Kids"])
        await self._run(cfg)
        close = self.db.query(DiscoveredArtist).filter_by(artist_name="Close One").one()
        before = close.rank_score
        self.assertTrue(dv.record_feedback(self.db, close.id, "more_like_this")["ok"])
        await self._run(cfg)
        self.db.refresh(close)
        self.assertGreater(close.rank_score, before)
        far = self.db.query(DiscoveredArtist).filter_by(artist_name="Far One").one()
        dv.record_feedback(self.db, far.id, "not_for_me", "genre")
        self.db.refresh(far)
        self.assertEqual(far.status, "rejected")
        self.assertFalse(dv.record_feedback(self.db, far.id, "bogus")["ok"])

    async def test_known_feedback_excludes_artist_from_future_runs(self):
        cfg = self._cfg(seed_exclude_artists=["Kids"])
        await self._run(cfg)
        mid = self.db.query(DiscoveredArtist).filter_by(artist_name="Mid One").one()
        dv.record_feedback(self.db, mid.id, "known")
        res = await self._run(cfg, dry_run=True)
        self.assertEqual(res["skip_counts"].get("marked as already known"), 1)

    async def test_seeds_come_from_the_listen_log_when_it_has_enough_plays(self):
        now = datetime.utcnow()
        for i in range(30):
            self.db.add(ListenEvent(source="plex", artist="Seed A", artist_key="seed a", track=f"t{i}",
                                    track_key=f"t{i}", played_at=now - timedelta(hours=i), is_primary=1))
        self.db.commit()
        res = await self._run(self._cfg(listening_enabled=True, listening_sources=[]), dry_run=True)
        self.assertEqual(res["seed_source"], "listening log")
        self.assertEqual(res["seeds"], 1)

    async def test_shelves_scorecard_and_sampler_shapes(self):
        cfg = self._cfg(seed_exclude_artists=["Kids"])
        await self._run(cfg)
        out = dv.build_shelves(self.db, cfg, adventure=0.5)
        ids = {s["id"] for s in out["shelves"]}
        self.assertIn("mixed", ids)
        self.assertIn("close", ids)
        self.assertTrue(all(i["match"] <= 100 for s in out["shelves"] for i in s["items"]))
        cheap = dv.build_shelves(self.db, cfg, adventure=0.0)
        wild = next((s for s in cheap["shelves"] if s["id"] == "mixed"), None)
        self.assertFalse(any(i["bucket"] == "wildcard" for i in wild["items"]))  # adventure 0 = no stretches
        # scorecard: a play after the suggestion counts as a hit, from any source
        row = self.db.query(DiscoveredArtist).filter_by(artist_name="Close One").one()
        row.created_at = datetime.utcnow() - timedelta(days=40)
        self.db.add(ListenEvent(source="lastfm", artist="Close One", artist_key="close one", track="t",
                                track_key="t", played_at=datetime.utcnow() - timedelta(days=35), is_primary=1))
        self.db.commit()
        card = dv.scorecard(self.db, cfg)
        self.assertGreaterEqual(card["totals"]["hits"], 1)
        self.assertTrue(card["by_bucket"])


if __name__ == "__main__":
    unittest.main()
