import json
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.app_setting import AppSetting
from app.schemas.settings import (ScoringWeights, ScoringProfiles, ImportMatchingSettings,
                                  OllamaSettings, LlmPolicies, CleanupSettings, SyncSettings,
                                  NotificationSettings, LlmScheduleSettings, BackupSettings)

router = APIRouter(prefix="/settings", tags=["settings"])


def _get_json_setting(db: Session, key: str, schema):
    row = db.query(AppSetting).filter_by(key=key).first()
    if not row or not row.value:
        return schema()
    return schema(**json.loads(row.value))


def _put_json_setting(db: Session, key: str, model) -> None:
    row = db.query(AppSetting).filter_by(key=key).first()
    if not row:
        row = AppSetting(key=key)
        db.add(row)
    row.value = json.dumps(model.model_dump())
    db.commit()


def _get_weights(db: Session) -> ScoringWeights:
    row = db.query(AppSetting).filter_by(key="scoring_weights").first()
    if not row:
        return ScoringWeights()
    return ScoringWeights(**json.loads(row.value))


@router.get("/scoring", response_model=ScoringWeights)
def get_scoring_weights(db: Session = Depends(get_db)):
    return _get_weights(db)


@router.put("/scoring", response_model=ScoringWeights)
def update_scoring_weights(weights: ScoringWeights, db: Session = Depends(get_db)):
    row = db.query(AppSetting).filter_by(key="scoring_weights").first()
    if not row:
        row = AppSetting(key="scoring_weights")
        db.add(row)
    row.value = json.dumps(weights.model_dump())
    db.commit()

    from app.services.scorer import rescore_all, load_scoring_profiles
    rescore_all(db, weights, load_scoring_profiles(db))

    return weights


@router.get("/scoring-profiles", response_model=ScoringProfiles)
def get_scoring_profiles(db: Session = Depends(get_db)):
    return _get_json_setting(db, "scoring_profiles", ScoringProfiles)


@router.put("/scoring-profiles", response_model=ScoringProfiles)
def update_scoring_profiles(body: ScoringProfiles, db: Session = Depends(get_db)):
    _put_json_setting(db, "scoring_profiles", body)
    from app.services.scorer import rescore_all
    rescore_all(db, _get_weights(db), body)
    return body


@router.get("/import-matching", response_model=ImportMatchingSettings)
def get_import_matching(db: Session = Depends(get_db)):
    return _get_json_setting(db, "import_matching", ImportMatchingSettings)


@router.put("/import-matching", response_model=ImportMatchingSettings)
def update_import_matching(body: ImportMatchingSettings, db: Session = Depends(get_db)):
    # LLM-09 — validate user-authored regex at save time (fail loud here) so a
    # typo'd pattern is caught immediately rather than silently skipped on
    # every match at runtime (apply_custom_junk_rules() is fail-soft there —
    # this is the earlier, louder check for the common case).
    import re as _re
    for rule in body.junk_strip_rules:
        pattern = (rule.get("pattern") or "").strip() if isinstance(rule, dict) else ""
        if not pattern:
            raise HTTPException(status_code=400, detail=f"Junk strip rule '{rule.get('name', '(unnamed)')}' has no pattern")
        try:
            _re.compile(pattern)
        except _re.error as e:
            raise HTTPException(status_code=400, detail=f"Junk strip rule '{rule.get('name', pattern)}' has invalid regex: {e}")
    _put_json_setting(db, "import_matching", body)
    return body


@router.get("/ollama", response_model=OllamaSettings)
def get_ollama(db: Session = Depends(get_db)):
    return _get_json_setting(db, "ollama", OllamaSettings)


@router.put("/ollama", response_model=OllamaSettings)
def update_ollama(body: OllamaSettings, db: Session = Depends(get_db)):
    _put_json_setting(db, "ollama", body)
    from app.services import llm_assist
    llm_assist.set_breaker_config(body.breaker_threshold, body.breaker_cooldown_minutes)
    return body


@router.get("/llm-policies", response_model=LlmPolicies)
def get_llm_policies(db: Session = Depends(get_db)):
    return _get_json_setting(db, "llm_policies", LlmPolicies)


@router.put("/llm-policies", response_model=LlmPolicies)
def update_llm_policies(body: LlmPolicies, db: Session = Depends(get_db)):
    """LLM-08 — per-source_app match/blend overrides + per-Plex-library explain
    overrides. Purely a resolution-time overlay; no rescoring/re-blend triggered
    on save (unlike scoring-profiles, which eagerly rescores every item)."""
    _put_json_setting(db, "llm_policies", body)
    return body


@router.get("/llm/stats")
def llm_stats():
    """In-memory LLM call stats + circuit-breaker state (reset on restart)."""
    from app.services import llm_assist
    return llm_assist.get_stats()


@router.post("/llm/breaker/reset")
def llm_breaker_reset():
    """Manually close an open circuit breaker and clear the failure streak."""
    from app.services import llm_assist
    llm_assist.reset_breaker()
    return llm_assist.get_stats()


@router.get("/llm-schedule", response_model=LlmScheduleSettings)
def get_llm_schedule(db: Session = Depends(get_db)):
    return _get_json_setting(db, "llm_schedule", LlmScheduleSettings)


@router.put("/llm-schedule", response_model=LlmScheduleSettings)
def update_llm_schedule(body: LlmScheduleSettings, db: Session = Depends(get_db)):
    _put_json_setting(db, "llm_schedule", body)
    return body


@router.get("/backup", response_model=BackupSettings)
def get_backup(db: Session = Depends(get_db)):
    return _get_json_setting(db, "backup", BackupSettings)


@router.put("/backup", response_model=BackupSettings)
def update_backup(body: BackupSettings, db: Session = Depends(get_db)):
    _put_json_setting(db, "backup", body)
    return body


@router.post("/backup/run")
async def run_backup_now(db: Session = Depends(get_db)):
    from app.services.backup import run_backup, prune_backups
    result = await run_backup()
    if result["ok"]:
        cfg = _get_json_setting(db, "backup", BackupSettings)
        prune_backups(cfg.retention_count)
        row = db.query(AppSetting).filter_by(key="last_backup").first()
        if not row:
            row = AppSetting(key="last_backup")
            db.add(row)
        import datetime as _dt
        row.value = _dt.datetime.utcnow().isoformat()
        db.commit()
    return result


@router.get("/backup/list")
def list_backup_files():
    from app.services.backup import list_backups
    return list_backups()


@router.get("/export")
def export_settings_now(request: Request, db: Session = Depends(get_db)):
    """OPS-02 — config-as-code JSON snapshot (every AppSetting + non-secret
    integration metadata) as a downloadable attachment."""
    from app.services.settings_export import export_settings
    data = export_settings(db, request.app.version)
    return JSONResponse(
        content=data,
        headers={"Content-Disposition": 'attachment; filename="powarr-settings.json"'},
    )


@router.post("/import")
def import_settings_now(payload: dict = Body(...), db: Session = Depends(get_db)):
    """OPS-02 — restore a settings export produced by GET /settings/export.
    Never touches integration secrets (api_key/username/password/extra_config)
    — those aren't in the export to begin with, so a restored instance needs
    them re-entered."""
    from app.services.settings_export import import_settings
    try:
        return import_settings(db, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/export/run")
def run_settings_export_now(request: Request, db: Session = Depends(get_db)):
    """OPS-02 — write a settings export file into settings-exports/ (same
    on-demand shape as POST /settings/backup/run)."""
    from app.services.settings_export import run_settings_export, prune_settings_exports
    result = run_settings_export(db, request.app.version)
    if result["ok"]:
        cfg = _get_json_setting(db, "backup", BackupSettings)
        prune_settings_exports(cfg.export_settings_retention_count)
    return result


@router.get("/export/list")
def list_settings_export_files():
    from app.services.settings_export import list_settings_exports
    return list_settings_exports()


@router.get("/cleanup", response_model=CleanupSettings)
def get_cleanup(db: Session = Depends(get_db)):
    return _get_json_setting(db, "cleanup", CleanupSettings)


@router.put("/cleanup", response_model=CleanupSettings)
def update_cleanup(body: CleanupSettings, db: Session = Depends(get_db)):
    _put_json_setting(db, "cleanup", body)
    return body


@router.get("/sync", response_model=SyncSettings)
def get_sync(db: Session = Depends(get_db)):
    return _get_json_setting(db, "sync", SyncSettings)


@router.put("/sync", response_model=SyncSettings)
def update_sync(body: SyncSettings, db: Session = Depends(get_db)):
    _put_json_setting(db, "sync", body)
    return body


@router.get("/notifications", response_model=NotificationSettings)
def get_notifications(db: Session = Depends(get_db)):
    return _get_json_setting(db, "notifications", NotificationSettings)


@router.put("/notifications", response_model=NotificationSettings)
def update_notifications(body: NotificationSettings, db: Session = Depends(get_db)):
    _put_json_setting(db, "notifications", body)
    return body


@router.post("/notifications/test")
async def test_notification(db: Session = Depends(get_db)):
    from app.services import notifier
    ok = await notifier.notify(db, "Powarr test", "Notifications are working 🎉", tags="tada")
    return {"ok": ok, "message": "Sent" if ok else "Failed — check URL/topic and that notifications are enabled"}


@router.post("/ollama/refine-prompt")
async def refine_prompt(body: dict = Body(...), db: Session = Depends(get_db)):
    """Have the configured LLM clean up a rough prompt draft into a solid template."""
    from app.services import llm_assist
    cfg = _get_json_setting(db, "ollama", OllamaSettings)
    if not (cfg.enabled and cfg.host and cfg.model):
        raise HTTPException(status_code=400, detail="LLM assist is not enabled — configure it on the Integrations page")
    draft = (body.get("draft") or "").strip()
    task = body.get("task") or "match"
    if not draft:
        raise HTTPException(status_code=400, detail="Draft text required")
    refined = await llm_assist.refine_prompt(cfg.host, cfg.model, draft, task, cfg.api_style)
    if not refined:
        raise HTTPException(status_code=502, detail="No response from the LLM — try again or check the host")
    return {"refined": refined}


@router.get("/ollama/context-length")
async def ollama_context_length(db: Session = Depends(get_db)):
    """The saved model's real context window (Ollama /api/show), or null when the
    host/model isn't configured, the api style is openai, or the field is absent."""
    from app.services import llm_assist
    cfg = _get_json_setting(db, "ollama", OllamaSettings)
    length = await llm_assist.get_model_context_length(cfg.host, cfg.model, cfg.api_style)
    return {"context_length": length, "model": cfg.model or None}


# Canned inputs for the no-real-data benchmark case — fixed so latency numbers
# are comparable between runs and models.
_BENCH_MATCH = {
    "release": "The.Example.Show.S02E05.1080p.WEB.h264-GRP",
    "candidate": "The Example Show - S02E05 - The Middle Episode",
    "context": "Source app: sonarr. Queue error: no files eligible for import",
    "det_summary": "episode title similarity 0.91; season+episode numbers match (heuristic confidence 0.88)",
}
_BENCH_PACK = {
    "release": "The.Example.Show.S02.1080p.WEB-DL-GRP",
    "candidate": "The Example Show",
    "files": ["The.Example.Show.S02E01.mkv", "The.Example.Show.S02E02.mkv"],
    "folder": "The.Example.Show.S02.1080p.WEB-DL-GRP",
}
_BENCH_ITEM = ("The Example Movie (2011), movie, library=Movies, 8.2 GB, watched 0x, "
               "last watched never, deletion score 72.4/100 "
               "(factors: watch=1.00, size=0.40, age=0.20, release=0.55)")


@router.post("/ollama/preview")
async def ollama_preview(body: dict = Body(default={}), db: Session = Depends(get_db)):
    """Dry-run the saved prompt/model settings and report what came back.
    {"task": "match"|"explain"|"pack", "use_real_data": bool} — real data pulls one
    current row; canned data is the fixed self-test/benchmark. Nothing is persisted."""
    import time
    from app.services import llm_assist
    cfg = _get_json_setting(db, "ollama", OllamaSettings)
    if not (cfg.enabled and cfg.host and cfg.model):
        raise HTTPException(status_code=400, detail="LLM assist is not enabled — configure it on the Integrations page")
    task = body.get("task") or "match"
    use_real = bool(body.get("use_real_data"))
    pk = llm_assist.prompt_kwargs(cfg)
    if not llm_assist.acquire_slot():
        raise HTTPException(status_code=409, detail="An LLM run is already in progress")
    try:
        source = "canned sample"
        if task == "explain":
            item_summary = _BENCH_ITEM
            if use_real:
                from app.models.media import MediaItem
                from app.services.media_llm import item_summary as summarize
                row = (db.query(MediaItem).filter(MediaItem.score > 0)
                       .order_by(MediaItem.score.desc()).first())
                if row:
                    item_summary, source = summarize(row), f"media item #{row.id}"
            prompt = llm_assist.build_explain_prompt(
                cfg.explain_prompt, item_summary, cfg.verbosity, **pk)
            started = time.monotonic()
            raw = await llm_assist._generate(
                cfg.host, cfg.model_for("explain"), prompt, cfg.api_style, json_format=False,
                verbose=cfg.verbosity == "verbose", model_size=cfg.model_size,
                keep_alive_minutes=cfg.keep_alive_minutes, **llm_assist.inference_kwargs(cfg))
            latency_ms = round((time.monotonic() - started) * 1000)
            output = llm_assist._strip_think(raw or "")
            return {"output": output or None, "latency_ms": latency_ms, "json_valid": None,
                    "message": f"Ran the deletion-rationale prompt against {source}."
                               + ("" if output else " Empty reply after <think> stripping — try Minimal verbosity.")}

        if task == "pack":
            release = _BENCH_PACK["release"]
            candidate = _BENCH_PACK["candidate"]
            files = list(_BENCH_PACK["files"])
            folder = _BENCH_PACK["folder"]
            if use_real:
                import json as _json
                from app.models.failed_import import FailedImport
                # Pack flag lives in raw_metadata (no dedicated column).
                for row in (db.query(FailedImport)
                            .filter(FailedImport.matched_title.isnot(None))
                            .order_by(FailedImport.created_at.desc()).limit(40)):
                    if not row.pack:
                        continue
                    release = row.raw_title or release
                    candidate = row.matched_title or candidate
                    try:
                        meta = _json.loads(row.raw_metadata or "{}")
                    except (ValueError, TypeError):
                        meta = {}
                    path = meta.get("outputPath") or meta.get("output_path") or ""
                    if path:
                        folder = str(path).rstrip("/").split("/")[-1]
                    source = f"failed import #{row.id}"
                    break
            files_str = ", ".join(files)
            prompt = llm_assist.build_pack_prompt(
                cfg.pack_prompt, release, candidate, files_str, "Multi-file pack preview.",
                cfg.verbosity, folder_name=folder, **pk)
            started = time.monotonic()
            raw = await llm_assist._generate(
                cfg.host, cfg.model_for("match"), prompt, cfg.api_style, json_format=True,
                verbose=cfg.verbosity == "verbose", model_size=cfg.model_size,
                keep_alive_minutes=cfg.keep_alive_minutes, **llm_assist.inference_kwargs(cfg))
            latency_ms = round((time.monotonic() - started) * 1000)
            parsed = llm_assist._parse_pack_matches(raw or "")
            ok = bool(parsed)
            message = f"Ran the pack-file prompt against {source}."
            if not ok:
                message += " Reply did not parse as a file-match array — try Minimal verbosity."
            return {"output": llm_assist._strip_think(raw or "") or None, "latency_ms": latency_ms,
                    "json_valid": ok, "message": message}

        fields = dict(_BENCH_MATCH)
        preview_app = "sonarr"
        if use_real:
            from app.models.failed_import import FailedImport
            row = (db.query(FailedImport).filter(FailedImport.matched_title.isnot(None))
                   .order_by(FailedImport.created_at.desc()).first())
            if row:
                det = row.match_rationale or "series/title heuristics only"
                if getattr(cfg, "compact_det_summary", True):
                    det = llm_assist.compact_det_summary(
                        det, row.heuristic_confidence or row.confidence)
                else:
                    det = f"{det} (heuristic confidence {row.heuristic_confidence or row.confidence})"
                fields = {"release": row.raw_title, "candidate": row.matched_title,
                          "context": f"Source app: {row.source_app}. Queue error: {(row.message or '')[:200]}",
                          "det_summary": det}
                preview_app = row.source_app or "sonarr"
                source = f"failed import #{row.id}"
        prompt = llm_assist.build_review_prompt(
            cfg.match_prompt, fields["release"], fields["candidate"], fields["context"],
            fields["det_summary"], cfg.verbosity, llm_assist.REPLY_FORMAT, cfg.confidence_style,
            source_app=preview_app, **pk)
        started = time.monotonic()
        raw = await llm_assist._generate(
            cfg.host, cfg.model_for("match"), prompt, cfg.api_style,
            json_format=True, verbose=cfg.verbosity == "verbose",
            model_size=cfg.model_size, keep_alive_minutes=cfg.keep_alive_minutes,
            **llm_assist.inference_kwargs(cfg))
        latency_ms = round((time.monotonic() - started) * 1000)
        parsed = llm_assist._parse_json(raw or "") or llm_assist._parse_simple(raw or "")
        ok = bool(parsed and "agrees" in parsed)
        message = f"Ran the match-review prompt against {source}."
        if not ok:
            message += (" Reply did not parse as a verdict — this model may be too small for "
                        "structured matching; try Minimal verbosity or Classified confidence.")
        return {"output": llm_assist._strip_think(raw or "") or None, "latency_ms": latency_ms,
                "json_valid": ok, "message": message}
    finally:
        llm_assist.release_slot()
