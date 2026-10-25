"""Quality-upgrade candidates: what Radarr and Sonarr themselves say is below
its profile's cutoff (the "Wanted > Cutoff Unmet" list). Read-only and live:
the *arr apps own this logic (quality profiles, custom formats), so Powarr just
asks rather than re-deriving it. Each app fails independently."""
import json
import logging
from collections import defaultdict
from typing import Any

import httpx

from app.models.integration import Integration

logger = logging.getLogger("powarr")
MAX_RECORDS = 1000
PAGE_SIZE = 200


def _quality_name(file_obj: dict | None) -> str | None:
    q = ((file_obj or {}).get("quality") or {}).get("quality") or {}
    return q.get("name")


def parse_movies(records: list[dict]) -> list[dict]:
    out = []
    for r in records:
        f = r.get("movieFile") or {}
        out.append({
            "id": r.get("id"),
            "title": r.get("title") or "Unknown",
            "year": r.get("year"),
            "quality": _quality_name(f),
            "size": int(f.get("size") or 0),
            "monitored": bool(r.get("monitored", True)),
            "episodes": None,
        })
    return out


def parse_episodes(records: list[dict]) -> list[dict]:
    """Collapse episode rows to one row per series, so a 300-episode backlog is
    one line, not 300."""
    by: dict[Any, dict] = defaultdict(lambda: {"episodes": 0, "size": 0, "qualities": set()})
    titles: dict[Any, tuple] = {}
    for r in records:
        series = r.get("series") or {}
        sid = r.get("seriesId") or series.get("id")
        if sid is None:
            continue
        titles[sid] = (series.get("title") or "Unknown", series.get("year"),
                       bool(series.get("monitored", True)))
        agg = by[sid]
        agg["episodes"] += 1
        f = r.get("episodeFile") or {}
        agg["size"] += int(f.get("size") or 0)
        qn = _quality_name(f)
        if qn:
            agg["qualities"].add(qn)
    out = []
    for sid, agg in by.items():
        title, year, monitored = titles[sid]
        out.append({"id": sid, "title": title, "year": year,
                    "quality": ", ".join(sorted(agg["qualities"])) or None,
                    "size": agg["size"], "monitored": monitored,
                    "episodes": agg["episodes"]})
    return out


async def _paged(client: httpx.AsyncClient, base: str, headers: dict, params: dict) -> tuple[list[dict], int]:
    records: list[dict] = []
    total = 0
    page = 1
    while len(records) < MAX_RECORDS:
        r = await client.get(f"{base}/wanted/cutoff", headers=headers,
                             params={**params, "page": page, "pageSize": PAGE_SIZE,
                                     "monitored": "true"})
        r.raise_for_status()
        data = r.json()
        batch = data.get("records", [])
        total = int(data.get("totalRecords") or total)
        records.extend(batch)
        if not batch or len(records) >= total:
            break
        page += 1
    return records[:MAX_RECORDS], total


async def _one(db, name: str) -> dict:
    row = db.query(Integration).filter_by(name=name, enabled=True).first()
    if not row or not row.url or not row.api_key:
        return {"configured": False, "error": None, "total": 0, "items": []}
    from app.api.v1.integrations import _get_client
    client = _get_client(row)
    try:
        async with httpx.AsyncClient(timeout=45, follow_redirects=True) as http:
            if name == "radarr":
                records, total = await _paged(http, client._base(), client._headers(), {})
                items = parse_movies(records)
            else:
                records, total = await _paged(http, client._base(), client._headers(),
                                              {"includeSeries": "true"})
                items = parse_episodes(records)
        items.sort(key=lambda i: i["size"], reverse=True)
        return {"configured": True, "error": None, "total": total, "items": items}
    except Exception as e:
        logger.info(f"upgrade candidates ({name}) failed: {e}")
        return {"configured": True, "error": str(e), "total": 0, "items": []}


async def build(db) -> dict:
    return {"radarr": await _one(db, "radarr"), "sonarr": await _one(db, "sonarr")}
