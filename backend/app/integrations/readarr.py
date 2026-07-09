from typing import Any
import httpx

from app.integrations.base import BaseIntegration


class ReadarrIntegration(BaseIntegration):
    name = "readarr"

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

    async def get_authors(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(f"{self._base()}/author", headers=self._headers())
            r.raise_for_status()
            return r.json()

    async def get_queue(self, page_size: int = 100, max_records: int = 500) -> list[dict]:
        return await self._paged(f"{self._base()}/queue",
                                 {"includeUnknownAuthorItems": "true"}, page_size, max_records)

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

    async def get_manual_import(self, download_id: str, folder: str | None = None) -> list[dict]:
        return await self._fetch_manual_import(download_id, filter_existing=False, folder=folder)

    async def push_import_command(self, download_id: str, author_id: int | None = None,
                                  folder: str | None = None) -> dict:
        """Fetch manual-import candidates for a download and execute a ManualImport
        command for the importable ones. Imports MUST go through POST /command —
        the bare POST /manualimport route is the reprocess endpoint and never imports."""
        try:
            # downloadId only (+ optional folder fallback) — never widen the scan
            # beyond the download (see the Sonarr seriesId incident, 2026-07-05)
            candidates = await self._fetch_manual_import(
                download_id, filter_existing=True, folder=folder)
            from app.services.import_matcher import partition_import_candidates
            importable, covered = partition_import_candidates(candidates)
            files = []
            for f in importable:
                aid = (f.get("author") or {}).get("id") or f.get("authorId") or author_id
                book_id = (f.get("book") or {}).get("id") or f.get("bookId")
                if not aid or not book_id or not f.get("path"):
                    continue
                entry = {
                    "path": f["path"],
                    "authorId": aid,
                    "bookId": book_id,
                    "foreignEditionId": f.get("foreignEditionId"),
                    "quality": f.get("quality"),
                    "downloadId": f.get("downloadId") or download_id,
                }
                files.append({k: v for k, v in entry.items() if v is not None})
            skipped = len(covered)
            if not files:
                if skipped and candidates:
                    return {"ok": False, "reason": "all_covered", "imported": 0, "skipped": skipped,
                            "message": f"All {skipped} file(s) already in library at equal-or-better quality"}
                return {"ok": False, "reason": "no_files", "imported": 0, "skipped": skipped,
                        "message": "Download files are gone — nothing left to import"}
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                pr = await client.post(f"{self._base()}/command", headers=self._headers(),
                                       json={"name": "ManualImport", "files": files, "importMode": "move",
                                             "replaceExistingFiles": False})
                if pr.status_code in (200, 201, 202):
                    msg = f"Manual import command queued for {len(files)} file(s)"
                    if skipped:
                        msg += f" ({skipped} already covered — skipped)"
                    msg += " — confirmed against history afterward"
                    return {"ok": True, "imported": len(files), "skipped": skipped,
                            "partial": bool(skipped), "message": msg}
                return {"ok": False, "message": f"Import push failed: HTTP {pr.status_code}", "imported": 0}
        except Exception as e:
            return self._manual_import_error_result(e)
