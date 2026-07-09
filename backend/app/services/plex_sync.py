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


def upsert_media_items(db, items: list[dict], weights: ScoringWeights,
                       progress=None) -> int:
    """Upsert Plex items by plex_rating_key + rescore. Loads every existing row's
    key in ONE query into a dict instead of a SELECT per item — on a large library
    that turns tens of thousands of round trips into one. progress(n) is called
    (if given) every 50 items and at the end for the task-tracker countdown."""
    existing_by_key = {m.plex_rating_key: m for m in db.query(MediaItem).all()}
    total = len(items)
    upserted = 0
    for item_data in items:
        key = item_data["plex_rating_key"]
        existing = existing_by_key.get(key)
        if existing:
            for k, v in item_data.items():
                setattr(existing, k, v)
            existing.score = score_item(item_data, weights)
        else:
            data = dict(item_data)
            data["score"] = score_item(item_data, weights)
            new_item = MediaItem(**data)
            db.add(new_item)
            # Guard against the same rating key appearing twice in one payload.
            existing_by_key[key] = new_item
        upserted += 1
        if progress and (upserted % 50 == 0 or upserted == total):
            progress(upserted)
    return upserted


async def run_plex_sync(db) -> dict:
    """Full library sync from Plex; upserts + rescores. Raises on Plex being unconfigured."""
    row = db.query(Integration).filter_by(name="plex").first()
    if not row or not row.enabled:
        raise ValueError("Plex integration not enabled")

    from app.services import tasks
    task_id = tasks.create_task("plex_sync", "Syncing Plex library")
    try:
        from app.integrations.plex import PlexIntegration
        plex = PlexIntegration(row.url, row.api_key)
        weights = _get_setting(db, "scoring_weights", ScoringWeights)

        items = await plex.fetch_media_items()
        tasks.update_task(task_id, total=len(items))
        upserted = upsert_media_items(
            db, items, weights,
            progress=lambda n: tasks.update_task(task_id, current=n))

        _set_setting(db, "last_synced", datetime.utcnow().isoformat())
        db.commit()

        cleanup = _get_setting(db, "cleanup", CleanupSettings)
        protected = 0
        watch_protected = 0
        if cleanup.protect_requested:
            tasks.update_task(task_id, message="Refreshing Seerr protection…")
            try:
                protected = await refresh_seerr_protection(db)
            except Exception as e:
                logger.warning(f"Seerr protection refresh failed (non-fatal): {e}")
        if cleanup.protect_other_users:
            tasks.update_task(task_id, message="Refreshing Tautulli multi-user protection…")
            try:
                watch_protected = await refresh_tautulli_watch_protection(db)
            except Exception as e:
                logger.warning(f"Tautulli watch protection refresh failed (non-fatal): {e}")

        tasks.update_task(task_id, message="Linking *arr IDs…")
        try:
            from app.services.arr_link import link_arr_ids
            linked = await link_arr_ids(db)
        except Exception as e:
            logger.warning(f"arr-link failed (non-fatal): {e}")
            linked = {}

        tasks.finish_task(task_id, "done", f"Synced {upserted} item(s)")
        return {"synced": upserted, "protected": protected,
                "watch_protected": watch_protected, "linked": linked}
    except Exception as e:
        tasks.finish_task(task_id, "failed", str(e))
        raise


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


async def refresh_tautulli_watch_protection(db) -> int:
    """Mark items watched by another Tautulli user within N days as watch_protected.

    Uses one get_history call (not per-item stats). Resets prior watch_protected
    flags first. Primary user's watches (primary_tautulli_user) never protect.
    No-op when Tautulli isn't configured or the toggle is off.
    """
    cleanup = _get_setting(db, "cleanup", CleanupSettings)
    if not cleanup.protect_other_users:
        return 0
    row = db.query(Integration).filter_by(name="tautulli", enabled=True).first()
    if not row or not row.url or not row.api_key:
        return 0

    from app.integrations.tautulli import TautulliIntegration
    tautulli = TautulliIntegration(row.url, row.api_key)
    history = await tautulli.get_recent_history(days=cleanup.other_user_watch_days)
    primary = (cleanup.primary_tautulli_user or "").strip().lower()

    protect_keys: set[str] = set()
    for h in history:
        name = (h.get("friendly_name") or h.get("user") or "").strip().lower()
        if primary and name == primary:
            continue
        if not name:
            continue
        rk = h.get("rating_key")
        if rk:
            protect_keys.add(str(rk))

    db.query(MediaItem).filter(MediaItem.watch_protected.is_(True)).update(
        {"watch_protected": False}, synchronize_session=False)

    count = 0
    if protect_keys:
        # Chunk IN() to keep the query size sane on large libraries
        keys = list(protect_keys)
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            count += (db.query(MediaItem)
                      .filter(MediaItem.plex_rating_key.in_(chunk))
                      .update({"watch_protected": True}, synchronize_session=False))
    db.commit()
    logger.info(f"Tautulli watch protection: {count} item(s) protected "
                f"from {len(protect_keys)} rating key(s) in history "
                f"(last {cleanup.other_user_watch_days}d)")
    return count
