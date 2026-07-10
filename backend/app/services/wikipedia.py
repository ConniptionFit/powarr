"""Wikipedia REST summary lookup — fallback bio/image source when MusicBrainz's
artist relations link out to a Wikipedia page and neither Lidarr nor MusicBrainz
itself has a usable image or description. Fail-soft, no auth, no rate limiting
needed (single lookups, not bulk)."""
from __future__ import annotations

from typing import Any

import httpx

_USER_AGENT = "Powarr/0.40.0 (https://github.com/ConniptionFit/powarr)"


async def get_summary(lang: str, title: str) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(
                f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}",
                headers={"User-Agent": _USER_AGENT},
            )
            if r.status_code != 200:
                return None
            data = r.json()
            return {
                "extract": data.get("extract"),
                "thumbnail": (data.get("thumbnail") or {}).get("source"),
            }
    except Exception:
        return None
