import asyncio
import json
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from fastapi.responses import StreamingResponse, HTMLResponse
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db, SessionLocal
from app.models.failed_import import FailedImport
from app.models.integration import Integration
from app.schemas.failed_import import FailedImportOut, ImportStats, AutoEligibleOut, RecentDownloadOut
from app.services import import_matcher
from app.services.import_matcher import scan_once, _get_client
from app.services.auto_eligible import (
    load_import_matching, list_auto_eligible_ids, auto_eligible_query, describe_auto_gate,
)

router = APIRouter(prefix="/imports", tags=["imports"])

STATUSES = ("suggested", "auto_resolved", "accepted", "rejected", "closed_external",
            "resolve_failed", "orphan_pending", "orphaned")
# Virtual list filters (not FailedImport.status values) — v0.35.0
NEEDS_ATTENTION_STATUSES = ("suggested", "resolve_failed", "orphan_pending")
# Terminal statuses that can be reopened when the download is still queued
REOPENABLE_STATUSES = ("accepted", "rejected", "orphaned", "closed_external", "auto_resolved")
# Still queued "out of scope" leftovers — exclude closed_external (historical noise
# from pack siblings sharing a downloadId) and Needs attention (already listed).
STILL_QUEUED_STATUSES = ("accepted", "rejected", "orphaned", "auto_resolved")


def _dedupe_still_queued(rows: list) -> list:
    """One row per download_id (or queue_item_id), newest first — pack siblings share ids."""
    seen: set[str] = set()
    out = []
    for row in rows:
        key = f"d:{row.download_id}" if row.download_id else f"q:{row.queue_item_id}"
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


@router.get("/notify-action")
async def notify_action(token: str, db: Session = Depends(get_db)):
    """Click-to-act target for the ntfy Accept/Reject notification buttons
    (v0.26.0) — auth-exempt (see main.py's _AUTH_EXEMPT) since the click comes
    from the ntfy client/app, not a browser session; a signed, expiring,
    action-scoped token is the gate instead. Explicitly re-checks the row's
    status (rather than trusting _accept/_reject, which have no such guard) so
    a stale or replayed link is a safe no-op instead of a duplicate *arr push."""
    from app.services.action_tokens import verify_action_token
    result = verify_action_token(db, token)
    if not result:
        return HTMLResponse("<h3>This link has expired or is invalid.</h3>", status_code=400)
    item_id, action = result
    item = db.query(FailedImport).filter_by(id=item_id).first()
    if not item:
        return HTMLResponse("<h3>This suggestion no longer exists.</h3>", status_code=404)
    if item.status not in ("suggested", "resolve_failed"):
        return HTMLResponse(f"<h3>Already {item.status.replace('_', ' ')} — no action taken.</h3>")
    try:
        result_data = await _accept(item_id, db) if action == "accept" else _reject(item_id, db)
    except HTTPException as e:
        return HTMLResponse(f"<h3>Couldn't {action} '{item.raw_title}'.</h3><p>{e.detail}</p>", status_code=e.status_code)
    verb = "accepted" if action == "accept" else "rejected"
    return HTMLResponse(f"<h3>'{item.raw_title}' {verb}.</h3><p>{result_data.get('message', '')}</p>")


@router.get("", response_model=list[FailedImportOut])
def list_imports(
    db: Session = Depends(get_db),
    status: Optional[str] = Query(
        None,
        description="Status filter, or virtual: needs_attention | still_in_queue (v0.35.0)",
    ),
    limit: int = Query(200),
    offset: int = Query(0),
):
    q = db.query(FailedImport)
    if status == "needs_attention":
        q = q.filter(FailedImport.status.in_(NEEDS_ATTENTION_STATUSES))
    elif status == "still_in_queue":
        # Out-of-scope leftovers: stuck in *arr after Accept/Reject/orphan/auto-resolve.
        rows = (
            db.query(FailedImport)
            .filter(
                FailedImport.still_in_queue.is_(True),
                FailedImport.status.in_(STILL_QUEUED_STATUSES),
            )
            .order_by(FailedImport.created_at.desc())
            .limit(5000)
            .all()
        )
        deduped = _dedupe_still_queued(rows)
        return deduped[offset:offset + max(limit, 1000)]
    elif status:
        q = q.filter(FailedImport.status == status)
    return q.order_by(FailedImport.created_at.desc()).offset(offset).limit(limit).all()


@router.get("/recent-downloads", response_model=list[RecentDownloadOut])
async def recent_downloads(
    db: Session = Depends(get_db),
    source_app: Optional[str] = Query(None, description="Narrow to one *arr app; all enabled apps otherwise"),
    search: Optional[str] = Query(None, description="Case-insensitive match against release title or library title"),
    limit: int = Query(100, le=500),
):
    """FI-09 — browse/search recently grabbed downloads (history-based, all
    enabled *arr apps), independent of stuck-import detection. Distinct from
    Scan Now, which only surfaces items the queue/history heuristics flag as
    stuck."""
    from app.services.recent_downloads import list_recent_downloads
    return await list_recent_downloads(db, source_app=source_app, search=search, max_records=limit)


@router.post("/recent-downloads/reimport")
async def force_reimport(payload: dict = Body(...), db: Session = Depends(get_db)):
    """FI-09 — force a re-import of a specific past grab, chosen from
    /recent-downloads rather than the stuck-import triage table. Goes through
    the exact same push_import_command() path as Accept (downloadId-only
    manual-import GET, library-folder guard) — this endpoint only supplies a
    different source for download_id/matched_id, it does not bypass any of
    those safety rules. Never writes a FailedImport row."""
    source_app = payload.get("source_app")
    download_id = payload.get("download_id")
    matched_id = payload.get("matched_id")
    if not source_app or not download_id or not matched_id:
        raise HTTPException(status_code=400, detail="source_app, download_id, and matched_id are required")
    row = db.query(Integration).filter_by(name=source_app, enabled=True).first()
    if not row:
        raise HTTPException(status_code=400, detail=f"{source_app} integration not enabled")
    client = _get_client(source_app, row)
    result = await client.push_import_command(download_id, matched_id)
    return result


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
    cfg = load_import_matching(db)
    auto_eligible_count = auto_eligible_query(db, cfg).count() if cfg.auto_resolve_enabled else 0
    still_rows = (
        db.query(FailedImport)
        .filter(
            FailedImport.still_in_queue.is_(True),
            FailedImport.status.in_(STILL_QUEUED_STATUSES),
        )
        .order_by(FailedImport.created_at.desc())
        .limit(5000)
        .all()
    )
    still_in_queue = len(_dedupe_still_queued(still_rows))
    needs_attention = sum(counts[s] for s in NEEDS_ATTENTION_STATUSES)
    return ImportStats(
        **counts, by_service=by_service, auto_resolved_7d=auto_7d,
        auto_eligible_count=auto_eligible_count,
        still_in_queue=still_in_queue, needs_attention=needs_attention,
    )


@router.get("/export.csv")
def export_imports_csv(
    db: Session = Depends(get_db),
    status: Optional[str] = Query(None),
    limit: int = Query(10000),
):
    """CSV of failed-import triage rows (Approved Queue #14)."""
    from app.services.csv_export import streaming_csv, _dt
    q = db.query(FailedImport)
    if status:
        q = q.filter(FailedImport.status == status)
    items = q.order_by(FailedImport.created_at.desc()).limit(min(limit, 20000)).all()
    rows = [[
        i.id, i.source_app, i.raw_title, i.matched_title or "", i.matched_id or "",
        round(i.confidence or 0, 3) if i.confidence is not None else "",
        i.status, bool(i.quality_downgrade), i.message or "",
        _dt(i.created_at), _dt(i.resolved_at),
    ] for i in items]
    return streaming_csv(
        "powarr-failed-imports.csv",
        ["id", "source_app", "raw_title", "matched_title", "matched_id",
         "confidence", "status", "quality_covered", "message",
         "created_at", "resolved_at"],
        rows,
    )


@router.get("/llm-log/export.csv")
def export_llm_match_log_csv(
    db: Session = Depends(get_db),
    limit: int = Query(10000),
):
    """CSV of match-review LLM call logs for offline prompt-engineering replay
    (LLM-LOG-01) — inputs, raw reply, parsed verdict, App-check flags, and the
    ground-truth resolution backfilled from the closed failed-import row."""
    from app.services.csv_export import streaming_csv, _dt
    from app.models.llm_match_log import LlmMatchLog
    items = (db.query(LlmMatchLog).order_by(LlmMatchLog.created_at.desc())
             .limit(min(limit, 20000)).all())
    rows = [[
        i.id, _dt(i.created_at), i.failed_import_id or "", i.site or "",
        i.source_app or "", i.model or "", i.scaffold_version or "",
        i.prompt_hash or "", i.release_title or "", i.candidate_title or "",
        i.context or "", i.det_summary or "",
        "" if i.evidence_artist_ok is None else i.evidence_artist_ok,
        "" if i.evidence_album_ok is None else i.evidence_album_ok,
        i.raw_reply or "", "" if i.parse_ok is None else i.parse_ok,
        "" if i.agrees is None else i.agrees,
        "" if i.confidence_adjustment is None else i.confidence_adjustment,
        "" if i.enforced is None else i.enforced,
        i.latency_ms or "", i.resolution or "", _dt(i.resolved_at),
    ] for i in items]
    return streaming_csv(
        "powarr-llm-match-log.csv",
        ["id", "created_at", "failed_import_id", "site", "source_app", "model",
         "scaffold_version", "prompt_hash", "release_title", "candidate_title",
         "context", "det_summary", "evidence_artist_ok", "evidence_album_ok",
         "raw_reply", "parse_ok", "agrees", "confidence_adjustment", "enforced",
         "latency_ms", "resolution", "resolved_at"],
        rows,
    )


@router.get("/trends")
def import_trends(db: Session = Depends(get_db), days: int = Query(30, ge=7, le=90)):
    """Daily new/resolved failed-import counts for the dashboard sparkline (#18)."""
    from sqlalchemy import func, cast, Date
    from app.config import settings as app_settings
    cutoff = datetime.utcnow() - timedelta(days=days)
    # Postgres: cast to Date; SQLite: date() via func.date
    if app_settings.is_sqlite:
        day_expr_created = func.date(FailedImport.created_at)
        day_expr_resolved = func.date(FailedImport.resolved_at)
    else:
        day_expr_created = cast(FailedImport.created_at, Date)
        day_expr_resolved = cast(FailedImport.resolved_at, Date)

    new_rows = (db.query(day_expr_created, func.count(FailedImport.id))
                .filter(FailedImport.created_at >= cutoff)
                .group_by(day_expr_created).all())
    resolved_rows = (db.query(day_expr_resolved, func.count(FailedImport.id))
                     .filter(FailedImport.resolved_at >= cutoff,
                             FailedImport.status.in_(
                                 ("auto_resolved", "accepted", "rejected", "orphaned")))
                     .group_by(day_expr_resolved).all())

    def _key(d):
        if d is None:
            return None
        return d.isoformat() if hasattr(d, "isoformat") else str(d)

    new_map = {_key(d): n for d, n in new_rows if d is not None}
    res_map = {_key(d): n for d, n in resolved_rows if d is not None}
    labels, new_vals, res_vals = [], [], []
    for i in range(days - 1, -1, -1):
        day = (datetime.utcnow() - timedelta(days=i)).date().isoformat()
        labels.append(day)
        new_vals.append(int(new_map.get(day, 0)))
        res_vals.append(int(res_map.get(day, 0)))
    return {"days": days, "labels": labels, "new": new_vals, "resolved": res_vals}


@router.get("/auto-eligible", response_model=AutoEligibleOut)
def auto_eligible(db: Session = Depends(get_db)):
    """IDs eligible for the Process N Items button (v0.28.0).

    Server-backed so the count and the subsequent batch-accept agree on
    auto_resolve_enabled + high_confidence_threshold + matched_id.
    """
    cfg = load_import_matching(db)
    ids = list_auto_eligible_ids(db, cfg) if cfg.auto_resolve_enabled else []
    return AutoEligibleOut(
        enabled=cfg.auto_resolve_enabled,
        threshold=cfg.high_confidence_threshold,
        count=len(ids),
        ids=ids,
    )


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
    from app.schemas.settings import OllamaSettings
    cfg = db.query(AppSetting).filter_by(key="ollama").first()
    ollama_cfg = OllamaSettings(**json.loads(cfg.value)) if cfg and cfg.value else OllamaSettings()
    if not ollama_cfg.task_enabled("match"):
        raise HTTPException(status_code=400, detail="LLM assist is not enabled for import matching — check Settings → LLM Assist")
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


@router.post("/rescore")
async def rescore(payload: dict = Body(default={}), db: Session = Depends(get_db)):
    """Deterministic (no-LLM) rescore of Lidarr/Readarr matches (v0.37.0): re-runs
    the containment-check-aware album/book matcher against a fresh library/history
    fetch. {"ids": [...]} for checked rows; omit to rescore all open music/book
    rows. Runs in the background; an SSE "rescore" event fires when it finishes."""
    from app.services import tasks
    from app.services.import_matcher import rescore_music
    ids = payload.get("ids") or None
    q = db.query(FailedImport).filter(
        FailedImport.source_app.in_(("lidarr", "readarr")))
    if ids:
        q = q.filter(FailedImport.id.in_(ids))
    else:
        q = q.filter(FailedImport.status.in_(("suggested", "resolve_failed")))
    count = q.count()
    tasks.spawn_background(rescore_music(ids))
    return {"started": count,
            "message": f"Rescore started on {count} music/book item(s) — no LLM, "
                       "results stream in live"}


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
    from app.schemas.settings import OllamaSettings
    row_cfg = db.query(AppSetting).filter_by(key="ollama").first()
    ollama_cfg = OllamaSettings(**json.loads(row_cfg.value)) if row_cfg and row_cfg.value else OllamaSettings()
    if not ollama_cfg.task_enabled("match"):
        raise HTTPException(status_code=400, detail="LLM assist is not enabled for import matching — check Settings → LLM Assist")

    # Fetch file list from the download
    row = db.query(Integration).filter_by(name=item.source_app, enabled=True).first()
    if not row:
        return {"matches": [], "message": f"{item.source_app} integration not enabled"}
    client = _get_client(item.source_app, row)
    folder = import_matcher.extract_output_path(raw_metadata=item.raw_metadata,
                                                messages=item.message)
    try:
        candidates = await client.get_manual_import(item.download_id, folder=folder)
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

    folder = import_matcher.extract_output_path(raw_metadata=item.raw_metadata,
                                                messages=item.message) or ""
    folder_name = folder.rstrip("/").split("/")[-1] if folder else ""
    matches = await llm_assist.review_pack_files(
        host=ollama_cfg.host, model=ollama_cfg.model_for("match"),
        release_title=item.raw_title, candidate_title=item.matched_title,
        file_names=file_names,
        api_style=ollama_cfg.api_style,
        template=ollama_cfg.pack_prompt,
        verbosity=ollama_cfg.verbosity,
        model_size=ollama_cfg.model_size,
        keep_alive_minutes=ollama_cfg.keep_alive_minutes,
        folder_name=folder_name,
        **llm_assist.prompt_kwargs(ollama_cfg),
        **llm_assist.inference_kwargs(ollama_cfg),
    )

    # Persist results to database
    if matches:
        item.pack_file_matches = json.dumps(matches)
        item.updated_at = datetime.utcnow()
        db.commit()

    return {"matches": matches or [], "file_count": len(file_names)}


# Coalesced accept queue (v0.33.0): single-item Accept and batch Accept share one
# running import_batch tray card; additional Accepts bump total instead of stacking
# duplicate cards of the same kind. Different kinds (LLM vs import) still stack.
_accept_lock = asyncio.Lock()
_accept_pending: list[int] = []
_accept_task_id: str | None = None
_accept_worker_running = False


async def _enqueue_accepts(ids: list[int]) -> dict:
    """Append ids to the coalesced accept queue; start a worker if needed.
    Returns {async, task_id, total, added, coalesced} for the API response.
    Locked so concurrent Accepts can't spawn duplicate workers/cards."""
    global _accept_task_id, _accept_worker_running
    from app.services import tasks
    async with _accept_lock:
        pending_set = set(_accept_pending)
        added = [i for i in ids if i not in pending_set]
        _accept_pending.extend(added)
        t = tasks.get_task(_accept_task_id) if _accept_task_id else None
        # Live card: bump total so the tray count grows as more Accepts land.
        if t and t.status == "running" and _accept_worker_running:
            if added:
                total = (t.total or 0) + len(added)
                tasks.bump_total(
                    _accept_task_id, len(added),
                    label=f"Importing {total} item(s)",
                    message=f"+{len(added)} queued — {total} total",
                )
            else:
                total = t.total or 0
            return {"async": True, "task_id": _accept_task_id, "total": total,
                    "added": len(added), "coalesced": True, "results": []}
        # Worker is shutting down (or between finish and finally) — leave items
        # in pending; the worker's finally block starts the next card+worker.
        if _accept_worker_running:
            return {"async": True, "task_id": _accept_task_id or "",
                    "total": (t.total if t else 0) or len(_accept_pending),
                    "added": len(added), "coalesced": True, "results": []}
        # Idle — open a fresh tray card and worker.
        total = len(_accept_pending)
        if total == 0:
            return {"async": True, "task_id": "", "total": 0,
                    "added": 0, "coalesced": False, "results": []}
        _accept_task_id = tasks.create_task(
            "import_batch", f"Importing {total} item(s)", total=total)
        _accept_worker_running = True
        tasks.spawn_background(_accept_worker())
        return {"async": True, "task_id": _accept_task_id, "total": total,
                "added": len(added), "coalesced": False, "results": []}


async def _accept_worker() -> None:
    """Drain the coalesced accept queue; own SessionLocal per item."""
    global _accept_worker_running, _accept_task_id
    from app.services import tasks
    ok = orphaned = failed = 0
    processed = 0
    task_id = _accept_task_id
    try:
        while True:
            async with _accept_lock:
                if not _accept_pending:
                    break
                item_id = _accept_pending.pop(0)
                task_id = _accept_task_id or task_id
            processed += 1
            db = SessionLocal()
            try:
                result = await _accept(item_id, db)
                if result.get("ok"):
                    ok += 1
                elif result.get("status") == "orphaned" or result.get("reason") == "no_files":
                    orphaned += 1
                else:
                    failed += 1
                parts = [f"{ok} imported"]
                if orphaned:
                    parts.append(f"{orphaned} gone")
                if failed:
                    parts.append(f"{failed} failed")
                msg = ", ".join(parts)
                if result.get("message"):
                    msg = f"{msg} — {result['message']}"
                t = tasks.get_task(task_id) if task_id else None
                total = t.total if t and t.total else processed
                if t:
                    tasks.update_task(
                        task_id, current=processed, total=total,
                        label=f"Importing {total} item(s)", message=msg)
            except HTTPException as e:
                failed += 1
                if task_id:
                    tasks.update_task(
                        task_id, current=processed,
                        message=f"{ok} imported, {orphaned} gone, {failed} failed — {e.detail}")
            except Exception as e:
                failed += 1
                if task_id:
                    tasks.update_task(
                        task_id, current=processed,
                        message=f"{ok} imported, {orphaned} gone, {failed} failed — {e}")
            finally:
                db.close()
        finish = f"{ok} imported, {orphaned} gone (orphaned), {failed} failed"
        status = "done" if failed == 0 else ("failed" if ok == 0 and orphaned == 0 else "done")
        if task_id:
            tasks.finish_task(task_id, status, finish)
        import_matcher.publish({
            "type": "import_batch",
            "ok": ok, "orphaned": orphaned, "failed": failed,
            "total": processed, "task_id": task_id,
        })
    except Exception as e:
        if task_id:
            tasks.finish_task(task_id, "failed", str(e))
        import_matcher.publish({
            "type": "import_batch",
            "ok": ok, "orphaned": orphaned, "failed": failed,
            "total": processed, "task_id": task_id, "error": str(e),
        })
    finally:
        # Under the lock: clear the worker flag and, if Accepts arrived during
        # finish, start a fresh card+worker so nothing is orphaned.
        async with _accept_lock:
            _accept_worker_running = False
            if _accept_pending:
                total = len(_accept_pending)
                _accept_task_id = tasks.create_task(
                    "import_batch", f"Importing {total} item(s)", total=total)
                _accept_worker_running = True
                tasks.spawn_background(_accept_worker())
            else:
                _accept_task_id = None


@router.post("/batch")
async def batch_action(payload: dict = Body(...), db: Session = Depends(get_db)):
    """Accept / reject / confirm_orphan / reopen several suggestions at once.

    Body: {"ids": [...], "action": "accept"|"reject"|"confirm_orphan"|"reopen"}.
    Accept runs in the background (v0.28.0) so large batches don't 504 at the
    reverse proxy — returns {async: true, task_id, total} immediately; progress
    lands in the Active Processes tray via the tasks SSE bus. Additional Accepts
    while one is running coalesce onto the same tray card (v0.33.0). Reject,
    confirm_orphan, and reopen stay synchronous (cheap DB writes).
    """
    ids = payload.get("ids") or []
    action = payload.get("action")
    if action not in ("accept", "reject", "confirm_orphan", "reopen") or not ids:
        raise HTTPException(
            status_code=400,
            detail="Body must be {ids: [...], action: accept|reject|confirm_orphan|reopen}",
        )
    if action == "accept":
        return await _enqueue_accepts(list(ids))
    results = []
    for item_id in ids:
        try:
            if action == "confirm_orphan":
                results.append(_confirm_orphan(item_id, db))
            elif action == "reopen":
                results.append(_reopen(item_id, db))
            else:
                results.append(_reject(item_id, db))
        except HTTPException as e:
            results.append({"id": item_id, "error": e.detail})
    return {"async": False, "results": results}


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
    folder = import_matcher.extract_output_path(raw_metadata=item.raw_metadata,
                                                messages=item.message)
    try:
        candidates = await client.get_manual_import(item.download_id, folder=folder)
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
        rejections = [r.get("reason") for r in (f.get("rejections") or [])]
        covered = import_matcher.file_is_covered(f)
        files.append({
            "path": f.get("relativePath") or f.get("path"),
            "raw_path": raw_path,
            "size": f.get("size", 0),
            "quality": ((f.get("quality") or {}).get("quality") or {}).get("name"),
            "mapped_to": mapped,
            "detail": detail,
            "overridden": override is not None,
            "rejections": rejections,
            # v0.32.0 — green/red row highlighting for gap-fill packs/albums
            "import_status": "covered" if covered else ("ok" if not rejections else "blocked"),
        })
    return {"files": files, "message": None}


@router.get("/{item_id}/auto-gate")
def auto_gate_status(item_id: int, db: Session = Depends(get_db)):
    """Read-only breakdown of the dual-signal auto-import gate for this row (FI-07)
    — which leg(s) passed/failed against the configured thresholds and, when it
    isn't auto-importing, exactly why not. Inspects existing signals only, never
    triggers a rescore or LLM call."""
    item = db.query(FailedImport).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Failed import not found")
    cfg = load_import_matching(db)
    return describe_auto_gate(item, cfg)


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
    folder = import_matcher.extract_output_path(raw_metadata=item.raw_metadata,
                                                messages=item.message)
    if item.source_app == "sonarr" and item.mapping_overrides:
        result = await client.push_import_command(
            item.download_id, item.matched_id,
            overrides=json.loads(item.mapping_overrides), folder=folder)
    else:
        result = await client.push_import_command(
            item.download_id, item.matched_id, folder=folder)
    item.message = result["message"]
    if result["ok"]:
        item.status = "accepted"
        item.resolved_at = datetime.utcnow()
        if result.get("partial"):
            item.partial_import = True
    elif result.get("reason") == "all_covered":
        # Every file already in the library — treat like Covered auto-reject.
        item.status = "rejected"
        item.resolved_at = datetime.utcnow()
        item.quality_downgrade = True
        item.partial_import = False
    elif result.get("reason") == "no_files" or import_matcher.looks_like_missing_files(result.get("message")):
        # *arr returned zero importable candidates for this downloadId — the
        # files are gone (removed/imported elsewhere). Don't leave the row in
        # triage as a push failure; mark orphaned with a clear warning.
        item.status = "orphaned"
        item.resolved_at = datetime.utcnow()
        item.verified = False
        warn = "Download files are gone — nothing left to import"
        item.message = warn if not item.message else (
            item.message if warn in item.message else f"{item.message} | {warn}")
        result = {**result, "ok": False, "reason": "no_files", "message": item.message,
                  "warning": warn}
        # Clear the *arr's dead queue record too (orphan_auto_purge opt-in) —
        # otherwise the same entry is re-detected as a new suggestion every scan.
        await import_matcher.purge_dead_queue_entry(client, item, load_import_matching(db))
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


def _reopen(item_id: int, db: Session) -> dict:
    """Put a terminal-status row back into triage (v0.35.0 Still queued view).

    Used when Accept/Reject already ran but the download is still stuck in the
    *arr queue — reopen → suggested so Accept/Reject/LLM work again. Prefer
    still_in_queue=True rows; allow reopen without that flag for manual recovery.
    """
    item = db.query(FailedImport).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Failed import not found")
    if item.status in NEEDS_ATTENTION_STATUSES:
        return {"id": item.id, "status": item.status, "message": "Already in triage"}
    if item.status not in REOPENABLE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reopen status '{item.status}'",
        )
    prev = item.status
    item.status = "suggested"
    item.resolved_at = None
    item.message = ((item.message + " | ") if item.message else "") + f"Reopened from {prev}"
    item.updated_at = datetime.utcnow()
    db.commit()
    return {"id": item.id, "status": item.status, "previous_status": prev}




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
    if item.status in REOPENABLE_STATUSES or item.status == "resolve_failed":
        item.status = "suggested"
        item.resolved_at = None
    db.commit()
    return {"id": item.id, "matched_id": item.matched_id, "matched_title": item.matched_title,
            "confidence": item.confidence, "status": item.status}


@router.post("/{item_id}/accept")
async def accept_import(item_id: int, db: Session = Depends(get_db)):
    """Queue a single Accept onto the coalesced import_batch tray card (v0.33.0).
    Returns immediately with {async, task_id, …}; progress streams via Active Processes."""
    # Validate the row exists before queueing so the UI gets a fast 404.
    item = db.query(FailedImport).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Failed import not found")
    return await _enqueue_accepts([item_id])


@router.post("/{item_id}/confirm-orphan")
def confirm_orphan(item_id: int, db: Session = Depends(get_db)):
    return _confirm_orphan(item_id, db)


@router.post("/{item_id}/reopen")
def reopen_import(item_id: int, db: Session = Depends(get_db)):
    """Return a terminal-status row to suggested triage (Still queued / History)."""
    return _reopen(item_id, db)


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


@router.post("/{item_id}/recover")
async def recover_series(item_id: int, command: str = Query("RescanSeries"), db: Session = Depends(get_db)):
    """Trigger a recovery command for a stuck series/media (OPS-01).
    Usage: after an incident (e.g., v0.6.3 One Piece), rescan library files
    to rebuild metadata/index. Endpoints: RescanSeries, RetagSeries, RefreshMovie, etc.

    Args:
        item_id: FailedImport row id
        command: Sonarr/Radarr command name (RescanSeries, RetagSeries, RefreshMovie, RefreshAlbum, etc)
    """
    item = db.query(FailedImport).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Import not found")
    if not item.matched_id:
        raise HTTPException(status_code=400, detail="No matched series/media id available for recovery")

    try:
        client = _get_client(item.source_app, db)
        result = await client.run_command(command, item.matched_id)
        return {"ok": result.get("ok"), "message": result.get("message"), "commandId": result.get("commandId")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Recovery failed: {str(e)}")
