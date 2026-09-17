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


_GOOD_RESOLUTIONS = {"accepted", "auto_resolved"}
# "orphaned" is deliberately NOT in here — real finding from testing this
# against live data: the resolution reflects whether the download's files
# were still there when the row closed, not whether the LLM's match
# judgment was right. An orphaned row can happen after a perfectly correct
# agree — the download client removed the torrent for reasons that have
# nothing to do with match quality. Scoring it as "the LLM was wrong" would
# conflate two unrelated failure modes and produce a misleading accuracy
# number (an early version of this code did exactly that: 47 real logged
# calls, 100% agree rate, but only an 8.5% "outcome match" — because 43 of
# them were orphaned, not because the matches were bad).
_BAD_RESOLUTIONS = {"rejected"}


def _group_stats(rows: list[LlmMatchLog]) -> dict:
    """LLM-06 — aggregate one group of log rows into the numbers the accuracy
    dashboard shows. `outcome_agreement_rate` is the closest thing to a real
    accuracy signal this app has: of calls with BOTH a parsed verdict and a
    closed ground-truth resolution that actually reflects match quality
    (accepted/auto_resolved/rejected — NOT orphaned, see _BAD_RESOLUTIONS),
    how often did "agrees" line up with the row eventually being accepted/
    auto-resolved (agrees=True) or rejected (agrees=False)? It's a proxy, not ground truth on the LLM's
    reasoning — a right verdict for the wrong reason still counts as agreement
    here — but it's the only closed-loop signal available without a human
    labeling pass."""
    total = len(rows)
    if total == 0:
        return {
            "total": 0, "parse_ok_rate": None, "agree_rate": None,
            "enforced_rate": None, "avg_latency_ms": None,
            "outcome_agreement_rate": None, "outcome_sample_size": 0,
            "resolution_breakdown": {},
        }
    parsed = [r for r in rows if r.parse_ok]
    agreeing = [r for r in parsed if r.agrees is True]
    enforced = [r for r in parsed if r.enforced]
    latencies = [r.latency_ms for r in rows if r.latency_ms is not None]

    resolution_breakdown: dict[str, int] = {}
    for r in rows:
        if r.resolution:
            resolution_breakdown[r.resolution] = resolution_breakdown.get(r.resolution, 0) + 1

    scored = [r for r in parsed if r.agrees is not None and r.resolution in _GOOD_RESOLUTIONS | _BAD_RESOLUTIONS]
    correct = sum(1 for r in scored
                 if (r.agrees and r.resolution in _GOOD_RESOLUTIONS)
                 or (not r.agrees and r.resolution in _BAD_RESOLUTIONS))

    return {
        "total": total,
        "parse_ok_rate": round(len(parsed) / total, 3),
        "agree_rate": round(len(agreeing) / len(parsed), 3) if parsed else None,
        "enforced_rate": round(len(enforced) / len(parsed), 3) if parsed else None,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "outcome_agreement_rate": round(correct / len(scored), 3) if scored else None,
        "outcome_sample_size": len(scored),
        "resolution_breakdown": resolution_breakdown,
    }


def compute_accuracy_stats(db, days: int | None = None) -> dict:
    """LLM-06 — in-app accuracy dashboard data, grouped by source_app/model/
    scaffold_version. CSV export (llm-log/export.csv) is raw rows for offline
    replay; this is the same data pre-aggregated for at-a-glance tuning."""
    q = db.query(LlmMatchLog)
    if days:
        cutoff = datetime.utcnow() - timedelta(days=days)
        q = q.filter(LlmMatchLog.created_at >= cutoff)
    rows = q.all()

    def _by(attr: str) -> list[dict]:
        groups: dict[str, list[LlmMatchLog]] = {}
        for r in rows:
            key = getattr(r, attr) or "(unknown)"
            groups.setdefault(key, []).append(r)
        return [{"key": k, **_group_stats(v)} for k, v in sorted(groups.items())]

    return {
        "overall": _group_stats(rows),
        "by_source_app": _by("source_app"),
        "by_model": _by("model"),
        "by_scaffold_version": _by("scaffold_version"),
    }


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
