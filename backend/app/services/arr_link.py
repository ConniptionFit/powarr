"""Automatic *arr ID linking: match Sonarr/Radarr/Lidarr library entries to Plex
media items by normalized title (+year for movies) so deletion propagation has
real IDs to act on. Runs after every Plex sync; only fills missing IDs, never
overwrites an existing link."""
import logging

from app.models.integration import Integration
from app.models.media import MediaItem
from app.services.import_matcher import _normalize
from app.services.secret_box import decrypt

logger = logging.getLogger("powarr")

# INT-02 — the same media_type -> *arr app mapping link_arr_ids() already uses
# implicitly (movie/radarr, episode/sonarr, track/lidarr); named here so the
# manual-override endpoints and the auto-linker never drift apart.
APP_FOR_MEDIA_TYPE = {"movie": "radarr", "episode": "sonarr", "track": "lidarr"}
ID_FIELD_FOR_MEDIA_TYPE = {"movie": "radarr_id", "episode": "sonarr_id", "track": "lidarr_id"}


async def search_arr_candidates(db, media_type: str, query: str = "", limit: int = 50) -> list[dict]:
    """INT-02 — full-library search of the *arr app matching this media_type, for
    the manual ID-override UI (fixing a bad auto-link from link_arr_ids() without
    full resync gymnastics). Movie -> Radarr; episode -> Sonarr (series-level,
    same as the auto-linker); track -> Lidarr (artist-level). Case-insensitive
    substring match on title/artist name; empty query returns the whole library
    (capped at `limit`, alphabetical) for browsing. Fails soft to [] when the app
    isn't connected — never raises into a 500 for a missing/disabled integration."""
    app_name = APP_FOR_MEDIA_TYPE.get(media_type)
    if not app_name:
        return []
    row = db.query(Integration).filter_by(name=app_name, enabled=True).first()
    if not row or not row.url or not row.api_key:
        return []
    try:
        if app_name == "radarr":
            from app.integrations.radarr import RadarrIntegration
            movies = await RadarrIntegration(row.url, decrypt(row.api_key) or "").get_movies()
            candidates = [{"id": m["id"], "title": m.get("title", ""), "year": m.get("year")} for m in movies]
        elif app_name == "sonarr":
            from app.integrations.sonarr import SonarrIntegration
            series = await SonarrIntegration(row.url, decrypt(row.api_key) or "").get_series()
            candidates = [{"id": s["id"], "title": s.get("title", ""), "year": s.get("year")} for s in series]
        else:
            from app.integrations.lidarr import LidarrIntegration
            artists = await LidarrIntegration(row.url, decrypt(row.api_key) or "").get_artists()
            candidates = [{"id": a["id"], "title": a.get("artistName", ""), "year": None} for a in artists]
    except Exception as e:
        logger.warning(f"arr-link: candidate search failed for {app_name}: {e}")
        return []
    q = query.strip().lower()
    if q:
        candidates = [c for c in candidates if q in c["title"].lower()]
    candidates.sort(key=lambda c: c["title"].lower())
    return candidates[:limit]


async def link_arr_ids(db) -> dict:
    counts = {"radarr": 0, "sonarr": 0, "lidarr": 0}

    # Radarr → movies, matched by normalized title + year (±1)
    row = db.query(Integration).filter_by(name="radarr", enabled=True).first()
    if row and row.url and row.api_key:
        try:
            from app.integrations.radarr import RadarrIntegration
            movies = await RadarrIntegration(row.url, decrypt(row.api_key) or "").get_movies()
            index = {(_normalize(m.get("title", "")), m.get("year")): m["id"] for m in movies}
            for item in db.query(MediaItem).filter(MediaItem.media_type == "movie",
                                                   MediaItem.radarr_id.is_(None)).all():
                key = _normalize(item.title)
                for year in (item.year, (item.year or 0) - 1, (item.year or 0) + 1):
                    if (key, year) in index:
                        item.radarr_id = index[(key, year)]
                        counts["radarr"] += 1
                        break
        except Exception as e:
            logger.warning(f"arr-link: radarr matching failed: {e}")

    # Sonarr → episodes, matched by show name (parent_title) → series id
    row = db.query(Integration).filter_by(name="sonarr", enabled=True).first()
    if row and row.url and row.api_key:
        try:
            from app.integrations.sonarr import SonarrIntegration
            series = await SonarrIntegration(row.url, decrypt(row.api_key) or "").get_series()
            index = {_normalize(s.get("title", "")): s["id"] for s in series}
            for item in db.query(MediaItem).filter(MediaItem.media_type == "episode",
                                                   MediaItem.sonarr_id.is_(None),
                                                   MediaItem.parent_title.isnot(None)).all():
                sid = index.get(_normalize(item.parent_title))
                if sid:
                    item.sonarr_id = sid
                    counts["sonarr"] += 1
        except Exception as e:
            logger.warning(f"arr-link: sonarr matching failed: {e}")

    # Lidarr → tracks, matched by artist name (parent_title) → artist id
    row = db.query(Integration).filter_by(name="lidarr", enabled=True).first()
    if row and row.url and row.api_key:
        try:
            from app.integrations.lidarr import LidarrIntegration
            artists = await LidarrIntegration(row.url, decrypt(row.api_key) or "").get_artists()
            index = {_normalize(a.get("artistName", "")): a["id"] for a in artists}
            for item in db.query(MediaItem).filter(MediaItem.media_type == "track",
                                                   MediaItem.lidarr_id.is_(None),
                                                   MediaItem.parent_title.isnot(None)).all():
                aid = index.get(_normalize(item.parent_title))
                if aid:
                    item.lidarr_id = aid
                    counts["lidarr"] += 1
        except Exception as e:
            logger.warning(f"arr-link: lidarr matching failed: {e}")

    db.commit()
    total = sum(counts.values())
    if total:
        logger.info(f"arr-link: linked {total} item(s): {counts}")
    return counts
