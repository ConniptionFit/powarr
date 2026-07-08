"""Regression tests for SCAL-01: Plex sync must not pull an entire library in one
response, nor issue a SELECT per item.

- PlexIntegration._fetch_all walks the section in container pages and stops at
  totalSize (or an empty page).
- plex_sync.upsert_media_items loads existing rows once and upserts by rating key:
  new items insert, repeated keys update in place (no duplicate rows).
"""
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.integrations.plex import PLEX_PAGE_SIZE, PlexIntegration
from app.models.media import MediaItem
from app.schemas.settings import ScoringWeights
from app.services.plex_sync import upsert_media_items


class _Resp:
    def __init__(self, metadata, total):
        self._d = {"MediaContainer": {"Metadata": metadata, "totalSize": total}}

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


class _PagedClient:
    """Serves a fixed item list in container pages, like Plex's /all endpoint."""

    def __init__(self, n):
        self.all = [{"ratingKey": str(i)} for i in range(n)]
        self.calls = 0

    async def get(self, url, headers=None, params=None):
        self.calls += 1
        start = int(params["X-Plex-Container-Start"])
        size = int(params["X-Plex-Container-Size"])
        return _Resp(self.all[start:start + size], len(self.all))


class FetchAllPaginationTests(unittest.IsolatedAsyncioTestCase):
    def _plex(self):
        return PlexIntegration("http://plex:32400", "token")

    async def test_walks_all_pages(self):
        client = _PagedClient(2 * PLEX_PAGE_SIZE + 200)
        items = await self._plex()._fetch_all(client, {}, "1")
        self.assertEqual(len(items), 2 * PLEX_PAGE_SIZE + 200)
        self.assertEqual(client.calls, 3)  # full, full, partial — then stop

    async def test_exact_multiple_stops_without_extra_call(self):
        client = _PagedClient(2 * PLEX_PAGE_SIZE)
        items = await self._plex()._fetch_all(client, {}, "1")
        self.assertEqual(len(items), 2 * PLEX_PAGE_SIZE)
        self.assertEqual(client.calls, 2)  # totalSize reached, no empty trailing page

    async def test_empty_section(self):
        client = _PagedClient(0)
        items = await self._plex()._fetch_all(client, {}, "1")
        self.assertEqual(items, [])
        self.assertEqual(client.calls, 1)


def _item(key, **over):
    base = dict(plex_rating_key=key, title=f"Title {key}", year=2020, media_type="movie",
                library_section="Movies", parent_title=None, file_path=f"/m/{key}.mkv",
                file_size=1024 ** 3, added_at=None, release_date=None,
                watch_count=0, last_watched_at=None)
    base.update(over)
    return base


class UpsertMediaItemsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.weights = ScoringWeights()

    def tearDown(self):
        self.db.close()

    def test_inserts_new_items_with_scores(self):
        n = upsert_media_items(self.db, [_item("1"), _item("2")], self.weights)
        self.db.commit()
        self.assertEqual(n, 2)
        self.assertEqual(self.db.query(MediaItem).count(), 2)
        for m in self.db.query(MediaItem).all():
            self.assertGreater(m.score, 0)  # never-watched → nonzero deletion score

    def test_repeated_key_updates_in_place_no_duplicate(self):
        upsert_media_items(self.db, [_item("1", file_size=1024 ** 3)], self.weights)
        self.db.commit()
        # Same rating key, larger file → update the row, don't insert a second.
        upsert_media_items(self.db, [_item("1", file_size=8 * 1024 ** 3), _item("2")], self.weights)
        self.db.commit()
        self.assertEqual(self.db.query(MediaItem).count(), 2)
        row = self.db.query(MediaItem).filter_by(plex_rating_key="1").one()
        self.assertEqual(row.file_size, 8 * 1024 ** 3)

    def test_duplicate_key_within_one_payload_is_not_double_inserted(self):
        n = upsert_media_items(self.db, [_item("1"), _item("1", title="Dup")], self.weights)
        self.db.commit()
        self.assertEqual(n, 2)  # both processed
        self.assertEqual(self.db.query(MediaItem).count(), 1)  # one row
        self.assertEqual(self.db.query(MediaItem).one().title, "Dup")

    def test_progress_callback_reports_final_count(self):
        seen = []
        upsert_media_items(self.db, [_item(str(i)) for i in range(3)], self.weights,
                           progress=seen.append)
        self.assertEqual(seen[-1], 3)


if __name__ == "__main__":
    unittest.main()
