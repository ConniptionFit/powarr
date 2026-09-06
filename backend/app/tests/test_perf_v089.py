"""v0.89.0 performance work — the behaviour the optimisations must preserve.

These guard the parts where "make it faster" could quietly change results:
the pooled HTTP client's lifecycle, the additive index migration, the
single-pass library-health aggregation, and the duplicate finder's tie-break.
"""
import asyncio
import unittest

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.database import Base, _INDEXES
from app.integrations.base import close_shared_client, shared_async_client
from app.models.media import MediaItem
from app.services.duplicate_finder import find_duplicate_groups
from app.services.library_health import compute_library_health


def _item(**over):
    base = dict(title="Title", media_type="movie", file_size=0, ignored=False)
    base.update(over)
    return MediaItem(**base)


class SharedAsyncClientTests(unittest.TestCase):
    """The pool is only a win if it is actually reused, and only safe if it is
    never shared across event loops (a client's pool belongs to its loop)."""

    def test_same_loop_reuses_one_client(self):
        async def go():
            a = shared_async_client()
            b = shared_async_client()
            await close_shared_client()
            return a is b

        self.assertTrue(asyncio.run(go()))

    def test_separate_loops_get_separate_clients(self):
        async def go():
            c = shared_async_client()
            # Do not close: leaving it behind is what proves the next loop
            # builds its own rather than inheriting this dead one.
            return c

        c1 = asyncio.run(go())
        c2 = asyncio.run(go())
        self.assertIsNot(c1, c2)

    def test_closed_client_is_replaced(self):
        async def go():
            a = shared_async_client()
            await a.aclose()
            b = shared_async_client()
            # Read the state before tearing down — close_shared_client() would
            # close `b` too and make the assertion meaningless.
            result = (a is b, b.is_closed)
            await close_shared_client()
            return result

        same, closed = asyncio.run(go())
        self.assertFalse(same)
        self.assertFalse(closed)

    def test_close_without_client_is_noop(self):
        asyncio.run(close_shared_client())  # must not raise


class IndexMigrationTests(unittest.TestCase):
    """Indexes are created additively at startup and must be idempotent —
    _migrate() runs on every boot."""

    def test_indexes_created_and_idempotent(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)

        import app.database as database
        original = database.engine
        database.engine = engine
        try:
            database._ensure_indexes()
            first = {ix["name"] for ix in inspect(engine).get_indexes("media_items")}
            database._ensure_indexes()  # second boot
            second = {ix["name"] for ix in inspect(engine).get_indexes("media_items")}
        finally:
            database.engine = original

        for table, specs in _INDEXES.items():
            current = {ix["name"] for ix in inspect(engine).get_indexes(table)}
            for name, _cols in specs:
                self.assertIn(name, current, f"{name} on {table} was not created")
        self.assertEqual(first, second, "re-running the migration changed the schema")


class RedundantIdIndexDropTests(unittest.TestCase):
    """PERF-01 — the one destructive migration in database.py. It must remove
    SQLAlchemy's duplicate primary-key indexes and nothing else."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def _names(self, table="media_items"):
        return {ix["name"] for ix in inspect(self.engine).get_indexes(table)}

    def _run(self):
        import app.database as database
        original = database.engine
        database.engine = self.engine
        try:
            database._drop_redundant_id_indexes()
        finally:
            database.engine = original

    def test_drops_duplicate_pk_index_and_is_idempotent(self):
        with self.engine.connect() as c:
            c.execute(text("CREATE INDEX ix_media_items_id ON media_items (id)"))
            c.commit()
        self.assertIn("ix_media_items_id", self._names())
        self._run()
        self.assertNotIn("ix_media_items_id", self._names())
        self._run()  # second boot must not raise
        self.assertNotIn("ix_media_items_id", self._names())

    def test_leaves_unrelated_indexes_alone(self):
        """A differently-named index on id, a unique one, and a multi-column
        index all fail at least one of the safety conditions."""
        with self.engine.connect() as c:
            c.execute(text("CREATE INDEX my_own_id_idx ON media_items (id)"))
            c.execute(text("CREATE UNIQUE INDEX ix_media_items_id_u ON media_items (id)"))
            c.execute(text("CREATE INDEX ix_media_items_id_multi ON media_items (id, title)"))
            c.commit()
        before = self._names()
        self._run()
        self.assertEqual(before, self._names())

    def test_perf_indexes_survive(self):
        """The v0.89.0 indexes must not be collateral damage."""
        import app.database as database
        original = database.engine
        database.engine = self.engine
        try:
            database._ensure_indexes()
        finally:
            database.engine = original
        self._run()
        for name, _cols in _INDEXES["media_items"]:
            self.assertIn(name, self._names(), f"{name} was dropped")


class LibraryHealthEquivalenceTests(unittest.TestCase):
    """The single-pass rollup has to agree with counting things the long way."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    def test_rollup_matches_independent_counts(self):
        from datetime import datetime
        self.db.add_all([
            _item(plex_rating_key="m1", media_type="movie", file_size=10, radarr_id=1),
            _item(plex_rating_key="m2", media_type="movie", file_size=20),
            _item(plex_rating_key="e1", media_type="episode", file_size=5, sonarr_id=7),
            _item(plex_rating_key="t1", media_type="track", file_size=1, lidarr_id=3),
            _item(plex_rating_key="p1", media_type="movie", file_size=99, protected=True),
            _item(plex_rating_key="w1", media_type="movie", file_size=99, watch_protected=True),
            _item(plex_rating_key="i1", media_type="movie", file_size=99, ignored=True),
            _item(plex_rating_key="d1", media_type="movie", file_size=99,
                  pending_delete_at=datetime(2026, 1, 1)),
        ])
        self.db.commit()
        h = compute_library_health(self.db)

        active = self.db.query(MediaItem).filter(MediaItem.pending_delete_at.is_(None))
        self.assertEqual(sum(t["count"] for t in h["by_type"]), active.count())
        self.assertEqual(h["protections"]["seerr_requested"],
                         active.filter(MediaItem.protected.is_(True)).count())
        self.assertEqual(h["protections"]["recently_watched"],
                         active.filter(MediaItem.watch_protected.is_(True)).count())
        self.assertEqual(h["ignored_items"],
                         active.filter(MediaItem.ignored.is_(True)).count())
        self.assertEqual(h["pending_soft_deletes"],
                         self.db.query(MediaItem)
                         .filter(MediaItem.pending_delete_at.isnot(None)).count())
        movie = next(c for c in h["arr_link_coverage"] if c["media_type"] == "movie")
        self.assertEqual(movie["linked"], 1)

    def test_arr_link_coverage_order_is_stable(self):
        """Order comes from the declared mapping, not from GROUP BY output —
        this list is rendered as-is."""
        self.db.add_all([
            _item(plex_rating_key="t1", media_type="track", lidarr_id=1),
            _item(plex_rating_key="e1", media_type="episode", sonarr_id=1),
            _item(plex_rating_key="m1", media_type="movie", radarr_id=1),
        ])
        self.db.commit()
        order = [c["media_type"] for c in compute_library_health(self.db)["arr_link_coverage"]]
        self.assertEqual(order, ["movie", "episode", "track"])


class DuplicateTieBreakTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    def test_all_zero_size_group_picks_lowest_id_deterministically(self):
        """Container types (show/artist) carry file_size 0 on every member, so
        the sort is all ties and row order from the database decides nothing.
        Without an explicit tie-break suggested_keep_id could differ between
        two identical requests."""
        self.db.add_all([
            _item(plex_rating_key="s1", title="Crashing", media_type="show", file_size=0),
            _item(plex_rating_key="s2", title="Crashing", media_type="show", file_size=0),
        ])
        self.db.commit()
        ids = sorted(i.id for i in self.db.query(MediaItem).all())
        for _ in range(3):
            groups = find_duplicate_groups(self.db)
            self.assertEqual(len(groups), 1)
            self.assertFalse(groups[0]["has_size_signal"])
            self.assertEqual(groups[0]["suggested_keep_id"], ids[0])

    def test_largest_file_still_wins_over_id(self):
        self.db.add_all([
            _item(plex_rating_key="a", title="Dune", year=2021, file_size=10),
            _item(plex_rating_key="b", title="Dune", year=2021, file_size=999),
        ])
        self.db.commit()
        big = self.db.query(MediaItem).filter(MediaItem.file_size == 999).one()
        groups = find_duplicate_groups(self.db)
        self.assertEqual(groups[0]["suggested_keep_id"], big.id)


if __name__ == "__main__":
    unittest.main()
