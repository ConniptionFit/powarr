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


def passes_auto_thresholds(heuristic: float | None, llm: float | None,
                           cfg: ImportMatchingSettings) -> bool:
    """Dual-signal auto-import gate (v0.44.0). Pure, unit-tested.

    The algorithm leg compares the deterministic (heuristic) confidence against
    high_confidence_threshold; the LLM leg compares llm_confidence against
    llm_auto_threshold. auto_import_mode picks which leg(s) must pass. A missing
    LLM score fails the LLM leg (never treated as a pass)."""
    algo_ok = heuristic is not None and float(heuristic) >= cfg.high_confidence_threshold
    llm_ok = llm is not None and float(llm) >= cfg.llm_auto_threshold
    mode = (cfg.auto_import_mode or "either").lower()
    if mode == "algorithm":
        return algo_ok
    if mode == "llm":
        return llm_ok
    if mode == "both":
        return algo_ok and llm_ok
    return algo_ok or llm_ok  # "either" (default) — also any unknown value


def _threshold_clause(cfg: ImportMatchingSettings):
    """SQL twin of passes_auto_thresholds. Pre-v0.44 rows without a stored
    heuristic_confidence fall back to the blended confidence (the only score
    those rows ever had); NULL llm_confidence is SQL-false, matching the
    missing-LLM-score rule."""
    from sqlalchemy import and_, func, or_
    algo = func.coalesce(FailedImport.heuristic_confidence,
                         FailedImport.confidence) >= cfg.high_confidence_threshold
    llm = FailedImport.llm_confidence >= cfg.llm_auto_threshold
    mode = (cfg.auto_import_mode or "either").lower()
    if mode == "algorithm":
        return algo
    if mode == "llm":
        return llm
    if mode == "both":
        return and_(algo, llm)
    return or_(algo, llm)


def auto_eligible_query(db: Session, cfg: ImportMatchingSettings | None = None):
    """Rows that Process N Items / auto-batch-accept may push.

    Requires auto_resolve_enabled and the auto_import_mode threshold gate
    (passes_auto_thresholds). Returns an empty query when auto-resolve is off.
    """
    cfg = cfg or load_import_matching(db)
    q = db.query(FailedImport).filter(FailedImport.id == -1)  # empty by default
    if not cfg.auto_resolve_enabled:
        return q
    return (
        db.query(FailedImport)
        .filter(
            FailedImport.status.in_(AUTO_ELIGIBLE_STATUSES),
            _threshold_clause(cfg),
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
    heuristic = item.heuristic_confidence if item.heuristic_confidence is not None \
        else item.confidence
    return passes_auto_thresholds(heuristic, item.llm_confidence, cfg)
