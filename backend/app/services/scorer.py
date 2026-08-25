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


def score_breakdown(item: dict, weights: ScoringWeights, *,
                    now: datetime | None = None, with_factors: bool = True) -> dict:
    """Per-factor 0–1 values + final 0–100 score (v0.31.0).

    Shared by `score_item` and deletion LLM `item_summary` so the explain prompt
    can cite concrete drivers instead of only the aggregate score.
    """
    factors: dict[str, float] = {}
    score = 0.0
    total_weight = 0.0
    # LIB-09 — both are for the bulk caller (preview_weight_change), which runs
    # this 300k+ times: `now` is hoisted so the clock is read once per run
    # instead of once per item, and `with_factors` skips building/rounding the
    # per-factor dict when only the final score is wanted. Parameters on the
    # one function rather than a second copy of the formula — the scoring
    # formula is a confirmation-gated surface and must have a single source.
    now = now or _utcnow()
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

        if with_factors:
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
        if with_factors:
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
        if with_factors:
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
        if with_factors:
            factors["release"] = round(factor, 3)
        score += w.release_date_weight * factor

    total = round((score / total_weight) * 100, 2) if total_weight else 0.0
    return {"score": total, "factors": factors, "series_watched": bool(item.get("series_watched"))}


def score_item(item: dict, weights: ScoringWeights, *,
               now: datetime | None = None) -> float:
    """
    Returns a score from 0-100. Higher = better deletion candidate.

    Watch factor (v0.30): never-watched boost only applies when *neither* the
    item nor any sibling episode in the same series (`series_watched` /
    `series_last_watched_at`) has been watched. Size uses a sqrt curve so
    mid-size files aren't over-prioritized vs huge ones. Watch decay uses
    `watch_half_life_days` instead of a hard 365-day linear ramp.
    """
    return score_breakdown(item, weights, now=now, with_factors=False)["score"]


def _series_watch_index(db, only_parent: str | None = None) -> dict[str, dict]:
    """parent_title → {watched: bool, last: datetime|None} for episode/track rows.

    One pass over the library so rescore/sync can ask "has anyone watched any
    episode of this show?" without N+1 queries. `only_parent` narrows that pass
    to a single show — the whole-library aggregate is the right shape for a
    rescore, but wasteful when one item's breakdown is being explained (LIB-08).
    """
    from app.models.media import MediaItem
    from sqlalchemy import func

    # Aggregate per parent_title among episode/track rows
    q = db.query(
        MediaItem.parent_title,
        func.coalesce(func.sum(MediaItem.watch_count), 0),
        func.max(MediaItem.last_watched_at),
    ).filter(
        MediaItem.parent_title.isnot(None),
        MediaItem.media_type.in_(("episode", "track")),
    )
    if only_parent is not None:
        q = q.filter(MediaItem.parent_title == only_parent)
    rows = q.group_by(MediaItem.parent_title).all()

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


# LIB-08 — per-factor attribution for a single item, for the UI's "why this
# score?" panel. score_breakdown() has always computed these numbers, but the
# only thing consuming them was the LLM explain prompt (media_llm), so the sole
# user-facing answer to "why 87?" required a working LLM. The arithmetic is
# already done and free; this just names the parts and reports what each one
# actually contributed to the final 0-100.
FACTOR_META: dict[str, tuple[str, str]] = {
    "watch": ("Watch history", "watch_history_weight"),
    "size": ("File size", "file_size_weight"),
    "age": ("Time in library", "file_age_weight"),
    "release": ("Release age", "release_date_weight"),
}


def score_contributions(item: dict, weights: ScoringWeights) -> dict:
    """{score, factors[], series_watched} where each factor reports the points
    it added and the most it could have added.

    `factor` is the 0-1 strength of that signal for this item; `weight` is the
    configured importance; `contribution` is what the two produced out of 100.
    Reported against `max_contribution` so a factor scoring 1.0 on a small
    weight reads as the minor influence it is, rather than looking maxed out.
    """
    bd = score_breakdown(item, weights)
    factors = bd["factors"]
    total_weight = sum(getattr(weights, attr) for key, (_lbl, attr) in FACTOR_META.items()
                       if key in factors)
    rows = []
    for key, (label, attr) in FACTOR_META.items():
        if key not in factors:
            continue  # weight is 0 — the factor is switched off, not merely low
        w = float(getattr(weights, attr) or 0)
        f = float(factors[key])
        rows.append({
            "key": key,
            "label": label,
            "factor": round(f, 3),
            "weight": w,
            "contribution": round((w * f / total_weight) * 100, 2) if total_weight else 0.0,
            "max_contribution": round((w / total_weight) * 100, 2) if total_weight else 0.0,
        })
    rows.sort(key=lambda r: r["contribution"], reverse=True)
    return {"score": bd["score"], "factors": rows, "series_watched": bd["series_watched"]}


# LIB-09 — dry-run a scoring-weight change before saving it.
#
# Changing weights silently changes which items are suggested for deletion, and
# — with auto-delete on — which ones actually go. The Non-negotiable Principles
# already gate weight changes behind explicit confirmation, but there was
# nothing to confirm *against*: no way to see that a tweak moves 1,200 more
# items above the threshold until after it had been saved and everything
# rescored. This answers that question without writing anything.
def preview_weight_change(db, proposed: ScoringWeights,
                          current: ScoringWeights | None = None,
                          profiles: ScoringProfiles | None = None,
                          sample_limit: int = 10) -> dict:
    """Compare the current scores against what `proposed` would produce.

    Read-only — scores are computed in memory and never persisted, so this is
    safe to call on every slider nudge. Loads only the columns the scorer
    reads (the over-hydration lesson from v0.89.0) and reuses one series-watch
    index rather than rebuilding it per item.
    """
    from app.models.media import MediaItem

    if profiles is None:
        profiles = load_scoring_profiles(db)
    if current is None:
        current = _load_scoring_weights(db)

    now = _utcnow()  # one clock read for the whole run, not one per item
    series_idx = _series_watch_index(db)
    rows = db.query(
        MediaItem.id, MediaItem.title, MediaItem.media_type, MediaItem.library_section,
        MediaItem.watch_count, MediaItem.last_watched_at, MediaItem.file_size,
        MediaItem.added_at, MediaItem.release_date, MediaItem.parent_title,
        MediaItem.score, MediaItem.ignored, MediaItem.protected,
        MediaItem.watch_protected, MediaItem.seeding_protected,
        MediaItem.progress_protected,
    ).filter(MediaItem.pending_delete_at.is_(None)).all()

    cur_above = new_above = 0
    cur_bytes = new_bytes = 0
    newly_above: list[dict] = []
    newly_below: list[dict] = []

    # weights_for_library builds a fresh ScoringWeights model per call via
    # merge_weights. Resolved per row that is two model constructions each, and
    # a real library has a handful of sections, not 160k — so resolve once per
    # distinct section instead. (Measured: 7.2s -> 1.9s on this library.)
    eff_cache: dict[tuple[str, str | None], ScoringWeights] = {}

    def _eff(base: ScoringWeights, tag: str, section: str | None) -> ScoringWeights:
        key = (tag, section)
        hit = eff_cache.get(key)
        if hit is None:
            hit = weights_for_library(base, profiles, section)
            eff_cache[key] = hit
        return hit

    for r in rows:
        # Mirror the eligibility filters Deletion Suggestions itself applies —
        # a count that included protected or ignored rows would not match the
        # list the user is about to see.
        eligible = not (r.ignored or r.protected or r.watch_protected
                        or r.seeding_protected or r.progress_protected)
        if not eligible:
            continue
        # The current score is already stored — `update_scoring_weights` always
        # rescores on save, so the column is by definition "the current weights
        # applied". Reading it instead of recomputing halves the scoring work
        # and, more importantly, makes the "current" side of this comparison
        # exactly the number the user is looking at on the suggestions page
        # rather than a re-derivation that could round differently.
        cur_score = float(r.score or 0.0)
        new_eff = _eff(proposed, "proposed", r.library_section)
        new_score = score_item(_item_score_dict(r, series_idx), new_eff, now=now)

        was_above = cur_score >= current.min_score_threshold
        now_above = new_score >= proposed.min_score_threshold
        if was_above:
            cur_above += 1
            cur_bytes += r.file_size or 0
        if now_above:
            new_above += 1
            new_bytes += r.file_size or 0
        if now_above and not was_above:
            newly_above.append({"id": r.id, "title": r.title, "media_type": r.media_type,
                                "score_before": round(cur_score, 2),
                                "score_after": round(new_score, 2)})
        elif was_above and not now_above:
            newly_below.append({"id": r.id, "title": r.title, "media_type": r.media_type,
                                "score_before": round(cur_score, 2),
                                "score_after": round(new_score, 2)})

    # Biggest movers first — the most useful few, not an unbounded dump.
    newly_above.sort(key=lambda d: d["score_after"] - d["score_before"], reverse=True)
    newly_below.sort(key=lambda d: d["score_before"] - d["score_after"], reverse=True)
    return {
        "evaluated": len(rows),
        "current": {"above_threshold": cur_above, "total_size_bytes": cur_bytes,
                    "threshold": current.min_score_threshold},
        "proposed": {"above_threshold": new_above, "total_size_bytes": new_bytes,
                     "threshold": proposed.min_score_threshold},
        "newly_above_count": len(newly_above),
        "newly_below_count": len(newly_below),
        "newly_above": newly_above[:sample_limit],
        "newly_below": newly_below[:sample_limit],
    }


def _load_scoring_weights(db) -> ScoringWeights:
    from app.models.app_setting import AppSetting
    row = db.query(AppSetting).filter_by(key="scoring_weights").first()
    if not row or not row.value:
        return ScoringWeights()
    try:
        return ScoringWeights(**json.loads(row.value))
    except (ValueError, TypeError):
        return ScoringWeights()
