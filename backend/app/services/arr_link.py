"""Automatic *arr ID linking: match Sonarr/Radarr/Lidarr library entries to Plex
media items by normalized title (+year for movies) so deletion propagation has
real IDs to act on. Runs after every Plex sync; only fills missing IDs, never
overwrites an existing link."""
import logging

from app.models.integration import Integration
from app.models.media import MediaItem
from app.services.import_matcher import _normalize

logger = logging.getLogger("powarr")


async def link_arr_ids(db) -> dict:
    counts = {"radarr": 0, "sonarr": 0, "lidarr": 0}

    # Radarr → movies, matched by normalized title + year (±1)
    row = db.query(Integration).filter_by(name="radarr", enabled=True).first()
    if row and row.url and row.api_key:
        try:
            from app.integrations.radarr import RadarrIntegration
            movies = await RadarrIntegration(row.url, row.api_key).get_movies()
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
            series = await SonarrIntegration(row.url, row.api_key).get_series()
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
            artists = await LidarrIntegration(row.url, row.api_key).get_artists()
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
