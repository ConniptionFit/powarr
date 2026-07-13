from typing import Any
import httpx

from app.integrations.base import BaseIntegration

_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"


class YoutubeIntegration(BaseIntegration):
    """AD-18 — read-only public Data API v3 search (API key only, no OAuth, no
    uploads/playback tracking). Used purely to resolve an artist name to a
    video ID for an embedded preview player on Discovery/Related Artists cards.
    `url` is unused (fixed Google endpoint) — has its own settings card with
    just an API-key field rather than the generic URL+key `IntegrationCard`."""
    name = "youtube"

    def _headers(self) -> dict[str, str]:
        return {}  # auth is a ?key= query param, not a header

    async def test_connection(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                r = await client.get(_SEARCH_URL, params={
                    "part": "snippet", "type": "video", "maxResults": 1,
                    "q": "test", "key": self.api_key,
                })
                r.raise_for_status()
                return {"ok": True, "message": "Connected", "version": None}
        except Exception as e:
            return {"ok": False, "message": str(e), "version": None}

    async def search_video(self, artist_name: str) -> dict | None:
        """First video result for "{artist} official" — {video_id, title}, or
        None if nothing resolves (never raises)."""
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                r = await client.get(_SEARCH_URL, params={
                    "part": "snippet", "type": "video", "maxResults": 1,
                    "q": f"{artist_name} official", "key": self.api_key,
                })
                r.raise_for_status()
                items = (r.json() or {}).get("items") or []
                if not items:
                    return None
                video_id = ((items[0].get("id") or {}).get("videoId"))
                title = ((items[0].get("snippet") or {}).get("title"))
                return {"video_id": video_id, "title": title} if video_id else None
        except Exception:
            return None
