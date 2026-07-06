from typing import Any
import httpx

from app.integrations.base import BaseIntegration


def build_manual_import_files(candidates: list[dict], series_id: int | None,
                              download_id: str) -> list[dict]:
    """Map GET /manualimport candidates to ManualImport-command file entries
    (pure, unit-tested). Mirrors what Sonarr's own interactive import sends:
    flat seriesId + episodeIds, never the nested series/episodes objects."""
    files = []
    for f in candidates:
        sid = (f.get("series") or {}).get("id") or f.get("seriesId") or series_id
        episode_ids = f.get("episodeIds") or \
            [e["id"] for e in (f.get("episodes") or []) if e.get("id")]
        if not sid or not episode_ids or not f.get("path"):
            continue
        entry = {
            "path": f["path"],
            "folderName": f.get("folderName"),
            "seriesId": sid,
            "episodeIds": episode_ids,
            "quality": f.get("quality"),
            "languages": f.get("languages") or [],
            "releaseGroup": f.get("releaseGroup"),
            "indexerFlags": f.get("indexerFlags") or 0,
            "releaseType": f.get("releaseType"),
            "downloadId": f.get("downloadId") or download_id,
        }
        files.append({k: v for k, v in entry.items() if v is not None})
    return files


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

    async def get_queue(self, page_size: int = 100, max_records: int = 500) -> list[dict]:
        # includeSeries/includeEpisode inline the series (seriesType) and episode
        # (title, season/episode/absoluteEpisodeNumber) objects on each record —
        # the episode-level matcher reads them; no extra API calls per cycle.
        return await self._paged(f"{self._base()}/queue",
                                 {"includeUnknownSeriesItems": "true",
                                  "includeSeries": "true",
                                  "includeEpisode": "true"}, page_size, max_records)

    async def get_history(self, event_type: int | None = 1, page_size: int = 100,
                          max_records: int = 300) -> list[dict]:
        # eventType 1 = grabbed; None = all event types
        params = {"sortKey": "date", "sortDirection": "descending"}
        if event_type is not None:
            params["eventType"] = event_type
        return await self._paged(f"{self._base()}/history", params, page_size, max_records)

    async def _paged(self, url: str, params: dict, page_size: int, max_records: int) -> list[dict]:
        """Walk paged *arr endpoints until totalRecords or the cap is reached."""
        records: list[dict] = []
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            page = 1
            while len(records) < max_records:
                r = await client.get(url, headers=self._headers(),
                                     params={**params, "page": page, "pageSize": page_size})
                r.raise_for_status()
                data = r.json()
                batch = data.get("records", []) if isinstance(data, dict) else data
                records.extend(batch)
                total = data.get("totalRecords") if isinstance(data, dict) else None
                if not batch or (total is not None and len(records) >= total):
                    break
                page += 1
        return records[:max_records]

    async def get_episodes(self, series_id: int) -> list[dict]:
        """All episodes of a series (single lightweight call) — used by the
        season-pack matcher to check coverage against a season's episode count."""
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(f"{self._base()}/episode", headers=self._headers(),
                                 params={"seriesId": series_id})
            r.raise_for_status()
            return r.json()

    async def get_manual_import(self, download_id: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            r = await client.get(f"{self._base()}/manualimport", headers=self._headers(),
                                 params={"downloadId": download_id, "filterExistingFiles": "false"})
            r.raise_for_status()
            return r.json()

    async def push_import_command(self, download_id: str, series_id: int | None = None) -> dict:
        """Fetch manual-import candidates for a download and execute a ManualImport
        command for the importable ones. Imports MUST go through POST /command —
        the bare POST /manualimport route is Sonarr's reprocess/re-evaluate endpoint:
        it never imports, and 404s when a candidate lacks a flat seriesId."""
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                params = {"downloadId": download_id, "filterExistingFiles": "false"}
                if series_id:
                    params["seriesId"] = series_id
                r = await client.get(f"{self._base()}/manualimport", headers=self._headers(), params=params)
                r.raise_for_status()
                files = build_manual_import_files(r.json(), series_id, download_id)
                if not files:
                    return {"ok": False, "message": "No importable files resolved for this download", "imported": 0}
                pr = await client.post(f"{self._base()}/command", headers=self._headers(),
                                       json={"name": "ManualImport", "files": files, "importMode": "move"})
                if pr.status_code in (200, 201, 202):
                    return {"ok": True, "imported": len(files),
                            "message": f"Manual import command queued for {len(files)} file(s) — "
                                       "confirmed against history afterward"}
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
