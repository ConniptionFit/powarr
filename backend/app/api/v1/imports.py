from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.models.failed_import import FailedImport
from app.models.integration import Integration
from app.schemas.failed_import import FailedImportOut, ImportStats
from app.services.import_matcher import scan_once, _get_client

router = APIRouter(prefix="/imports", tags=["imports"])


@router.get("", response_model=list[FailedImportOut])
def list_imports(
    db: Session = Depends(get_db),
    status: Optional[str] = Query(None),
    limit: int = Query(200),
    offset: int = Query(0),
):
    q = db.query(FailedImport)
    if status:
        q = q.filter(FailedImport.status == status)
    return q.order_by(FailedImport.created_at.desc()).offset(offset).limit(limit).all()


@router.get("/stats", response_model=ImportStats)
def import_stats(db: Session = Depends(get_db)):
    counts = {}
    for status in ("suggested", "auto_resolved", "accepted", "rejected"):
        counts[status] = db.query(FailedImport).filter_by(status=status).count()
    return ImportStats(**counts)


@router.post("/scan")
async def trigger_scan():
    """Run one detection cycle immediately (in addition to the background poller)."""
    return await scan_once()


@router.post("/{item_id}/accept")
async def accept_import(item_id: int, db: Session = Depends(get_db)):
    item = db.query(FailedImport).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Failed import not found")
    if not item.download_id:
        raise HTTPException(status_code=400, detail="No download id on this item — resolve it in the *arr app directly")
    row = db.query(Integration).filter_by(name=item.source_app, enabled=True).first()
    if not row:
        raise HTTPException(status_code=400, detail=f"{item.source_app} integration not enabled")

    client = _get_client(item.source_app, row)
    result = await client.push_import_command(item.download_id, item.matched_id)
    item.message = result["message"]
    if result["ok"]:
        item.status = "accepted"
        item.resolved_at = datetime.utcnow()
    db.commit()
    return {"id": item.id, "status": item.status, **result}


@router.post("/{item_id}/reject")
def reject_import(item_id: int, db: Session = Depends(get_db)):
    item = db.query(FailedImport).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Failed import not found")
    item.status = "rejected"
    item.resolved_at = datetime.utcnow()
    db.commit()
    return {"id": item.id, "status": item.status}
