"""Storage forecast: how fast the library is growing net of cleanup, and (when
the caller supplies a capacity) how long until it fills.

Computed from synced local tables only, like library health, so it is cheap and
cannot hang on an integration. Powarr does not know the size of the underlying
disk (Plex does not report it and the *arr disk-space calls are per root
folder), so capacity is an input rather than something we guess at.

Growth = bytes of leaf media (movie/episode/track) by `added_at`, minus bytes
logged in the deletion log, averaged over the lookback window. Container rows
(show/artist/album) are excluded so nothing is counted twice.
"""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import Date, cast, func
from sqlalchemy.orm import Session

from app.models.deletion_log import DeletionLog
from app.models.media import MediaItem

LEAF_TYPES = ("movie", "episode", "track")
# Longest horizon we will report; beyond this "no fill date" is more honest
# than a number extrapolated from a few weeks of history.
MAX_HORIZON_DAYS = 3650


def _day_expr(db: Session, column):
    if db.get_bind().dialect.name == "sqlite":
        return func.date(column)
    return cast(column, Date)


def _by_day(db: Session, column, size_col, extra_filter, cutoff) -> dict:
    day = _day_expr(db, column)
    rows = (db.query(day, func.coalesce(func.sum(size_col), 0))
            .filter(column >= cutoff, *extra_filter)
            .group_by(day).all())
    return {str(d): int(b) for d, b in rows if d}


def project(current_bytes: int, net_per_day: float,
            capacity_bytes: Optional[int]) -> dict:
    """Pure projection from a net daily growth rate. Split out so the maths
    is testable without a database."""
    out = {"days_until_full": None, "full_date": None, "used_pct": None,
           "status": "no_capacity"}
    if not capacity_bytes or capacity_bytes <= 0:
        return out
    out["used_pct"] = round(current_bytes / capacity_bytes * 100, 1)
    if current_bytes >= capacity_bytes:
        out.update(days_until_full=0, full_date=datetime.utcnow().date().isoformat(),
                   status="full")
    elif net_per_day <= 0:
        out["status"] = "shrinking" if net_per_day < 0 else "flat"
    else:
        days = (capacity_bytes - current_bytes) / net_per_day
        if days > MAX_HORIZON_DAYS:
            out["status"] = "distant"
        else:
            d = int(days)
            out.update(days_until_full=d, status="growing",
                       full_date=(datetime.utcnow().date() + timedelta(days=d)).isoformat())
    return out


def build_forecast(db: Session, days: int = 90,
                   capacity_bytes: Optional[int] = None) -> dict:
    now = datetime.utcnow()
    cutoff = now - timedelta(days=days)
    active = MediaItem.pending_delete_at.is_(None)
    leaf = MediaItem.media_type.in_(LEAF_TYPES)

    current = int(db.query(func.coalesce(func.sum(MediaItem.file_size), 0))
                  .filter(active, leaf).scalar() or 0)
    added = _by_day(db, MediaItem.added_at, MediaItem.file_size, (active, leaf), cutoff)
    removed = _by_day(db, DeletionLog.deleted_at, DeletionLog.file_size, (), cutoff)

    start = (now - timedelta(days=days - 1)).date()
    points = []
    for i in range(days):
        key = str(start + timedelta(days=i))
        points.append({"date": key, "added": added.get(key, 0),
                       "removed": removed.get(key, 0)})
    total_added = sum(p["added"] for p in points)
    total_removed = sum(p["removed"] for p in points)
    net_per_day = (total_added - total_removed) / days

    result = {
        "days": days,
        "current_bytes": current,
        "added_bytes": total_added,
        "removed_bytes": total_removed,
        "net_bytes_per_day": int(net_per_day),
        "capacity_bytes": capacity_bytes,
        "points": points,
    }
    result.update(project(current, net_per_day, capacity_bytes))
    return result
