"""AN-01: import funnel analytics — per-source_app conversion counts
(suggested -> accepted/auto -> verified) and a failure-reason breakdown, for
the "Sonarr failed 12/80 (15%) this week" style Overview readout.

Pure aggregation over already-persisted FailedImport rows — no new writes, no
new tracking table. The failure-reason breakdown reuses `trackedDownloadState`
(importPending/importFailed/importBlocked), already snapshotted into each
row's raw_metadata at scan-time creation (services/import_matcher.py), plus
Powarr's own `orphaned` status for files that disappeared after triage
started. Complements LLM-06's accuracy dashboard, which covers match-review
correctness rather than the funnel shape."""
import json
from datetime import datetime, timedelta

from app.models.failed_import import FailedImport

# Successful terminal outcomes vs. failure terminal outcomes — everything else
# (suggested, orphan_pending) is still open, not yet counted either way.
_SUCCESS_STATUSES = {"accepted", "auto_resolved"}
_FAILURE_STATUSES = {"rejected", "orphaned", "resolve_failed"}
_QUEUE_STUCK_STATES = ("importPending", "importFailed", "importBlocked")


def _tracked_state(row: FailedImport) -> str:
    """The *arr `trackedDownloadState` this row was stuck at when created, or
    'orphaned' if Powarr's own tracking later found the files gone, or
    'unknown' when neither is available (older rows predate the field)."""
    if row.status == "orphaned":
        return "orphaned"
    try:
        meta = json.loads(row.raw_metadata or "{}")
    except (ValueError, TypeError):
        meta = {}
    state = meta.get("trackedDownloadState")
    return state if state in _QUEUE_STUCK_STATES else "unknown"


def _group_stats(rows: list[FailedImport]) -> dict:
    total = len(rows)
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r.status] = by_status.get(r.status, 0) + 1

    accepted_or_auto = sum(by_status.get(s, 0) for s in _SUCCESS_STATUSES)
    verified = sum(1 for r in rows if r.status in _SUCCESS_STATUSES and r.verified)
    failed = sum(by_status.get(s, 0) for s in _FAILURE_STATUSES)

    reason_breakdown: dict[str, int] = {}
    for r in rows:
        if r.status not in _FAILURE_STATUSES:
            continue
        reason = _tracked_state(r)
        reason_breakdown[reason] = reason_breakdown.get(reason, 0) + 1

    return {
        "total": total,
        "suggested": by_status.get("suggested", 0),
        "accepted": by_status.get("accepted", 0),
        "auto_resolved": by_status.get("auto_resolved", 0),
        "accepted_or_auto": accepted_or_auto,
        "verified": verified,
        "verified_rate": round(verified / accepted_or_auto, 3) if accepted_or_auto else None,
        "rejected": by_status.get("rejected", 0),
        "orphaned": by_status.get("orphaned", 0),
        "resolve_failed": by_status.get("resolve_failed", 0),
        "closed_external": by_status.get("closed_external", 0),
        "failed": failed,
        "failed_rate": round(failed / total, 3) if total else None,
        "failure_reason_breakdown": reason_breakdown,
    }


def compute_import_funnel(db, days: int | None = None) -> dict:
    """Per-source_app funnel stats over FailedImport rows created in the last
    `days` (None = all time), plus an overall total across apps."""
    q = db.query(FailedImport)
    if days is not None:
        cutoff = datetime.utcnow() - timedelta(days=days)
        q = q.filter(FailedImport.created_at >= cutoff)
    rows = q.all()

    by_app: dict[str, list[FailedImport]] = {}
    for r in rows:
        by_app.setdefault(r.source_app or "(unknown)", []).append(r)

    return {
        "days": days,
        "overall": _group_stats(rows),
        "by_app": [
            {"app": app, **_group_stats(app_rows)}
            for app, app_rows in sorted(by_app.items())
        ],
    }
