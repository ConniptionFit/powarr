"""Background maintenance loop: scheduled Plex sync + soft-delete purge.
Runs alongside (not inside) the failed-import poller; checks due-ness every 5 minutes."""
import asyncio
import json
import logging
from datetime import datetime, timedelta

from app.database import SessionLocal
from app.models.app_setting import AppSetting
from app.models.media import MediaItem
from app.schemas.settings import CleanupSettings, SyncSettings

logger = logging.getLogger("powarr")

CHECK_INTERVAL = 300


def _get_setting(db, key: str, schema):
    row = db.query(AppSetting).filter_by(key=key).first()
    if not row or not row.value:
        return schema()
    return schema(**json.loads(row.value))


async def _purge_due_soft_deletes(db) -> int:
    cleanup = _get_setting(db, "cleanup", CleanupSettings)
    if cleanup.soft_delete_days <= 0:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=cleanup.soft_delete_days)
    due = db.query(MediaItem).filter(
        MediaItem.pending_delete_at.isnot(None),
        MediaItem.pending_delete_at < cutoff,
    ).all()
    if not due:
        return 0
    from app.services.deleter import propagate_and_delete
    purged = 0
    for item in due:
        try:
            await propagate_and_delete(item, db)
            purged += 1
        except Exception as e:
            logger.error(f"Soft-delete purge failed for '{item.title}': {e}")
    db.commit()
    if purged:
        logger.info(f"Soft-delete purge: {purged} item(s) deleted after {cleanup.soft_delete_days}-day window")
    return purged


async def _scheduled_plex_sync(db) -> None:
    sync_cfg = _get_setting(db, "sync", SyncSettings)
    if sync_cfg.plex_sync_interval_hours <= 0:
        return
    row = db.query(AppSetting).filter_by(key="last_synced").first()
    if row and row.value:
        try:
            last = datetime.fromisoformat(row.value)
            if datetime.utcnow() - last < timedelta(hours=sync_cfg.plex_sync_interval_hours):
                return
        except ValueError:
            pass
    from app.services.plex_sync import run_plex_sync
    result = await run_plex_sync(db)
    logger.info(f"Scheduled Plex sync: {result}")


async def maintenance_loop():
    logger.info("Maintenance scheduler started")
    while True:
        try:
            db = SessionLocal()
            try:
                await _purge_due_soft_deletes(db)
                await _scheduled_plex_sync(db)
            finally:
                db.close()
        except asyncio.CancelledError:
            logger.info("Maintenance scheduler stopped")
            raise
        except Exception as e:
            logger.error(f"Maintenance cycle failed: {e}")
        await asyncio.sleep(CHECK_INTERVAL)
