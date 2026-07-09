import json
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.log_buffer import get_recent_logs
from app.models.app_setting import AppSetting
from app.schemas.settings import ImportMatchingSettings, SyncSettings

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    """Docker HEALTHCHECK target — DB only, always cheap (auth-exempt)."""
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "db": "ok" if db_ok else "error"}


@router.get("/dependencies")
async def dependencies(probe: bool = Query(False), db: Session = Depends(get_db)):
    """Per-integration health (TEL-01, v0.34.0). Cached from scan by default;
    pass probe=true for a live test_connection fan-out of enabled integrations."""
    from app.models.integration import Integration
    from app.services import circuit_breaker, dep_health
    from app.api.v1.integrations import _get_client, INTEGRATION_NAMES

    enabled = db.query(Integration).filter(Integration.enabled == True).all()  # noqa: E712
    names = [r.name for r in enabled if r.name in INTEGRATION_NAMES]
    if probe:
        for row in enabled:
            if row.name not in INTEGRATION_NAMES:
                continue
            if circuit_breaker.breaker_open(row.name):
                dep_health.record(row.name, False, "circuit breaker open", source="probe")
                continue
            try:
                client = _get_client(row)
                result = await client.test_connection()
                ok = bool(result.get("ok"))
                circuit_breaker.record_result(row.name, ok, result.get("message") or "")
                dep_health.record(row.name, ok, result.get("message") or "", source="probe")
            except Exception as e:
                circuit_breaker.record_result(row.name, False, str(e))
                dep_health.record(row.name, False, str(e), source="probe")
    return {"integrations": dep_health.snapshot(names),
            "breakers": circuit_breaker.get_stats()}


@router.post("/dependencies/breaker/reset")
def reset_dependency_breaker(name: Optional[str] = Query(None)):
    from app.services import circuit_breaker
    circuit_breaker.reset_breaker(name)
    return {"ok": True, "breakers": circuit_breaker.get_stats()}


@router.get("/logs")
def logs(lines: int = Query(200, le=1000)):
    return {"lines": get_recent_logs(lines)}


class ScheduleOut(BaseModel):
    last_scan_at: Optional[datetime] = None
    next_scan_at: Optional[datetime] = None  # None = scanning disabled
    last_synced_at: Optional[datetime] = None
    next_sync_at: Optional[datetime] = None  # None = manual sync only (interval 0)


def _get_setting(db: Session, key: str, schema):
    row = db.query(AppSetting).filter_by(key=key).first()
    if not row or not row.value:
        return schema()
    return schema(**json.loads(row.value))


def _get_timestamp(db: Session, key: str) -> Optional[datetime]:
    row = db.query(AppSetting).filter_by(key=key).first()
    if not row or not row.value:
        return None
    try:
        return datetime.fromisoformat(row.value)
    except ValueError:
        return None


@router.get("/schedule", response_model=ScheduleOut)
def schedule(db: Session = Depends(get_db)):
    """Next-run timestamps for the dashboard's scan/sync countdowns."""
    import_cfg = _get_setting(db, "import_matching", ImportMatchingSettings)
    sync_cfg = _get_setting(db, "sync", SyncSettings)
    last_scan_at = _get_timestamp(db, "last_scan_at")
    last_synced_at = _get_timestamp(db, "last_synced")

    next_scan_at = None
    if import_cfg.enabled:
        interval = max(60, import_cfg.poll_interval_seconds)
        next_scan_at = (last_scan_at or datetime.utcnow()) + timedelta(seconds=interval)

    next_sync_at = None
    if sync_cfg.plex_sync_interval_hours > 0:
        next_sync_at = (last_synced_at or datetime.utcnow()) + timedelta(hours=sync_cfg.plex_sync_interval_hours)

    return ScheduleOut(last_scan_at=last_scan_at, next_scan_at=next_scan_at,
                       last_synced_at=last_synced_at, next_sync_at=next_sync_at)
