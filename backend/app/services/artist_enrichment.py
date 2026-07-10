"""Artist image/bio enrichment for Artist Discovery candidates. Lidarr lookup is
primary (image, overview, genres); MusicBrainz supplies active years plus the
url-rels that drive every fallback: a Wikipedia page (direct rel, or resolved
through the far more common wikidata rel's sitelinks) for bio + thumbnail, the
Wikidata P18 Commons image, and finally a Deezer artist picture (rel id first,
exact-name search last) — Deezer covers the niche artists Lidarr's metadata
proxy and Wikipedia both come up empty for. Fail-soft throughout — enrichment
never blocks candidate creation, every field can be None.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("powarr")


def _lidarr_image(images: list[dict]) -> str | None:
    by_type = {img.get("coverType"): img.get("url") for img in (images or [])}
    return by_type.get("Poster") or by_type.get("Fanart") or by_type.get("Banner")


async def enrich(lidarr, mbid: str | None, name: str) -> dict[str, Any]:
    """lidarr: a LidarrIntegration instance, or None if Lidarr isn't configured/enabled."""
    image_url: str | None = None
    bio: str | None = None
    genres: list[str] = []
    years_active: str | None = None

    if lidarr:
        try:
            term = f"lidarr:{mbid}" if mbid else name
            results = await lidarr.lookup_artist(term)
            match = results[0] if results else None
            if match:
                image_url = _lidarr_image(match.get("images") or [])
                bio = (match.get("overview") or "").strip() or None
                genres = match.get("genres") or []
        except Exception as e:
            logger.debug(f"Artist enrichment: Lidarr lookup failed for {name}: {e}")

    if mbid and (not image_url or not bio or not years_active):
        from app.integrations import musicbrainz
        mb = await musicbrainz.get_artist(mbid)
        if mb:
            years_active = years_active or musicbrainz.life_span_text(mb)
            if not genres:
                genres = musicbrainz.genres(mb)
            if not image_url or not bio:
                from app.services import wikipedia
                wiki = musicbrainz.wikipedia_title(mb)
                wikidata_image = None
                if not wiki:
                    # Direct wikipedia rels are rare — resolve the wikidata rel's
                    # sitelinks instead (P18 doubles as another image source).
                    qid = musicbrainz.wikidata_qid(mb)
                    entity = await wikipedia.resolve_wikidata(qid) if qid else None
                    if entity:
                        wikidata_image = entity.get("image")
                        if entity.get("title"):
                            wiki = (entity["lang"], entity["title"])
                if wiki:
                    summary = await wikipedia.get_summary(*wiki)
                    if summary:
                        image_url = image_url or summary.get("thumbnail")
                        bio = bio or summary.get("extract")
                image_url = image_url or wikidata_image
            if not image_url:
                from app.integrations import deezer
                deezer_id = musicbrainz.deezer_artist_id(mb)
                if deezer_id:
                    image_url = await deezer.get_artist_image(deezer_id)

    if not image_url:
        from app.integrations import deezer
        image_url = await deezer.search_artist_image(name)

    return {"image_url": image_url, "bio": bio, "genres": genres, "years_active": years_active}
