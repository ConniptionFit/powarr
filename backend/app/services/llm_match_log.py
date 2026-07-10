"""LLM match-review call logging (LLM-LOG-01). record() writes one row per real
LLM reply at the two review sites (scan-time + on-demand rescore); the scheduler
calls maintain() each maintenance cycle to backfill ground-truth resolutions
from closed failed-import rows and prune on retention limits. All best-effort —
logging must never break a scan or rescore."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.models.failed_import import FailedImport
from app.models.llm_match_log import LlmMatchLog

logger = logging.getLogger("powarr")

RETENTION_DAYS = 90
MAX_ROWS = 5000

# FailedImport statuses that represent a closed/decided row — the ground-truth
# label for the LLM's verdict. suggested/resolve_failed/orphan_pending stay open.
TERMINAL_STATUSES = ("accepted", "rejected", "auto_resolved", "orphaned", "closed_external")


def record(db, *, failed_import_id: int | None, site: str, source_app: str,
           model: str, release_title: str, candidate_title: str, context: str,
           det_summary: str, capture: dict, agrees: bool | None,
           confidence_adjustment: float | None, enforced: bool,
           checks: tuple[bool, bool] | None) -> None:
    """Add one log row (no commit — rides the caller's transaction). Call only
    when capture['replied'] is set; a no-reply call has nothing to replay."""
    try:
        db.add(LlmMatchLog(
            failed_import_id=failed_import_id, site=site, source_app=source_app,
            model=model, scaffold_version=capture.get("scaffold_version"),
            prompt_hash=capture.get("prompt_hash"),
            release_title=(release_title or "")[:1000],
            candidate_title=(candidate_title or "")[:1000],
            context=(context or "")[:4000], det_summary=(det_summary or "")[:2000],
            evidence_artist_ok=checks[0] if checks else None,
            evidence_album_ok=checks[1] if checks else None,
            raw_reply=capture.get("raw"), parse_ok=capture.get("parse_ok"),
            agrees=agrees, confidence_adjustment=confidence_adjustment,
            enforced=enforced, latency_ms=capture.get("latency_ms"),
        ))
    except Exception as e:
        logger.info(f"LLM match log write failed (non-fatal): {e}")


def maintain(db) -> dict:
    """Backfill resolutions from closed failed-import rows, then prune by age and
    row cap. Idempotent; cheap at personal-library scale."""
    backfilled = 0
    try:
        open_logs = (db.query(LlmMatchLog)
                     .filter(LlmMatchLog.resolution.is_(None),
                             LlmMatchLog.failed_import_id.isnot(None)).all())
        if open_logs:
            fi_ids = {log.failed_import_id for log in open_logs}
            closed = {fi.id: fi for fi in db.query(FailedImport)
                      .filter(FailedImport.id.in_(fi_ids),
                              FailedImport.status.in_(TERMINAL_STATUSES)).all()}
            for log in open_logs:
                fi = closed.get(log.failed_import_id)
                if fi:
                    log.resolution = fi.status
                    log.resolved_at = fi.resolved_at or datetime.utcnow()
                    backfilled += 1

        cutoff = datetime.utcnow() - timedelta(days=RETENTION_DAYS)
        pruned = (db.query(LlmMatchLog).filter(LlmMatchLog.created_at < cutoff)
                  .delete(synchronize_session=False))
        count = db.query(LlmMatchLog).count()
        if count > MAX_ROWS:
            # Oldest rows beyond the cap — subquery keeps it a single DELETE.
            ids = [r.id for r in db.query(LlmMatchLog.id)
                   .order_by(LlmMatchLog.created_at.asc()).limit(count - MAX_ROWS).all()]
            pruned += (db.query(LlmMatchLog).filter(LlmMatchLog.id.in_(ids))
                       .delete(synchronize_session=False))
        db.commit()
        return {"backfilled": backfilled, "pruned": pruned}
    except Exception as e:
        db.rollback()
        logger.info(f"LLM match log maintenance failed (non-fatal): {e}")
        return {"backfilled": 0, "pruned": 0}
