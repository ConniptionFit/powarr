"""Jellyfin library sync. Same upsert + scoring path as the Plex sync, so a
Jellyfin library gets deletion scores, protections and *arr links exactly like
a Plex one. Plex-only steps (Tautulli multi-user and in-progress protection)
are skipped: Jellyfin has no Tautulli equivalent in Powarr."""
import asyncio
import logging
from datetime import datetime

from app.models.integration import Integration
from app.schemas.settings import CleanupSettings, ScoringWeights
from app.services.plex_sync import (
    _get_setting, _set_setting, refresh_seeding_protection, refresh_seerr_protection,
    trim_memory, upsert_media_items,
)
from app.services.scorer import load_scoring_profiles

logger = logging.getLogger("powarr")


async def run_jellyfin_sync(db) -> dict:
    row = db.query(Integration).filter_by(name="jellyfin").first()
    if not row or not row.enabled:
        raise ValueError("Jellyfin integration not enabled")
    if not row.url or not row.api_key:
        raise ValueError("Jellyfin URL and API key are required")

    from app.services import tasks
    task_id = tasks.create_task("plex_sync", "Syncing Jellyfin library")
    try:
        from app.integrations.jellyfin import JellyfinIntegration
        from app.services.secret_box import decrypt
        jf = JellyfinIntegration(row.url, decrypt(row.api_key) or "")
        weights = _get_setting(db, "scoring_weights", ScoringWeights)
        profiles = load_scoring_profiles(db)

        items = await jf.fetch_media_items()
        tasks.update_task(task_id, total=len(items))
        upserted = await asyncio.to_thread(upsert_media_items, db, items, weights, profiles, None)
        tasks.update_task(task_id, current=upserted)
        now = datetime.utcnow().isoformat()
        _set_setting(db, "last_synced_jellyfin", now)
        # The Overview "last synced" tile reads last_synced; only a
        # Jellyfin-only install should move it (a Plex sync owns it otherwise).
        plex_on = db.query(Integration).filter_by(name="plex", enabled=True).first()
        if not plex_on:
            _set_setting(db, "last_synced", now)
        db.commit()
        db.expunge_all()
        trim_memory()

        cleanup = _get_setting(db, "cleanup", CleanupSettings)
        protected = seeding = 0
        if cleanup.protect_requested:
            try:
                protected = await refresh_seerr_protection(db)
            except Exception as e:
                logger.warning(f"Seerr protection refresh failed (non-fatal): {e}")
        if cleanup.protect_seeding_torrents:
            try:
                seeding = await refresh_seeding_protection(db)
            except Exception as e:
                logger.warning(f"Seeding protection refresh failed (non-fatal): {e}")
        try:
            from app.services.arr_link import link_arr_ids
            linked = await link_arr_ids(db)
        except Exception as e:
            logger.warning(f"arr-link failed (non-fatal): {e}")
            linked = {}

        warning = ("Plex is also enabled; items in both libraries will appear twice. "
                   "Use one media server as the source." if plex_on else None)
        db.expunge_all()
        trim_memory()
        tasks.finish_task(task_id, "done", f"Synced {upserted} item(s) from Jellyfin")
        return {"synced": upserted, "protected": protected, "seeding_protected": seeding,
                "linked": linked, "warning": warning}
    except Exception as e:
        tasks.finish_task(task_id, "failed", str(e))
        raise
