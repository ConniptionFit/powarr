"""LLM deletion-rationale caching + on-demand batch run for Cleanup candidates.

Mirrors the Failed Imports LLM flow: sequential, single-flight (the shared slot in
llm_assist), fail-soft, one item per call. The cache is keyed by a hash of the
prompt template, model config, and the item's scoring-relevant fields — so a
changed template, model, verbosity, or score makes the cache miss without any
explicit invalidation bookkeeping."""
import asyncio
import hashlib
import json
import logging
from datetime import datetime

from app.database import SessionLocal
from app.models.app_setting import AppSetting
from app.models.media import MediaItem
from app.schemas.settings import CleanupSettings, OllamaSettings, ScoringWeights
from app.services import llm_assist
from app.services.import_matcher import publish
from app.services.scorer import (
    _item_score_dict, _series_watch_index, load_scoring_profiles, score_breakdown,
    weights_for_library,
)

logger = logging.getLogger("powarr")


def rationale_key(ollama: OllamaSettings, item: MediaItem) -> str:
    """Cache key for a stored rationale: prompt/model config + the item fields the
    prompt is built from. Any change to either regenerates on next request."""
    payload = json.dumps({
        "template": ollama.explain_prompt,
        # Effective model for the explain task (== `model` when no override is
        # set, so pre-v0.27.0 cached rationales stay valid).
        "model": ollama.model_for("explain"),
        "api_style": ollama.api_style,
        "verbosity": ollama.verbosity,
        "model_size": ollama.model_size,
        "score": item.score,
        "watch_count": item.watch_count,
        "last_watched_at": item.last_watched_at.isoformat() if item.last_watched_at else None,
        "file_size": item.file_size,
        "library_section": getattr(item, "library_section", None),
        # Bump when item_summary shape changes so cached prose regenerates.
        "summary_v": 2,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def item_summary(item: MediaItem, db=None, *, series_idx: dict | None = None,
                  weights: ScoringWeights | None = None,
                  profiles=None) -> str:
    """Compact deletion-candidate line for the explain prompt (v0.31.0).

    Includes library section + per-factor score breakdown when a DB session (or
    preloaded series index / weights) is available; falls back to the aggregate
    score alone otherwise.
    """
    parts = [
        f"{item.title} ({item.year or 'unknown year'})",
        item.media_type or "unknown",
    ]
    if item.library_section:
        parts.append(f"library={item.library_section}")
    if item.parent_title and item.media_type in ("episode", "track"):
        parts.append(f"series={item.parent_title}")
    parts.append(f"{round((item.file_size or 0) / 1024**3, 1)} GB")
    parts.append(f"watched {item.watch_count or 0}x")
    parts.append(f"last watched {item.last_watched_at or 'never'}")

    breakdown = None
    if db is not None or (series_idx is not None and weights is not None):
        try:
            if weights is None and db is not None:
                weights = _load(db, "scoring_weights", ScoringWeights)
            if profiles is None and db is not None:
                profiles = load_scoring_profiles(db)
            if series_idx is None and db is not None:
                series_idx = _series_watch_index(db)
            eff = weights_for_library(weights, profiles, item.library_section)
            breakdown = score_breakdown(_item_score_dict(item, series_idx or {}), eff)
        except Exception as e:
            logger.info(f"item_summary breakdown unavailable: {e}")

    if breakdown:
        factors = breakdown["factors"]
        factor_bits = ", ".join(f"{k}={v:.2f}" for k, v in factors.items())
        series_note = "; series watched" if breakdown.get("series_watched") else ""
        parts.append(
            f"deletion score {breakdown['score']}/100 "
            f"(factors: {factor_bits or 'none'}{series_note})")
    else:
        parts.append(f"deletion score {item.score}/100")
    return ", ".join(parts)


async def generate_and_store(item: MediaItem, ollama: OllamaSettings, db) -> str | None:
    """One LLM call for one item; persists rationale + timestamp + cache key on
    success. Fail-soft: returns None and stores nothing on no-response."""
    rationale = await llm_assist.explain_deletion(
        ollama.host, ollama.model_for("explain"), item_summary(item, db), ollama.api_style,
        template=ollama.explain_prompt, verbosity=ollama.verbosity,
        model_size=ollama.model_size, keep_alive_minutes=ollama.keep_alive_minutes,
        **llm_assist.prompt_kwargs(ollama),
        **llm_assist.inference_kwargs(ollama))
    if rationale:
        item.llm_rationale = rationale
        item.llm_rationale_at = datetime.utcnow()
        item.llm_rationale_key = rationale_key(ollama, item)
        db.commit()
    return rationale


def _load(db, key: str, schema):
    row = db.query(AppSetting).filter_by(key=key).first()
    if not row or not row.value:
        return schema()
    return schema(**json.loads(row.value))


async def llm_media_run(ids: list[int] | None = None, limit: int = 50) -> dict:
    """On-demand LLM rationale generation for deletion candidates — the given ids,
    or the backlog of candidates without a current-key cached rationale. Sequential,
    holds the shared single-flight slot, publishes an SSE event when done."""
    if not llm_assist.acquire_slot():
        return {"scored": 0, "skipped": 0, "message": "An LLM run is already in progress"}
    from app.services import tasks
    task_id = tasks.create_task("llm_run", "Generating deletion rationales with the LLM")
    scored = skipped = 0
    try:
        db = SessionLocal()
        try:
            ollama = _load(db, "ollama", OllamaSettings)
            if not ollama.task_enabled("explain"):
                tasks.finish_task(task_id, "done", "LLM assist is not configured/enabled for deletion rationales")
                return {"scored": 0, "skipped": 0, "message": "LLM assist is not configured/enabled for deletion rationales"}
            candidates = eligible_candidates(db, ollama, ids, limit)
            tasks.update_task(task_id, total=len(candidates))
            for i, item in enumerate(candidates, 1):
                rationale = await generate_and_store(item, ollama, db)
                if rationale:
                    scored += 1
                else:
                    skipped += 1
                tasks.update_task(task_id, current=i, message=f"{scored} scored, {skipped} skipped")
                if ollama.batch_delay_ms > 0:
                    await asyncio.sleep(ollama.batch_delay_ms / 1000)
        finally:
            db.close()
    except Exception as e:
        tasks.finish_task(task_id, "failed", str(e))
        raise
    finally:
        llm_assist.release_slot()
    logger.info(f"Media LLM run: {scored} scored, {skipped} skipped")
    publish({"type": "media_llm_run", "scored": scored, "skipped": skipped})
    tasks.finish_task(task_id, "done", f"{scored} scored, {skipped} skipped")
    return {"scored": scored, "skipped": skipped, "message": f"{scored} scored, {skipped} skipped"}


def eligible_candidates(db, ollama: OllamaSettings, ids: list[int] | None,
                        limit: int = 50) -> list[MediaItem]:
    """Explicit ids as given; otherwise deletion candidates (same filters the
    Cleanup list applies). Either way, items with a current-key cached rationale
    are skipped — regenerating one is the per-item force=true explain path."""
    if ids:
        rows = db.query(MediaItem).filter(MediaItem.id.in_(ids)).all()
        return [r for r in rows
                if not (r.llm_rationale and r.llm_rationale_key == rationale_key(ollama, r))
                ][:limit]
    weights = _load(db, "scoring_weights", ScoringWeights)
    cleanup = _load(db, "cleanup", CleanupSettings)
    q = (db.query(MediaItem)
         .filter(MediaItem.score >= weights.min_score_threshold,
                 MediaItem.ignored.is_(False),
                 MediaItem.protected.isnot(True),
                 MediaItem.watch_protected.isnot(True),
                 MediaItem.pending_delete_at.is_(None)))
    if cleanup.excluded_libraries:
        q = q.filter(~MediaItem.library_section.in_(cleanup.excluded_libraries))
    out = []
    for item in q.order_by(MediaItem.score.desc()).all():
        if item.llm_rationale and item.llm_rationale_key == rationale_key(ollama, item):
            continue
        out.append(item)
        if len(out) >= limit:
            break
    return out
