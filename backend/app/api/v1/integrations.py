import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Any

from app.database import get_db
from app.models.integration import Integration
from app.schemas.settings import IntegrationConfig, IntegrationConfigUpdate

router = APIRouter(prefix="/integrations", tags=["integrations"])

INTEGRATION_NAMES = ("plex", "tautulli", "sonarr", "radarr", "lidarr",
                     "readarr", "seerr", "qbittorrent", "transmission")


def _get_client(integration: Integration):
    extra = json.loads(integration.extra_config) if integration.extra_config else {}
    if integration.name == "plex":
        from app.integrations.plex import PlexIntegration
        return PlexIntegration(integration.url, integration.api_key, extra)
    if integration.name == "tautulli":
        from app.integrations.tautulli import TautulliIntegration
        return TautulliIntegration(integration.url, integration.api_key, extra)
    if integration.name == "sonarr":
        from app.integrations.sonarr import SonarrIntegration
        return SonarrIntegration(integration.url, integration.api_key, extra)
    if integration.name == "radarr":
        from app.integrations.radarr import RadarrIntegration
        return RadarrIntegration(integration.url, integration.api_key, extra)
    if integration.name == "lidarr":
        from app.integrations.lidarr import LidarrIntegration
        return LidarrIntegration(integration.url, integration.api_key, extra)
    if integration.name == "readarr":
        from app.integrations.readarr import ReadarrIntegration
        return ReadarrIntegration(integration.url, integration.api_key, extra)
    if integration.name == "seerr":
        from app.integrations.seerr import SeerrIntegration
        return SeerrIntegration(integration.url, integration.api_key, extra)
    if integration.name == "qbittorrent":
        from app.integrations.qbittorrent import QbittorrentIntegration
        return QbittorrentIntegration(integration.url, integration.api_key, extra)
    if integration.name == "transmission":
        from app.integrations.transmission import TransmissionIntegration
        return TransmissionIntegration(integration.url, integration.api_key, extra)
    raise HTTPException(status_code=404, detail=f"Unknown integration: {integration.name}")


# --- Ollama (optional local-LLM assist; AppSetting-backed, not an Integration row) ---
# Registered before the generic /{name} routes so "ollama" never falls through to them.

def _ollama_settings(db: Session):
    from app.models.app_setting import AppSetting
    from app.schemas.settings import OllamaSettings
    row = db.query(AppSetting).filter_by(key="ollama").first()
    if not row or not row.value:
        return OllamaSettings()
    return OllamaSettings(**json.loads(row.value))


@router.get("/ollama/models")
async def ollama_models(db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.services import llm_assist
    cfg = _ollama_settings(db)
    return await llm_assist.list_models(cfg.host, cfg.api_style)


@router.post("/ollama/test")
async def ollama_test(db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.services import llm_assist
    cfg = _ollama_settings(db)
    return await llm_assist.test_connection(cfg.host, cfg.model, cfg.api_style)


@router.post("/seerr/sync")
async def sync_seerr(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Refresh request-based deletion protection from Seerr on demand."""
    from app.services.plex_sync import refresh_seerr_protection
    try:
        protected = await refresh_seerr_protection(db)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Seerr sync failed: {e}")
    return {"protected": protected}


@router.get("", response_model=list[IntegrationConfig])
def list_integrations(db: Session = Depends(get_db)):
    rows = db.query(Integration).all()
    result = []
    for row in rows:
        extra = json.loads(row.extra_config) if row.extra_config else {}
        result.append(IntegrationConfig(
            name=row.name,
            url=row.url,
            api_key=row.api_key,
            enabled=row.enabled,
            remove_from_monitored_on_delete=extra.get("remove_from_monitored_on_delete", True),
            delete_from_arr_list=extra.get("delete_from_arr_list", False),
        ))
    return result


@router.put("/{name}", response_model=IntegrationConfig)
def update_integration(name: str, body: IntegrationConfigUpdate, db: Session = Depends(get_db)):
    if name not in INTEGRATION_NAMES:
        raise HTTPException(status_code=404, detail="Unknown integration")
    row = db.query(Integration).filter_by(name=name).first()
    if not row:
        raise HTTPException(status_code=404, detail="Integration not found")

    if body.url is not None:
        row.url = body.url
    if body.api_key is not None:
        row.api_key = body.api_key
    if body.enabled is not None:
        row.enabled = body.enabled

    extra = json.loads(row.extra_config) if row.extra_config else {}
    if body.remove_from_monitored_on_delete is not None:
        extra["remove_from_monitored_on_delete"] = body.remove_from_monitored_on_delete
    if body.delete_from_arr_list is not None:
        extra["delete_from_arr_list"] = body.delete_from_arr_list
    row.extra_config = json.dumps(extra)

    db.commit()
    db.refresh(row)
    return IntegrationConfig(
        name=row.name, url=row.url, api_key=row.api_key, enabled=row.enabled,
        remove_from_monitored_on_delete=extra.get("remove_from_monitored_on_delete", True),
        delete_from_arr_list=extra.get("delete_from_arr_list", False),
    )


@router.post("/{name}/test")
async def test_integration(name: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.query(Integration).filter_by(name=name).first()
    if not row:
        raise HTTPException(status_code=404, detail="Integration not found")
    client = _get_client(row)
    return await client.test_connection()


@router.post("/plex/sync")
async def sync_plex(db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.services.plex_sync import run_plex_sync
    try:
        return await run_plex_sync(db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
