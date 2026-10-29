"""Unified listening log: the same play seen by two sources counts once."""
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.discovery_v2 import ListenEvent
from app.schemas.settings import ArtistDiscoverySettings
from app.services import listening as L

T0 = datetime(2026, 10, 1, 12, 0, 0)


def play(source, secs, artist="Portishead", track="Roads", **kw):
    return L.RawPlay(source=source, artist=artist, track=track, ts=T0 + timedelta(seconds=secs),
                     event_id=f"{source}:{secs}:{artist}:{track}", **kw)


class MergeTests(unittest.TestCase):
    def test_plex_and_lastfm_within_tolerance_merge(self):
        groups = L.merge_plays([play("plex", 0), play("lastfm", 45)])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].sources, {"plex", "lastfm"})
        self.assertEqual(groups[0].primary.source, "plex")  # media server wins by default priority

    def test_outside_tolerance_stays_separate(self):
        self.assertEqual(len(L.merge_plays([play("plex", 0), play("lastfm", 900)])), 2)

    def test_half_track_length_widens_tolerance(self):
        long_track = dict(duration_s=900)  # half = 450 s
        groups = L.merge_plays([play("plex", 0, **long_track), play("lastfm", 400, **long_track)])
        self.assertEqual(len(groups), 1)

    def test_same_source_twice_is_a_replay(self):
        self.assertEqual(len(L.merge_plays([play("plex", 0), play("plex", 60)])), 2)

    def test_replay_does_not_collapse_three_plays_into_one(self):
        plays = [play("plex", 0), play("lastfm", 5), play("plex", 300), play("lastfm", 305)]
        self.assertEqual(len(L.merge_plays(plays, base_tolerance_s=60)), 2)

    def test_different_tracks_never_merge(self):
        self.assertEqual(len(L.merge_plays([play("plex", 0), play("lastfm", 5, track="Glory Box")])), 2)

    def test_remaster_suffix_and_feat_are_ignored_in_matching(self):
        self.assertEqual(L.norm_track("Roads - 2016 Remastered"), L.norm_track("Roads"))
        self.assertEqual(L.norm_track("Roads (feat. Someone)"), L.norm_track("Roads"))

    def test_priority_setting_picks_the_primary(self):
        groups = L.merge_plays([play("plex", 0), play("lastfm", 30)], priority=["lastfm", "plex"])
        self.assertEqual(groups[0].primary.source, "lastfm")


class IngestTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.cfg = ArtistDiscoverySettings(listening_sources=["lastfm", "plex"])

    def tearDown(self):
        self.db.close()

    def test_ingest_merges_and_counts_one_primary(self):
        r = L.ingest(self.db, [play("plex", 0), play("lastfm", 40)], self.cfg)
        self.assertEqual(r, {"new": 1, "merged": 1})
        rows = self.db.query(ListenEvent).all()
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(r.is_primary for r in rows), 1)
        self.assertEqual(L.log_size(self.db), 1)

    def test_late_report_from_second_source_merges_into_stored_play(self):
        L.ingest(self.db, [play("plex", 0)], self.cfg)
        r = L.ingest(self.db, [play("lastfm", 30)], self.cfg)  # arrives in a later sync
        self.assertEqual(r, {"new": 0, "merged": 1})
        self.assertEqual(L.log_size(self.db), 1)

    def test_ingest_is_idempotent(self):
        batch = [play("plex", 0), play("lastfm", 40)]
        L.ingest(self.db, batch, self.cfg)
        again = L.ingest(self.db, [play("plex", 0), play("lastfm", 40)], self.cfg)
        self.assertEqual(again, {"new": 0, "merged": 0})
        self.assertEqual(self.db.query(ListenEvent).count(), 2)

    def test_higher_priority_late_arrival_takes_over_as_primary(self):
        L.ingest(self.db, [play("lastfm", 20)], self.cfg)
        L.ingest(self.db, [play("plex", 0)], self.cfg)
        prim = self.db.query(ListenEvent).filter_by(is_primary=1).all()
        self.assertEqual([p.source for p in prim], ["plex"])
        self.assertEqual(L.log_size(self.db), 1)

    def test_short_plays_ignored(self):
        r = L.ingest(self.db, [play("plex", 0, played_s=10)], self.cfg)
        self.assertEqual(r, {"new": 0, "merged": 0})

    def test_disabled_source_ignored(self):
        cfg = ArtistDiscoverySettings(listening_sources=["lastfm"])
        self.assertEqual(L.ingest(self.db, [play("plex", 0)], cfg), {"new": 0, "merged": 0})

    def test_seed_plays_and_preview(self):
        L.ingest(self.db, [play("plex", 0), play("lastfm", 40), play("plex", 1000, artist="Massive Attack", track="Angel")],
                 self.cfg)
        seeds = L.seed_plays(self.db, "overall")
        self.assertEqual({s["name"]: s["plays"] for s in seeds}, {"Portishead": 1, "Massive Attack": 1})
        prev = L.dedupe_preview(self.db, days=3650)
        self.assertEqual((prev["raw_reports"], prev["real_plays"], prev["seen_by_multiple_sources"]), (3, 2, 1))

    def test_seed_half_life_discounts_old_plays(self):
        old = L.RawPlay(source="plex", artist="Old", track="x", ts=datetime.utcnow() - timedelta(days=60), event_id="o")
        new = L.RawPlay(source="plex", artist="New", track="y", ts=datetime.utcnow() - timedelta(days=1), event_id="n")
        L.ingest(self.db, [old, new], self.cfg)
        w = {s["name"]: s["weighted"] for s in L.seed_plays(self.db, "overall", half_life_days=30)}
        self.assertLess(w["Old"], w["New"])

    def test_user_filter(self):
        cfg = ArtistDiscoverySettings(listening_users=["John"])
        self.assertTrue(L._user_ok(cfg, "john"))
        self.assertFalse(L._user_ok(cfg, "Guest"))
        self.assertTrue(L._user_ok(ArtistDiscoverySettings(), "anyone"))


if __name__ == "__main__":
    unittest.main()
