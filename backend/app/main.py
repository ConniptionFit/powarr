from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
import asyncio
import logging
import time
import uuid

from fastapi.responses import JSONResponse

from app.database import init_db
from app.api.v1 import media, settings, integrations, imports, system, auth, tasks, playlists, artist_discovery
from app import log_buffer

logging.basicConfig(level=logging.INFO)
log_buffer.install()
logger = logging.getLogger("powarr")

app = FastAPI(title="Powarr", version="0.62.0", docs_url="/api/docs", openapi_url=None)

# Paths that stay reachable without a session: the auth flow itself, the
# health endpoint (Docker healthcheck probes from inside the container), and
# the ntfy click-to-act notification target (v0.26.0) — gated by its own
# signed, expiring, single-action token instead of a session.
_AUTH_EXEMPT = ("/api/v1/auth/", "/api/v1/system/health", "/api/v1/imports/notify-action")


@app.middleware("http")
async def request_id_middleware(request, call_next):
    """TEL-02 (v0.34.0): correlate logs across the async boundary."""
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    log_buffer.set_request_id(rid)
    started = time.perf_counter()
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        if request.url.path.startswith("/api/"):
            ms = int((time.perf_counter() - started) * 1000)
            logger.info(f"{request.method} {request.url.path} → {response.status_code} {ms}ms")
        return response
    finally:
        log_buffer.set_request_id(None)


@app.middleware("http")
async def auth_middleware(request, call_next):
    path = request.url.path
    # Only the API is gated; the SPA shell stays public so the login modal can render.
    if path.startswith("/api/") and not any(path.startswith(p) for p in _AUTH_EXEMPT):
        from app.database import SessionLocal
        from app.services import auth as auth_svc
        cfg = auth_svc.load_config_cached(SessionLocal)
        if not auth_svc.evaluate_request(request, cfg)["allowed"]:
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})
    return await call_next(request)

_poller_task: asyncio.Task | None = None
_maintenance_task: asyncio.Task | None = None


@app.on_event("startup")
async def startup():
    global _poller_task, _maintenance_task
    logger.info("Powarr starting up...")
    init_db()
    _seed_integrations()
    _migrate_qdrant_settings()
    _seed_settings()
    _migrate_auto_resolve_default()
    _apply_llm_breaker_config()
    from app.services.import_matcher import poller_loop
    from app.services.scheduler import maintenance_loop
    _poller_task = asyncio.create_task(poller_loop())
    _maintenance_task = asyncio.create_task(maintenance_loop())


@app.on_event("shutdown")
async def shutdown():
    for task in (_poller_task, _maintenance_task):
        if task:
            task.cancel()


def _seed_integrations():
    from app.database import SessionLocal
    from app.models.integration import Integration

    db = SessionLocal()
    try:
        for name in ("plex", "tautulli", "sonarr", "radarr", "lidarr",
                     "readarr", "seerr", "qbittorrent", "transmission", "lastfm"):
            if not db.query(Integration).filter_by(name=name).first():
                db.add(Integration(name=name))
        db.commit()
    finally:
        db.close()


def _apply_llm_breaker_config():
    """The circuit breaker lives in llm_assist module state (no DB access there by
    design) — push the saved thresholds in at startup; the PUT /settings/ollama
    endpoint re-applies them on every save."""
    from app.database import SessionLocal
    from app.models.app_setting import AppSetting
    from app.schemas.settings import OllamaSettings
    from app.services import llm_assist
    import json

    db = SessionLocal()
    try:
        row = db.query(AppSetting).filter_by(key="ollama").first()
        cfg = OllamaSettings(**json.loads(row.value)) if row and row.value else OllamaSettings()
        llm_assist.set_breaker_config(cfg.breaker_threshold, cfg.breaker_cooldown_minutes)
    finally:
        db.close()


def _migrate_qdrant_settings():
    """v0.40.0: Qdrant connection moved from per-module (smart_playlists.qdrant_*)
    to a single shared AppSetting key "qdrant". One-time carry-over for anyone who
    already configured Qdrant under Smart Playlists before this version — runs
    only when "qdrant" doesn't exist yet, and only if there's something to carry."""
    from app.database import SessionLocal
    from app.models.app_setting import AppSetting
    import json

    db = SessionLocal()
    try:
        if db.query(AppSetting).filter_by(key="qdrant").first():
            return  # already migrated or already configured directly
        old = db.query(AppSetting).filter_by(key="smart_playlists").first()
        if not old or not old.value:
            return
        try:
            data = json.loads(old.value)
        except ValueError:
            return
        url = data.get("qdrant_url") or ""
        if not url:
            return
        qdrant_cfg = {
            "url": url,
            "api_key": data.get("qdrant_api_key") or "",
            "collection": data.get("collection") or "music_affinity_space",
        }
        db.add(AppSetting(key="qdrant", value=json.dumps(qdrant_cfg)))
        db.commit()
        logger.info("Migrated Qdrant connection from smart_playlists settings to the shared 'qdrant' AppSetting")
    finally:
        db.close()


def _migrate_auto_resolve_default():
    """v0.43.0: auto_resolve_enabled changed from False to True by default.
    For existing DBs, preserve the current setting (don't force-upgrade).
    Fresh installs get the new default. This runs after _seed_settings()."""
    from app.database import SessionLocal
    from app.models.app_setting import AppSetting
    import json

    db = SessionLocal()
    try:
        row = db.query(AppSetting).filter_by(key="import_matching").first()
        if not row or not row.value:
            return  # _seed_settings() will handle it with new defaults
        try:
            cfg = json.loads(row.value)
            # If field is missing (pre-v0.43.0 JSON), add it with the new default
            if "auto_resolve_enabled" not in cfg:
                cfg["auto_resolve_enabled"] = True
                row.value = json.dumps(cfg)
                db.commit()
                logger.info("Migrated import_matching.auto_resolve_enabled to True (new default)")
            # If field exists (even as False from pre-v0.43.0), leave it alone
        except (ValueError, KeyError):
            pass  # Corrupted; leave as-is
    finally:
        db.close()


def _seed_settings():
    from app.database import SessionLocal
    from app.models.app_setting import AppSetting
    from app.schemas.settings import (ScoringWeights, ScoringProfiles, ImportMatchingSettings,
                                      OllamaSettings, CleanupSettings, SyncSettings,
                                      NotificationSettings, SmartPlaylistSettings)
    import json

    defaults = {
        "scoring_weights": ScoringWeights,
        "scoring_profiles": ScoringProfiles,
        "import_matching": ImportMatchingSettings,
        "ollama": OllamaSettings,
        "cleanup": CleanupSettings,
        "sync": SyncSettings,
        "notifications": NotificationSettings,
        "smart_playlists": SmartPlaylistSettings,
    }
    db = SessionLocal()
    try:
        for key, schema in defaults.items():
            if not db.query(AppSetting).filter_by(key=key).first():
                db.add(AppSetting(key=key, value=json.dumps(schema().model_dump())))
        # Auth seeded via its own service so the generated session_secret persists
        from app.services import auth as auth_svc
        if not db.query(AppSetting).filter_by(key="auth").first():
            db.add(AppSetting(key="auth", value=json.dumps(auth_svc.default_config())))
        db.commit()
    finally:
        db.close()


app.include_router(media.router, prefix="/api/v1")
app.include_router(settings.router, prefix="/api/v1")
app.include_router(integrations.router, prefix="/api/v1")
app.include_router(imports.router, prefix="/api/v1")
app.include_router(system.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(tasks.router, prefix="/api/v1")
app.include_router(playlists.router, prefix="/api/v1")
app.include_router(artist_discovery.router, prefix="/api/v1")

STATIC_DIR = Path(__file__).parent.parent / "static"

if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        # no-cache: browsers must revalidate index.html so new deploys' hashed
        # bundles are picked up immediately (assets themselves are content-hashed)
        return FileResponse(str(STATIC_DIR / "index.html"),
                            headers={"Cache-Control": "no-cache"})
