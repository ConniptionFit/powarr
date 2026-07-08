import asyncio
import json
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db, SessionLocal
from app.models.failed_import import FailedImport
from app.models.integration import Integration
from app.schemas.failed_import import FailedImportOut, ImportStats
from app.services import import_matcher
from app.services.import_matcher import scan_once, _get_client

router = APIRouter(prefix="/imports", tags=["imports"])

STATUSES = ("suggested", "auto_resolved", "accepted", "rejected", "closed_external",
            "resolve_failed", "orphan_pending", "orphaned")


@router.get("", response_model=list[FailedImportOut])
def list_imports(
    db: Session = Depends(get_db),
    status: Optional[str] = Query(None),
    limit: int = Query(200),
    offset: int = Query(0),
):
    q = db.query(FailedImport)
    if status:
        q = q.filter(FailedImport.status == status)
    return q.order_by(FailedImport.created_at.desc()).offset(offset).limit(limit).all()


@router.get("/stats", response_model=ImportStats)
def import_stats(db: Session = Depends(get_db)):
    counts = {s: db.query(FailedImport).filter_by(status=s).count() for s in STATUSES}
    by_service = dict(
        db.query(FailedImport.source_app, __import__("sqlalchemy").func.count(FailedImport.id))
        .filter(FailedImport.status == "suggested")
        .group_by(FailedImport.source_app).all()
    )
    week_ago = datetime.utcnow() - timedelta(days=7)
    auto_7d = db.query(FailedImport).filter(
        FailedImport.status == "auto_resolved",
        FailedImport.resolved_at >= week_ago,
    ).count()
    return ImportStats(**counts, by_service=by_service, auto_resolved_7d=auto_7d)


@router.get("/events")
async def import_events():
    """SSE stream: pushes an event after every scan cycle that changed something."""
    queue = import_matcher.subscribe()

    async def stream():
        try:
            yield "event: connected\ndata: {}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            import_matcher.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/scan")
async def trigger_scan():
    """Run one detection cycle immediately (in addition to the background poller)."""
    return await scan_once()


@router.post("/llm-run")
async def llm_run(payload: dict = Body(default={}), db: Session = Depends(get_db)):
    """On-demand LLM scoring. {"ids": [...]} for checked rows; omit ids to process
    the backlog of open rows without an LLM score. Runs in the background —
    an SSE "llm_run" event fires when it finishes. If a run is already active,
    this queues instead of failing — it starts automatically once the current
    one releases the slot (SSE "llm_queued" / "llm_run_started" mark the
    transition)."""
    from app.models.app_setting import AppSetting
    from app.services import tasks
    from app.services.import_matcher import llm_rescore, llm_run_active, queue_llm_run
    ids = payload.get("ids") or None
    cfg = db.query(AppSetting).filter_by(key="ollama").first()
    if not cfg or not cfg.value or not json.loads(cfg.value).get("enabled"):
        raise HTTPException(status_code=400, detail="LLM assist is not enabled — configure it on the Integrations page")
    if ids:
        count = db.query(FailedImport).filter(FailedImport.id.in_(ids)).count()
    else:
        count = db.query(FailedImport).filter(
            FailedImport.status.in_(("suggested", "resolve_failed")),
            FailedImport.llm_confidence.is_(None),
        ).count()
    if llm_run_active():
        position = queue_llm_run(ids)
        return {"started": 0, "total_eligible": count, "queued": True, "queue_position": position,
                "message": f"An LLM run is already in progress — queued (position {position}), "
                           "will start automatically when it finishes"}
    tasks.spawn_background(llm_rescore(ids))
    return {"started": min(count, 50), "total_eligible": count, "queued": False,
            "message": f"LLM run started on {min(count, 50)} item(s) — results stream in live"}


@router.post("/{item_id}/llm-review-pack")
async def llm_review_pack(item_id: int, db: Session = Depends(get_db)):
    """Per-file LLM review for season packs: matches each file to its episode.
    Returns and persists [{"file": "filename.mkv", "season": 1, "episode": 2, "confidence": "high", "reason": "..."}]."""
    from app.models.app_setting import AppSetting
    from app.services import llm_assist
    item = db.query(FailedImport).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Failed import not found")
    if not item.pack:
        return {"matches": [], "message": "Not a season pack — use the regular LLM-run for this item"}
    if not item.matched_title or not item.matched_id:
        return {"matches": [], "message": "No matched library entry — run LLM-run first to match this download"}
    if not item.download_id:
        return {"matches": [], "message": "No download id on this item"}
    cfg = db.query(AppSetting).filter_by(key="ollama").first()
    if not cfg or not cfg.value or not json.loads(cfg.value).get("enabled"):
        raise HTTPException(status_code=400, detail="LLM assist is not enabled — configure it on the Integrations page")

    # Fetch file list from the download
    row = db.query(Integration).filter_by(name=item.source_app, enabled=True).first()
    if not row:
        return {"matches": [], "message": f"{item.source_app} integration not enabled"}
    client = _get_client(item.source_app, row)
    try:
        candidates = await client.get_manual_import(item.download_id)
    except Exception as e:
        return {"matches": [], "message": f"Manual-import lookup failed: {e}"}

    # Extract just the file names from the paths
    file_names = []
    for f in candidates:
        path = f.get("relativePath") or f.get("path") or ""
        if path:
            file_names.append(path.split("/")[-1] if "/" in path else path)

    if not file_names:
        return {"matches": [], "message": "No files found in this download"}

    ollama_cfg = json.loads(cfg.value) if cfg.value else {}
    matches = await llm_assist.review_pack_files(
        host=ollama_cfg.get("host"), model=ollama_cfg.get("model"),
        release_title=item.raw_title, candidate_title=item.matched_title,
        file_names=file_names,
        api_style=ollama_cfg.get("api_style", "ollama"),
        template=ollama_cfg.get("pack_prompt", ""),
        verbosity=ollama_cfg.get("verbosity", "brief"),
        model_size=ollama_cfg.get("model_size", "medium"),
        keep_alive_minutes=ollama_cfg.get("keep_alive_minutes", 10)
    )

    # Persist results to database
    if matches:
        item.pack_file_matches = json.dumps(matches)
        item.updated_at = datetime.utcnow()
        db.commit()

    return {"matches": matches or [], "file_count": len(file_names)}


@router.post("/batch")
async def batch_action(payload: dict = Body(...), db: Session = Depends(get_db)):
    """Accept or reject several suggestions at once: {"ids": [...], "action": "accept"|"reject"}."""
    ids = payload.get("ids") or []
    action = payload.get("action")
    if action not in ("accept", "reject", "confirm_orphan") or not ids:
        raise HTTPException(status_code=400, detail="Body must be {ids: [...], action: accept|reject|confirm_orphan}")
    results = []
    for item_id in ids:
        try:
            if action == "accept":
                results.append(await _accept(item_id, db))
            elif action == "confirm_orphan":
                results.append(_confirm_orphan(item_id, db))
            else:
                results.append(_reject(item_id, db))
        except HTTPException as e:
            results.append({"id": item_id, "error": e.detail})
    return {"results": results}


@router.get("/{item_id}/files")
async def import_files(item_id: int, db: Session = Depends(get_db)):
    """Read-only manual-import preview: the files in this download and how the *arr app maps them."""
    item = db.query(FailedImport).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Failed import not found")
    if not item.download_id:
        return {"files": [], "message": "No download id on this item"}
    row = db.query(Integration).filter_by(name=item.source_app, enabled=True).first()
    if not row:
        return {"files": [], "message": f"{item.source_app} integration not enabled"}
    client = _get_client(item.source_app, row)
    try:
        candidates = await client.get_manual_import(item.download_id)
    except Exception as e:
        return {"files": [], "message": f"Manual-import lookup failed: {e}"}
    overrides = json.loads(item.mapping_overrides or "{}")
    files = []
    for f in candidates:
        mapped = (f.get("series") or {}).get("title") or (f.get("movie") or {}).get("title") \
            or (f.get("artist") or {}).get("artistName") or (f.get("author") or {}).get("authorName")
        raw_path = f.get("path")
        override = overrides.get(raw_path) if raw_path else None
        if override:
            detail = f"S{override['season']:02d}E{override['episode']:02d}" + \
                (f" '{override['title']}'" if override.get("title") else "")
        elif f.get("episodes"):
            detail = ", ".join(f"S{e.get('seasonNumber')}E{e.get('episodeNumber')}" for e in f["episodes"][:8])
        elif f.get("album"):
            detail = f["album"].get("title", "")
        else:
            detail = ""
        files.append({
            "path": f.get("relativePath") or f.get("path"),
            "raw_path": raw_path,
            "size": f.get("size", 0),
            "quality": ((f.get("quality") or {}).get("quality") or {}).get("name"),
            "mapped_to": mapped,
            "detail": detail,
            "overridden": override is not None,
            "rejections": [r.get("reason") for r in (f.get("rejections") or [])],
        })
    return {"files": files, "message": None}


@router.get("/{item_id}/episode-options")
async def episode_options(item_id: int, db: Session = Depends(get_db)):
    """All episodes of the matched series, for the editable Mapped To column's
    episode picker (Sonarr only — other *arr apps have no per-file sub-unit to
    reassign; a wrong match there is a Change Match, not a file-mapping fix)."""
    item = db.query(FailedImport).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Failed import not found")
    if item.source_app != "sonarr":
        return {"episodes": [], "message": "Episode mapping is only available for Sonarr (TV) items"}
    if not item.matched_id:
        return {"episodes": [], "message": "No matched series — set a match first"}
    row = db.query(Integration).filter_by(name="sonarr", enabled=True).first()
    if not row:
        return {"episodes": [], "message": "sonarr integration not enabled"}
    client = _get_client("sonarr", row)
    try:
        eps = await client.get_episodes(item.matched_id)
    except Exception as e:
        return {"episodes": [], "message": f"Episode lookup failed: {e}"}
    episodes = sorted(
        ({"id": e["id"], "season": e.get("seasonNumber"), "episode": e.get("episodeNumber"),
          "title": e.get("title") or ""} for e in eps if e.get("id")),
        key=lambda e: (e["season"] if e["season"] is not None else -1,
                      e["episode"] if e["episode"] is not None else -1),
    )
    return {"episodes": episodes, "message": None}


@router.put("/{item_id}/file-mapping")
def update_file_mapping(item_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    """User correction of one file's episode mapping (Sonarr episode-level rows
    only). Body: {"path": raw_path, "episode_id": int, "season": int, "episode":
    int, "title": str}. Persisted into mapping_overrides (keyed by the file's raw
    absolute path — stable across manual-import re-fetches) and applied at accept
    time so the correction actually changes what gets imported, not just displayed."""
    path = payload.get("path")
    episode_id = payload.get("episode_id")
    season, episode = payload.get("season"), payload.get("episode")
    if not path or not isinstance(episode_id, int) or not isinstance(season, int) or not isinstance(episode, int):
        raise HTTPException(status_code=400,
                            detail="Body must be {path: str, episode_id: int, season: int, episode: int}")
    item = db.query(FailedImport).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Failed import not found")
    overrides = json.loads(item.mapping_overrides or "{}")
    overrides[path] = {"episode_id": episode_id, "season": season, "episode": episode,
                       "title": payload.get("title") or ""}
    item.mapping_overrides = json.dumps(overrides)
    item.updated_at = datetime.utcnow()
    db.commit()
    return {"id": item.id, "overrides": overrides}


async def _accept(item_id: int, db: Session) -> dict:
    item = db.query(FailedImport).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Failed import not found")
    if not item.download_id:
        raise HTTPException(status_code=400, detail="No download id on this item — resolve it in the *arr app directly")
    row = db.query(Integration).filter_by(name=item.source_app, enabled=True).first()
    if not row:
        raise HTTPException(status_code=400, detail=f"{item.source_app} integration not enabled")

    client = _get_client(item.source_app, row)
    if item.source_app == "sonarr" and item.mapping_overrides:
        result = await client.push_import_command(item.download_id, item.matched_id,
                                                   overrides=json.loads(item.mapping_overrides))
    else:
        result = await client.push_import_command(item.download_id, item.matched_id)
    item.message = result["message"]
    if result["ok"]:
        item.status = "accepted"
        item.resolved_at = datetime.utcnow()
    db.commit()
    return {"id": item.id, "status": item.status, **result}


def _reject(item_id: int, db: Session) -> dict:
    item = db.query(FailedImport).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Failed import not found")
    item.status = "rejected"
    item.resolved_at = datetime.utcnow()
    db.commit()
    return {"id": item.id, "status": item.status}


def _confirm_orphan(item_id: int, db: Session) -> dict:
    """User sign-off on a confirmed-missing download: orphan_pending → orphaned."""
    item = db.query(FailedImport).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Failed import not found")
    if item.status != "orphan_pending":
        raise HTTPException(status_code=400, detail="Item is not awaiting orphan confirmation")
    item.status = "orphaned"
    item.resolved_at = datetime.utcnow()
    item.message = ((item.message + " | ") if item.message else "") + "Orphan confirmed by user"
    db.commit()
    return {"id": item.id, "status": item.status}




@router.get("/{item_id}/candidates")
async def match_candidates(item_id: int, query: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """Top library candidates for a manual match override, scored by title similarity."""
    item = db.query(FailedImport).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Failed import not found")
    row = db.query(Integration).filter_by(name=item.source_app, enabled=True).first()
    if not row:
        raise HTTPException(status_code=400, detail=f"{item.source_app} integration not enabled")
    from app.services.import_matcher import APP_FIELDS, title_similarity
    _, lib_method, title_key = APP_FIELDS[item.source_app]
    client = _get_client(item.source_app, row)
    try:
        library = await getattr(client, lib_method)()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Library fetch failed: {e}")
    q = query or item.raw_title
    scored = sorted(
        ({"id": e["id"], "title": e.get(title_key, ""),
          "score": round(title_similarity(q, e.get(title_key, "")), 3)} for e in library),
        key=lambda c: c["score"], reverse=True,
    )[:10]
    return {"candidates": scored}


@router.post("/{item_id}/match")
def set_match(item_id: int, body: dict = Body(...), db: Session = Depends(get_db)):
    """Manual match override: point this failed import at a different library entry."""
    item = db.query(FailedImport).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Failed import not found")
    matched_id = body.get("matched_id")
    if not matched_id:
        raise HTTPException(status_code=400, detail="matched_id required")
    item.matched_id = int(matched_id)
    item.matched_title = body.get("matched_title") or item.matched_title
    item.confidence = 1.0  # user said so
    item.llm_confidence = None
    item.llm_rationale = None
    item.message = ((item.message + " | ") if item.message else "") + "Manually matched"
    if item.status in ("rejected", "closed_external", "resolve_failed"):
        item.status = "suggested"
        item.resolved_at = None
    db.commit()
    return {"id": item.id, "matched_id": item.matched_id, "matched_title": item.matched_title,
            "confidence": item.confidence, "status": item.status}


@router.post("/{item_id}/accept")
async def accept_import(item_id: int, db: Session = Depends(get_db)):
    return await _accept(item_id, db)


@router.post("/{item_id}/confirm-orphan")
def confirm_orphan(item_id: int, db: Session = Depends(get_db)):
    return _confirm_orphan(item_id, db)


@router.post("/{item_id}/keep")
def keep_import(item_id: int, db: Session = Depends(get_db)):
    """Dismiss the orphan verdict: put the row back in triage. If the download is
    truly gone, the next scan cycle will re-flag it."""
    item = db.query(FailedImport).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Failed import not found")
    if item.status != "orphan_pending":
        raise HTTPException(status_code=400, detail="Item is not awaiting orphan confirmation")
    item.status = "suggested"
    item.resolved_at = None
    item.message = ((item.message + " | ") if item.message else "") + "Orphan dismissed — kept in triage"
    db.commit()
    return {"id": item.id, "status": item.status}


@router.post("/{item_id}/reject")
async def reject_import(item_id: int, remove_download: bool = Query(False), db: Session = Depends(get_db)):
    result = _reject(item_id, db)
    if remove_download:
        item = db.query(FailedImport).filter_by(id=item_id).first()
        if item and item.download_id:
            result["download_client"] = await import_matcher.remove_from_download_clients(item.download_id, db)
        else:
            result["download_client"] = ["No download id — nothing to remove"]
    return result
