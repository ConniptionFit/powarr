"""Plex library sync + Seerr request protection, callable from the API endpoint
and the background scheduler alike."""
import json
import logging
from datetime import datetime

from app.models.app_setting import AppSetting
from app.models.integration import Integration
from app.models.media import MediaItem
from app.schemas.settings import ScoringWeights, CleanupSettings
from app.services.scorer import score_item

logger = logging.getLogger("powarr")


def _get_setting(db, key: str, schema):
    row = db.query(AppSetting).filter_by(key=key).first()
    if not row or not row.value:
        return schema()
    return schema(**json.loads(row.value))


def _set_setting(db, key: str, value: str) -> None:
    row = db.query(AppSetting).filter_by(key=key).first()
    if not row:
        row = AppSetting(key=key)
        db.add(row)
    row.value = value


async def run_plex_sync(db) -> dict:
    """Full library sync from Plex; upserts + rescores. Raises on Plex being unconfigured."""
    row = db.query(Integration).filter_by(name="plex").first()
    if not row or not row.enabled:
        raise ValueError("Plex integration not enabled")

    from app.integrations.plex import PlexIntegration
    plex = PlexIntegration(row.url, row.api_key)
    weights = _get_setting(db, "scoring_weights", ScoringWeights)

    items = await plex.fetch_media_items()
    upserted = 0
    for item_data in items:
        existing = db.query(MediaItem).filter_by(plex_rating_key=item_data["plex_rating_key"]).first()
        if existing:
            for k, v in item_data.items():
                setattr(existing, k, v)
            existing.score = score_item(item_data, weights)
        else:
            item_data["score"] = score_item(item_data, weights)
            db.add(MediaItem(**item_data))
        upserted += 1

    _set_setting(db, "last_synced", datetime.utcnow().isoformat())
    db.commit()

    cleanup = _get_setting(db, "cleanup", CleanupSettings)
    protected = 0
    if cleanup.protect_requested:
        try:
            protected = await refresh_seerr_protection(db)
        except Exception as e:
            logger.warning(f"Seerr protection refresh failed (non-fatal): {e}")

    try:
        from app.services.arr_link import link_arr_ids
        linked = await link_arr_ids(db)
    except Exception as e:
        logger.warning(f"arr-link failed (non-fatal): {e}")
        linked = {}

    return {"synced": upserted, "protected": protected, "linked": linked}


async def refresh_seerr_protection(db) -> int:
    """Mark media items matching recent Seerr requests as protected. Resets prior
    flags first so items fall out of protection when requests age out. No-op
    (returns 0) when Seerr isn't configured."""
    row = db.query(Integration).filter_by(name="seerr", enabled=True).first()
    if not row or not row.url or not row.api_key:
        return 0

    from app.integrations.seerr import SeerrIntegration
    seerr = SeerrIntegration(row.url, row.api_key)
    requested = await seerr.get_requested_titles()

    db.query(MediaItem).filter(MediaItem.protected.is_(True)).update({"protected": False})

    count = 0
    for req in requested:
        title = req["title"]
        if req["media_type"] == "movie":
            q = db.query(MediaItem).filter(MediaItem.media_type == "movie", MediaItem.title.ilike(title))
            if req["year"]:
                q = q.filter(MediaItem.year.in_((req["year"] - 1, req["year"], req["year"] + 1)))
        else:
            # TV request → protect all episodes of the show
            q = db.query(MediaItem).filter(MediaItem.media_type == "episode",
                                           MediaItem.parent_title.ilike(title))
        count += q.update({"protected": True}, synchronize_session=False)
    db.commit()
    logger.info(f"Seerr protection: {count} item(s) protected across {len(requested)} request(s)")
    return count
