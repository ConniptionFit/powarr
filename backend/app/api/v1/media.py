import json
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.models.media import MediaItem
from app.models.integration import Integration
from app.schemas.media import MediaItemOut, MediaStats
from app.schemas.settings import ScoringWeights

router = APIRouter(prefix="/media", tags=["media"])


@router.get("", response_model=list[MediaItemOut])
def list_media(
    db: Session = Depends(get_db),
    media_type: Optional[str] = Query(None),
    min_score: Optional[float] = Query(None),
    ignored: Optional[bool] = Query(False),
    sort_by: str = Query("score"),
    order: str = Query("desc"),
    limit: int = Query(200),
    offset: int = Query(0),
):
    q = db.query(MediaItem)
    if ignored is not None:
        q = q.filter(MediaItem.ignored == ignored)
    if media_type:
        q = q.filter(MediaItem.media_type == media_type)
    if min_score is not None:
        q = q.filter(MediaItem.score >= min_score)

    col = getattr(MediaItem, sort_by, MediaItem.score)
    q = q.order_by(col.desc() if order == "desc" else col.asc())
    return q.offset(offset).limit(limit).all()


@router.get("/stats", response_model=MediaStats)
def get_stats(db: Session = Depends(get_db)):
    from app.models.app_setting import AppSetting
    from datetime import datetime

    weights_row = db.query(AppSetting).filter_by(key="scoring_weights").first()
    weights = ScoringWeights(**json.loads(weights_row.value)) if weights_row else ScoringWeights()

    total = db.query(MediaItem).count()
    total_size = db.query(MediaItem).with_entities(
        __import__("sqlalchemy").func.sum(MediaItem.file_size)
    ).scalar() or 0
    candidates = db.query(MediaItem).filter(MediaItem.score >= weights.min_score_threshold, MediaItem.ignored == False).all()
    savings = sum(c.file_size for c in candidates)

    return MediaStats(
        total_items=total,
        total_size_bytes=total_size,
        candidates_above_threshold=len(candidates),
        potential_savings_bytes=savings,
        last_synced=None,
    )


@router.delete("/batch")
async def delete_media_batch(ids: list[int] = Body(...), db: Session = Depends(get_db)):
    """Delete multiple media items by ID, propagating to *arr apps for each."""
    results = []
    for item_id in ids:
        try:
            # Reuse single delete logic inline
            item = db.query(MediaItem).filter_by(id=item_id).first()
            if not item:
                continue
            await _delete_from_arrs(item, db)
            db.delete(item)
            results.append(item_id)
        except Exception:
            pass
    db.commit()
    return {"deleted": results}


@router.patch("/{item_id}/ignore")
def toggle_ignore(item_id: int, ignored: bool, db: Session = Depends(get_db)):
    item = db.query(MediaItem).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")
    item.ignored = ignored
    db.commit()
    return {"id": item_id, "ignored": item.ignored}


async def _delete_from_arrs(item: MediaItem, db: Session):
    if item.radarr_id:
        row = db.query(Integration).filter_by(name="radarr", enabled=True).first()
        if row:
            extra = json.loads(row.extra_config) if row.extra_config else {}
            from app.integrations.radarr import RadarrIntegration
            r = RadarrIntegration(row.url, row.api_key)
            if extra.get("delete_from_arr_list"):
                await r.delete_movie(item.radarr_id)
            elif extra.get("remove_from_monitored_on_delete", True):
                await r.unmonitor_movie(item.radarr_id)

    if item.sonarr_id:
        row = db.query(Integration).filter_by(name="sonarr", enabled=True).first()
        if row:
            extra = json.loads(row.extra_config) if row.extra_config else {}
            from app.integrations.sonarr import SonarrIntegration
            s = SonarrIntegration(row.url, row.api_key)
            if extra.get("delete_from_arr_list"):
                await s.delete_series(item.sonarr_id)
            elif extra.get("remove_from_monitored_on_delete", True):
                await s.unmonitor_series(item.sonarr_id)

    if item.lidarr_id:
        row = db.query(Integration).filter_by(name="lidarr", enabled=True).first()
        if row:
            extra = json.loads(row.extra_config) if row.extra_config else {}
            from app.integrations.lidarr import LidarrIntegration
            li = LidarrIntegration(row.url, row.api_key)
            if extra.get("delete_from_arr_list"):
                await li.delete_artist(item.lidarr_id)
            elif extra.get("remove_from_monitored_on_delete", True):
                await li.unmonitor_artist(item.lidarr_id)


@router.delete("/{item_id}")
async def delete_media(item_id: int, db: Session = Depends(get_db)):
    item = db.query(MediaItem).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")
    await _delete_from_arrs(item, db)
    db.delete(item)
    db.commit()
    return {"deleted": item_id}
