import json
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.models.app_setting import AppSetting
from app.models.deletion_log import DeletionLog
from app.models.media import MediaItem
from app.schemas.media import MediaItemOut, MediaStats, DeletionLogOut, DeletionStats
from app.schemas.settings import ScoringWeights, CleanupSettings
from app.services.deleter import propagate_and_delete

router = APIRouter(prefix="/media", tags=["media"])


def _get_setting(db: Session, key: str, schema):
    row = db.query(AppSetting).filter_by(key=key).first()
    if not row or not row.value:
        return schema()
    return schema(**json.loads(row.value))


@router.get("", response_model=list[MediaItemOut])
def list_media(
    db: Session = Depends(get_db),
    media_type: Optional[str] = Query(None),
    min_score: Optional[float] = Query(None),
    ignored: Optional[bool] = Query(False),
    include_protected: bool = Query(False),
    pending: bool = Query(False),  # true = list only items awaiting soft-delete purge
    sort_by: str = Query("score"),
    order: str = Query("desc"),
    limit: int = Query(200),
    offset: int = Query(0),
):
    q = db.query(MediaItem)
    if pending:
        q = q.filter(MediaItem.pending_delete_at.isnot(None))
    else:
        q = q.filter(MediaItem.pending_delete_at.is_(None))
        if ignored is not None:
            q = q.filter(MediaItem.ignored == ignored)
        if not include_protected:
            q = q.filter(MediaItem.protected.isnot(True))
        cleanup = _get_setting(db, "cleanup", CleanupSettings)
        if cleanup.excluded_libraries:
            q = q.filter(~MediaItem.library_section.in_(cleanup.excluded_libraries))
    if media_type:
        q = q.filter(MediaItem.media_type == media_type)
    if min_score is not None:
        q = q.filter(MediaItem.score >= min_score)

    col = getattr(MediaItem, sort_by, MediaItem.score)
    q = q.order_by(col.desc() if order == "desc" else col.asc())
    return q.offset(offset).limit(limit).all()


@router.get("/stats", response_model=MediaStats)
def get_stats(db: Session = Depends(get_db)):
    weights = _get_setting(db, "scoring_weights", ScoringWeights)

    total = db.query(MediaItem).count()
    total_size = db.query(func.sum(MediaItem.file_size)).scalar() or 0
    candidates = db.query(MediaItem).filter(
        MediaItem.score >= weights.min_score_threshold,
        MediaItem.ignored.is_(False),
        MediaItem.pending_delete_at.is_(None),
    ).all()
    savings = sum(c.file_size for c in candidates)

    last_synced = None
    row = db.query(AppSetting).filter_by(key="last_synced").first()
    if row and row.value:
        try:
            last_synced = datetime.fromisoformat(row.value)
        except ValueError:
            pass

    return MediaStats(
        total_items=total,
        total_size_bytes=total_size,
        candidates_above_threshold=len(candidates),
        potential_savings_bytes=savings,
        last_synced=last_synced,
    )


@router.get("/libraries")
def list_libraries(db: Session = Depends(get_db)) -> list[str]:
    rows = db.query(MediaItem.library_section).distinct().all()
    return sorted({r[0] for r in rows if r[0]})


@router.get("/deletion-log", response_model=list[DeletionLogOut])
def deletion_log(db: Session = Depends(get_db), limit: int = Query(200), offset: int = Query(0)):
    return (db.query(DeletionLog).order_by(DeletionLog.deleted_at.desc())
            .offset(offset).limit(limit).all())


@router.get("/deletion-stats", response_model=DeletionStats)
def deletion_stats(db: Session = Depends(get_db)):
    cutoff = datetime.utcnow() - timedelta(days=30)
    q = db.query(func.count(DeletionLog.id), func.coalesce(func.sum(DeletionLog.file_size), 0)) \
        .filter(DeletionLog.deleted_at >= cutoff)
    count_30d, freed_30d = q.one()
    total_count, total_freed = db.query(
        func.count(DeletionLog.id), func.coalesce(func.sum(DeletionLog.file_size), 0)).one()
    return DeletionStats(
        deleted_30d=count_30d, freed_30d_bytes=int(freed_30d),
        deleted_total=total_count, freed_total_bytes=int(total_freed),
    )


@router.delete("/batch")
async def delete_media_batch(ids: list[int] = Body(...), db: Session = Depends(get_db)):
    """Delete multiple media items by ID. Honors the soft-delete window when configured."""
    cleanup = _get_setting(db, "cleanup", CleanupSettings)
    deleted, pending = [], []
    for item_id in ids:
        try:
            item = db.query(MediaItem).filter_by(id=item_id).first()
            if not item:
                continue
            if cleanup.soft_delete_days > 0 and item.pending_delete_at is None:
                item.pending_delete_at = datetime.utcnow()
                pending.append(item_id)
            else:
                await propagate_and_delete(item, db)
                deleted.append(item_id)
        except Exception:
            pass
    db.commit()
    return {"deleted": deleted, "pending_delete": pending}


@router.patch("/{item_id}/ignore")
def toggle_ignore(item_id: int, ignored: bool, db: Session = Depends(get_db)):
    item = db.query(MediaItem).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")
    item.ignored = ignored
    db.commit()
    return {"id": item_id, "ignored": item.ignored}


@router.post("/{item_id}/restore")
def restore_media(item_id: int, db: Session = Depends(get_db)):
    """Cancel a pending soft-delete."""
    item = db.query(MediaItem).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")
    if item.pending_delete_at is None:
        raise HTTPException(status_code=400, detail="Item is not pending deletion")
    item.pending_delete_at = None
    db.commit()
    return {"id": item_id, "restored": True}


@router.post("/{item_id}/explain")
async def explain_media(item_id: int, db: Session = Depends(get_db)):
    """Optional LLM one-liner on whether this is a good deletion candidate. Fails soft."""
    item = db.query(MediaItem).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")
    from app.schemas.settings import OllamaSettings
    ollama = _get_setting(db, "ollama", OllamaSettings)
    if not (ollama.enabled and ollama.host and ollama.model):
        return {"rationale": None, "message": "LLM assist not configured"}
    from app.services import llm_assist
    summary = (f"{item.title} ({item.year or 'unknown year'}), {item.media_type}, "
               f"{round((item.file_size or 0) / 1024**3, 1)} GB, watched {item.watch_count}x, "
               f"last watched {item.last_watched_at or 'never'}, deletion score {item.score}/100")
    rationale = await llm_assist.explain_deletion(
        ollama.host, ollama.model, summary, ollama.api_style,
        template=ollama.explain_prompt, verbose=ollama.verbosity == "verbose")
    return {"rationale": rationale, "message": None if rationale else "No response from LLM"}


@router.delete("/{item_id}")
async def delete_media(item_id: int, db: Session = Depends(get_db)):
    item = db.query(MediaItem).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")
    cleanup = _get_setting(db, "cleanup", CleanupSettings)
    if cleanup.soft_delete_days > 0 and item.pending_delete_at is None:
        item.pending_delete_at = datetime.utcnow()
        db.commit()
        return {"deleted": None, "pending_delete": item_id,
                "purge_after_days": cleanup.soft_delete_days}
    await propagate_and_delete(item, db)
    db.commit()
    return {"deleted": item_id}
