"""AD-21 — persistent thumbnail cache for artists actually in the user's library.

The Related Artists typeahead (AD-20) took its thumbnails from Last.fm's
artist.search response, but Last.fm has served blank or placeholder star images
for years, so the search box showed no artist photos at all. This module keeps
a locally-persisted image URL for every library artist instead:

- Library set = union of Lidarr artists and artist names from the synced Plex
  music library (MediaItem track parent_title, same signal as
  _plex_artist_names in artist_discovery.py).
- Image source: Lidarr's own remote poster URL first (free — already in the
  get_artists() payload, public fanart.tv/metadata-proxy URLs, no API key in
  the URL), Deezer name-search fallback for Plex-only artists (public CDN,
  same trust rule as enrichment: exact name match only).
- Cleanup: rows whose artist is no longer in the library are deleted on each
  refresh. If Lidarr is configured but unreachable the whole refresh aborts —
  a flaky integration must never read as "everything was deleted" (same rule
  as orphan cleanup and seeding protection).
- A row with image_url NULL is a confirmed miss; it is only re-searched after
  _MISS_RETRY_DAYS so a library full of obscure artists doesn't re-hit Deezer
  on every daily run.

Refreshed by the scheduler once a day (last_artist_thumb_refresh AppSetting
gate); consumed by search_artist_names() as an overlay on typeahead results.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from app.integrations import deezer
from app.models import ArtistThumbnail, MediaItem

logger = logging.getLogger("powarr")

_MISS_RETRY_DAYS = 7
_DEEZER_LOOKUP_CAP = 100  # max Deezer name-searches per refresh run
_DEEZER_PAUSE_S = 0.25


def _lidarr_remote_poster(images: list[dict] | None) -> str | None:
    """Public remoteUrl from a Lidarr library artist's images — never the
    relative /MediaCover url, which would need the Lidarr host + API key in
    the browser. coverType casing differs between Lidarr endpoints, so match
    case-insensitively."""
    by_type = {}
    for img in images or []:
        url = (img.get("remoteUrl") or "").strip()
        if url:
            by_type[(img.get("coverType") or "").lower()] = url
    return by_type.get("poster") or by_type.get("fanart") or by_type.get("banner")


def _plex_artists_raw(db) -> dict[str, str]:
    """norm-key -> display name for artists in the synced Plex music library.
    Unlike _plex_artist_names() this keeps the raw name, needed for the Deezer
    exact-name-match search."""
    from app.services.artist_discovery import _norm_artist
    rows = (db.query(MediaItem.parent_title)
            .filter(MediaItem.media_type == "track", MediaItem.parent_title.isnot(None))
            .distinct().all())
    out: dict[str, str] = {}
    for (raw,) in rows:
        key = _norm_artist(raw)
        if key and key not in out:
            out[key] = raw.strip()
    return out


async def refresh_library_thumbnails(db) -> dict[str, Any]:
    """Upsert a thumbnail row for every current library artist and delete rows
    for artists that have left the library. Never raises."""
    from app.services.artist_discovery import _lidarr_artist_index
    try:
        lidarr_index = await _lidarr_artist_index(db)
    except Exception as e:
        return {"ok": False,
                "message": f"Lidarr unreachable, thumbnail refresh skipped: {e}"}
    lidarr_by_name = lidarr_index[1] if lidarr_index else {}
    plex_names = _plex_artists_raw(db)

    library: dict[str, dict] = {}
    for key, artist in lidarr_by_name.items():
        name = (artist.get("artistName") or "").strip()
        if key and name:
            library[key] = {"name": name,
                            "mbid": artist.get("foreignArtistId"),
                            "lidarr_image": _lidarr_remote_poster(artist.get("images"))}
    for key, raw in plex_names.items():
        library.setdefault(key, {"name": raw, "mbid": None, "lidarr_image": None})

    if not library:
        # No Lidarr artists and no synced Plex music — could be a pre-first-sync
        # state, so don't treat existing rows as stale.
        return {"ok": True, "added": 0, "updated": 0, "removed": 0,
                "message": "No library artists found; nothing to refresh"}

    existing = {row.name_key: row for row in db.query(ArtistThumbnail).all()}

    removed = 0
    for key, row in existing.items():
        if key not in library:
            db.delete(row)
            removed += 1

    added = updated = deezer_calls = 0
    now = datetime.utcnow()
    retry_cutoff = now - timedelta(days=_MISS_RETRY_DAYS)
    for key, info in library.items():
        row = existing.get(key)
        if row and row.image_url:
            # Keep a lidarr-sourced URL current if Lidarr's poster changed —
            # free, the URL is already in the get_artists() payload.
            if (row.source == "lidarr" and info["lidarr_image"]
                    and row.image_url != info["lidarr_image"]):
                row.image_url = info["lidarr_image"]
                row.fetched_at = now
                updated += 1
            continue
        if (row and not info["lidarr_image"]
                and row.fetched_at and row.fetched_at > retry_cutoff):
            continue  # recent confirmed miss — don't re-hit Deezer yet
        url, source = info["lidarr_image"], "lidarr"
        if not url:
            if deezer_calls >= _DEEZER_LOOKUP_CAP:
                continue
            deezer_calls += 1
            url = await deezer.search_artist_image(info["name"])
            source = "deezer" if url else None
            await asyncio.sleep(_DEEZER_PAUSE_S)
        if row:
            row.image_url = url
            row.source = source
            row.fetched_at = now
            updated += 1
        else:
            db.add(ArtistThumbnail(name_key=key, artist_name=info["name"],
                                   image_url=url, source=source,
                                   musicbrainz_id=info["mbid"], fetched_at=now))
            added += 1
    db.commit()
    msg = f"Thumbnails: {added} added, {updated} updated, {removed} removed"
    return {"ok": True, "added": added, "updated": updated, "removed": removed,
            "message": msg}


def thumbnails_for(db, names: list[str]) -> dict[str, str]:
    """norm-key -> image URL for the given artist names, hits only (misses and
    unknown artists simply absent). Cheap enough for the typeahead hot path —
    one indexed IN query."""
    from app.services.artist_discovery import _norm_artist
    keys = [k for k in {_norm_artist(n) for n in names if n} if k]
    if not keys:
        return {}
    rows = (db.query(ArtistThumbnail)
            .filter(ArtistThumbnail.name_key.in_(keys),
                    ArtistThumbnail.image_url.isnot(None))
            .all())
    return {r.name_key: r.image_url for r in rows}
