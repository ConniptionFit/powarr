"""LIB-07..LIB-11 — the follow-ups from the v0.89 code review.

LIB-07  server-side library search + a sort_by whitelist
LIB-08  deterministic per-factor score breakdown
LIB-09  dry-run a scoring-weight change
LIB-11  reclaimed-space trend
"""
import json
import unittest
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.media import SORTABLE_FIELDS, router as media_router
from app.api.v1.settings import router as settings_router
from app.database import Base, get_db
from app.models.app_setting import AppSetting
from app.models.deletion_log import DeletionLog
from app.models.media import MediaItem
from app.schemas.settings import ScoringWeights
from app.services.scorer import preview_weight_change, score_contributions


def _item(**over):
    base = dict(title="Title", media_type="movie", file_size=1024,
                ignored=False, score=50.0)
    base.update(over)
    return MediaItem(**base)


class _ApiCase(unittest.TestCase):
    def setUp(self):
        # StaticPool + check_same_thread=False: an in-memory SQLite database
        # lives inside its connection, and TestClient runs the endpoint on a
        # different thread — without one shared connection the tables get
        # created in one database and queried in another ("no such table").
        self.engine = create_engine("sqlite:///:memory:",
                                    connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

        app = FastAPI()
        app.include_router(media_router, prefix="/api/v1")
        app.include_router(settings_router, prefix="/api/v1")
        db = self.db
        app.dependency_overrides[get_db] = lambda: db
        self.client = TestClient(app)

    def tearDown(self):
        self.db.close()


class LibrarySearchTests(_ApiCase):
    """LIB-07 — the box used to filter the fetched page client-side, so on a
    large library it searched a few hundred of tens of thousands of rows and
    'no results' was indistinguishable from 'not in your library'."""

    def setUp(self):
        super().setUp()
        self.db.add_all([
            _item(plex_rating_key="a", title="Dune", year=2021, score=90),
            _item(plex_rating_key="b", title="Dune: Part Two", year=2024, score=20),
            _item(plex_rating_key="c", title="Blade Runner 2049", year=2017, score=10),
            _item(plex_rating_key="d", title="Arrival", year=2016, score=5,
                  library_section="Movies"),
            _item(plex_rating_key="e", title="Some Episode", media_type="episode",
                  parent_title="Dune Prophecy", score=1),
        ])
        self.db.commit()

    def _titles(self, **params):
        r = self.client.get("/api/v1/media", params=params)
        self.assertEqual(r.status_code, 200, r.text)
        return sorted(i["title"] for i in r.json())

    def test_finds_low_scoring_item_outside_the_top_of_the_list(self):
        """The whole point: a low-scoring match is still found."""
        self.assertEqual(self._titles(search="Blade"), ["Blade Runner 2049"])

    def test_search_is_case_insensitive(self):
        self.assertEqual(self._titles(search="dune"), self._titles(search="DUNE"))

    def test_matches_parent_title_and_library_and_year(self):
        self.assertIn("Some Episode", self._titles(search="Prophecy"))
        self.assertIn("Arrival", self._titles(search="Movies"))
        self.assertEqual(self._titles(search="2021"), ["Dune"])

    def test_blank_search_is_ignored(self):
        self.assertEqual(len(self._titles(search="   ")), len(self._titles()))

    def test_no_match_returns_empty_not_everything(self):
        self.assertEqual(self._titles(search="zzzz-nothing"), [])


class SortByWhitelistTests(_ApiCase):
    """LIB-07 — `getattr(MediaItem, sort_by, default)` only fell back for names
    that don't exist, so any other class attribute resolved and blew up in
    order_by. Reachable via a stale localStorage value, not just a crafted URL."""

    def setUp(self):
        super().setUp()
        self.db.add(_item(plex_rating_key="a", title="Dune"))
        self.db.commit()

    def test_non_column_attributes_do_not_500(self):
        for bad in ["metadata", "risky_delete", "registry", "__class__", "nonexistent"]:
            r = self.client.get("/api/v1/media", params={"sort_by": bad})
            self.assertEqual(r.status_code, 200, f"sort_by={bad}: {r.text[:200]}")

    def test_whitelisted_fields_all_work(self):
        for field in sorted(SORTABLE_FIELDS):
            r = self.client.get("/api/v1/media", params={"sort_by": field})
            self.assertEqual(r.status_code, 200, f"sort_by={field}: {r.text[:200]}")

    def test_invalid_sort_falls_back_to_score_order(self):
        self.db.add_all([_item(plex_rating_key="b", title="Low", score=1.0),
                         _item(plex_rating_key="c", title="High", score=99.0)])
        self.db.commit()
        rows = self.client.get("/api/v1/media", params={"sort_by": "metadata"}).json()
        self.assertEqual(rows[0]["title"], "High")


class ScoreBreakdownTests(_ApiCase):
    """LIB-08 — the deterministic 'why this score?', which unlike the LLM
    Explain cannot be unavailable."""

    def test_contributions_sum_to_the_score(self):
        w = ScoringWeights()
        payload = {"watch_count": 0, "last_watched_at": None, "file_size": 5 * 1024 ** 3,
                   "added_at": datetime.utcnow() - timedelta(days=400),
                   "release_date": datetime.utcnow() - timedelta(days=3000)}
        result = score_contributions(payload, w)
        summed = sum(f["contribution"] for f in result["factors"])
        self.assertAlmostEqual(summed, result["score"], places=1)

    def test_zero_weight_factors_are_omitted_not_shown_as_zero(self):
        w = ScoringWeights(file_size_weight=0)
        result = score_contributions({"watch_count": 1, "file_size": 10}, w)
        self.assertNotIn("size", [f["key"] for f in result["factors"]])

    def test_endpoint_returns_factors_and_404s_for_missing(self):
        self.db.add(_item(plex_rating_key="a", title="Dune"))
        self.db.commit()
        item_id = self.db.query(MediaItem).one().id
        r = self.client.get(f"/api/v1/media/{item_id}/score-breakdown")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["title"], "Dune")
        self.assertTrue(body["factors"])
        for f in body["factors"]:
            self.assertLessEqual(f["contribution"], f["max_contribution"] + 1e-6)
        self.assertEqual(
            self.client.get("/api/v1/media/999999/score-breakdown").status_code, 404)


class ScoringPreviewTests(_ApiCase):
    """LIB-09 — dry-run a weight change. Must not write anything."""

    def setUp(self):
        super().setUp()
        self.db.add_all([
            _item(plex_rating_key="a", title="Big", file_size=50 * 1024 ** 3, score=70.0),
            _item(plex_rating_key="b", title="Small", file_size=1024, score=70.0),
            _item(plex_rating_key="c", title="Protected", file_size=50 * 1024 ** 3,
                  score=99.0, protected=True),
        ])
        self.db.commit()

    def test_preview_writes_nothing(self):
        before = {i.id: i.score for i in self.db.query(MediaItem).all()}
        preview_weight_change(self.db, ScoringWeights(file_size_weight=99.0))
        self.db.expire_all()
        after = {i.id: i.score for i in self.db.query(MediaItem).all()}
        self.assertEqual(before, after, "preview must not persist any score")

    def test_protected_items_are_excluded(self):
        """The count has to match the list the user is about to see, which
        never includes protected rows."""
        out = preview_weight_change(self.db, ScoringWeights())
        self.assertLessEqual(out["current"]["above_threshold"], 2)

    def test_reports_movers_in_both_directions(self):
        # Stored scores (10) sit below the default threshold, so dropping the
        # threshold to 0 pulls them across — the "newly suggested" direction.
        self.db.query(MediaItem).update({MediaItem.score: 10.0})
        self.db.commit()
        out = preview_weight_change(self.db, ScoringWeights(min_score_threshold=0.0))
        self.assertGreaterEqual(out["newly_above_count"], 1)
        self.assertEqual(out["newly_below_count"], 0)

        # And the opposite direction: an unreachable threshold drops everything.
        self.db.query(MediaItem).update({MediaItem.score: 99.0})
        self.db.commit()
        out = preview_weight_change(self.db, ScoringWeights(min_score_threshold=100.0))
        self.assertGreaterEqual(out["newly_below_count"], 1)
        self.assertEqual(out["newly_above_count"], 0)

    def test_endpoint_round_trip(self):
        r = self.client.post("/api/v1/settings/scoring/preview",
                             json=ScoringWeights().model_dump())
        self.assertEqual(r.status_code, 200, r.text)
        for key in ("evaluated", "current", "proposed", "newly_above", "newly_below"):
            self.assertIn(key, r.json())

    def test_current_side_uses_the_stored_score(self):
        """The 'current' column must be the number already on screen, not a
        re-derivation that might round differently."""
        self.db.add(AppSetting(key="scoring_weights",
                               value=json.dumps(ScoringWeights().model_dump())))
        self.db.commit()
        out = preview_weight_change(self.db, ScoringWeights())
        eligible = [i for i in self.db.query(MediaItem).all() if not i.protected]
        expected = sum(1 for i in eligible if i.score >= ScoringWeights().min_score_threshold)
        self.assertEqual(out["current"]["above_threshold"], expected)


class ReclaimTrendTests(_ApiCase):
    """LIB-11 — reclaimed space per day, from the log Powarr already keeps."""

    def test_empty_log_still_returns_a_full_window(self):
        r = self.client.get("/api/v1/media/reclaim-trend", params={"days": 30})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(len(body["points"]), 30)
        self.assertEqual(body["total_bytes"], 0)

    def test_buckets_by_day_and_keeps_zero_days(self):
        now = datetime.utcnow()
        self.db.add_all([
            DeletionLog(title="A", media_type="movie", file_size=100, deleted_at=now),
            DeletionLog(title="B", media_type="movie", file_size=200, deleted_at=now),
            DeletionLog(title="C", media_type="movie", file_size=50,
                        deleted_at=now - timedelta(days=2)),
        ])
        self.db.commit()
        body = self.client.get("/api/v1/media/reclaim-trend", params={"days": 7}).json()
        self.assertEqual(body["total_bytes"], 350)
        self.assertEqual(body["total_deleted"], 3)
        self.assertEqual(len(body["points"]), 7, "zero days must not be collapsed")
        self.assertEqual(body["points"][-1]["bytes"], 300)

    def test_excludes_deletions_older_than_the_window(self):
        self.db.add(DeletionLog(title="Ancient", media_type="movie", file_size=999,
                                deleted_at=datetime.utcnow() - timedelta(days=200)))
        self.db.commit()
        body = self.client.get("/api/v1/media/reclaim-trend", params={"days": 30}).json()
        self.assertEqual(body["total_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
