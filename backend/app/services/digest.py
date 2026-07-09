"""Weekly ntfy digest builder (v0.29.0, Approved Queue #15)."""
from datetime import datetime, timedelta

from app.models.failed_import import FailedImport
from app.models.deletion_log import DeletionLog
from app.models.media import MediaItem
from app.models.app_setting import AppSetting
from app.schemas.settings import ScoringWeights
import json
from sqlalchemy import func


def build_digest_message(db) -> str:
    week_ago = datetime.utcnow() - timedelta(days=7)
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

    row = db.query(AppSetting).filter_by(key="scoring_weights").first()
    weights = ScoringWeights(**json.loads(row.value)) if row and row.value else ScoringWeights()
    candidates = db.query(MediaItem).filter(
        MediaItem.score >= weights.min_score_threshold,
        MediaItem.ignored.is_(False),
        MediaItem.protected.isnot(True),
        MediaItem.watch_protected.isnot(True),
        MediaItem.pending_delete_at.is_(None),
    ).count()

    deleted_7d, freed_7d = db.query(
        func.count(DeletionLog.id),
        func.coalesce(func.sum(DeletionLog.file_size), 0),
    ).filter(DeletionLog.deleted_at >= week_ago).one()
    freed_gb = round(int(freed_7d) / (1024 ** 3), 2)

    lines = [
        f"Failed imports open: {suggested} suggested, {resolve_failed} push failures",
        f"Last 7d imports: {auto_7d} auto-resolved, {accepted_7d} accepted, {orphaned_7d} orphaned",
        f"Deletion candidates above threshold: {candidates}",
        f"Last 7d deletions: {deleted_7d} items, {freed_gb} GB freed",
    ]
    return "\n".join(lines)
