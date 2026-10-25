"""Jellyfin as a library source (read-only).

Mirrors what PlexIntegration.fetch_media_items hands to the sync: one dict per
leaf item (movie / episode / track) in the shape MediaItem expects. Rating keys
are stored as "jellyfin:<ItemId>" so they can never collide with Plex's, and so
Plex-only features (Tautulli matching, playlists) simply find nothing to act on.

Watch data: Jellyfin keeps play state per user. Powarr treats an item as
watched if *any* user played it (play counts summed, newest play date), which
errs on the side of keeping things someone in the household watched.
"""
import re
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.integrations.base import BaseIntegration

PAGE_SIZE = 500
KEY_PREFIX = "jellyfin:"
MAX_USERS = 25
ITEM_TYPES = {"Movie": "movie", "Episode": "episode", "Audio": "track"}
COLLECTION_TYPES = {"movies": "Movie", "tvshows": "Episode", "music": "Audio"}


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    """Jellyfin timestamps are ISO-8601 UTC with up to 7 fractional digits, which
    fromisoformat rejects; trim to microseconds. Returns naive UTC, like the
    rest of the MediaItem columns."""
    if not value:
        return None
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})?", value.strip())
    if not m:
        try:
            return datetime.strptime(value.strip()[:10], "%Y-%m-%d")
        except ValueError:
            return None
    base, frac, tz = m.groups()
    dt = datetime.fromisoformat(f"{base}.{(frac or '0')[:6].ljust(6, '0')}{'+00:00' if tz in (None, 'Z') else tz}")
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def parse_item(entry: dict, library: str) -> Optional[dict]:
    """Pure: one Jellyfin /Items entry -> the dict upsert_media_items takes."""
    media_type = ITEM_TYPES.get(entry.get("Type") or "")
    item_id = entry.get("Id")
    if not media_type or not item_id:
        return None
    sources = entry.get("MediaSources") or [{}]
    src = sources[0] or {}
    if media_type == "episode":
        parent = entry.get("SeriesName")
    elif media_type == "track":
        artists = entry.get("AlbumArtist") or (entry.get("Artists") or [None])[0]
        parent = artists
    else:
        parent = None
    return {
        "plex_rating_key": f"{KEY_PREFIX}{item_id}",
        "title": entry.get("Name") or "Unknown",
        "year": entry.get("ProductionYear"),
        "media_type": media_type,
        "library_section": library,
        "parent_title": parent,
        "file_path": entry.get("Path") or src.get("Path"),
        "file_size": int(src.get("Size") or 0),
        "added_at": _parse_dt(entry.get("DateCreated")),
        "release_date": _parse_dt(entry.get("PremiereDate")),
        "watch_count": 0,
        "last_watched_at": None,
    }


class JellyfinIntegration(BaseIntegration):
    name = "jellyfin"

    def _headers(self) -> dict[str, str]:
        return {"X-Emby-Token": self.api_key, "Accept": "application/json"}

    async def test_connection(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                r = await client.get(f"{self.url}/System/Info", headers=self._headers())
                if r.status_code in (401, 403):
                    return {"ok": False, "message": "API key rejected", "version": None}
                r.raise_for_status()
                data = r.json()
                return {"ok": True, "message": "Connected", "version": data.get("Version")}
        except Exception as e:
            return {"ok": False, "message": str(e), "version": None}

    async def _pages(self, client: httpx.AsyncClient, params: dict) -> list[dict]:
        out: list[dict] = []
        start = 0
        while True:
            r = await client.get(f"{self.url}/Items", headers=self._headers(),
                                 params={**params, "StartIndex": start, "Limit": PAGE_SIZE})
            r.raise_for_status()
            data = r.json()
            batch = data.get("Items") or []
            out.extend(batch)
            start += len(batch)
            total = data.get("TotalRecordCount")
            if not batch or (total is not None and start >= total):
                return out

    async def fetch_media_items(self) -> list[dict]:
        items: dict[str, dict] = {}
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            r = await client.get(f"{self.url}/Library/VirtualFolders", headers=self._headers())
            r.raise_for_status()
            for folder in r.json():
                kind = COLLECTION_TYPES.get((folder.get("CollectionType") or "").lower())
                if not kind or not folder.get("ItemId"):
                    continue
                entries = await self._pages(client, {
                    "ParentId": folder["ItemId"], "Recursive": "true",
                    "IncludeItemTypes": kind,
                    "Fields": "Path,MediaSources,DateCreated,PremiereDate,ProductionYear",
                })
                for e in entries:
                    parsed = parse_item(e, folder.get("Name") or "Jellyfin")
                    if parsed:
                        items[e["Id"]] = parsed

            # Watch state, one lightweight pass per user over played items only.
            ur = await client.get(f"{self.url}/Users", headers=self._headers())
            ur.raise_for_status()
            for user in (ur.json() or [])[:MAX_USERS]:
                played = await self._pages(client, {
                    "UserId": user["Id"], "Recursive": "true", "Filters": "IsPlayed",
                    "IncludeItemTypes": "Movie,Episode,Audio", "Fields": "",
                })
                for e in played:
                    target = items.get(e.get("Id"))
                    if not target:
                        continue
                    ud = e.get("UserData") or {}
                    target["watch_count"] += max(int(ud.get("PlayCount") or 0), 1)
                    when = _parse_dt(ud.get("LastPlayedDate"))
                    if when and (target["last_watched_at"] is None or when > target["last_watched_at"]):
                        target["last_watched_at"] = when
        return list(items.values())
