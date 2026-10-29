"""Ranking helpers: tag fit, obscurity, freshness, blending, buckets, diversity, feedback."""
import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.discovery_v2 import DiscoveryFeedback
from app.schemas.settings import ArtistDiscoverySettings
from app.services import discovery_ranker as rk

CFG = ArtistDiscoverySettings()


class HelperTests(unittest.TestCase):
    def test_tag_fit_ranges(self):
        prof = rk.tag_profile({"A": ["trip hop", "electronic", "downtempo"], "B": ["trip-hop", "ambient"]},
                              {"A": 1.0, "B": 0.5})
        self.assertGreater(rk.tag_fit(["trip hop", "electronic"], prof), rk.tag_fit(["metal", "thrash"], prof))
        self.assertEqual(rk.tag_fit(["metal"], prof), 0.0)
        self.assertIsNone(rk.tag_fit([], prof))
        self.assertIsNone(rk.tag_fit(["x"], {}))

    def test_obscurity(self):
        self.assertGreater(rk.obscurity(1_000, 0), rk.obscurity(900_000, 0))
        self.assertEqual(rk.obscurity(5_000_000, 1_000_000), 0.0)
        self.assertIsNone(rk.obscurity(None, 0))

    def test_freshness(self):
        now = datetime(2026, 10, 29)
        self.assertEqual(rk.freshness("2026-10-29", 120, now), 1.0)
        self.assertAlmostEqual(rk.freshness("2026-07-01", 120, now), 1.0 - 0.5 * 120 / 120, delta=0.05)
        self.assertEqual(rk.freshness("2020-01-01", 120, now), 0.0)
        self.assertEqual(rk.freshness(None, 120, now), 0.0)

    def test_score_is_neutral_without_tag_or_listener_data(self):
        total, parts = rk.score_candidate(1.0, 1, None, None, 0.0, 0.0, CFG)
        self.assertAlmostEqual(total, 1.0)
        self.assertIsNone(parts["tag_fit"]["value"])

    def test_score_rewards_agreement_fit_and_penalises_feedback(self):
        base, _ = rk.score_candidate(1.0, 1, 0.5, 0.5, 0.0, 0.0, CFG)
        better, _ = rk.score_candidate(1.0, 3, 1.0, 0.5, 0.0, 0.0, CFG)
        worse, _ = rk.score_candidate(1.0, 1, 0.5, 0.5, 0.0, -1.0, CFG)
        self.assertGreater(better, base)
        self.assertLess(worse, base)

    def test_buckets(self):
        self.assertEqual(rk.assign_bucket(0.9, CFG), "close")
        self.assertEqual(rk.assign_bucket(0.3, CFG), "branching")
        self.assertEqual(rk.assign_bucket(0.05, CFG), "wildcard")
        self.assertEqual(rk.assign_bucket(None, CFG), "branching")

    def test_diversify_spreads_seeds_and_defers_over_cap(self):
        items = [{"score": 1.0 - i * 0.01, "seeds": ["A"], "tags": ["rock"], "n": i} for i in range(4)]
        items.append({"score": 0.9, "seeds": ["B"], "tags": ["jazz"], "n": 9})
        out = rk.diversify(items, strength=0.8, max_per_seed=2)
        self.assertEqual(out[1]["n"], 9)  # the different seed jumps ahead of A's near-duplicates
        self.assertEqual([i["n"] for i in out].index(3) > 2, True)  # A's third sits behind the cap
        self.assertEqual(len(out), 5)  # deferred, never dropped

    def test_reason_line(self):
        r = rk.build_reason(["Radiohead", "Portishead"], ["trip hop"], "close", ["lastfm", "deezer"],
                            {"title": "Album", "date": "2026-09-01"})
        self.assertIn("2 of your top artists, mostly Radiohead", r)
        self.assertIn("trip hop", r)
        self.assertIn("deezer", r)
        self.assertIn("Album (2026)", r)


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def _fb(self, kind, tags, reason=None, seeds=None):
        import json
        self.db.add(DiscoveryFeedback(artist_key="x", artist_name="X", kind=kind, reason=reason,
                                      tags=json.dumps(tags), seeds=json.dumps(seeds or [])))
        self.db.commit()

    def test_not_for_me_genre_lowers_matching_tags(self):
        self._fb("not_for_me", ["black metal"], reason="genre")
        m = rk.load_feedback(self.db)
        self.assertLess(m.adjust(["black metal"], []), 0)
        self.assertEqual(m.adjust(["jazz"], []), 0)

    def test_more_like_this_boosts_tags_and_seeds(self):
        self._fb("more_like_this", ["shoegaze"], seeds=["Slowdive"])
        m = rk.load_feedback(self.db)
        self.assertGreater(m.adjust(["shoegaze"], ["Slowdive"]), m.adjust(["shoegaze"], []))

    def test_dislike_teaches_less_than_genre(self):
        self._fb("not_for_me", ["pop"], reason="dislike")
        m1 = rk.load_feedback(self.db).adjust(["pop"], [])
        self.db.query(DiscoveryFeedback).delete()
        self._fb("not_for_me", ["pop"], reason="genre")
        m2 = rk.load_feedback(self.db).adjust(["pop"], [])
        self.assertGreater(m1, m2)

    def test_known_is_tracked(self):
        self._fb("known", [])
        self.assertIn("x", rk.load_feedback(self.db).known)


class EvidenceCacheTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_cache_roundtrip_and_ttl(self):
        rk.cache_put(self.db, "k", "K", "lastfm", "info", {"listeners": 5})
        self.assertEqual(rk.cache_get(self.db, "k", "lastfm", "info", 14), {"listeners": 5})
        rk.cache_put(self.db, "k", "K", "lastfm", "info", {"listeners": 9})  # replaces
        self.assertEqual(rk.cache_get(self.db, "k", "lastfm", "info", 14), {"listeners": 9})
        self.assertIsNone(rk.cache_get(self.db, "missing", "lastfm", "info", 14))


if __name__ == "__main__":
    unittest.main()
