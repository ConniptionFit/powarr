"""Read-only Qdrant client for Smart Playlists (MOD-01). Never upserts."""
from __future__ import annotations

from typing import Any

import httpx

from app.integrations.base import BaseIntegration


class QdrantIntegration(BaseIntegration):
    """Not registered in INTEGRATION_NAMES — configured via AppSetting smart_playlists."""

    name = "qdrant"

    def __init__(self, url: str, api_key: str = "", collection: str = "music_affinity_space"):
        super().__init__(url, api_key)
        self.collection = collection

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["api-key"] = self.api_key
        return h

    async def test_connection(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                r = await client.get(f"{self.url}/collections/{self.collection}",
                                     headers=self._headers())
                r.raise_for_status()
                return {"ok": True, "message": "Connected", "version": None}
        except Exception as e:
            return {"ok": False, "message": str(e), "version": None}

    async def scroll_monitored_artists(self, *, limit: int = 256,
                                       offset: Any = None) -> tuple[list[dict], Any]:
        """Scroll points with is_monitored_lidarr == true. Returns (points, next_offset)."""
        body: dict[str, Any] = {
            "limit": limit,
            "with_payload": True,
            "with_vector": False,
            "filter": {
                "must": [{"key": "is_monitored_lidarr", "match": {"value": True}}],
            },
        }
        if offset is not None:
            body["offset"] = offset
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.post(
                f"{self.url}/collections/{self.collection}/points/scroll",
                headers=self._headers(), json=body)
            r.raise_for_status()
            data = r.json().get("result") or {}
            return data.get("points") or [], data.get("next_page_offset")
