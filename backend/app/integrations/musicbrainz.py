"""Read-only MusicBrainz client for Artist Discovery enrichment (bio fallback,
active years, genre fallback). Public API, no credentials — rate-limited to
respect MusicBrainz's ~1 req/sec guidance with a descriptive User-Agent, both
required by their API usage policy. Never called on the hot path — only when
building the display fields for a newly-created review-queue candidate."""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx

_API = "https://musicbrainz.org/ws/2"
_USER_AGENT = "Powarr/0.41.0 (https://github.com/ConniptionFit/powarr)"

_lock = asyncio.Lock()
_last_call = 0.0
_MIN_INTERVAL = 1.05  # seconds between requests


async def _throttle() -> None:
    global _last_call
    async with _lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call = time.monotonic()


async def get_artist(mbid: str) -> dict[str, Any] | None:
    """GET /artist/{mbid} with genres + url-rels (used to find a Wikipedia link)."""
    if not mbid:
        return None
    await _throttle()
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(
                f"{_API}/artist/{mbid}",
                params={"fmt": "json", "inc": "genres+url-rels"},
                headers={"User-Agent": _USER_AGENT},
            )
            r.raise_for_status()
            return r.json()
    except Exception:
        return None


def life_span_text(data: dict[str, Any]) -> str | None:
    """Format life-span.begin/end into a compact 'active years' string."""
    span = data.get("life-span") or {}
    begin = (span.get("begin") or "")[:4]
    end = (span.get("end") or "")[:4]
    ended = span.get("ended")
    if not begin:
        return None
    if ended and end:
        return f"{begin}–{end}"
    if ended:
        return begin
    return f"{begin}–present"


def genres(data: dict[str, Any]) -> list[str]:
    return [g["name"] for g in (data.get("genres") or []) if g.get("name")][:8]


def era_decade(data: dict[str, Any]) -> str | None:
    """SP-15 — bucket life-span.begin into a decade label ("1990s") for era
    tagging. Distinct from life_span_text() (a display string like "1990-2005")
    — this is a coarse, groupable key an artist's Qdrant point can carry."""
    begin = ((data.get("life-span") or {}).get("begin") or "")[:4]
    if not begin.isdigit():
        return None
    decade = (int(begin) // 10) * 10
    return f"{decade}s"


def wikipedia_title(data: dict[str, Any]) -> tuple[str, str] | None:
    """Find a direct wikipedia relation among url-rels; returns (lang, title) or None.
    Rare in practice — MusicBrainz deprecated direct wikipedia rels in favor of
    wikidata rels years ago, so most artists only carry wikidata_qid()."""
    for rel in data.get("relations") or []:
        url = ((rel.get("url") or {}).get("resource")) or ""
        if "wikipedia.org/wiki/" not in url:
            continue
        try:
            lang = url.split("//")[1].split(".")[0]
            title = url.rsplit("/wiki/", 1)[1]
            return lang, title
        except (IndexError, KeyError):
            continue
    return None


def wikidata_qid(data: dict[str, Any]) -> str | None:
    """Find a wikidata relation among url-rels; returns the Q-id or None."""
    for rel in data.get("relations") or []:
        url = ((rel.get("url") or {}).get("resource")) or ""
        if "wikidata.org/wiki/" not in url:
            continue
        qid = url.rsplit("/wiki/", 1)[1].strip()
        if qid.startswith("Q"):
            return qid
    return None


def deezer_artist_id(data: dict[str, Any]) -> str | None:
    """Find a Deezer artist relation among url-rels; returns the numeric id or None."""
    for rel in data.get("relations") or []:
        url = ((rel.get("url") or {}).get("resource")) or ""
        m = re.search(r"deezer\.com/(?:[a-z]{2}/)?artist/(\d+)", url)
        if m:
            return m.group(1)
    return None
