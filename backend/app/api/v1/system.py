from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.log_buffer import get_recent_logs

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
