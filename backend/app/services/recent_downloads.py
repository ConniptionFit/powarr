"""FI-09: browse recently grabbed downloads across all enabled *arr apps and
force a re-import, independent of stuck-import detection (Scan Now only
surfaces items the queue/history heuristics flag as stuck). This is a direct
one-shot action, not a triage row — it never writes a FailedImport.

The re-import action reuses the exact same push_import_command() every other
import path (Accept, auto-resolve) already goes through — downloadId-only
manual-import GET, never seriesId on that GET, library-folder guard — see the
warning in SonarrIntegration.push_import_command(). This module only adds a
browse/search list on top of get_history(), it does not touch that path."""
import asyncio

from app.models.integration import Integration
from app.services.import_matcher import (APP_FIELDS, _get_client, _album_display_title,
                                         _book_display_title)

_DISPLAY_TITLE = {
    "lidarr": _album_display_title,
    "readarr": _book_display_title,
}


async def _fetch_soft(coro, default):
    """Await a client call, returning `default` (never raising) on failure —
    one app's flaky endpoint must not take down the whole browse list."""
    try:
        return await coro
    except Exception:
        return default


async def _app_downloads(db, app_name: str, q: str, max_records: int) -> list[dict]:
    row = db.query(Integration).filter_by(name=app_name, enabled=True).first()
    if not row:
        return []
    client = _get_client(app_name, row)
    id_key, lib_method, title_key = APP_FIELDS[app_name]
    display = _DISPLAY_TITLE.get(app_name)

    # The three reads are independent — run them concurrently instead of
    # three sequential round-trips per app (this was the bulk of the wall
    # time when four apps were each queried one after another).
    history, queue, library = await asyncio.gather(
        _fetch_soft(client.get_history(event_type=1, max_records=max_records), []),
        _fetch_soft(client.get_queue(), []),
        _fetch_soft(getattr(client, lib_method)(), []),
    )
    if not history:
        return []
    queued_ids = {item.get("downloadId") for item in queue if item.get("downloadId")}
    lib_by_id = {item["id"]: item for item in library}

    rows: list[dict] = []
    seen: set[str] = set()
    for h in history:
        download_id = h.get("downloadId")
        if not download_id or download_id in seen:
            continue
        seen.add(download_id)

        matched_id = h.get(id_key)
        matched_item = lib_by_id.get(matched_id) if matched_id else None
        matched_title = None
        if matched_item:
            matched_title = display(matched_item) if display else matched_item.get(title_key)

        source_title = h.get("sourceTitle") or ""
        if q and q not in source_title.lower() and q not in (matched_title or "").lower():
            continue

        rows.append({
            "source_app": app_name,
            "source_title": source_title,
            "download_id": download_id,
            "matched_id": matched_id,
            "matched_title": matched_title,
            "event_date": h.get("date"),
            "still_in_queue": download_id in queued_ids,
        })
    return rows


async def list_recent_downloads(db, source_app: str | None = None, search: str | None = None,
                                max_records: int = 100) -> list[dict]:
    """One row per distinct downloadId grabbed recently (newest first),
    across every enabled *arr app unless source_app narrows it to one.
    Every app is queried concurrently, and fails soft — one app's
    history/queue/library fetch failing doesn't drop the others."""
    apps = [source_app] if source_app else list(APP_FIELDS.keys())
    q = (search or "").strip().lower()
    per_app = await asyncio.gather(
        *(_app_downloads(db, app_name, q, max_records) for app_name in apps)
    )
    results = [row for app_rows in per_app for row in app_rows]
    results.sort(key=lambda r: r["event_date"] or "", reverse=True)
    return results[:max_records]
