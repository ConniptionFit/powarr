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
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "db": "ok" if db_ok else "error"}


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
