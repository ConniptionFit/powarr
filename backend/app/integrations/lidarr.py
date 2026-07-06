from typing import Any
import httpx

from app.integrations.base import BaseIntegration


class LidarrIntegration(BaseIntegration):
    name = "lidarr"

    def _base(self) -> str:
        return f"{self.url}/api/v1"

    async def test_connection(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                r = await client.get(f"{self._base()}/system/status", headers=self._headers())
                r.raise_for_status()
                data = r.json()
                return {"ok": True, "message": "Connected", "version": data.get("version")}
        except Exception as e:
            return {"ok": False, "message": str(e), "version": None}

    async def get_artists(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(f"{self._base()}/artist", headers=self._headers())
            r.raise_for_status()
            return r.json()

    async def delete_artist(self, artist_id: int, delete_files: bool = True) -> bool:
        params = {"deleteFiles": str(delete_files).lower()}
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.delete(
                f"{self._base()}/artist/{artist_id}",
                headers=self._headers(),
                params=params,
            )
            return r.status_code in (200, 204)

    async def get_queue(self, page_size: int = 100, max_records: int = 500) -> list[dict]:
        return await self._paged(f"{self._base()}/queue",
                                 {"includeUnknownArtistItems": "true"}, page_size, max_records)

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

    async def get_manual_import(self, download_id: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            r = await client.get(f"{self._base()}/manualimport", headers=self._headers(),
                                 params={"downloadId": download_id, "filterExistingFiles": "false"})
            r.raise_for_status()
            return r.json()

    async def push_import_command(self, download_id: str, artist_id: int | None = None) -> dict:
        """Fetch manual-import candidates for a download and execute a ManualImport
        command for the importable ones. Imports MUST go through POST /command —
        the bare POST /manualimport route is the reprocess endpoint and never imports."""
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                # downloadId only + filter already-imported files — never widen the scan
                # beyond the download (see the Sonarr seriesId incident, 2026-07-05)
                params = {"downloadId": download_id, "filterExistingFiles": "true"}
                r = await client.get(f"{self._base()}/manualimport", headers=self._headers(), params=params)
                r.raise_for_status()
                files = []
                for f in r.json():
                    aid = (f.get("artist") or {}).get("id") or f.get("artistId") or artist_id
                    album_id = (f.get("album") or {}).get("id") or f.get("albumId")
                    if not aid or not album_id or not f.get("path"):
                        continue
                    entry = {
                        "path": f["path"],
                        "artistId": aid,
                        "albumId": album_id,
                        "albumReleaseId": (f.get("albumRelease") or {}).get("id") or f.get("albumReleaseId"),
                        "trackIds": f.get("trackIds") or [t["id"] for t in (f.get("tracks") or []) if t.get("id")],
                        "quality": f.get("quality"),
                        "downloadId": f.get("downloadId") or download_id,
                    }
                    files.append({k: v for k, v in entry.items() if v is not None})
                if not files:
                    return {"ok": False, "message": "No importable files resolved for this download", "imported": 0}
                pr = await client.post(f"{self._base()}/command", headers=self._headers(),
                                       json={"name": "ManualImport", "files": files, "importMode": "move",
                                             "replaceExistingFiles": False})
                if pr.status_code in (200, 201, 202):
                    return {"ok": True, "imported": len(files),
                            "message": f"Manual import command queued for {len(files)} file(s) — "
                                       "confirmed against history afterward"}
                return {"ok": False, "message": f"Import push failed: HTTP {pr.status_code}", "imported": 0}
        except Exception as e:
            return {"ok": False, "message": str(e), "imported": 0}

    async def unmonitor_artist(self, artist_id: int) -> bool:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(f"{self._base()}/artist/{artist_id}", headers=self._headers())
            r.raise_for_status()
            artist = r.json()
            artist["monitored"] = False
            put_r = await client.put(
                f"{self._base()}/artist/{artist_id}",
                headers=self._headers(),
                json=artist,
            )
            return put_r.status_code == 202
