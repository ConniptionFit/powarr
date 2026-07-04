from datetime import datetime, timezone
from app.schemas.settings import ScoringWeights


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def score_item(item: dict, weights: ScoringWeights) -> float:
    """
    Returns a score from 0-100. Higher = better deletion candidate.
    Each enabled factor contributes proportionally to its weight.
    """
    score = 0.0
    total_weight = 0.0
    now = _utcnow()

    w = weights

    # --- Watch history factor ---
    if w.watch_history_weight > 0:
        total_weight += w.watch_history_weight
        watch_count = item.get("watch_count", 0) or 0
        last_watched = item.get("last_watched_at")

        if watch_count == 0:
            factor = 1.0 * w.never_watched_boost
            factor = min(factor, 1.0)
        elif last_watched:
            if isinstance(last_watched, str):
                last_watched = datetime.fromisoformat(last_watched)
            days_since = (now - last_watched.replace(tzinfo=None)).days
            factor = min(days_since / 365.0, 1.0)
        else:
            factor = 0.5

        score += w.watch_history_weight * factor

    # --- File size factor (larger = higher priority) ---
    if w.file_size_weight > 0:
        total_weight += w.file_size_weight
        file_size = item.get("file_size", 0) or 0
        max_bytes = w.max_size_gb_reference * 1024 ** 3
        factor = min(file_size / max_bytes, 1.0) if max_bytes > 0 else 0.0
        score += w.file_size_weight * factor

    # --- File age factor (older added_at = higher priority) ---
    if w.file_age_weight > 0:
        total_weight += w.file_age_weight
        added_at = item.get("added_at")
        if added_at:
            if isinstance(added_at, str):
                added_at = datetime.fromisoformat(added_at)
            days_old = (now - added_at.replace(tzinfo=None)).days
            factor = min(days_old / w.max_age_days_reference, 1.0)
        else:
            factor = 0.0
        score += w.file_age_weight * factor

    # --- Release date factor (older release = higher priority) ---
    if w.release_date_weight > 0:
        total_weight += w.release_date_weight
        release_date = item.get("release_date")
        if release_date:
            if isinstance(release_date, str):
                release_date = datetime.fromisoformat(release_date)
            years_old = (now - release_date.replace(tzinfo=None)).days / 365.0
            factor = min(years_old / w.max_release_age_years_reference, 1.0)
        else:
            factor = 0.0
        score += w.release_date_weight * factor

    if total_weight == 0:
        return 0.0

    return round((score / total_weight) * 100, 2)


def rescore_all(db, weights: ScoringWeights):
    from app.models.media import MediaItem

    items = db.query(MediaItem).all()
    for item in items:
        item.score = score_item(
            {
                "watch_count": item.watch_count,
                "last_watched_at": item.last_watched_at,
                "file_size": item.file_size,
                "added_at": item.added_at,
                "release_date": item.release_date,
            },
            weights,
        )
    db.commit()
    return len(items)
