from typing import Any
import httpx

from app.integrations.base import BaseIntegration


class RadarrIntegration(BaseIntegration):
    name = "radarr"

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

    async def get_movies(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(f"{self._base()}/movie", headers=self._headers())
            r.raise_for_status()
            return r.json()

    async def delete_movie(self, movie_id: int, delete_files: bool = True, add_import_exclusion: bool = False) -> bool:
        params = {"deleteFiles": str(delete_files).lower(), "addImportExclusion": str(add_import_exclusion).lower()}
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.delete(
                f"{self._base()}/movie/{movie_id}",
                headers=self._headers(),
                params=params,
            )
            return r.status_code in (200, 204)

    async def get_queue(self, page_size: int = 100) -> list[dict]:
        params = {"page": 1, "pageSize": page_size, "includeUnknownMovieItems": "true"}
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(f"{self._base()}/queue", headers=self._headers(), params=params)
            r.raise_for_status()
            data = r.json()
            return data.get("records", []) if isinstance(data, dict) else data

    async def get_history(self, event_type: int | None = 1, page_size: int = 100) -> list[dict]:
        # eventType 1 = grabbed; None = all event types
        params = {"page": 1, "pageSize": page_size,
                  "sortKey": "date", "sortDirection": "descending"}
        if event_type is not None:
            params["eventType"] = event_type
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(f"{self._base()}/history", headers=self._headers(), params=params)
            r.raise_for_status()
            data = r.json()
            return data.get("records", []) if isinstance(data, dict) else data

    async def get_manual_import(self, download_id: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            r = await client.get(f"{self._base()}/manualimport", headers=self._headers(),
                                 params={"downloadId": download_id, "filterExistingFiles": "false"})
            r.raise_for_status()
            return r.json()

    async def push_import_command(self, download_id: str, movie_id: int | None = None) -> dict:
        """Fetch manual-import candidates for a download and POST back the importable ones."""
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                params = {"downloadId": download_id, "filterExistingFiles": "false"}
                r = await client.get(f"{self._base()}/manualimport", headers=self._headers(), params=params)
                r.raise_for_status()
                files = []
                for f in r.json():
                    if movie_id and not f.get("movie") and not f.get("movieId"):
                        f["movieId"] = movie_id
                    if not (f.get("movie") or f.get("movieId")):
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

    async def unmonitor_movie(self, movie_id: int) -> bool:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(f"{self._base()}/movie/{movie_id}", headers=self._headers())
            r.raise_for_status()
            movie = r.json()
            movie["monitored"] = False
            put_r = await client.put(
                f"{self._base()}/movie/{movie_id}",
                headers=self._headers(),
                json=movie,
            )
            return put_r.status_code == 202
