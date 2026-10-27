"""Direct discovery lane: scoring, presets, caps, dry run, decision log, status."""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.artist_discovery import ArtistDiscoveryDecision, ArtistDiscoveryRun, DiscoveredArtist
from app.schemas.settings import ArtistDiscoverySettings
from app.services import discovery_engine as de

_ENR = {"image_url": None, "bio": None, "genres": [], "years_active": None}


class _Lastfm:
    def __init__(self, top, similar):
        self._top, self._similar = top, similar

    async def get_top_artists(self, period="overall", limit=200):
        return self._top

    async def get_similar_artists(self, artist, mbid=None, limit=15):
        return self._similar.get(artist, [])

    async def get_top_tags(self, artist, mbid=None):
        return []


class _Lidarr:
    async def lookup_artist(self, term):
        return [{"artistName": term, "foreignArtistId": f"mbid-{term}"}]


TOP = [{"name": "Seed A", "playcount": "100"}, {"name": "Seed B", "playcount": "50"},
       {"name": "Seed C", "playcount": "1"}]
SIMILAR = {
    "Seed A": [{"name": "Strong", "match": "0.95", "mbid": "m-strong"},
               {"name": "Weak", "match": "0.2"},
               {"name": "Owned", "match": "0.9"},
               {"name": "Mid", "match": "0.5", "mbid": "m-mid"}],
    "Seed B": [{"name": "Strong", "match": "0.9", "mbid": "m-strong"},
               {"name": "Seed A", "match": "0.99"}],
}


class DirectLaneTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    async def _run(self, cfg, dry_run=False, lastfm=None, lidarr=True, add_ok=True, run=None):
        added = []

        async def fake_add(db, cid):
            row = db.query(DiscoveredArtist).filter_by(id=cid).first()
            if not add_ok:
                return {"ok": False, "message": "Lidarr add failed: boom"}
            row.status = "accepted"
            added.append(row.artist_name)
            return {"ok": True}

        with patch("app.services.artist_discovery._lastfm_client", return_value=lastfm or _Lastfm(TOP, SIMILAR)), \
             patch("app.services.artist_discovery._lidarr_client", return_value=_Lidarr() if lidarr else None), \
             patch("app.services.artist_discovery._lidarr_artist_index",
                   return_value=({}, {"owned": {}})), \
             patch("app.services.artist_discovery._plex_artist_names", return_value=set()), \
             patch("app.services.artist_discovery._enrich_candidate", return_value=_ENR), \
             patch("app.services.artist_discovery.add_to_lidarr", side_effect=fake_add):
            res = await de.run_direct_discovery(self.db, cfg, dry_run=dry_run, run=run)
        res["_added_calls"] = added
        return res

    def _cfg(self, **kw):
        base = dict(enabled=True, aggressiveness="custom", min_seed_plays=3, suggest_score=0.5,
                    auto_add_score=1.2, auto_add_min_connections=2, max_adds_per_run=3,
                    max_adds_per_day=5, max_queue_per_run=10, min_match=0.4, filter_youtube_channels=False)
        base.update(kw)
        return ArtistDiscoverySettings(**base)

    async def test_scoring_ownership_and_min_plays(self):
        res = await self._run(self._cfg())
        self.assertTrue(res["ok"])
        self.assertEqual(res["seeds"], 2)  # Seed C has 1 play, below min_seed_plays
        names = {d["artist_name"] for d in res["decisions"]}
        self.assertIn("Strong", names)
        self.assertNotIn("Seed A", names)  # a seed is never its own suggestion
        self.assertNotIn("Owned", names)   # in Lidarr
        self.assertNotIn("Weak", names)    # under min_match
        strong = next(d for d in res["decisions"] if d["artist_name"] == "Strong")
        self.assertEqual(strong["connections"], 2)
        self.assertEqual(strong["action"], "queued")  # auto-add is off
        self.assertIn("auto-add is off", strong["reason"])

    async def test_auto_add_respects_threshold_and_run_cap(self):
        cfg = self._cfg(auto_add_enabled=True, auto_add_score=0.4, auto_add_min_connections=1, max_adds_per_run=1)
        res = await self._run(cfg)
        self.assertEqual(res["added"], 1)
        self.assertEqual(res["_added_calls"], ["Strong"])
        mid = next(d for d in res["decisions"] if d["artist_name"] == "Mid")
        self.assertEqual(mid["action"], "queued")
        self.assertIn("add cap", mid["reason"])

    async def test_min_connections_blocks_single_seed_auto_add(self):
        cfg = self._cfg(auto_add_enabled=True, auto_add_score=0.1, auto_add_min_connections=2)
        res = await self._run(cfg)
        self.assertEqual(res["_added_calls"], ["Strong"])  # Mid has one seed only
        mid = next(d for d in res["decisions"] if d["artist_name"] == "Mid")
        self.assertEqual(mid["action"], "queued")
        self.assertIn("auto-add needs 2", mid["reason"])

    async def test_daily_cap_counts_previous_adds(self):
        for i in range(2):
            self.db.add(ArtistDiscoveryDecision(artist_name=f"x{i}", action="added"))
        self.db.commit()
        cfg = self._cfg(auto_add_enabled=True, auto_add_score=0.4, auto_add_min_connections=1, max_adds_per_day=2)
        res = await self._run(cfg)
        self.assertEqual(res["added"], 0)
        strong = next(d for d in res["decisions"] if d["artist_name"] == "Strong")
        self.assertIn("daily add cap", strong["reason"])

    async def test_dry_run_writes_nothing(self):
        cfg = self._cfg(auto_add_enabled=True, auto_add_score=0.4, auto_add_min_connections=1)
        res = await self._run(cfg, dry_run=True)
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["_added_calls"], [])
        self.assertEqual(self.db.query(DiscoveredArtist).count(), 0)
        self.assertEqual(self.db.query(ArtistDiscoveryDecision).count(), 0)
        self.assertIn("would_add", {d["action"] for d in res["decisions"]})

    async def test_real_run_logs_decisions_and_dedupes_rejected(self):
        self.db.add(DiscoveredArtist(artist_name="Mid", status="rejected", source="graph"))
        self.db.commit()
        run = ArtistDiscoveryRun(run_type="direct")
        self.db.add(run)
        self.db.flush()
        res = await self._run(self._cfg(), run=run)
        rows = {r.artist_name: r for r in self.db.query(ArtistDiscoveryDecision).all()}
        self.assertEqual(rows["Strong"].action, "queued")
        self.assertEqual(rows["Strong"].run_id, run.id)
        self.assertEqual(rows["Mid"].action, "skipped")
        self.assertIn("rejected", rows["Mid"].reason)
        cand = self.db.query(DiscoveredArtist).filter_by(artist_name="Strong").one()
        self.assertEqual(cand.source, "direct")
        self.assertEqual(sorted(__import__("json").loads(cand.seed_artist_names)), ["Seed A", "Seed B"])
        # second run: Strong is already queued, nothing new
        res2 = await self._run(self._cfg())
        self.assertEqual(res2["queued"], 0)

    async def test_failed_lidarr_add_is_reported(self):
        cfg = self._cfg(auto_add_enabled=True, auto_add_score=0.4, auto_add_min_connections=1)
        res = await self._run(cfg, add_ok=False, run=None)
        self.assertGreaterEqual(res["failed"], 1)
        failed = next(d for d in res["decisions"] if d["action"] == "failed")
        self.assertIn("boom", failed["reason"])
        self.assertEqual(self.db.query(DiscoveredArtist).filter_by(artist_name=failed["artist_name"]).one().status, "pending")

    async def test_blockers_when_lastfm_missing_or_disabled(self):
        with patch("app.services.artist_discovery._lastfm_client", return_value=None), \
             patch("app.services.artist_discovery._lidarr_client", return_value=None):
            res = await de.run_direct_discovery(self.db, self._cfg())
        self.assertFalse(res["ok"])
        self.assertIn("Last.fm", res["message"])
        res = await de.run_direct_discovery(self.db, ArtistDiscoverySettings(enabled=False))
        self.assertFalse(res["ok"])

    async def test_nothing_clears_the_bar_says_so(self):
        res = await self._run(self._cfg(suggest_score=50.0))
        self.assertTrue(res["ok"])
        self.assertIn("Nothing cleared the bar", res["message"])

    async def test_no_seeds_message(self):
        res = await self._run(self._cfg(min_seed_plays=1000))
        self.assertIn("No seeds", res["message"])

    async def test_require_mbid_resolves_through_lidarr(self):
        res = await self._run(self._cfg(require_musicbrainz_id=True))
        self.assertIn("Strong", {d["artist_name"] for d in res["decisions"]})


class PresetTests(unittest.TestCase):
    def test_presets_override_knobs_but_custom_does_not(self):
        cfg = ArtistDiscoverySettings(aggressiveness="aggressive", max_adds_per_run=1)
        self.assertEqual(de.effective_settings(cfg).max_adds_per_run, de.PRESETS["aggressive"]["max_adds_per_run"])
        cfg = ArtistDiscoverySettings(aggressiveness="custom", max_adds_per_run=1)
        self.assertEqual(de.effective_settings(cfg).max_adds_per_run, 1)

    def test_presets_get_progressively_looser(self):
        c, b, a = (de.PRESETS[n] for n in ("conservative", "balanced", "aggressive"))
        self.assertGreater(c["suggest_score"], b["suggest_score"])
        self.assertGreater(b["suggest_score"], a["suggest_score"])
        self.assertLess(c["max_adds_per_run"], a["max_adds_per_run"])

    def test_payload_lists_all_choices(self):
        self.assertEqual([p["name"] for p in de.presets_payload()],
                         ["off", "conservative", "balanced", "aggressive", "custom"])

    def test_auto_add_never_enabled_by_preset(self):
        self.assertFalse(de.effective_settings(ArtistDiscoverySettings(aggressiveness="aggressive")).auto_add_enabled)


class StatusAndMigrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    async def test_status_flags_fresh_install(self):
        st = await de.get_status(self.db)
        by = {c["key"]: c for c in st["checks"]}
        self.assertEqual(st["overall"], "error")
        self.assertEqual(by["enabled"]["status"], "error")
        self.assertEqual(by["lastfm"]["status"], "error")
        self.assertEqual(by["auto_add"]["status"], "warn")

    def test_legacy_auto_add_threshold_turns_new_switch_on(self):
        import json
        from app.models.app_setting import AppSetting
        from app.services.artist_discovery import load_settings
        self.db.add(AppSetting(key="artist_discovery", value=json.dumps(
            {"enabled": True, "suggest_connection_threshold": 3, "auto_add_connection_threshold": 5,
             "embeddings_enabled": True})))
        self.db.commit()
        self.assertTrue(load_settings(self.db).auto_add_enabled)

    def test_prune_decisions(self):
        from datetime import datetime, timedelta
        self.db.add(ArtistDiscoveryDecision(artist_name="old", action="skipped",
                                            created_at=datetime.utcnow() - timedelta(days=200)))
        self.db.add(ArtistDiscoveryDecision(artist_name="new", action="skipped"))
        self.db.commit()
        self.assertEqual(de.prune_decisions(self.db), 1)
        self.assertEqual(self.db.query(ArtistDiscoveryDecision).count(), 1)


class FullCycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_cycle_runs_direct_lane_without_qdrant(self):
        from app.services import artist_discovery as ad
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        ad.save_settings(db, ArtistDiscoverySettings(enabled=True, filter_youtube_channels=False))
        with patch("app.services.artist_discovery._qdrant", return_value=None), \
             patch("app.services.artist_discovery._lastfm_client", return_value=_Lastfm(TOP, SIMILAR)), \
             patch("app.services.artist_discovery._lidarr_client", return_value=None), \
             patch("app.services.artist_discovery._lidarr_artist_index", return_value=None), \
             patch("app.services.artist_discovery._plex_artist_names", return_value=set()), \
             patch("app.services.artist_discovery._enrich_candidate", return_value=_ENR):
            res = await ad.run_full_discovery_cycle(db, trigger="schedule")
        self.assertTrue(res["ok"], res["message"])
        run = db.query(ArtistDiscoveryRun).one()
        self.assertEqual(run.trigger, "schedule")
        self.assertGreaterEqual(run.candidates_found, 1)
        self.assertIn("Qdrant not configured", run.message)
        self.assertGreaterEqual(db.query(ArtistDiscoveryDecision).filter_by(run_id=run.id).count(), 1)
        db.close()

    async def test_disabled_module_says_why(self):
        from app.services import artist_discovery as ad
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        res = await ad.run_full_discovery_cycle(db)
        self.assertFalse(res["ok"])
        self.assertIn("switched off", res["message"])
        db.close()


class CentroidLaneDepthTests(unittest.IsolatedAsyncioTestCase):
    async def test_known_hits_do_not_use_up_the_cap(self):
        from app.services import artist_discovery as ad
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        hits = []
        for i in range(8):
            name = f"Known{i}"
            db.add(DiscoveredArtist(artist_name=name, status="rejected", source="centroid"))
            hits.append({"score": 0.9, "payload": {"artist_name": name, "musicbrainz_id": ""}})
        hits.append({"score": 0.8, "payload": {"artist_name": "Fresh", "musicbrainz_id": ""}})
        db.commit()

        class Q:
            async def search(self, *a, **k):
                self.limit = k["limit"]
                return hits

        q = Q()
        cfg = ArtistDiscoverySettings(max_candidates_per_run=2, filter_youtube_channels=False)
        with patch("app.services.artist_discovery._enrich_candidate", return_value=_ENR):
            out = await ad._run_centroid_lane(db, q, [0.1], cfg, "centroid", set())
        self.assertGreater(q.limit, 2)
        self.assertEqual(out["created"], 1)
        db.close()
