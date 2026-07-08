from datetime import datetime
from typing import Any, Optional
import httpx

from app.integrations.base import BaseIntegration

PLEX_PAGE_SIZE = 500  # items per container page when walking a library section


class PlexIntegration(BaseIntegration):
    name = "plex"

    async def test_connection(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                r = await client.get(
                    f"{self.url}/identity",
                    headers={"X-Plex-Token": self.api_key, "Accept": "application/json"},
                )
                r.raise_for_status()
                data = r.json()
                version = data.get("MediaContainer", {}).get("version")
                return {"ok": True, "message": "Connected", "version": version}
        except Exception as e:
            return {"ok": False, "message": str(e), "version": None}

    async def fetch_media_items(self) -> list[dict]:
        items = []
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            headers = {"X-Plex-Token": self.api_key, "Accept": "application/json"}

            libs_r = await client.get(f"{self.url}/library/sections", headers=headers)
            libs_r.raise_for_status()
            sections = libs_r.json()["MediaContainer"].get("Directory", [])

            for section in sections:
                section_key = section["key"]
                section_title = section["title"]
                section_type = section["type"]

                if section_type == "movie":
                    entries = await self._fetch_all(client, headers, section_key)
                    for entry in entries:
                        items.append(self._parse_leaf(entry, section_title, "movie", None))

                elif section_type == "show":
                    # type=4 fetches episodes directly — the only level with file parts
                    entries = await self._fetch_all(client, headers, section_key, leaf_type=4)
                    for entry in entries:
                        items.append(self._parse_leaf(entry, section_title, "episode", entry.get("grandparentTitle")))

                elif section_type == "artist":
                    # type=10 fetches tracks directly
                    entries = await self._fetch_all(client, headers, section_key, leaf_type=10)
                    for entry in entries:
                        items.append(self._parse_leaf(entry, section_title, "track", entry.get("grandparentTitle")))

        return [i for i in items if i]

    async def _fetch_all(self, client, headers, section_key: str, leaf_type: Optional[int] = None) -> list[dict]:
        """Walk a section in pages via Plex container pagination, rather than pulling
        an entire library (tens of thousands of episodes) in one response. Stops when
        totalSize is reached or a page comes back empty."""
        items: list[dict] = []
        start = 0
        while True:
            params: dict = {"X-Plex-Container-Start": start,
                            "X-Plex-Container-Size": PLEX_PAGE_SIZE}
            if leaf_type is not None:
                params["type"] = leaf_type
            r = await client.get(
                f"{self.url}/library/sections/{section_key}/all",
                headers=headers,
                params=params,
            )
            r.raise_for_status()
            container = r.json().get("MediaContainer", {})
            batch = container.get("Metadata", []) or []
            items.extend(batch)
            start += len(batch)
            total = container.get("totalSize")
            if not batch or (total is not None and start >= total):
                break
        return items

    def _parse_leaf(self, entry: dict, section_title: str, media_type: str, parent_title: Optional[str]) -> dict:
        media_list = entry.get("Media", [{}])
        part = media_list[0].get("Part", [{}])[0] if media_list else {}
        file_size = part.get("size", 0) or 0

        added_at = entry.get("addedAt")
        originally_available = entry.get("originallyAvailableAt")

        return {
            "plex_rating_key": str(entry.get("ratingKey", "")),
            "title": entry.get("title", "Unknown"),
            "year": entry.get("year"),
            "media_type": media_type,
            "library_section": section_title,
            "parent_title": parent_title,
            "file_path": part.get("file"),
            "file_size": file_size,
            "added_at": datetime.fromtimestamp(added_at) if added_at else None,
            "release_date": datetime.strptime(originally_available, "%Y-%m-%d") if originally_available else None,
            "watch_count": entry.get("viewCount", 0) or 0,
            "last_watched_at": datetime.fromtimestamp(entry["lastViewedAt"]) if entry.get("lastViewedAt") else None,
        }
