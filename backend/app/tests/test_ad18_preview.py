"""Unit tests for AD-18: listen-before-add preview (YouTube/Spotify)."""
import base64
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.integrations.spotify import SpotifyIntegration
from app.integrations.youtube import YoutubeIntegration
from app.models.integration import Integration
from app.services.artist_preview import clear_preview_cache, get_preview


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

    async def post(self, url, **kwargs):
        if self._raise_exc:
            raise self._raise_exc
        return _FakeResponse(self._payload, self._status_code)


class YoutubeClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_video_returns_first_result(self):
        payload = {"items": [{"id": {"videoId": "abc123"}, "snippet": {"title": "Artist - Official"}}]}
        client = YoutubeIntegration("", "fakekey")
        with patch("app.integrations.youtube.httpx.AsyncClient", lambda **kw: _FakeAsyncClient(payload)):
            result = await client.search_video("Some Artist")
        self.assertEqual(result, {"video_id": "abc123", "title": "Artist - Official"})

    async def test_search_video_none_when_no_items(self):
        client = YoutubeIntegration("", "fakekey")
        with patch("app.integrations.youtube.httpx.AsyncClient", lambda **kw: _FakeAsyncClient({"items": []})):
            result = await client.search_video("Nonexistent")
        self.assertIsNone(result)

    async def test_search_video_fails_soft(self):
        client = YoutubeIntegration("", "fakekey")
        with patch("app.integrations.youtube.httpx.AsyncClient",
                   lambda **kw: _FakeAsyncClient({}, raise_exc=RuntimeError("boom"))):
            result = await client.search_video("X")
        self.assertIsNone(result)


class SpotifyClientTests(unittest.IsolatedAsyncioTestCase):
    def _client(self):
        return SpotifyIntegration("", "client_secret_val", username="client_id_val")

    async def test_get_token_uses_basic_auth_of_id_and_secret(self):
        captured = {}

        class _CapturingClient(_FakeAsyncClient):
            async def post(self, url, **kwargs):
                captured["headers"] = kwargs.get("headers")
                return await super().post(url, **kwargs)

        payload = {"access_token": "tok123"}
        with patch("app.integrations.spotify.httpx.AsyncClient", lambda **kw: _CapturingClient(payload)):
            token = await self._client()._get_token()
        self.assertEqual(token, "tok123")
        expected = base64.b64encode(b"client_id_val:client_secret_val").decode()
        self.assertEqual(captured["headers"]["Authorization"], f"Basic {expected}")

    async def test_get_token_none_without_credentials(self):
        client = SpotifyIntegration("", "", username="")
        token = await client._get_token()
        self.assertIsNone(token)

    async def test_search_preview_none_when_no_preview_url(self):
        client = self._client()
        token_payload = {"access_token": "tok"}
        search_payload = {"tracks": {"items": [{"name": "Track", "preview_url": None}]}}

        call_count = {"n": 0}

        class _SequencedClient(_FakeAsyncClient):
            async def post(self, url, **kwargs):
                return _FakeResponse(token_payload)

            async def get(self, url, **kwargs):
                return _FakeResponse(search_payload)

        with patch("app.integrations.spotify.httpx.AsyncClient", lambda **kw: _SequencedClient({})):
            result = await client.search_preview("Some Artist")
        self.assertIsNone(result)

    async def test_search_preview_returns_url_when_present(self):
        client = self._client()
        token_payload = {"access_token": "tok"}
        search_payload = {"tracks": {"items": [{"name": "Track", "preview_url": "https://p.scdn.co/x.mp3"}]}}

        class _SequencedClient(_FakeAsyncClient):
            async def post(self, url, **kwargs):
                return _FakeResponse(token_payload)

            async def get(self, url, **kwargs):
                return _FakeResponse(search_payload)

        with patch("app.integrations.spotify.httpx.AsyncClient", lambda **kw: _SequencedClient({})):
            result = await client.search_preview("Some Artist")
        self.assertEqual(result, {"preview_url": "https://p.scdn.co/x.mp3", "title": "Track"})


class _FakeYoutubeClient:
    def __init__(self, result=None):
        self._result = result

    async def search_video(self, artist_name):
        return self._result


class _FakeSpotifyClient:
    def __init__(self, result=None):
        self._result = result

    async def search_preview(self, artist_name):
        return self._result


class GetPreviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        clear_preview_cache()

    def tearDown(self):
        self.db.close()
        clear_preview_cache()

    async def test_no_sources_configured_returns_empty_list(self):
        result = await get_preview(self.db, "Some Artist")
        self.assertEqual(result["sources"], [])

    async def test_disabled_source_is_omitted(self):
        self.db.add(Integration(name="youtube", api_key="k", enabled=False))
        self.db.commit()
        result = await get_preview(self.db, "Some Artist")
        self.assertEqual(result["sources"], [])

    async def test_youtube_enabled_and_available(self):
        self.db.add(Integration(name="youtube", api_key="k", enabled=True))
        self.db.commit()
        with patch("app.api.v1.integrations._get_client",
                   return_value=_FakeYoutubeClient({"video_id": "abc", "title": "T"})):
            result = await get_preview(self.db, "Some Artist")
        self.assertEqual(result["sources"], [{"source": "youtube", "available": True, "video_id": "abc", "title": "T"}])

    async def test_youtube_enabled_but_no_match(self):
        self.db.add(Integration(name="youtube", api_key="k", enabled=True))
        self.db.commit()
        with patch("app.api.v1.integrations._get_client", return_value=_FakeYoutubeClient(None)):
            result = await get_preview(self.db, "Some Artist")
        self.assertEqual(result["sources"], [{"source": "youtube", "available": False, "message": "No video found"}])

    async def test_both_sources_enabled(self):
        self.db.add(Integration(name="youtube", api_key="k", enabled=True))
        self.db.add(Integration(name="spotify", api_key="secret", username="id", enabled=True))
        self.db.commit()

        def fake_get_client(row):
            if row.name == "youtube":
                return _FakeYoutubeClient({"video_id": "abc", "title": "T"})
            return _FakeSpotifyClient({"preview_url": "https://p.scdn.co/x.mp3", "title": "Track"})

        with patch("app.api.v1.integrations._get_client", side_effect=fake_get_client):
            result = await get_preview(self.db, "Some Artist")
        self.assertEqual(len(result["sources"]), 2)
        sources_by_name = {s["source"]: s for s in result["sources"]}
        self.assertTrue(sources_by_name["youtube"]["available"])
        self.assertTrue(sources_by_name["spotify"]["available"])


class PreviewCacheTests(unittest.IsolatedAsyncioTestCase):
    """v0.79.0 — availability checks fire per card render (viewport-lazy), so
    results are cached in-process to protect the YouTube search quota."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        clear_preview_cache()

    def tearDown(self):
        self.db.close()
        clear_preview_cache()

    async def test_second_call_served_from_cache(self):
        self.db.add(Integration(name="youtube", api_key="k", enabled=True))
        self.db.commit()
        calls = {"n": 0}

        class _CountingClient(_FakeYoutubeClient):
            async def search_video(self, artist_name):
                calls["n"] += 1
                return {"video_id": "abc", "title": "T"}

        with patch("app.api.v1.integrations._get_client", return_value=_CountingClient()):
            first = await get_preview(self.db, "Some Artist")
            second = await get_preview(self.db, "Some Artist")
        self.assertEqual(calls["n"], 1)
        self.assertEqual(first, second)

    async def test_cache_key_includes_enabled_fingerprint(self):
        # A miss cached while only YouTube was enabled must not mask Spotify
        # after the user turns Spotify on.
        yt = Integration(name="youtube", api_key="k", enabled=True)
        sp = Integration(name="spotify", api_key="secret", username="id", enabled=False)
        self.db.add_all([yt, sp])
        self.db.commit()

        def fake_get_client(row):
            if row.name == "youtube":
                return _FakeYoutubeClient(None)
            return _FakeSpotifyClient({"preview_url": "https://p.scdn.co/x.mp3", "title": "Track"})

        with patch("app.api.v1.integrations._get_client", side_effect=fake_get_client):
            miss = await get_preview(self.db, "Some Artist")
            self.assertFalse(any(s["available"] for s in miss["sources"]))
            sp.enabled = True
            self.db.commit()
            hit = await get_preview(self.db, "Some Artist")
        by_name = {s["source"]: s for s in hit["sources"]}
        self.assertTrue(by_name["spotify"]["available"])

    async def test_artist_name_normalized_in_key(self):
        self.db.add(Integration(name="youtube", api_key="k", enabled=True))
        self.db.commit()
        calls = {"n": 0}

        class _CountingClient(_FakeYoutubeClient):
            async def search_video(self, artist_name):
                calls["n"] += 1
                return {"video_id": "abc", "title": "T"}

        with patch("app.api.v1.integrations._get_client", return_value=_CountingClient()):
            await get_preview(self.db, "Some Artist")
            await get_preview(self.db, "  some artist ")
        self.assertEqual(calls["n"], 1)


if __name__ == "__main__":
    unittest.main()
