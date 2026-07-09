from abc import ABC, abstractmethod
from typing import Any, Optional

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

    async def _fetch_manual_import(self, download_id: str, *,
                                   filter_existing: bool = True,
                                   folder: Optional[str] = None) -> list[dict]:
        """GET /manualimport with a folder fallback for Servarr NullReference 500s.

        Sonarr (and occasionally Lidarr) throws HTTP 500
        \"Object reference not set to an instance of an object\" when asked for
        candidates by downloadId on a download whose files are already missing
        from the torrent client. Retrying with folder=outputPath alone returns
        200 + [] — which we treat as \"files gone\". Never combine folder +
        downloadId on the retry (that still 500s). Never pass seriesId/movieId
        (library-folder incident, 2026-07-05).
        """
        params: dict[str, str] = {
            "downloadId": download_id,
            "filterExistingFiles": "true" if filter_existing else "false",
        }
        async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
            r = await client.get(f"{self._base()}/manualimport",  # type: ignore[attr-defined]
                                 headers=self._headers(), params=params)
            if r.status_code == 200:
                data = r.json()
                return data if isinstance(data, list) else []
            # Folder-only fallback for the known NullReference crash
            if r.status_code >= 500 and folder:
                fr = await client.get(
                    f"{self._base()}/manualimport",  # type: ignore[attr-defined]
                    headers=self._headers(),
                    params={"folder": folder,
                            "filterExistingFiles": "true" if filter_existing else "false"},
                )
                if fr.status_code == 200:
                    data = fr.json()
                    return data if isinstance(data, list) else []
            r.raise_for_status()
            return []

    @staticmethod
    def _manual_import_error_result(exc: Exception) -> dict:
        """Map a failed manualimport/push exception to a structured result.

        Servarr NullReference 500s on downloadId (files already missing) are
        classified as reason=no_files so Accept/scan can orphan the row instead
        of leaving a raw httpx 500 string in triage.
        """
        msg = str(exc)
        low = msg.lower()
        if "500" in low and ("nullreference" in low or "object reference not set" in low):
            return {"ok": False, "reason": "no_files", "imported": 0,
                    "message": "Download files are gone — nothing left to import "
                               "(*arr manualimport crashed looking up this download)"}
        return {"ok": False, "message": msg, "imported": 0}

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
