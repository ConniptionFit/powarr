from typing import Any
import httpx

from app.integrations.base import BaseIntegration


class BazarrIntegration(BaseIntegration):
    """INT-01 — read-only. Bazarr tracks subtitles per Radarr movie / Sonarr
    series using the same *arr-assigned IDs Powarr already stores on MediaItem
    (radarr_id/sonarr_id), so no separate ID-mapping is needed. Deliberately
    exposes only subtitle-count lookups, never a delete/cleanup call — there is
    no live Bazarr instance in this environment to verify a destructive
    request's exact shape against, so the deletion-preview flow only ever
    warns ahead of a delete rather than acting on Bazarr's own history."""
    name = "bazarr"

    def _base(self) -> str:
        return f"{self.url}/api"

    async def test_connection(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                r = await client.get(f"{self._base()}/system/status", headers=self._headers())
                r.raise_for_status()
                data = r.json()
                version = ((data or {}).get("data") or {}).get("bazarr_version") if isinstance(data, dict) else None
                return {"ok": True, "message": "Connected", "version": version}
        except Exception as e:
            return {"ok": False, "message": str(e), "version": None}

    @staticmethod
    def _count(row: dict) -> int:
        return len(row.get("subtitles") or []) + len(row.get("missing_subtitles") or [])

    async def movie_subtitle_count(self, radarr_id: int) -> int | None:
        """Existing + missing subtitle count Bazarr knows about for this Radarr
        movie, or None if Bazarr has no record of it (never raises)."""
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                r = await client.get(f"{self._base()}/movies", headers=self._headers(),
                                     params={"radarrid": radarr_id})
                r.raise_for_status()
                rows = (r.json() or {}).get("data") or []
                return self._count(rows[0]) if rows else None
        except Exception:
            return None

    async def series_subtitle_count(self, sonarr_series_id: int) -> int | None:
        """Summed existing + missing subtitle count across every episode Bazarr
        tracks for this Sonarr series, or None if Bazarr has no record of it."""
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                r = await client.get(f"{self._base()}/episodes", headers=self._headers(),
                                     params={"seriesid": sonarr_series_id})
                r.raise_for_status()
                rows = (r.json() or {}).get("data") or []
                return sum(self._count(row) for row in rows) if rows else None
        except Exception:
            return None
