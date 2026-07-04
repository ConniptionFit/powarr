from typing import Any
import httpx

from app.integrations.base import BaseIntegration


class SonarrIntegration(BaseIntegration):
    name = "sonarr"

    def _base(self) -> str:
        return f"{self.url}/api/v3"

    async def test_connection(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                r = await client.get(f"{self._base()}/system/status", headers=self._headers())
                r.raise_for_status()
                data = r.json()
                return {"ok": True, "message": "Connected", "version": data.get("version")}
        except Exception as e:
            return {"ok": False, "message": str(e), "version": None}

    async def get_series(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(f"{self._base()}/series", headers=self._headers())
            r.raise_for_status()
            return r.json()

    async def delete_series(self, series_id: int, delete_files: bool = True, add_import_exclusion: bool = False) -> bool:
        params = {"deleteFiles": str(delete_files).lower(), "addImportExclusion": str(add_import_exclusion).lower()}
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.delete(
                f"{self._base()}/series/{series_id}",
                headers=self._headers(),
                params=params,
            )
            return r.status_code in (200, 204)

    async def get_queue(self, page_size: int = 100) -> list[dict]:
        params = {"page": 1, "pageSize": page_size, "includeUnknownSeriesItems": "true"}
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(f"{self._base()}/queue", headers=self._headers(), params=params)
            r.raise_for_status()
            data = r.json()
            return data.get("records", []) if isinstance(data, dict) else data

    async def get_history(self, event_type: int = 1, page_size: int = 100) -> list[dict]:
        # eventType 1 = grabbed
        params = {"page": 1, "pageSize": page_size, "eventType": event_type,
                  "sortKey": "date", "sortDirection": "descending"}
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(f"{self._base()}/history", headers=self._headers(), params=params)
            r.raise_for_status()
            data = r.json()
            return data.get("records", []) if isinstance(data, dict) else data

    async def push_import_command(self, download_id: str, series_id: int | None = None) -> dict:
        """Fetch manual-import candidates for a download and POST back the importable ones."""
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                params = {"downloadId": download_id, "filterExistingFiles": "false"}
                if series_id:
                    params["seriesId"] = series_id
                r = await client.get(f"{self._base()}/manualimport", headers=self._headers(), params=params)
                r.raise_for_status()
                files = []
                for f in r.json():
                    if series_id and not f.get("series") and not f.get("seriesId"):
                        f["seriesId"] = series_id
                    has_series = f.get("series") or f.get("seriesId")
                    has_episodes = f.get("episodes") or f.get("episodeIds")
                    if not has_series or not has_episodes:
                        continue
                    f["importMode"] = "move"
                    files.append(f)
                if not files:
                    return {"ok": False, "message": "No importable files resolved for this download", "imported": 0}
                pr = await client.post(f"{self._base()}/manualimport", headers=self._headers(), json=files)
                if pr.status_code in (200, 201, 202):
                    return {"ok": True, "message": f"Imported {len(files)} file(s)", "imported": len(files)}
                return {"ok": False, "message": f"Import push failed: HTTP {pr.status_code}", "imported": 0}
        except Exception as e:
            return {"ok": False, "message": str(e), "imported": 0}

    async def unmonitor_series(self, series_id: int) -> bool:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(f"{self._base()}/series/{series_id}", headers=self._headers())
            r.raise_for_status()
            series = r.json()
            series["monitored"] = False
            put_r = await client.put(
                f"{self._base()}/series/{series_id}",
                headers=self._headers(),
                json=series,
            )
            return put_r.status_code == 202
