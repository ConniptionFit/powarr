import unittest
from datetime import datetime, timedelta

from app.models.media import MediaItem
from app.services import rules as R
from app.tests.test_lib07_to_11 import _ApiCase

GB = 1024 ** 3
NOW = datetime.utcnow()


def _m(db, key, **kw):
    base = dict(plex_rating_key=key, title=key, media_type="movie", file_size=GB,
                added_at=NOW - timedelta(days=800), watch_count=0)
    base.update(kw)
    db.add(MediaItem(**base))


def _rule(*conds, match="all"):
    return {"name": "t", "match": match,
            "conditions": [{"field": f, "op": o, "value": v} for f, o, v in conds]}


class ValidationTests(unittest.TestCase):
    def test_rejects_unknown_field_and_bad_op(self):
        with self.assertRaises(R.RuleError):
            R.clean_rule(_rule(("password", "eq", "x")))
        with self.assertRaises(R.RuleError):
            R.clean_rule(_rule(("size_gb", "contains", 1)))
        with self.assertRaises(R.RuleError):
            R.clean_rule(_rule(("size_gb", "gt", "abc")))
        with self.assertRaises(R.RuleError):
            R.clean_rule({"name": "", "conditions": [{"field": "year", "op": "gt", "value": 1}]})
        with self.assertRaises(R.RuleError):
            R.clean_rule({"name": "x", "conditions": []})

    def test_assigns_id(self):
        r = R.clean_rule(_rule(("year", "lt", 2000)))
        self.assertTrue(r["id"])


class RuleApiTests(_ApiCase):
    def _seed(self):
        _m(self.db, "old_big", file_size=30 * GB, last_watched_at=NOW - timedelta(days=700))
        _m(self.db, "recent_big", file_size=30 * GB, last_watched_at=NOW - timedelta(days=10))
        _m(self.db, "old_small", file_size=1 * GB, last_watched_at=NOW - timedelta(days=900))
        _m(self.db, "never", file_size=20 * GB)                      # falls back to added_at
        _m(self.db, "gone", file_size=50 * GB, pending_delete_at=NOW)  # soft-deleted
        _m(self.db, "nodates", file_size=5 * GB, added_at=None)
        self.db.commit()

    def _count(self, rule):
        return self.client.post("/api/v1/media/rules/preview", json=rule).json()

    def test_and_rule(self):
        self._seed()
        out = self._count(_rule(("days_unwatched", "gt", 540), ("size_gb", "gt", 10)))
        self.assertEqual(out["count"], 2)  # old_big + never
        self.assertEqual(out["total_bytes"], 50 * GB)
        self.assertEqual(out["sample"][0]["title"], "old_big")

    def test_or_rule_and_soft_deleted_excluded(self):
        self._seed()
        out = self._count(_rule(("size_gb", "gt", 40), ("title", "contains", "small"), match="any"))
        self.assertEqual(out["count"], 1)  # 'gone' excluded

    def test_undated_items_never_match_age_rules(self):
        self._seed()
        out = self._count(_rule(("days_in_library", "gt", 1)))
        titles = {s["title"] for s in out["sample"]}
        self.assertNotIn("nodates", titles)

    def test_bad_rule_is_422(self):
        r = self.client.post("/api/v1/media/rules/preview", json=_rule(("x", "eq", 1)))
        self.assertEqual(r.status_code, 422)

    def test_save_load_export(self):
        self._seed()
        rule = _rule(("size_gb", "gte", 20))
        rule["id"] = "big"
        saved = self.client.put("/api/v1/media/rules", json=[rule]).json()["rules"]
        self.assertEqual(saved[0]["id"], "big")
        got = self.client.get("/api/v1/media/rules").json()
        self.assertEqual(len(got["rules"]), 1)
        self.assertIn("size_gb", got["fields"])
        csv_text = self.client.get("/api/v1/media/rules/big/export.csv").text
        self.assertIn("old_big", csv_text)
        self.assertNotIn("gone", csv_text)
        self.assertEqual(self.client.get("/api/v1/media/rules/nope/export.csv").status_code, 404)

    def test_put_invalid_is_422(self):
        r = self.client.put("/api/v1/media/rules", json=[{"name": "x", "conditions": []}])
        self.assertEqual(r.status_code, 422)


class CountEndpointTests(_ApiCase):
    def test_count_matches_list_filters(self):
        for i in range(5):
            _m(self.db, f"c{i}", score=50.0 + i)
        _m(self.db, "prot", protected=True, score=99.0)
        _m(self.db, "pend", pending_delete_at=NOW, score=99.0)
        self.db.commit()
        total = self.client.get("/api/v1/media/count").json()["total"]
        listed = self.client.get("/api/v1/media", params={"limit": 100}).json()
        self.assertEqual(total, len(listed))
        self.assertEqual(total, 5)  # protected and pending excluded
        self.assertEqual(self.client.get("/api/v1/media/count", params={"min_score": 53}).json()["total"], 2)
        self.assertEqual(self.client.get("/api/v1/media/count", params={"include_protected": True}).json()["total"], 6)
