import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Any

from app.database import get_db
from app.models.integration import Integration
from app.schemas.settings import IntegrationConfig, IntegrationConfigUpdate, QdrantSettings

router = APIRouter(prefix="/integrations", tags=["integrations"])

INTEGRATION_NAMES = ("plex", "tautulli", "sonarr", "radarr", "lidarr",
                     "readarr", "seerr", "qbittorrent", "transmission", "lastfm", "bazarr")

# Integrations that are torrent/download clients — anything needing "which client
# holds this download?" (orphan cleanup, reject-and-remove) iterates this, never
# hardcodes client names elsewhere.
DOWNLOAD_CLIENT_NAMES = ("qbittorrent", "transmission")


def _get_client(integration: Integration):
    from app.services.secret_box import decrypt
    extra = json.loads(integration.extra_config) if integration.extra_config else {}
    api_key = decrypt(integration.api_key) or ""
    password = decrypt(integration.password) or ""
    if integration.name == "plex":
        from app.integrations.plex import PlexIntegration
        return PlexIntegration(integration.url, api_key, extra)
    if integration.name == "tautulli":
        from app.integrations.tautulli import TautulliIntegration
        return TautulliIntegration(integration.url, api_key, extra)
    if integration.name == "sonarr":
        from app.integrations.sonarr import SonarrIntegration
        return SonarrIntegration(integration.url, api_key, extra)
    if integration.name == "radarr":
        from app.integrations.radarr import RadarrIntegration
        return RadarrIntegration(integration.url, api_key, extra)
    if integration.name == "lidarr":
        from app.integrations.lidarr import LidarrIntegration
        return LidarrIntegration(integration.url, api_key, extra)
    if integration.name == "readarr":
        from app.integrations.readarr import ReadarrIntegration
        return ReadarrIntegration(integration.url, api_key, extra)
    if integration.name == "seerr":
        from app.integrations.seerr import SeerrIntegration
        return SeerrIntegration(integration.url, api_key, extra)
    if integration.name == "qbittorrent":
        from app.integrations.qbittorrent import QbittorrentIntegration
        return QbittorrentIntegration(integration.url, api_key, extra,
                                      username=integration.username or "",
                                      password=password)
    if integration.name == "transmission":
        from app.integrations.transmission import TransmissionIntegration
        return TransmissionIntegration(integration.url, api_key, extra)
    if integration.name == "lastfm":
        from app.integrations.lastfm import LastFmIntegration
        return LastFmIntegration(integration.url, api_key, extra,
                                 username=integration.username or "")
    if integration.name == "bazarr":
        from app.integrations.bazarr import BazarrIntegration
        return BazarrIntegration(integration.url, api_key, extra)
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


# --- Qdrant (v0.40.0; shared by Smart Playlists + Artist Discovery) ---
# Registered before the generic /{name} routes so "qdrant" never falls through.

class QdrantSettingsOut(QdrantSettings):
    api_key_set: bool = False


@router.get("/qdrant/settings", response_model=QdrantSettingsOut)
def get_qdrant_settings(db: Session = Depends(get_db)):
    from app.services import qdrant_config
    cfg = qdrant_config.load_settings(db)
    out = QdrantSettingsOut(**cfg.model_dump())
    out.api_key_set = bool(cfg.api_key)
    out.api_key = ""
    return out


@router.put("/qdrant/settings", response_model=QdrantSettingsOut)
def put_qdrant_settings(body: QdrantSettings, db: Session = Depends(get_db)):
    from app.services import qdrant_config
    from app.services.secret_box import encrypt
    current = qdrant_config.load_settings(db)
    api_key = current.api_key
    if (body.api_key or "").strip():
        api_key = encrypt(body.api_key) or body.api_key
    cfg = QdrantSettings(url=body.url, api_key=api_key, collection=body.collection)
    qdrant_config.save_settings(db, cfg)
    out = QdrantSettingsOut(**cfg.model_dump())
    out.api_key_set = bool(cfg.api_key)
    out.api_key = ""
    return out


@router.post("/qdrant/test")
async def qdrant_test(db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.services import qdrant_config
    client = qdrant_config.client(db)
    if not client:
        return {"ok": False, "message": "Qdrant URL not configured"}
    result = await client.test_connection()
    if not result.get("ok"):
        return result
    info = await client.get_collection_info()
    return {**result, "collection_info": info}


@router.post("/qdrant/full-sync")
async def qdrant_full_sync(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Manual, on-demand full resync of every point in the shared collection —
    same differential sync Artist Discovery runs on its own schedule/Run Now,
    exposed here so it can be triggered from the connection card directly
    without visiting Music -> Artist Discovery. Never runs automatically from
    this route."""
    from app.services import artist_discovery as artist_discovery_service
    return await artist_discovery_service.run_differential_sync(db)


@router.post("/seerr/sync")
async def sync_seerr(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Refresh request-based deletion protection from Seerr on demand."""
    from app.services.plex_sync import refresh_seerr_protection
    try:
        protected = await refresh_seerr_protection(db)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Seerr sync failed: {e}")
    return {"protected": protected}


# Upstream API keys / download-client passwords are write-only over the API: a
# read masks them (SECRET_MASK) and reports only whether a secret is stored. They
# never leave the backend in cleartext, and the mask can't be written back (see
# _is_new_secret) so editing a URL can't wipe or round-trip a key.
SECRET_MASK = "sec_************"


def _is_new_secret(value: str | None) -> bool:
    """A secret field on update is applied only when it carries a real new value.
    A blank field or the display mask means 'leave the stored secret unchanged'."""
    return bool(value) and value != SECRET_MASK


def _public_config(row: Integration, extra: dict) -> IntegrationConfig:
    """Client-safe view of an integration row — secrets masked, never echoed."""
    return IntegrationConfig(
        name=row.name,
        url=row.url,
        api_key=SECRET_MASK if row.api_key else None,
        api_key_set=bool(row.api_key),
        username=row.username,  # username is not a secret
        password=SECRET_MASK if row.password else None,
        password_set=bool(row.password),
        enabled=row.enabled,
        remove_from_monitored_on_delete=extra.get("remove_from_monitored_on_delete", True),
        delete_from_arr_list=extra.get("delete_from_arr_list", False),
    )


@router.get("", response_model=list[IntegrationConfig])
def list_integrations(db: Session = Depends(get_db)):
    result = []
    for row in db.query(Integration).all():
        extra = json.loads(row.extra_config) if row.extra_config else {}
        result.append(_public_config(row, extra))
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
    if _is_new_secret(body.api_key):
        from app.services.secret_box import encrypt
        row.api_key = encrypt(body.api_key)
    if body.username is not None:
        row.username = body.username
    if _is_new_secret(body.password):
        from app.services.secret_box import encrypt
        row.password = encrypt(body.password)
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
    return _public_config(row, extra)


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
