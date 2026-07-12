"""Weekly ntfy digest builder (v0.29.0, Approved Queue #15; per-section toggles
+ artists/playlists sections v0.50.0)."""
from datetime import datetime, timedelta

from app.models.artist_add_log import ArtistAddLog
from app.models.failed_import import FailedImport
from app.models.deletion_log import DeletionLog
from app.models.media import MediaItem
from app.models.smart_playlist import SmartPlaylist
from app.models.app_setting import AppSetting
from app.schemas.settings import CleanupSettings, NotificationSettings, ScoringWeights
import json
from sqlalchemy import func


def _load(db, key: str, schema):
    row = db.query(AppSetting).filter_by(key=key).first()
    if not row or not row.value:
        return schema()
    return schema(**json.loads(row.value))


def build_digest_message(db) -> str:
    week_ago = datetime.utcnow() - timedelta(days=7)
    cfg = _load(db, "notifications", NotificationSettings)
    lines: list[str] = []

    if cfg.digest_include_imports:
        suggested = db.query(FailedImport).filter_by(status="suggested").count()
        resolve_failed = db.query(FailedImport).filter_by(status="resolve_failed").count()
        auto_7d = db.query(FailedImport).filter(
            FailedImport.status == "auto_resolved",
            FailedImport.resolved_at >= week_ago,
        ).count()
        accepted_7d = db.query(FailedImport).filter(
            FailedImport.status == "accepted",
            FailedImport.resolved_at >= week_ago,
        ).count()
        orphaned_7d = db.query(FailedImport).filter(
            FailedImport.status == "orphaned",
            FailedImport.resolved_at >= week_ago,
        ).count()
        lines.append(f"Failed imports open: {suggested} suggested, {resolve_failed} push failures")
        lines.append(f"Last 7d imports: {auto_7d} auto-resolved, {accepted_7d} accepted, {orphaned_7d} orphaned")

    if cfg.digest_include_artists:
        added_7d = db.query(ArtistAddLog).filter(ArtistAddLog.added_at >= week_ago).count()
        names = [r[0] for r in db.query(ArtistAddLog.artist_name)
                 .filter(ArtistAddLog.added_at >= week_ago)
                 .order_by(ArtistAddLog.added_at.desc()).limit(5).all()]
        line = f"Last 7d artists added: {added_7d}"
        if names:
            more = added_7d - len(names)
            line += " (" + ", ".join(names) + (f", +{more} more" if more > 0 else "") + ")"
        lines.append(line)

    if cfg.digest_include_playlists:
        created_7d = db.query(SmartPlaylist).filter(
            SmartPlaylist.plex_created_at.isnot(None),
            SmartPlaylist.plex_created_at >= week_ago,
        ).count()
        titles = [r[0] for r in db.query(SmartPlaylist.title)
                  .filter(SmartPlaylist.plex_created_at.isnot(None),
                         SmartPlaylist.plex_created_at >= week_ago)
                  .order_by(SmartPlaylist.plex_created_at.desc()).limit(5).all()]
        line = f"Last 7d playlists created: {created_7d}"
        if titles:
            more = created_7d - len(titles)
            line += " (" + ", ".join(titles) + (f", +{more} more" if more > 0 else "") + ")"
        lines.append(line)

    if cfg.digest_include_cleanup:
        weights = _load(db, "scoring_weights", ScoringWeights)
        cleanup = _load(db, "cleanup", CleanupSettings)
        candidates_q = db.query(MediaItem).filter(
            MediaItem.score >= weights.min_score_threshold,
            MediaItem.ignored.is_(False),
            MediaItem.protected.isnot(True),
            MediaItem.watch_protected.isnot(True),
            MediaItem.pending_delete_at.is_(None),
        )
        if cleanup.excluded_libraries:
            candidates_q = candidates_q.filter(~MediaItem.library_section.in_(cleanup.excluded_libraries))
        candidates = candidates_q.count()

        deleted_7d, freed_7d = db.query(
            func.count(DeletionLog.id),
            func.coalesce(func.sum(DeletionLog.file_size), 0),
        ).filter(DeletionLog.deleted_at >= week_ago).one()
        freed_gb = round(int(freed_7d) / (1024 ** 3), 2)
        lines.append(f"Deletion candidates above threshold: {candidates}")
        lines.append(f"Last 7d deletions: {deleted_7d} items, {freed_gb} GB freed")

    if not lines:
        lines.append("No digest sections enabled — check Settings → Notifications.")
    return "\n".join(lines)
