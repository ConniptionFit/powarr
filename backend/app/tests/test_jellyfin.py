import asyncio
import unittest
from datetime import datetime
from unittest.mock import patch

from app.integrations import jellyfin as J
from app.models.integration import Integration
from app.models.media import MediaItem
from app.tests.test_lib07_to_11 import _ApiCase


class ParseTests(unittest.TestCase):
    def test_datetime_with_seven_fraction_digits_and_z(self):
        self.assertEqual(J._parse_dt("2024-03-05T10:20:30.1234567Z"),
                         datetime(2024, 3, 5, 10, 20, 30, 123456))

    def test_datetime_with_offset_becomes_naive_utc(self):
        self.assertEqual(J._parse_dt("2024-03-05T12:00:00+02:00"), datetime(2024, 3, 5, 10, 0, 0))

    def test_garbage_is_none(self):
        self.assertIsNone(J._parse_dt("nope"))
        self.assertIsNone(J._parse_dt(None))

    def test_movie(self):
        d = J.parse_item({"Id": "abc", "Type": "Movie", "Name": "Heat", "ProductionYear": 1995,
                          "Path": "/m/Heat.mkv", "MediaSources": [{"Size": 123}],
                          "DateCreated": "2024-01-01T00:00:00.0000000Z"}, "Movies")
        self.assertEqual(d["plex_rating_key"], "jellyfin:abc")
        self.assertEqual((d["media_type"], d["file_size"], d["library_section"]), ("movie", 123, "Movies"))
        self.assertEqual(d["watch_count"], 0)

    def test_episode_and_track_parents(self):
        ep = J.parse_item({"Id": "e", "Type": "Episode", "Name": "Pilot", "SeriesName": "Show"}, "TV")
        self.assertEqual((ep["media_type"], ep["parent_title"]), ("episode", "Show"))
        tr = J.parse_item({"Id": "t", "Type": "Audio", "Name": "Song", "Artists": ["Band"]}, "Music")
        self.assertEqual((tr["media_type"], tr["parent_title"]), ("track", "Band"))

    def test_unknown_type_skipped(self):
        self.assertIsNone(J.parse_item({"Id": "x", "Type": "BoxSet"}, "L"))


class FetchTests(unittest.TestCase):
    def test_watch_state_aggregates_across_users(self):
        jf = J.JellyfinIntegration("http://jf", "key")
        folders = [{"Name": "Movies", "ItemId": "lib1", "CollectionType": "movies"}]
        lib_items = [{"Id": "m1", "Type": "Movie", "Name": "A", "MediaSources": [{"Size": 5}]},
                     {"Id": "m2", "Type": "Movie", "Name": "B", "MediaSources": [{"Size": 6}]}]
        played = {"u1": [{"Id": "m1", "UserData": {"PlayCount": 2, "LastPlayedDate": "2024-01-01T00:00:00Z"}}],
                  "u2": [{"Id": "m1", "UserData": {"PlayCount": 1, "LastPlayedDate": "2024-06-01T00:00:00Z"}}]}

        class Resp:
            def __init__(self, data): self._d = data
            def raise_for_status(self): pass
            def json(self): return self._d

        class Client:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url, headers=None, params=None):
                if url.endswith("/Library/VirtualFolders"): return Resp(folders)
                if url.endswith("/Users"): return Resp([{"Id": "u1"}, {"Id": "u2"}])
                if params.get("UserId"):
                    items = played[params["UserId"]]
                else:
                    items = lib_items
                return Resp({"Items": items, "TotalRecordCount": len(items)})

        with patch.object(J.httpx, "AsyncClient", Client):
            out = asyncio.run(jf.fetch_media_items())
        by = {i["title"]: i for i in out}
        self.assertEqual(by["A"]["watch_count"], 3)
        self.assertEqual(by["A"]["last_watched_at"], datetime(2024, 6, 1))
        self.assertEqual(by["B"]["watch_count"], 0)


class SyncApiTests(_ApiCase):
    def test_sync_requires_enabled(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.v1.integrations import router
        from app.database import get_db
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        db = self.db
        app.dependency_overrides[get_db] = lambda: db
        c = TestClient(app)
        r = c.post("/api/v1/integrations/jellyfin/sync")
        self.assertEqual(r.status_code, 400)

    def test_sync_upserts_items(self):
        from app.services import jellyfin_sync
        from app.services.secret_box import encrypt
        self.db.add(Integration(name="jellyfin", url="http://jf", api_key=encrypt("k"), enabled=True))
        self.db.commit()
        item = J.parse_item({"Id": "z", "Type": "Movie", "Name": "Z", "MediaSources": [{"Size": 9}]}, "Movies")

        async def fake_fetch(self):
            return [item]

        async def noop(*a, **k):
            return {}

        with patch.object(J.JellyfinIntegration, "fetch_media_items", fake_fetch), \
             patch("app.services.arr_link.link_arr_ids", noop):
            res = asyncio.run(jellyfin_sync.run_jellyfin_sync(self.db))
        self.assertEqual(res["synced"], 1)
        self.assertIsNone(res["warning"])
        row = self.db.query(MediaItem).filter_by(plex_rating_key="jellyfin:z").one()
        self.assertEqual(row.file_size, 9)
