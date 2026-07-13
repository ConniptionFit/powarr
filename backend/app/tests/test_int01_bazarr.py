"""Unit tests for INT-01: Bazarr subtitle orphan awareness."""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.integrations.bazarr import BazarrIntegration
from app.models.integration import Integration
from app.models.media import MediaItem


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, payload, status_code=200, raise_exc=None):
        self._payload = payload
        self._status_code = status_code
        self._raise_exc = raise_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kwargs):
        if self._raise_exc:
            raise self._raise_exc
        return _FakeResponse(self._payload, self._status_code)


class BazarrClientTests(unittest.IsolatedAsyncioTestCase):
    def _client(self):
        return BazarrIntegration("http://bazarr.local:6767", "apikey123")

    async def test_movie_subtitle_count_sums_existing_and_missing(self):
        payload = {"data": [{"subtitles": [{"code": "en"}], "missing_subtitles": [{"code": "fr"}, {"code": "de"}]}]}
        with patch("app.integrations.bazarr.httpx.AsyncClient", lambda **kw: _FakeAsyncClient(payload)):
            count = await self._client().movie_subtitle_count(42)
        self.assertEqual(count, 3)

    async def test_movie_subtitle_count_none_when_bazarr_has_no_record(self):
        with patch("app.integrations.bazarr.httpx.AsyncClient", lambda **kw: _FakeAsyncClient({"data": []})):
            count = await self._client().movie_subtitle_count(42)
        self.assertIsNone(count)

    async def test_movie_subtitle_count_fails_soft_on_error(self):
        with patch("app.integrations.bazarr.httpx.AsyncClient",
                   lambda **kw: _FakeAsyncClient({}, raise_exc=RuntimeError("boom"))):
            count = await self._client().movie_subtitle_count(42)
        self.assertIsNone(count)

    async def test_series_subtitle_count_sums_across_episodes(self):
        payload = {"data": [
            {"subtitles": [{"code": "en"}], "missing_subtitles": []},
            {"subtitles": [], "missing_subtitles": [{"code": "fr"}]},
        ]}
        with patch("app.integrations.bazarr.httpx.AsyncClient", lambda **kw: _FakeAsyncClient(payload)):
            count = await self._client().series_subtitle_count(7)
        self.assertEqual(count, 2)

    async def test_connection_parses_version(self):
        payload = {"data": {"bazarr_version": "1.4.2"}}
        with patch("app.integrations.bazarr.httpx.AsyncClient", lambda **kw: _FakeAsyncClient(payload)):
            result = await self._client().test_connection()
        self.assertTrue(result["ok"])
        self.assertEqual(result["version"], "1.4.2")

    async def test_connection_fails_soft(self):
        with patch("app.integrations.bazarr.httpx.AsyncClient",
                   lambda **kw: _FakeAsyncClient({}, raise_exc=RuntimeError("unreachable"))):
            result = await self._client().test_connection()
        self.assertFalse(result["ok"])


class _FakeBazarrClient:
    def __init__(self, movie_count=None, series_count=None):
        self._movie_count = movie_count
        self._series_count = series_count

    async def movie_subtitle_count(self, radarr_id):
        return self._movie_count

    async def series_subtitle_count(self, sonarr_series_id):
        return self._series_count


class SubtitleWarningEndpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()

    async def test_item_not_found_raises_404(self):
        from fastapi import HTTPException
        from app.api.v1.media import subtitle_warning
        with self.assertRaises(HTTPException) as ctx:
            await subtitle_warning(999, self.db)
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_bazarr_not_configured_reports_unavailable(self):
        from app.api.v1.media import subtitle_warning
        item = MediaItem(plex_rating_key="rk-movie-1", title="Movie", media_type="movie", radarr_id=42)
        self.db.add(item)
        self.db.commit()
        result = await subtitle_warning(item.id, self.db)
        self.assertFalse(result["available"])
        self.assertIsNone(result["subtitle_count"])

    async def test_radarr_linked_item_reports_movie_subtitle_count(self):
        from app.api.v1.media import subtitle_warning
        self.db.add(Integration(name="bazarr", url="http://bazarr.local", enabled=True))
        item = MediaItem(plex_rating_key="rk-movie-1", title="Movie", media_type="movie", radarr_id=42)
        self.db.add(item)
        self.db.commit()
        with patch("app.api.v1.integrations._get_client", return_value=_FakeBazarrClient(movie_count=3)):
            result = await subtitle_warning(item.id, self.db)
        self.assertTrue(result["available"])
        self.assertEqual(result["subtitle_count"], 3)

    async def test_sonarr_linked_item_reports_series_subtitle_count(self):
        from app.api.v1.media import subtitle_warning
        self.db.add(Integration(name="bazarr", url="http://bazarr.local", enabled=True))
        item = MediaItem(plex_rating_key="rk-show-1", title="Show", media_type="episode", sonarr_id=7)
        self.db.add(item)
        self.db.commit()
        with patch("app.api.v1.integrations._get_client", return_value=_FakeBazarrClient(series_count=5)):
            result = await subtitle_warning(item.id, self.db)
        self.assertTrue(result["available"])
        self.assertEqual(result["subtitle_count"], 5)

    async def test_item_with_no_arr_link_reports_unavailable(self):
        from app.api.v1.media import subtitle_warning
        self.db.add(Integration(name="bazarr", url="http://bazarr.local", enabled=True))
        item = MediaItem(plex_rating_key="rk-artist-1", title="Artist", media_type="artist")
        self.db.add(item)
        self.db.commit()
        with patch("app.api.v1.integrations._get_client", return_value=_FakeBazarrClient()):
            result = await subtitle_warning(item.id, self.db)
        self.assertFalse(result["available"])


if __name__ == "__main__":
    unittest.main()
