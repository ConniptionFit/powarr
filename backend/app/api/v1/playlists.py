"""Smart Playlists API (MOD-01, v0.35+) — scheduling, auto-add, track tracking."""
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.app_setting import AppSetting
from app.models.smart_playlist import (
    SmartPlaylist, SmartPlaylistCandidate, SmartPlaylistRun, SmartPlaylistTrack
)
from app.schemas.settings import SmartPlaylistSettings
from app.services import playlist_generator
from app.services.secret_box import encrypt

router = APIRouter(prefix="/smart-playlists", tags=["smart-playlists"])


class PlaylistOut(BaseModel):
    id: int
    genre_tag: str
    title: str
    plex_playlist_id: Optional[str] = None
    enabled: bool
    track_count: int = 0
    pending_count: int = 0
    last_generated_at: Optional[datetime] = None
    last_run_message: Optional[str] = None

    model_config = {"from_attributes": True}


class PlaylistDetailOut(PlaylistOut):
    """Extended playlist details with run history."""
    mood: Optional[str] = None
    era: Optional[str] = None
    auto_add_override: Optional[bool] = None
    max_tracks_override: Optional[int] = None


class CandidateOut(BaseModel):
    id: int
    playlist_id: int
    artist_name: str
    musicbrainz_id: Optional[str] = None
    status: str
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class TrackOut(BaseModel):
    id: int
    playlist_id: int
    plex_key: str
    artist_name: str
    track_title: Optional[str] = None
    added_at: datetime

    model_config = {"from_attributes": True}


class PlaylistRunOut(BaseModel):
    id: int
    playlist_id: Optional[int] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
    status: str
    message: Optional[str] = None
    candidates_found: int = 0
    candidates_accepted: int = 0
    tracks_added: int = 0

    model_config = {"from_attributes": True}


class SmartPlaylistSettingsOut(SmartPlaylistSettings):
    qdrant_api_key_set: bool = False


@router.get("/settings", response_model=SmartPlaylistSettingsOut)
def get_settings(db: Session = Depends(get_db)):
    cfg = playlist_generator.load_settings(db)
    out = SmartPlaylistSettingsOut(**cfg.model_dump())
    out.qdrant_api_key_set = bool(cfg.qdrant_api_key)
    out.qdrant_api_key = ""  # never echo
    return out


@router.put("/settings", response_model=SmartPlaylistSettingsOut)
def put_settings(body: SmartPlaylistSettings, db: Session = Depends(get_db)):
    row = db.query(AppSetting).filter_by(key="smart_playlists").first()
    current = playlist_generator.load_settings(db)
    data = body.model_dump()
    # Blank key = keep existing (same posture as integration secrets)
    if not (body.qdrant_api_key or "").strip():
        data["qdrant_api_key"] = current.qdrant_api_key
    else:
        data["qdrant_api_key"] = encrypt(body.qdrant_api_key) or body.qdrant_api_key
    if not row:
        row = AppSetting(key="smart_playlists")
        db.add(row)
    row.value = json.dumps(data)
    db.commit()
    out = SmartPlaylistSettingsOut(**SmartPlaylistSettings(**data).model_dump())
    out.qdrant_api_key_set = bool(data.get("qdrant_api_key"))
    out.qdrant_api_key = ""
    return out


@router.get("", response_model=list[PlaylistOut])
def list_playlists(db: Session = Depends(get_db)):
    rows = db.query(SmartPlaylist).order_by(SmartPlaylist.genre_tag).all()
    out = []
    for pl in rows:
        pending = db.query(SmartPlaylistCandidate).filter_by(
            playlist_id=pl.id, status="pending").count()
        out.append(PlaylistOut(
            id=pl.id, genre_tag=pl.genre_tag, title=pl.title,
            plex_playlist_id=pl.plex_playlist_id, enabled=pl.enabled,
            pending_count=pending))
    return out


@router.get("/candidates", response_model=list[CandidateOut])
def list_candidates(status: str = Query("pending"),
                    playlist_id: Optional[int] = None,
                    db: Session = Depends(get_db)):
    q = db.query(SmartPlaylistCandidate).filter_by(status=status)
    if playlist_id is not None:
        q = q.filter_by(playlist_id=playlist_id)
    return q.order_by(SmartPlaylistCandidate.created_at.desc()).limit(500).all()


@router.post("/run")
async def run_generate(body: dict = Body(default={})):
    genre = body.get("genre")
    return await playlist_generator.generate_candidates(genre)


@router.post("/candidates/{candidate_id}/accept")
async def accept(candidate_id: int):
    result = await playlist_generator.accept_candidate(candidate_id)
    if not result.get("ok") and result.get("message") == "Candidate not found":
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.post("/candidates/{candidate_id}/reject")
def reject(candidate_id: int):
    result = playlist_generator.reject_candidate(candidate_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("message"))
    return result


@router.post("/batch")
async def batch(body: dict = Body(...)):
    ids = body.get("ids") or []
    action = body.get("action")
    if action not in ("accept", "reject") or not ids:
        raise HTTPException(status_code=400, detail="Body: {ids, action: accept|reject}")
    results = []
    for cid in ids:
        if action == "accept":
            results.append(await playlist_generator.accept_candidate(cid))
        else:
            results.append(playlist_generator.reject_candidate(cid))
    return {"results": results}


@router.get("/{playlist_id}", response_model=PlaylistDetailOut)
def get_playlist(playlist_id: int, db: Session = Depends(get_db)):
    """Get detailed playlist info including metadata and overrides."""
    pl = db.query(SmartPlaylist).filter_by(id=playlist_id).first()
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")
    pending = db.query(SmartPlaylistCandidate).filter_by(
        playlist_id=pl.id, status="pending").count()
    return PlaylistDetailOut(
        **{k: getattr(pl, k) for k in PlaylistDetailOut.model_fields},
        pending_count=pending
    )


@router.get("/{playlist_id}/runs", response_model=list[PlaylistRunOut])
def get_playlist_runs(playlist_id: int, limit: int = Query(50, ge=1, le=500),
                      db: Session = Depends(get_db)):
    """Get run history for a playlist."""
    pl = db.query(SmartPlaylist).filter_by(id=playlist_id).first()
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")
    runs = db.query(SmartPlaylistRun).filter_by(playlist_id=playlist_id).order_by(
        SmartPlaylistRun.started_at.desc()).limit(limit).all()
    return runs


@router.get("/{playlist_id}/tracks", response_model=list[TrackOut])
def get_playlist_tracks(playlist_id: int, limit: int = Query(500, ge=1, le=1000),
                        db: Session = Depends(get_db)):
    """Get tracks in a playlist."""
    pl = db.query(SmartPlaylist).filter_by(id=playlist_id).first()
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")
    tracks = db.query(SmartPlaylistTrack).filter_by(playlist_id=playlist_id).order_by(
        SmartPlaylistTrack.added_at.desc()).limit(limit).all()
    return tracks


@router.put("/{playlist_id}", response_model=PlaylistDetailOut)
def update_playlist(playlist_id: int, body: dict = Body(...),
                   db: Session = Depends(get_db)):
    """Update playlist settings (auto_add_override, max_tracks_override, etc.)."""
    pl = db.query(SmartPlaylist).filter_by(id=playlist_id).first()
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")

    # Only allow updating override fields
    allowed = {"auto_add_override", "max_tracks_override", "enabled", "title"}
    for key in allowed:
        if key in body:
            setattr(pl, key, body[key])

    db.commit()
    pending = db.query(SmartPlaylistCandidate).filter_by(
        playlist_id=pl.id, status="pending").count()
    return PlaylistDetailOut(
        **{k: getattr(pl, k) for k in PlaylistDetailOut.model_fields},
        pending_count=pending
    )
