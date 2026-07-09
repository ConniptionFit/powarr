"""Cleanup deletion scoring (v0.30.0).

Higher score = stronger deletion candidate. Pre-v0.30 formula archived in
Obsidian: [[Scoring System — Pre-v0.30 Backup]].
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Optional

from app.schemas.settings import ScoringWeights, ScoringProfiles


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_dt(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    return None


def merge_weights(base: ScoringWeights, overlay: dict | None) -> ScoringWeights:
    """Apply a partial overlay dict onto a ScoringWeights instance."""
    if not overlay:
        return base
    data = base.model_dump()
    for k, v in overlay.items():
        if k in data and v is not None:
            data[k] = v
    return ScoringWeights(**data)


def weights_for_library(base: ScoringWeights, profiles: ScoringProfiles | None,
                        library_section: str | None) -> ScoringWeights:
    """Resolve effective weights for a Plex library (partial overlay on default)."""
    if not profiles or not library_section:
        return base
    overlay = (profiles.by_library or {}).get(library_section)
    return merge_weights(base, overlay)


def score_breakdown(item: dict, weights: ScoringWeights) -> dict:
    """Per-factor 0–1 values + final 0–100 score (v0.31.0).

    Shared by `score_item` and deletion LLM `item_summary` so the explain prompt
    can cite concrete drivers instead of only the aggregate score.
    """
    factors: dict[str, float] = {}
    score = 0.0
    total_weight = 0.0
    now = _utcnow()
    w = weights

    # --- Watch history factor ---
    if w.watch_history_weight > 0:
        total_weight += w.watch_history_weight
        watch_count = item.get("watch_count", 0) or 0
        last_watched = _as_dt(item.get("last_watched_at"))
        series_watched = bool(item.get("series_watched"))
        series_last = _as_dt(item.get("series_last_watched_at"))

        # Effective "household has engaged with this show/album"
        engaged = watch_count > 0 or series_watched
        effective_last = last_watched or series_last

        half_life = max(1.0, float(getattr(w, "watch_half_life_days", 365.0) or 365.0))

        if not engaged:
            # Truly untouched — never-watched boost (capped)
            factor = min(1.0 * w.never_watched_boost, 1.0)
        elif effective_last:
            days_since = max(0, (now - effective_last).days)
            # Smooth decay: 0 just after watch → approaches 1.0 over ~2× half-life
            factor = min(1.0 - math.exp(-days_since / half_life), 1.0)
        else:
            # Engaged but no usable timestamp
            factor = 0.35

        factors["watch"] = round(factor, 3)
        score += w.watch_history_weight * factor

    # --- File size factor (sqrt curve — large files still win, mid-size less extreme) ---
    if w.file_size_weight > 0:
        total_weight += w.file_size_weight
        file_size = item.get("file_size", 0) or 0
        max_bytes = w.max_size_gb_reference * 1024 ** 3
        if max_bytes > 0 and file_size > 0:
            linear = min(file_size / max_bytes, 1.0)
            factor = math.sqrt(linear)
        else:
            factor = 0.0
        factors["size"] = round(factor, 3)
        score += w.file_size_weight * factor

    # --- File age factor (older added_at = higher priority) ---
    if w.file_age_weight > 0:
        total_weight += w.file_age_weight
        added_at = _as_dt(item.get("added_at"))
        if added_at and w.max_age_days_reference > 0:
            days_old = max(0, (now - added_at).days)
            factor = min(days_old / w.max_age_days_reference, 1.0)
        else:
            factor = 0.0
        factors["age"] = round(factor, 3)
        score += w.file_age_weight * factor

    # --- Release date factor (older release = higher priority) ---
    if w.release_date_weight > 0:
        total_weight += w.release_date_weight
        release_date = _as_dt(item.get("release_date"))
        if release_date and w.max_release_age_years_reference > 0:
            years_old = max(0.0, (now - release_date).days / 365.0)
            factor = min(years_old / w.max_release_age_years_reference, 1.0)
        else:
            factor = 0.0
        factors["release"] = round(factor, 3)
        score += w.release_date_weight * factor

    total = round((score / total_weight) * 100, 2) if total_weight else 0.0
    return {"score": total, "factors": factors, "series_watched": bool(item.get("series_watched"))}


def score_item(item: dict, weights: ScoringWeights) -> float:
    """
    Returns a score from 0-100. Higher = better deletion candidate.

    Watch factor (v0.30): never-watched boost only applies when *neither* the
    item nor any sibling episode in the same series (`series_watched` /
    `series_last_watched_at`) has been watched. Size uses a sqrt curve so
    mid-size files aren't over-prioritized vs huge ones. Watch decay uses
    `watch_half_life_days` instead of a hard 365-day linear ramp.
    """
    return score_breakdown(item, weights)["score"]


def _series_watch_index(db) -> dict[str, dict]:
    """parent_title → {watched: bool, last: datetime|None} for episode/track rows.

    One pass over the library so rescore/sync can ask "has anyone watched any
    episode of this show?" without N+1 queries.
    """
    from app.models.media import MediaItem
    from sqlalchemy import func

    # Aggregate per parent_title among episode/track rows
    rows = (db.query(
        MediaItem.parent_title,
        func.coalesce(func.sum(MediaItem.watch_count), 0),
        func.max(MediaItem.last_watched_at),
    ).filter(
        MediaItem.parent_title.isnot(None),
        MediaItem.media_type.in_(("episode", "track")),
    ).group_by(MediaItem.parent_title).all())

    out: dict[str, dict] = {}
    for parent, total_watches, last in rows:
        if not parent:
            continue
        out[parent] = {
            "watched": (total_watches or 0) > 0 or last is not None,
            "last": last,
        }
    return out


def _item_score_dict(item, series_idx: dict[str, dict]) -> dict:
    series = series_idx.get(item.parent_title or "") if item.parent_title else None
    return {
        "watch_count": item.watch_count,
        "last_watched_at": item.last_watched_at,
        "file_size": item.file_size,
        "added_at": item.added_at,
        "release_date": item.release_date,
        "series_watched": bool(series and series["watched"]) if item.media_type in ("episode", "track") else False,
        "series_last_watched_at": (series or {}).get("last") if item.media_type in ("episode", "track") else None,
        "library_section": item.library_section,
    }


def load_scoring_profiles(db) -> ScoringProfiles:
    from app.models.app_setting import AppSetting
    row = db.query(AppSetting).filter_by(key="scoring_profiles").first()
    if not row or not row.value:
        return ScoringProfiles()
    try:
        return ScoringProfiles(**json.loads(row.value))
    except (ValueError, TypeError):
        return ScoringProfiles()


def rescore_all(db, weights: ScoringWeights, profiles: ScoringProfiles | None = None):
    from app.models.media import MediaItem

    if profiles is None:
        profiles = load_scoring_profiles(db)
    series_idx = _series_watch_index(db)
    items = db.query(MediaItem).all()
    for item in items:
        eff = weights_for_library(weights, profiles, item.library_section)
        new_score = score_item(_item_score_dict(item, series_idx), eff)
        if new_score != item.score:
            item.llm_rationale = None
            item.llm_rationale_at = None
            item.llm_rationale_key = None
        item.score = new_score
    db.commit()
    return len(items)
