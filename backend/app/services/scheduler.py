"""Background maintenance loop: scheduled Plex sync + soft-delete purge.
Runs alongside (not inside) the failed-import poller; checks due-ness every 5 minutes."""
import asyncio
import json
import logging
from datetime import datetime, timedelta

from app.database import SessionLocal
from app.models.app_setting import AppSetting
from app.models.media import MediaItem
from app.schemas.settings import (
    CleanupSettings, SyncSettings, LlmScheduleSettings, BackupSettings,
    NotificationSettings, SmartPlaylistSettings, ArtistDiscoverySettings,
    ImportMatchingSettings,
)

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
            await propagate_and_delete(item, db, delete_mode=item.pending_delete_mode)
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


def in_quiet_hours(hour: int, start: int, end: int) -> bool:
    """Pure hour-of-day window check (0-23), wrapping past midnight when
    end <= start (e.g. start=22, end=6 covers 22:00-05:59). end == start means
    the window is the single hour `start` only, never a full-day window —
    matches the plain-English "quiet hours are 1am to 1am" reading of an empty
    span rather than silently meaning "always on"."""
    start, end, hour = start % 24, end % 24, hour % 24
    if start == end:
        return hour == start
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


async def _scheduled_llm_run(db) -> None:
    cfg = _get_setting(db, "llm_schedule", LlmScheduleSettings)
    if not cfg.enabled:
        return
    if cfg.mode == "quiet_hours" and not in_quiet_hours(
        datetime.utcnow().hour, cfg.quiet_hours_start, cfg.quiet_hours_end
    ):
        return
    remaining = max(0, cfg.max_items_per_pass)
    if remaining == 0:
        return
    from app.services import llm_assist
    if llm_assist.slot_active():
        return  # an on-demand run (or a previous scheduled pass) is active — try next cycle
    if cfg.scan_imports:
        from app.services.import_matcher import llm_rescore
        result = await llm_rescore(ids=None, limit=remaining)
        remaining -= result.get("scored", 0) + result.get("skipped", 0)
    if cfg.scan_media and remaining > 0:
        from app.services.media_llm import llm_media_run
        await llm_media_run(ids=None, limit=remaining)


async def _scheduled_backup(db) -> None:
    cfg = _get_setting(db, "backup", BackupSettings)
    if not cfg.enabled or cfg.interval_hours <= 0:
        return
    row = db.query(AppSetting).filter_by(key="last_backup").first()
    if row and row.value:
        try:
            last = datetime.fromisoformat(row.value)
            if datetime.utcnow() - last < timedelta(hours=cfg.interval_hours):
                return
        except ValueError:
            pass
    from app.services.backup import run_backup, prune_backups
    result = await run_backup()
    if not result["ok"]:
        logger.error(f"Scheduled backup failed: {result['message']}")
        return
    if not row:
        row = AppSetting(key="last_backup")
        db.add(row)
    row.value = datetime.utcnow().isoformat()
    db.commit()
    pruned = prune_backups(cfg.retention_count)
    logger.info(f"Scheduled backup: {result['message']}" + (f", pruned {pruned} old backup(s)" if pruned else ""))


async def _scheduled_malformed_audit(db) -> None:
    """FI-10 — nightly re-check of already-imported Sonarr packs for
    incomplete coverage that went unnoticed once the download left triage.
    Notify-only; never rewrites the library. Off by default (see the setting's
    own comment for why)."""
    cfg = _get_setting(db, "import_matching", ImportMatchingSettings)
    if not cfg.malformed_audit_enabled or cfg.malformed_audit_interval_hours <= 0:
        return
    row = db.query(AppSetting).filter_by(key="last_malformed_audit").first()
    if row and row.value:
        try:
            last = datetime.fromisoformat(row.value)
            if datetime.utcnow() - last < timedelta(hours=cfg.malformed_audit_interval_hours):
                return
        except ValueError:
            pass
    from app.services.malformed_audit import run_malformed_import_audit
    result = await run_malformed_import_audit(
        db, cfg.malformed_audit_lookback_days, cfg.malformed_audit_threshold)
    if not row:
        row = AppSetting(key="last_malformed_audit")
        db.add(row)
    row.value = datetime.utcnow().isoformat()
    db.commit()
    logger.info(f"Malformed-import audit: checked {result['checked']} pack(s), "
               f"flagged {result['flagged']}")
    if result["flagged"]:
        from app.services import notifier
        titles = ", ".join(f.matched_title or f.source_title for f in result["new_flags"][:5])
        more = f" (+{result['flagged'] - 5} more)" if result["flagged"] > 5 else ""
        await notifier.notify(
            db, f"Powarr: {result['flagged']} possibly-malformed import(s) found",
            f"Coverage looks incomplete for: {titles}{more}. Review in Imports → Recent Downloads.",
            tags="warning,powarr",
        )


async def _scheduled_weekly_digest(db) -> None:
    """One ntfy summary per week when digest_enabled (Approved Queue #15)."""
    cfg = _get_setting(db, "notifications", NotificationSettings)
    if not cfg.enabled or not cfg.digest_enabled:
        return
    now = datetime.utcnow()
    if now.weekday() != cfg.digest_weekday % 7 or now.hour != cfg.digest_hour_utc % 24:
        return
    row = db.query(AppSetting).filter_by(key="last_digest").first()
    if row and row.value:
        try:
            last = datetime.fromisoformat(row.value)
            if now - last < timedelta(days=6, hours=12):
                return  # already sent this week
        except ValueError:
            pass
    from app.services.digest import build_digest_message
    from app.services import notifier
    msg = build_digest_message(db)
    ok = await notifier.notify(db, "Powarr weekly digest", msg, tags="calendar,powarr")
    if not ok:
        logger.info("Weekly digest: ntfy push skipped or failed")
        return
    if not row:
        row = AppSetting(key="last_digest")
        db.add(row)
    row.value = now.isoformat()
    db.commit()
    logger.info("Weekly digest sent")


async def _scheduled_playlist_generation(db) -> None:
    """Scheduled Smart Playlists generation with optional auto-add (MOD-01b, v0.35+)."""
    cfg = _get_setting(db, "smart_playlists", SmartPlaylistSettings)
    if not cfg.enabled or not cfg.schedule_enabled:
        return
    from app.services.playlist_generator import run_scheduled_generation
    result = await run_scheduled_generation()
    if result.get("ok") and result.get("playlists", 0) > 0:
        logger.info(f"Scheduled playlist generation: {result.get('message')}")


async def _scheduled_artist_discovery(db) -> None:
    """Scheduled full discovery cycle (ingest + centroid + graph sync)."""
    cfg = _get_setting(db, "artist_discovery", ArtistDiscoverySettings)
    if not cfg.enabled or not cfg.schedule_enabled:
        return
    row = db.query(AppSetting).filter_by(key="last_artist_discovery_run").first()
    if row and row.value:
        try:
            last = datetime.fromisoformat(row.value)
            if datetime.utcnow() - last < timedelta(hours=cfg.schedule_interval_hours):
                return
        except ValueError:
            pass
    from app.services.artist_discovery import run_full_discovery_cycle
    result = await run_full_discovery_cycle(db)
    if not row:
        row = AppSetting(key="last_artist_discovery_run")
        db.add(row)
    row.value = datetime.utcnow().isoformat()
    db.commit()
    logger.info(f"Scheduled Artist Discovery run: {result.get('message')}")


async def _scheduled_artist_discovery_sync(db) -> None:
    """Scheduled differential sync (Lidarr/Last.fm stats -> Qdrant)."""
    cfg = _get_setting(db, "artist_discovery", ArtistDiscoverySettings)
    if not cfg.enabled or not cfg.sync_schedule_enabled:
        return
    row = db.query(AppSetting).filter_by(key="last_artist_discovery_sync").first()
    if row and row.value:
        try:
            last = datetime.fromisoformat(row.value)
            if datetime.utcnow() - last < timedelta(hours=cfg.sync_interval_hours):
                return
        except ValueError:
            pass
    from app.services.artist_discovery import run_differential_sync
    result = await run_differential_sync(db)
    if not row:
        row = AppSetting(key="last_artist_discovery_sync")
        db.add(row)
    row.value = datetime.utcnow().isoformat()
    db.commit()
    logger.info(f"Scheduled Artist Discovery sync: {result.get('message')}")


async def maintenance_loop():
    logger.info("Maintenance scheduler started")
    while True:
        try:
            db = SessionLocal()
            try:
                await _purge_due_soft_deletes(db)
                await _scheduled_plex_sync(db)
                await _scheduled_llm_run(db)
                await _scheduled_backup(db)
                await _scheduled_weekly_digest(db)
                await _scheduled_malformed_audit(db)
                # SP-06 — artist DB refresh before playlist auto-updates so
                # generation sees fresh Qdrant taste/connection state.
                await _scheduled_artist_discovery(db)
                await _scheduled_artist_discovery_sync(db)
                await _scheduled_playlist_generation(db)
                # AD-08 — drop enrichment art on accepted artists past retention.
                from app.services.artist_discovery import purge_stale_thumbnails
                purge_stale_thumbnails(db)
                # LLM-LOG-01: backfill ground-truth resolutions onto match-review
                # log rows + retention prune (90 days / 5k rows). Sync + cheap.
                from app.services import llm_match_log
                llm_match_log.maintain(db)
            finally:
                db.close()
        except asyncio.CancelledError:
            logger.info("Maintenance scheduler stopped")
            raise
        except Exception as e:
            logger.error(f"Maintenance cycle failed: {e}")
        await asyncio.sleep(CHECK_INTERVAL)
