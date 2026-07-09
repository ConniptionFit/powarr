"""Helpers for threshold-eligible failed-import batch processing (v0.28.0).

Pure query/filter logic so the Process N Items button and the batch-accept
path agree on the same rules without the frontend inventing a threshold.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting
from app.models.failed_import import FailedImport
from app.schemas.settings import ImportMatchingSettings

AUTO_ELIGIBLE_STATUSES = ("suggested", "resolve_failed")


def load_import_matching(db: Session) -> ImportMatchingSettings:
    row = db.query(AppSetting).filter_by(key="import_matching").first()
    if not row or not row.value:
        return ImportMatchingSettings()
    import json
    try:
        return ImportMatchingSettings(**json.loads(row.value))
    except Exception:
        return ImportMatchingSettings()


def auto_eligible_query(db: Session, cfg: ImportMatchingSettings | None = None):
    """Rows that Process N Items / auto-batch-accept may push.

    Requires auto_resolve_enabled and confidence >= high_confidence_threshold.
    Returns an empty query when auto-resolve is off.
    """
    cfg = cfg or load_import_matching(db)
    q = db.query(FailedImport).filter(FailedImport.id == -1)  # empty by default
    if not cfg.auto_resolve_enabled:
        return q
    return (
        db.query(FailedImport)
        .filter(
            FailedImport.status.in_(AUTO_ELIGIBLE_STATUSES),
            FailedImport.confidence >= cfg.high_confidence_threshold,
            FailedImport.matched_id.isnot(None),
        )
        .order_by(FailedImport.created_at.desc())
    )


def list_auto_eligible_ids(db: Session, cfg: ImportMatchingSettings | None = None) -> list[int]:
    return [r.id for r in auto_eligible_query(db, cfg).all()]


def is_auto_eligible(item: FailedImport, cfg: ImportMatchingSettings) -> bool:
    """Pure predicate for unit tests — mirrors auto_eligible_query filters."""
    if not cfg.auto_resolve_enabled:
        return False
    if item.status not in AUTO_ELIGIBLE_STATUSES:
        return False
    if item.matched_id is None:
        return False
    return float(item.confidence or 0) >= cfg.high_confidence_threshold
