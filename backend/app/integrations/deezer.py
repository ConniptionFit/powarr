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
