"""Wikipedia REST summary lookup — fallback bio/image source when MusicBrainz's
artist relations link out to a Wikipedia page and neither Lidarr nor MusicBrainz
itself has a usable image or description. Since most MusicBrainz artists only
carry a *wikidata* relation (direct wikipedia rels were deprecated upstream),
this module also resolves a Wikidata Q-id to its Wikipedia sitelink title and
P18 Commons image. Fail-soft, no auth, no rate limiting needed (single lookups,
not bulk)."""
from __future__ import annotations

import re
from typing import Any

import httpx

_USER_AGENT = "Powarr/0.41.0 (https://github.com/ConniptionFit/powarr)"


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


def _sitelink_title(sitelinks: dict[str, Any]) -> tuple[str, str] | None:
    """Prefer enwiki, else any plain language Wikipedia sitelink (xxwiki)."""
    if "enwiki" in sitelinks:
        return "en", sitelinks["enwiki"].get("title") or ""
    for key, link in sitelinks.items():
        if re.fullmatch(r"[a-z]{2,3}wiki", key) and key != "commonswiki":
            return key[:-4], link.get("title") or ""
    return None


async def resolve_wikidata(qid: str) -> dict[str, Any] | None:
    """Resolve a Wikidata Q-id to {'lang', 'title', 'image'} — the Wikipedia
    sitelink (for a summary lookup) plus the P18 Commons image if present."""
    if not qid:
        return None
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(
                f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json",
                headers={"User-Agent": _USER_AGENT},
            )
            if r.status_code != 200:
                return None
            ent = (r.json().get("entities") or {}).get(qid) or {}
            out: dict[str, Any] = {"lang": None, "title": None, "image": None}
            link = _sitelink_title(ent.get("sitelinks") or {})
            if link and link[1]:
                out["lang"], out["title"] = link[0], link[1].replace(" ", "_")
            p18 = (ent.get("claims") or {}).get("P18")
            if p18:
                filename = (((p18[0].get("mainsnak") or {}).get("datavalue") or {}).get("value") or "")
                if filename:
                    out["image"] = ("https://commons.wikimedia.org/wiki/Special:FilePath/"
                                    f"{filename.replace(' ', '_')}?width=500")
            if out["title"] or out["image"]:
                return out
            return None
    except Exception:
        return None
