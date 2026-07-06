from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
import asyncio
import logging

from fastapi.responses import JSONResponse

from app.database import init_db
from app.api.v1 import media, settings, integrations, imports, system, auth
from app import log_buffer

logging.basicConfig(level=logging.INFO)
log_buffer.install()
logger = logging.getLogger("powarr")

app = FastAPI(title="Powarr", version="0.6.2", docs_url="/api/docs")

# Paths that stay reachable without a session: the auth flow itself, and the
# health endpoint (Docker healthcheck probes from inside the container).
_AUTH_EXEMPT = ("/api/v1/auth/", "/api/v1/system/health")


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
    _seed_settings()
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
                     "readarr", "seerr", "qbittorrent", "transmission"):
            if not db.query(Integration).filter_by(name=name).first():
                db.add(Integration(name=name))
        db.commit()
    finally:
        db.close()


def _seed_settings():
    from app.database import SessionLocal
    from app.models.app_setting import AppSetting
    from app.schemas.settings import (ScoringWeights, ImportMatchingSettings, OllamaSettings,
                                      CleanupSettings, SyncSettings)
    import json

    from app.schemas.settings import NotificationSettings
    defaults = {
        "scoring_weights": ScoringWeights,
        "import_matching": ImportMatchingSettings,
        "ollama": OllamaSettings,
        "cleanup": CleanupSettings,
        "sync": SyncSettings,
        "notifications": NotificationSettings,
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

STATIC_DIR = Path(__file__).parent.parent / "static"

if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        # no-cache: browsers must revalidate index.html so new deploys' hashed
        # bundles are picked up immediately (assets themselves are content-hashed)
        return FileResponse(str(STATIC_DIR / "index.html"),
                            headers={"Cache-Control": "no-cache"})
