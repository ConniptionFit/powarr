from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
import asyncio
import logging

from app.database import init_db
from app.api.v1 import media, settings, integrations, imports

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("powarr")

app = FastAPI(title="Powarr", version="0.1.0", docs_url="/api/docs")

_poller_task: asyncio.Task | None = None


@app.on_event("startup")
async def startup():
    global _poller_task
    logger.info("Powarr starting up...")
    init_db()
    _seed_integrations()
    _seed_settings()
    from app.services.import_matcher import poller_loop
    _poller_task = asyncio.create_task(poller_loop())


@app.on_event("shutdown")
async def shutdown():
    if _poller_task:
        _poller_task.cancel()


def _seed_integrations():
    from app.database import SessionLocal
    from app.models.integration import Integration

    db = SessionLocal()
    try:
        for name in ("plex", "tautulli", "sonarr", "radarr", "lidarr"):
            if not db.query(Integration).filter_by(name=name).first():
                db.add(Integration(name=name))
        db.commit()
    finally:
        db.close()


def _seed_settings():
    from app.database import SessionLocal
    from app.models.app_setting import AppSetting
    from app.schemas.settings import ScoringWeights, ImportMatchingSettings, OllamaSettings
    import json

    defaults = {
        "scoring_weights": ScoringWeights,
        "import_matching": ImportMatchingSettings,
        "ollama": OllamaSettings,
    }
    db = SessionLocal()
    try:
        for key, schema in defaults.items():
            if not db.query(AppSetting).filter_by(key=key).first():
                db.add(AppSetting(key=key, value=json.dumps(schema().model_dump())))
        db.commit()
    finally:
        db.close()


app.include_router(media.router, prefix="/api/v1")
app.include_router(settings.router, prefix="/api/v1")
app.include_router(integrations.router, prefix="/api/v1")
app.include_router(imports.router, prefix="/api/v1")

STATIC_DIR = Path(__file__).parent.parent / "static"

if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        return FileResponse(str(STATIC_DIR / "index.html"))
