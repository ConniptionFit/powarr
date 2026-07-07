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

logger = logging.getLogger("powarr")


def rationale_key(ollama: OllamaSettings, item: MediaItem) -> str:
    """Cache key for a stored rationale: prompt/model config + the item fields the
    prompt is built from. Any change to either regenerates on next request."""
    payload = json.dumps({
        "template": ollama.explain_prompt,
        "model": ollama.model,
        "api_style": ollama.api_style,
        "verbosity": ollama.verbosity,
        "model_size": ollama.model_size,
        "score": item.score,
        "watch_count": item.watch_count,
        "last_watched_at": item.last_watched_at.isoformat() if item.last_watched_at else None,
        "file_size": item.file_size,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def item_summary(item: MediaItem) -> str:
    return (f"{item.title} ({item.year or 'unknown year'}), {item.media_type}, "
            f"{round((item.file_size or 0) / 1024**3, 1)} GB, watched {item.watch_count}x, "
            f"last watched {item.last_watched_at or 'never'}, deletion score {item.score}/100")


async def generate_and_store(item: MediaItem, ollama: OllamaSettings, db) -> str | None:
    """One LLM call for one item; persists rationale + timestamp + cache key on
    success. Fail-soft: returns None and stores nothing on no-response."""
    rationale = await llm_assist.explain_deletion(
        ollama.host, ollama.model, item_summary(item), ollama.api_style,
        template=ollama.explain_prompt, verbosity=ollama.verbosity,
        model_size=ollama.model_size, keep_alive_minutes=ollama.keep_alive_minutes)
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
            if not (ollama.enabled and ollama.host and ollama.model):
                tasks.finish_task(task_id, "done", "LLM assist is not configured/enabled")
                return {"scored": 0, "skipped": 0, "message": "LLM assist is not configured/enabled"}
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
    Cleanup list applies) whose cached rationale is absent or stale-keyed."""
    if ids:
        return db.query(MediaItem).filter(MediaItem.id.in_(ids)).limit(limit).all()
    weights = _load(db, "scoring_weights", ScoringWeights)
    cleanup = _load(db, "cleanup", CleanupSettings)
    q = (db.query(MediaItem)
         .filter(MediaItem.score >= weights.min_score_threshold,
                 MediaItem.ignored.is_(False),
                 MediaItem.protected.isnot(True),
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
