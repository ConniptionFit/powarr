"""Qdrant client for the `music_affinity_space` collection shared by Smart Playlists
(read-only usage, MOD-01) and Artist Discovery (read+write usage). Write access is a
per-module convention, not enforced by this class — Smart Playlists' own code never
calls the write methods below."""
from __future__ import annotations

import hashlib
import uuid
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

    async def scroll(self, *, filter: dict | None = None, limit: int = 256,
                     offset: Any = None, with_vector: bool = False) -> tuple[list[dict], Any]:
        """Generic scroll — POST /collections/{collection}/points/scroll.
        Returns (points, next_offset)."""
        body: dict[str, Any] = {
            "limit": limit,
            "with_payload": True,
            "with_vector": with_vector,
        }
        if filter is not None:
            body["filter"] = filter
        if offset is not None:
            body["offset"] = offset

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.post(
                f"{self.url}/collections/{self.collection}/points/scroll",
                headers=self._headers(), json=body)
            r.raise_for_status()
            data = r.json().get("result") or {}
            return data.get("points") or [], data.get("next_page_offset")

    async def scroll_monitored_artists(self, *, limit: int = 256,
                                       offset: Any = None,
                                       year_min: int | None = None,
                                       year_max: int | None = None) -> tuple[list[dict], Any]:
        """Scroll points with is_monitored_lidarr == true.

        Args:
            limit: Items per page
            offset: Pagination cursor
            year_min: Optional minimum release year filter (foundation for era support)
            year_max: Optional maximum release year filter

        Returns:
            (points, next_offset) tuple
        """
        must_clauses = [{"key": "is_monitored_lidarr", "match": {"value": True}}]
        if year_min is not None:
            must_clauses.append({"key": "release_year", "range": {"gte": year_min}})
        if year_max is not None:
            must_clauses.append({"key": "release_year", "range": {"lte": year_max}})
        return await self.scroll(filter={"must": must_clauses}, limit=limit, offset=offset)

    async def retrieve_points(self, ids: list[str], with_vector: bool = False) -> list[dict]:
        """POST /collections/{collection}/points — fetch specific points by id."""
        if not ids:
            return []
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.post(
                f"{self.url}/collections/{self.collection}/points",
                headers=self._headers(),
                json={"ids": ids, "with_payload": True, "with_vector": with_vector})
            r.raise_for_status()
            return r.json().get("result") or []

    async def upsert_points(self, points: list[dict]) -> bool:
        """PUT /collections/{collection}/points — points are {id, vector, payload} dicts."""
        if not points:
            return True
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            r = await client.put(
                f"{self.url}/collections/{self.collection}/points",
                headers=self._headers(), json={"points": points})
            r.raise_for_status()
            return True

    async def set_payload(self, ids: list[str], payload: dict) -> bool:
        """POST /collections/{collection}/points/payload — merge fields into existing
        points' payloads without touching their vectors (no need to refetch+republish
        the 384-dim vector just to flip a flag or append a seed mbid)."""
        if not ids:
            return True
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.post(
                f"{self.url}/collections/{self.collection}/points/payload",
                headers=self._headers(), json={"payload": payload, "points": ids})
            r.raise_for_status()
            return True

    async def search(self, vector: list[float], *, limit: int = 10,
                     score_threshold: float | None = None,
                     must: list[dict] | None = None,
                     must_not: list[dict] | None = None) -> list[dict]:
        """POST /collections/{collection}/points/search — vector similarity search."""
        body: dict[str, Any] = {
            "vector": vector, "limit": limit, "with_payload": True, "with_vector": False,
        }
        if score_threshold is not None:
            body["score_threshold"] = score_threshold
        filt: dict[str, Any] = {}
        if must:
            filt["must"] = must
        if must_not:
            filt["must_not"] = must_not
        if filt:
            body["filter"] = filt
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.post(
                f"{self.url}/collections/{self.collection}/points/search",
                headers=self._headers(), json=body)
            r.raise_for_status()
            return r.json().get("result") or []

    @staticmethod
    def point_id(mbid: str | None, name: str) -> str:
        """Deterministic Qdrant point id — md5 of the MBID if present, else the
        normalized artist name, matching the n8n curator's dedup rule."""
        key = (mbid or "").strip() or " ".join(name.lower().split())
        digest = hashlib.md5(key.encode("utf-8")).hexdigest()
        return str(uuid.UUID(digest))

    async def get_collection_info(self) -> dict[str, Any]:
        """Get collection metadata and statistics."""
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                r = await client.get(
                    f"{self.url}/collections/{self.collection}",
                    headers=self._headers())
                r.raise_for_status()
                return r.json().get("result") or {}
        except Exception as e:
            return {"error": str(e)}
