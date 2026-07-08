import json
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.models.app_setting import AppSetting
from app.models.deletion_log import DeletionLog
from app.models.media import MediaItem
from app.schemas.media import MediaItemOut, MediaStats, DeletionLogOut, DeletionStats
from app.schemas.settings import ScoringWeights, CleanupSettings
from app.services.deleter import propagate_and_delete

router = APIRouter(prefix="/media", tags=["media"])


def _get_setting(db: Session, key: str, schema):
    row = db.query(AppSetting).filter_by(key=key).first()
    if not row or not row.value:
        return schema()
    return schema(**json.loads(row.value))


@router.get("", response_model=list[MediaItemOut])
def list_media(
    db: Session = Depends(get_db),
    media_type: Optional[str] = Query(None),
    min_score: Optional[float] = Query(None),
    ignored: Optional[bool] = Query(False),
    include_protected: bool = Query(False),
    pending: bool = Query(False),  # true = list only items awaiting soft-delete purge
    sort_by: str = Query("score"),
    order: str = Query("desc"),
    limit: int = Query(200),
    offset: int = Query(0),
):
    q = db.query(MediaItem)
    if pending:
        q = q.filter(MediaItem.pending_delete_at.isnot(None))
    else:
        q = q.filter(MediaItem.pending_delete_at.is_(None))
        if ignored is not None:
            q = q.filter(MediaItem.ignored == ignored)
        if not include_protected:
            q = q.filter(MediaItem.protected.isnot(True))
        cleanup = _get_setting(db, "cleanup", CleanupSettings)
        if cleanup.excluded_libraries:
            q = q.filter(~MediaItem.library_section.in_(cleanup.excluded_libraries))
    if media_type:
        q = q.filter(MediaItem.media_type == media_type)
    if min_score is not None:
        q = q.filter(MediaItem.score >= min_score)

    col = getattr(MediaItem, sort_by, MediaItem.score)
    q = q.order_by(col.desc() if order == "desc" else col.asc())
    return q.offset(offset).limit(limit).all()


@router.get("/stats", response_model=MediaStats)
def get_stats(db: Session = Depends(get_db)):
    weights = _get_setting(db, "scoring_weights", ScoringWeights)

    total = db.query(MediaItem).count()
    total_size = db.query(func.sum(MediaItem.file_size)).scalar() or 0
    candidates = db.query(MediaItem).filter(
        MediaItem.score >= weights.min_score_threshold,
        MediaItem.ignored.is_(False),
        MediaItem.pending_delete_at.is_(None),
    ).all()
    savings = sum(c.file_size for c in candidates)

    last_synced = None
    row = db.query(AppSetting).filter_by(key="last_synced").first()
    if row and row.value:
        try:
            last_synced = datetime.fromisoformat(row.value)
        except ValueError:
            pass

    return MediaStats(
        total_items=total,
        total_size_bytes=total_size,
        candidates_above_threshold=len(candidates),
        potential_savings_bytes=savings,
        last_synced=last_synced,
    )


@router.get("/libraries")
def list_libraries(db: Session = Depends(get_db)) -> list[str]:
    rows = db.query(MediaItem.library_section).distinct().all()
    return sorted({r[0] for r in rows if r[0]})


@router.get("/deletion-log", response_model=list[DeletionLogOut])
def deletion_log(db: Session = Depends(get_db), limit: int = Query(200), offset: int = Query(0)):
    return (db.query(DeletionLog).order_by(DeletionLog.deleted_at.desc())
            .offset(offset).limit(limit).all())


@router.get("/deletion-stats", response_model=DeletionStats)
def deletion_stats(db: Session = Depends(get_db)):
    cutoff = datetime.utcnow() - timedelta(days=30)
    q = db.query(func.count(DeletionLog.id), func.coalesce(func.sum(DeletionLog.file_size), 0)) \
        .filter(DeletionLog.deleted_at >= cutoff)
    count_30d, freed_30d = q.one()
    total_count, total_freed = db.query(
        func.count(DeletionLog.id), func.coalesce(func.sum(DeletionLog.file_size), 0)).one()
    return DeletionStats(
        deleted_30d=count_30d, freed_30d_bytes=int(freed_30d),
        deleted_total=total_count, freed_total_bytes=int(total_freed),
    )


@router.delete("/batch")
async def delete_media_batch(ids: list[int] = Body(...), db: Session = Depends(get_db)):
    """Delete multiple media items by ID. Honors the soft-delete window when configured."""
    from app.services import tasks
    task_id = tasks.create_task("deletion", f"Deleting {len(ids)} item(s)", total=len(ids))
    cleanup = _get_setting(db, "cleanup", CleanupSettings)
    deleted, pending = [], []
    for i, item_id in enumerate(ids, 1):
        try:
            item = db.query(MediaItem).filter_by(id=item_id).first()
            if not item:
                continue
            if cleanup.soft_delete_days > 0 and item.pending_delete_at is None:
                item.pending_delete_at = datetime.utcnow()
                pending.append(item_id)
            else:
                await propagate_and_delete(item, db)
                deleted.append(item_id)
        except Exception:
            pass
        tasks.update_task(task_id, current=i)
    db.commit()
    tasks.finish_task(task_id, "done", f"{len(deleted)} deleted, {len(pending)} pending")
    return {"deleted": deleted, "pending_delete": pending}


@router.patch("/{item_id}/ignore")
def toggle_ignore(item_id: int, ignored: bool, db: Session = Depends(get_db)):
    item = db.query(MediaItem).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")
    item.ignored = ignored
    db.commit()
    return {"id": item_id, "ignored": item.ignored}


@router.post("/{item_id}/restore")
def restore_media(item_id: int, db: Session = Depends(get_db)):
    """Cancel a pending soft-delete."""
    item = db.query(MediaItem).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")
    if item.pending_delete_at is None:
        raise HTTPException(status_code=400, detail="Item is not pending deletion")
    item.pending_delete_at = None
    db.commit()
    return {"id": item_id, "restored": True}


@router.post("/llm-run")
async def media_llm_run(payload: dict = Body(default={}), db: Session = Depends(get_db)):
    """On-demand LLM deletion rationales. {"ids": [...]} for specific items; omit
    to process candidates lacking a current cached rationale. Runs in the
    background — an SSE "media_llm_run" event fires when it finishes."""
    from app.schemas.settings import OllamaSettings
    from app.services import llm_assist, media_llm, tasks
    ids = payload.get("ids") or None
    if llm_assist.slot_active():
        raise HTTPException(status_code=409, detail="An LLM run is already in progress")
    ollama = _get_setting(db, "ollama", OllamaSettings)
    if not ollama.enabled:
        raise HTTPException(status_code=400, detail="LLM assist is not enabled — configure it on the Integrations page")
    count = len(media_llm.eligible_candidates(db, ollama, ids))
    tasks.spawn_background(media_llm.llm_media_run(ids))
    return {"started": count, "total_eligible": count,
            "message": f"LLM run started on {count} candidate(s) — results stream in live"}


@router.post("/{item_id}/explain")
async def explain_media(item_id: int, force: bool = Query(False), db: Session = Depends(get_db)):
    """Optional LLM one-liner on whether this is a good deletion candidate. Fails
    soft. Served from the cached rationale when its key still matches the current
    prompt/model/score; force=true regenerates regardless."""
    item = db.query(MediaItem).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")
    from app.schemas.settings import OllamaSettings
    ollama = _get_setting(db, "ollama", OllamaSettings)
    if not (ollama.enabled and ollama.host and ollama.model):
        return {"rationale": None, "message": "LLM assist not configured", "cached": False}
    from app.services import llm_assist, media_llm
    if (not force and item.llm_rationale
            and item.llm_rationale_key == media_llm.rationale_key(ollama, item)):
        return {"rationale": item.llm_rationale, "message": None, "cached": True,
                "generated_at": item.llm_rationale_at}
    if not llm_assist.acquire_slot():
        # Same single-flight contract as the batch runs — one LLM task at a time,
        # shared slot, so rapid clicks/tabs can't pile up parallel generations.
        raise HTTPException(status_code=409, detail="Another LLM task is already running")
    try:
        rationale = await media_llm.generate_and_store(item, ollama, db)
    finally:
        llm_assist.release_slot()
    return {"rationale": rationale, "message": None if rationale else "No response from LLM",
            "cached": False, "generated_at": item.llm_rationale_at}


@router.get("/{item_id}/explain/stream")
async def explain_media_stream(item_id: int, db: Session = Depends(get_db)):
    """SSE variant of explain: streams rationale tokens as they generate (verbose
    can take 45-60s — this makes the wait visible), then persists the full result
    to the same cache the POST endpoint uses. Events: {"delta": ...}* then
    {"done": true, "rationale": ..., "message": ...}. The POST endpoint remains
    the non-streaming/cached path."""
    import json as _json
    from fastapi.responses import StreamingResponse
    from app.database import SessionLocal
    from app.schemas.settings import OllamaSettings
    from app.services import llm_assist, media_llm
    if not db.query(MediaItem.id).filter_by(id=item_id).first():
        raise HTTPException(status_code=404, detail="Media item not found")
    ollama = _get_setting(db, "ollama", OllamaSettings)
    if not (ollama.enabled and ollama.host and ollama.model):
        raise HTTPException(status_code=400, detail="LLM assist not configured")
    if not llm_assist.acquire_slot():
        raise HTTPException(status_code=409, detail="Another LLM task is already running")

    async def stream():
        # Own session: the request-scoped one may close before streaming finishes.
        sdb = SessionLocal()
        try:
            item = sdb.query(MediaItem).filter_by(id=item_id).first()
            full = ""
            if ollama.verbosity == "minimal":
                # One-word verdict — nothing to stream; reuse the plain path.
                full = await media_llm.generate_and_store(item, ollama, sdb) or ""
                if full:
                    yield f"data: {_json.dumps({'delta': full})}\n\n"
            else:
                async for chunk in llm_assist.explain_deletion_stream(
                        ollama.host, ollama.model, media_llm.item_summary(item),
                        ollama.api_style, template=ollama.explain_prompt,
                        verbosity=ollama.verbosity, model_size=ollama.model_size,
                        keep_alive_minutes=ollama.keep_alive_minutes):
                    full += chunk
                    yield f"data: {_json.dumps({'delta': chunk})}\n\n"
                full = full.strip()
                if full:
                    item.llm_rationale = full
                    item.llm_rationale_at = datetime.utcnow()
                    item.llm_rationale_key = media_llm.rationale_key(ollama, item)
                    sdb.commit()
            yield f"data: {_json.dumps({'done': True, 'rationale': full or None, 'message': None if full else 'No response from LLM'})}\n\n"
        finally:
            llm_assist.release_slot()
            sdb.close()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.delete("/{item_id}")
async def delete_media(item_id: int, db: Session = Depends(get_db)):
    item = db.query(MediaItem).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")
    cleanup = _get_setting(db, "cleanup", CleanupSettings)
    if cleanup.soft_delete_days > 0 and item.pending_delete_at is None:
        item.pending_delete_at = datetime.utcnow()
        db.commit()
        return {"deleted": None, "pending_delete": item_id,
                "purge_after_days": cleanup.soft_delete_days}
    from app.services import tasks
    task_id = tasks.create_task("deletion", f"Deleting '{item.title}'")
    try:
        await propagate_and_delete(item, db)
        db.commit()
    except Exception as e:
        tasks.finish_task(task_id, "failed", str(e))
        raise
    tasks.finish_task(task_id, "done", f"Deleted '{item.title}'")
    return {"deleted": item_id}
