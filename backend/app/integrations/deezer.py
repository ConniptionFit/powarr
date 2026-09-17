"""Read-only Deezer client for Artist Discovery enrichment — artist pictures only.
Public API, no credentials, no settings UI (same precedent as musicbrainz.py).
Deezer reliably has photos for niche artists that Lidarr's metadata proxy and
Wikipedia both lack, and MusicBrainz url-rels usually carry a deezer artist link
for an exact-id lookup before falling back to name search. Never called on a hot
path — only when building display fields for a new review-queue candidate."""
from __future__ import annotations

import httpx

_API = "https://api.deezer.com"
_USER_AGENT = "Powarr/0.41.0 (https://github.com/ConniptionFit/powarr)"


def _picture(artist: dict) -> str | None:
    return artist.get("picture_medium") or artist.get("picture") or None


async def get_artist_image(deezer_id: str) -> str | None:
    """GET /artist/{id} — exact lookup via a MusicBrainz deezer url-rel."""
    if not deezer_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(f"{_API}/artist/{deezer_id}",
                                 headers={"User-Agent": _USER_AGENT})
            if r.status_code != 200:
                return None
            data = r.json()
            if data.get("error"):
                return None
            return _picture(data)
    except Exception:
        return None


async def search_artist_image(name: str) -> str | None:
    """GET /search/artist?q= — only trusted when the top hit's name matches
    exactly (case-insensitive), to avoid attaching a different artist's photo."""
    if not name:
        return None
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(f"{_API}/search/artist",
                                 params={"q": name, "limit": 1},
                                 headers={"User-Agent": _USER_AGENT})
            if r.status_code != 200:
                return None
            hits = r.json().get("data") or []
            if hits and (hits[0].get("name") or "").strip().lower() == name.strip().lower():
                return _picture(hits[0])
            return None
    except Exception:
        return None


_top_tracks_cache: dict[str, tuple[float, list[dict]]] = {}
_TOP_TRACKS_CACHE_TTL = 86400.0  # 24 hours


def clear_top_tracks_cache() -> None:
    """Test hook."""
    _top_tracks_cache.clear()


async def get_artist_top_tracks(name: str, limit: int = 5) -> list[dict]:
    """GET /artist/{id}/top?limit= — top tracks with 30s audio previews, album title and cover.
    Only trusted when Deezer search's top hit matches the requested artist name
    case-insensitively, preventing mismatched discographies on niche names.
    Cached in-process (24h hits / 1h misses)."""
    if not name:
        return []
    import time
    name_norm = name.strip().lower()
    now = time.time()
    cached = _top_tracks_cache.get(name_norm)
    if cached and now < cached[0]:
        return cached[1]

    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(f"{_API}/search/artist",
                                 params={"q": name, "limit": 1},
                                 headers={"User-Agent": _USER_AGENT})
            if r.status_code != 200:
                return []
            hits = r.json().get("data") or []
            if not hits or (hits[0].get("name") or "").strip().lower() != name_norm:
                _top_tracks_cache[name_norm] = (now + 3600.0, [])  # 1h miss cache
                return []
            artist_id = hits[0].get("id")
            if not artist_id:
                return []

            top_r = await client.get(f"{_API}/artist/{artist_id}/top",
                                     params={"limit": limit},
                                     headers={"User-Agent": _USER_AGENT})
            if top_r.status_code != 200:
                return []
            tracks_raw = top_r.json().get("data") or []
            tracks = []
            for t in tracks_raw:
                tracks.append({
                    "id": t.get("id"),
                    "title": t.get("title") or t.get("title_short") or "Unknown Track",
                    "duration": t.get("duration") or 0,
                    "preview": t.get("preview"),  # 30s mp3 URL
                    "album": (t.get("album") or {}).get("title"),
                    "album_cover": (t.get("album") or {}).get("cover_medium") or (t.get("album") or {}).get("cover"),
                })
            _top_tracks_cache[name_norm] = (now + _TOP_TRACKS_CACHE_TTL, tracks)
            return tracks
    except Exception:
        return []
