from abc import ABC, abstractmethod
from typing import Any

import httpx


class BaseIntegration(ABC):
    """All integrations extend this. Adding a new service = subclass + register in integrations API."""

    name: str = ""

    def __init__(self, url: str, api_key: str, extra_config: dict | None = None,
                 username: str = "", password: str = ""):
        self.url = url.rstrip("/") if url else ""
        self.api_key = api_key or ""
        self.extra_config = extra_config or {}
        self.username = username or ""
        self.password = password or ""

    @abstractmethod
    async def test_connection(self) -> dict[str, Any]:
        """Return {"ok": bool, "message": str, "version": str | None}"""
        ...

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.api_key, "Content-Type": "application/json"}

    async def remove_from_queue(self, queue_id: int, remove_from_client: bool = False,
                                blocklist: bool = False) -> bool:
        """DELETE the *arr's own queue entry. Sonarr/Radarr/Lidarr/Readarr all share this
        endpoint shape (Servarr family) — one implementation for every *arr subclass, each
        of which already provides _base(). Used by Powarr's auto-purge/auto-reject paths so
        a resolved row also disappears from the *arr's own Activity > Queue, not just
        Powarr's internal triage table. Fails soft — a failed cleanup call never blocks the
        local status change that triggered it."""
        params = {"removeFromClient": str(remove_from_client).lower(), "blocklist": str(blocklist).lower()}
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                r = await client.delete(f"{self._base()}/queue/{queue_id}",  # type: ignore[attr-defined]
                                        headers=self._headers(), params=params)
                return r.status_code in (200, 204)
        except Exception:
            return False
