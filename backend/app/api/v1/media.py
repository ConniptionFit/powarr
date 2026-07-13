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
from app.schemas.media import MediaItemOut, MediaStats, DeletionLogOut, DeletionStats, DeletionPreview, DuplicateGroup
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
            q = q.filter(MediaItem.protected.isnot(True),
                         MediaItem.watch_protected.isnot(True),
                         MediaItem.seeding_protected.isnot(True),
                         MediaItem.progress_protected.isnot(True))
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


@router.get("/duplicates", response_model=list[DuplicateGroup])
def list_duplicates(db: Session = Depends(get_db)):
    """LIB-03: groups of MediaItem rows that look like the same title living
    as separate Plex library entries (a stale grab left after an upgrade, a
    re-add, a duplicate import) — distinct from the score-sorted Deletion
    Suggestions flow. Read-only; actual removal goes through the existing
    preview-delete / batch-delete endpoints with whichever ids the caller
    picks to keep vs. delete."""
    from app.services.duplicate_finder import find_duplicate_groups
    return find_duplicate_groups(db)


@router.get("/stats", response_model=MediaStats)
def get_stats(db: Session = Depends(get_db)):
    weights = _get_setting(db, "scoring_weights", ScoringWeights)

    total = db.query(MediaItem).count()
    total_size = db.query(func.sum(MediaItem.file_size)).scalar() or 0
    cleanup = _get_setting(db, "cleanup", CleanupSettings)
    candidates_q = db.query(MediaItem).filter(
        MediaItem.score >= weights.min_score_threshold,
        MediaItem.ignored.is_(False),
        MediaItem.pending_delete_at.is_(None),
        # Same protect filters list_media applies (unless include_protected) —
        # otherwise this count drifts from what Deletion Suggestions actually
        # renders, the exact bug fixed for excluded_libraries in v0.50.0.
        MediaItem.protected.isnot(True),
        MediaItem.watch_protected.isnot(True),
        MediaItem.seeding_protected.isnot(True),
        MediaItem.progress_protected.isnot(True),
    )
    if cleanup.excluded_libraries:
        candidates_q = candidates_q.filter(~MediaItem.library_section.in_(cleanup.excluded_libraries))
    candidates = candidates_q.all()
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


@router.get("/export.csv")
def export_media_csv(
    db: Session = Depends(get_db),
    media_type: Optional[str] = Query(None),
    min_score: Optional[float] = Query(None),
    ignored: Optional[bool] = Query(False),
    include_protected: bool = Query(False),
    pending: bool = Query(False),
    sort_by: str = Query("score"),
    order: str = Query("desc"),
    limit: int = Query(10000),
):
    """CSV of deletion candidates (same filters as list_media; Approved Queue #14)."""
    from app.services.csv_export import streaming_csv, _dt
    items = list_media(
        db=db, media_type=media_type, min_score=min_score, ignored=ignored,
        include_protected=include_protected, pending=pending,
        sort_by=sort_by, order=order, limit=min(limit, 20000), offset=0,
    )
    rows = [[
        i.id, i.title, i.parent_title or "", i.year or "", i.media_type,
        i.library_section or "", i.file_size, round(i.score or 0, 2),
        i.watch_count, _dt(i.last_watched_at), i.file_path or "",
        bool(i.protected), bool(getattr(i, "watch_protected", False)),
        bool(getattr(i, "seeding_protected", False)), bool(getattr(i, "progress_protected", False)),
    ] for i in items]
    return streaming_csv(
        "powarr-deletion-candidates.csv",
        ["id", "title", "parent_title", "year", "media_type", "library",
         "file_size", "score", "watch_count", "last_watched_at", "file_path",
         "protected", "watch_protected", "seeding_protected", "progress_protected"],
        rows,
    )


@router.get("/deletion-log", response_model=list[DeletionLogOut])
def deletion_log(db: Session = Depends(get_db), limit: int = Query(200), offset: int = Query(0)):
    return (db.query(DeletionLog).order_by(DeletionLog.deleted_at.desc())
            .offset(offset).limit(limit).all())


@router.get("/deletion-log/export.csv")
def export_deletion_log_csv(db: Session = Depends(get_db), limit: int = Query(10000)):
    from app.services.csv_export import streaming_csv, _dt
    items = (db.query(DeletionLog).order_by(DeletionLog.deleted_at.desc())
             .limit(min(limit, 20000)).all())
    rows = [[
        i.id, i.title, i.parent_title or "", i.media_type,
        i.library_section or "", i.file_size, i.arr_action or "", _dt(i.deleted_at),
    ] for i in items]
    return streaming_csv(
        "powarr-deletion-log.csv",
        ["id", "title", "parent_title", "media_type", "library",
         "file_size", "arr_action", "deleted_at"],
        rows,
    )


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


@router.post("/preview-delete", response_model=DeletionPreview)
def preview_delete(ids: list[int] = Body(...), delete_mode: Optional[str] = Query(None),
                   db: Session = Depends(get_db)):
    """Non-destructive dry-run (LIB-01): projected GB freed, per-item *arr action
    and cascade warnings (deleting one episode/track can unmonitor or delete an
    entire series/artist in Sonarr/Lidarr — this surfaces that before it happens),
    current protection flags, and whether this would soft-delete or delete
    immediately. No writes — POST only because a JSON body of ids is cleaner
    than a long query string; nothing here mutates state.

    delete_mode (LIB-02): one of deleter.EPISODE_DELETE_MODES — when given, the
    preview reflects that specific Sonarr episode policy instead of the
    extra_config-driven default, so the modal shown before commit matches what
    will actually happen."""
    from app.services.deletion_preview import build_deletion_preview
    cleanup = _get_setting(db, "cleanup", CleanupSettings)
    return build_deletion_preview(db, ids, cleanup, delete_mode=delete_mode)


@router.delete("/batch")
async def delete_media_batch(ids: list[int] = Body(...), delete_mode: Optional[str] = Query(None),
                             db: Session = Depends(get_db)):
    """Delete multiple media items by ID. Honors the soft-delete window when configured.
    delete_mode (LIB-02): explicit Sonarr episode policy, see deleter.EPISODE_DELETE_MODES."""
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
                item.pending_delete_mode = delete_mode
                pending.append(item_id)
            else:
                await propagate_and_delete(item, db, delete_mode=delete_mode)
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


@router.get("/{item_id}/arr-candidates")
async def arr_candidates(item_id: int, q: str = Query(""), db: Session = Depends(get_db)):
    """INT-02 — search the *arr app's library matching this item's media_type,
    for the manual ID-override UI. Empty query browses the whole library."""
    item = db.query(MediaItem).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")
    from app.services.arr_link import search_arr_candidates
    return {"candidates": await search_arr_candidates(db, item.media_type, q)}


@router.put("/{item_id}/arr-link")
def update_arr_link(item_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    """INT-02 — manually set (or clear, {"value": null}) the *arr id fixing a bad
    auto-link from link_arr_ids() without a full resync. link_arr_ids() only ever
    fills a missing id (never overwrites one it finds already set), so a manual
    link here is permanent until cleared — it won't be silently reverted by the
    next Plex sync."""
    item = db.query(MediaItem).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")
    from app.services.arr_link import ID_FIELD_FOR_MEDIA_TYPE
    field = ID_FIELD_FOR_MEDIA_TYPE.get(item.media_type)
    if not field:
        raise HTTPException(status_code=400,
                            detail=f"Manual *arr linking isn't supported for media_type={item.media_type}")
    value = payload.get("value")
    setattr(item, field, int(value) if value is not None else None)
    db.commit()
    return {"id": item_id, field: getattr(item, field)}


@router.post("/llm-run")
async def media_llm_run(payload: dict = Body(default={}), db: Session = Depends(get_db)):
    """On-demand LLM deletion rationales. {"ids": [...]} for specific items; omit
    to process candidates lacking a current cached rationale. Runs in the
    background — an SSE "media_llm_run" event fires when it finishes."""
    from app.schemas.settings import LlmPolicies, OllamaSettings
    from app.services import llm_assist, media_llm, tasks
    ids = payload.get("ids") or None
    if llm_assist.slot_active():
        raise HTTPException(status_code=409, detail="An LLM run is already in progress")
    ollama = _get_setting(db, "ollama", OllamaSettings)
    policies = _get_setting(db, "llm_policies", LlmPolicies)
    # Coarse "connected at all" gate — a per-library override (LLM-08) can
    # enable explain for a specific library even when the global toggle is off.
    if not (ollama.enabled and ollama.host and ollama.model_for("explain")):
        raise HTTPException(status_code=400, detail="LLM assist is not enabled for deletion rationales — check Settings → LLM Assist")
    count = len(media_llm.eligible_candidates(db, ollama, ids, policies=policies))
    tasks.spawn_background(media_llm.llm_media_run(ids))
    return {"started": count, "total_eligible": count,
            "message": f"LLM run started on {count} candidate(s) — results stream in live"}


@router.post("/second-opinion-run")
async def media_second_opinion_run(payload: dict = Body(default={}), db: Session = Depends(get_db)):
    """LLM-07 — on-demand batch "risky delete" second opinions. {"ids": [...]}
    for specific items; omit to process candidates lacking a current cached
    verdict. Runs in the background — an SSE "media_llm_second_opinion_run"
    event fires when it finishes."""
    from app.schemas.settings import OllamaSettings
    from app.services import llm_assist, media_llm, tasks
    ids = payload.get("ids") or None
    if llm_assist.slot_active():
        raise HTTPException(status_code=409, detail="An LLM run is already in progress")
    ollama = _get_setting(db, "ollama", OllamaSettings)
    if not ollama.task_enabled("second_opinion"):
        raise HTTPException(status_code=400, detail="LLM second opinion is not enabled — check Settings → LLM Assist")
    count = len(media_llm.eligible_second_opinion_candidates(db, ollama, ids))
    tasks.spawn_background(media_llm.llm_second_opinion_run(ids))
    return {"started": count, "total_eligible": count,
            "message": f"Second-opinion run started on {count} candidate(s) — results stream in live"}


@router.post("/{item_id}/explain")
async def explain_media(item_id: int, force: bool = Query(False), db: Session = Depends(get_db)):
    """Optional LLM one-liner on whether this is a good deletion candidate. Fails
    soft. Served from the cached rationale when its key still matches the current
    prompt/model/score; force=true regenerates regardless."""
    item = db.query(MediaItem).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")
    from app.schemas.settings import LlmPolicies, OllamaSettings
    from app.services.media_llm import _explain_policy
    ollama = _get_setting(db, "ollama", OllamaSettings)
    policies = _get_setting(db, "llm_policies", LlmPolicies)
    if not _explain_policy(ollama, policies, item.library_section)[0]:
        return {"rationale": None, "message": "LLM assist not configured for deletion rationales", "cached": False}
    from app.services import llm_assist, media_llm
    if (not force and item.llm_rationale
            and item.llm_rationale_key == media_llm.rationale_key(ollama, item, policies)):
        return {"rationale": item.llm_rationale, "message": None, "cached": True,
                "generated_at": item.llm_rationale_at}
    if not llm_assist.acquire_slot():
        # Same single-flight contract as the batch runs — one LLM task at a time,
        # shared slot, so rapid clicks/tabs can't pile up parallel generations.
        raise HTTPException(status_code=409, detail="Another LLM task is already running")
    try:
        rationale = await media_llm.generate_and_store(item, ollama, db, policies)
    finally:
        llm_assist.release_slot()
    return {"rationale": rationale, "message": None if rationale else "No response from LLM",
            "cached": False, "generated_at": item.llm_rationale_at}


@router.post("/{item_id}/second-opinion")
async def second_opinion_media(item_id: int, force: bool = Query(False), db: Session = Depends(get_db)):
    """LLM-07 — one bare KEEP/DELETE "risky delete" second opinion for one item.
    Fails soft. Served from the cached verdict when its key still matches the
    current model/score; force=true regenerates regardless."""
    item = db.query(MediaItem).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")
    from app.schemas.settings import OllamaSettings
    ollama = _get_setting(db, "ollama", OllamaSettings)
    if not ollama.task_enabled("second_opinion"):
        return {"verdict": None, "message": "LLM second opinion not configured/enabled", "cached": False}
    from app.services import llm_assist, media_llm
    if (not force and item.llm_second_opinion
            and item.llm_second_opinion_key == media_llm.second_opinion_key(ollama, item)):
        return {"verdict": item.llm_second_opinion, "message": None, "cached": True,
                "generated_at": item.llm_second_opinion_at}
    if not llm_assist.acquire_slot():
        raise HTTPException(status_code=409, detail="Another LLM task is already running")
    try:
        verdict = await media_llm.generate_and_store_second_opinion(item, ollama, db)
    finally:
        llm_assist.release_slot()
    return {"verdict": verdict, "message": None if verdict else "No response from LLM",
            "cached": False, "generated_at": item.llm_second_opinion_at}


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
    from app.schemas.settings import LlmPolicies, OllamaSettings
    from app.services import llm_assist, media_llm
    from app.services.media_llm import _explain_policy
    row = db.query(MediaItem.id, MediaItem.library_section).filter_by(id=item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Media item not found")
    ollama = _get_setting(db, "ollama", OllamaSettings)
    policies = _get_setting(db, "llm_policies", LlmPolicies)
    if not _explain_policy(ollama, policies, row.library_section)[0]:
        raise HTTPException(status_code=400, detail="LLM assist not configured for deletion rationales")
    if not llm_assist.acquire_slot():
        raise HTTPException(status_code=409, detail="Another LLM task is already running")

    async def stream():
        # Own session: the request-scoped one may close before streaming finishes.
        sdb = SessionLocal()
        try:
            item = sdb.query(MediaItem).filter_by(id=item_id).first()
            _, effective_model = _explain_policy(ollama, policies, item.library_section)
            full = ""
            if ollama.verbosity == "minimal":
                # One-word verdict — nothing to stream; reuse the plain path.
                full = await media_llm.generate_and_store(item, ollama, sdb, policies) or ""
                if full:
                    yield f"data: {_json.dumps({'delta': full})}\n\n"
            else:
                async for chunk in llm_assist.explain_deletion_stream(
                        ollama.host, effective_model, media_llm.item_summary(item, sdb),
                        ollama.api_style, template=ollama.explain_prompt,
                        verbosity=ollama.verbosity, model_size=ollama.model_size,
                        keep_alive_minutes=ollama.keep_alive_minutes,
                        **llm_assist.prompt_kwargs(ollama),
                        **llm_assist.inference_kwargs(ollama)):
                    full += chunk
                    yield f"data: {_json.dumps({'delta': chunk})}\n\n"
                full = full.strip()
                if full:
                    item.llm_rationale = full
                    item.llm_rationale_at = datetime.utcnow()
                    item.llm_rationale_key = media_llm.rationale_key(ollama, item, policies)
                    sdb.commit()
            yield f"data: {_json.dumps({'done': True, 'rationale': full or None, 'message': None if full else 'No response from LLM'})}\n\n"
        finally:
            llm_assist.release_slot()
            sdb.close()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.delete("/{item_id}")
async def delete_media(item_id: int, delete_mode: Optional[str] = Query(None),
                       db: Session = Depends(get_db)):
    """delete_mode (LIB-02): explicit Sonarr episode policy, see deleter.EPISODE_DELETE_MODES."""
    item = db.query(MediaItem).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")
    cleanup = _get_setting(db, "cleanup", CleanupSettings)
    if cleanup.soft_delete_days > 0 and item.pending_delete_at is None:
        item.pending_delete_at = datetime.utcnow()
        item.pending_delete_mode = delete_mode
        db.commit()
        return {"deleted": None, "pending_delete": item_id,
                "purge_after_days": cleanup.soft_delete_days}
    from app.services import tasks
    task_id = tasks.create_task("deletion", f"Deleting '{item.title}'")
    try:
        await propagate_and_delete(item, db, delete_mode=delete_mode)
        db.commit()
    except Exception as e:
        tasks.finish_task(task_id, "failed", str(e))
        raise
    tasks.finish_task(task_id, "done", f"Deleted '{item.title}'")
    return {"deleted": item_id}
