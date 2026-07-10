"""Smart Playlists API (MOD-01, v0.35+) — scheduling, auto-add, track tracking."""
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

router = APIRouter(prefix="/smart-playlists", tags=["smart-playlists"])


class PlaylistOut(BaseModel):
    id: int
    genre_tag: str
    title: str
    plex_playlist_id: Optional[str] = None
    enabled: bool
    track_count: int = 0
    pending_count: int = 0  # legacy; always 0 under SP-01 blacklist model
    artist_count: int = 0
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


@router.get("/settings", response_model=SmartPlaylistSettings)
def get_settings(db: Session = Depends(get_db)):
    return playlist_generator.load_settings(db)


@router.put("/settings", response_model=SmartPlaylistSettings)
def put_settings(body: SmartPlaylistSettings, db: Session = Depends(get_db)):
    playlist_generator.save_settings(db, body)
    return body


@router.put("/blacklist")
def put_blacklist(body: dict = Body(...)):
    """SP-01 — replace the artist blacklist (Playlists page subsection)."""
    artists = body.get("blacklisted_artists")
    if artists is None:
        raise HTTPException(status_code=400, detail="Body: {blacklisted_artists: string[]}")
    if not isinstance(artists, list):
        raise HTTPException(status_code=400, detail="blacklisted_artists must be a list")
    return playlist_generator.update_blacklist([str(a) for a in artists])


@router.get("", response_model=list[PlaylistOut])
def list_playlists(db: Session = Depends(get_db)):
    rows = db.query(SmartPlaylist).order_by(SmartPlaylist.genre_tag).all()
    out = []
    for pl in rows:
        included = db.query(SmartPlaylistCandidate).filter_by(
            playlist_id=pl.id, status="accepted").count()
        pending = db.query(SmartPlaylistCandidate).filter_by(
            playlist_id=pl.id, status="pending").count()
        out.append(PlaylistOut(
            id=pl.id, genre_tag=pl.genre_tag, title=pl.title,
            plex_playlist_id=pl.plex_playlist_id, enabled=pl.enabled,
            track_count=pl.track_count or 0,
            pending_count=pending,
            artist_count=included,
            last_generated_at=pl.last_generated_at,
            last_run_message=pl.last_run_message,
        ))
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
async def update_playlist(playlist_id: int, body: dict = Body(...),
                          db: Session = Depends(get_db)):
    """Update playlist settings. Title changes are synced to Plex when pushed."""
    pl = db.query(SmartPlaylist).filter_by(id=playlist_id).first()
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")

    if "title" in body and body["title"] is not None:
        new_title = str(body["title"]).strip()
        if new_title and new_title != pl.title:
            result = await playlist_generator.rename_playlist(playlist_id, new_title)
            if not result.get("ok"):
                raise HTTPException(status_code=400, detail=result.get("message") or "Rename failed")
            pl = db.query(SmartPlaylist).filter_by(id=playlist_id).first()

    allowed = {"auto_add_override", "max_tracks_override", "enabled"}
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


@router.delete("/{playlist_id}")
async def delete_playlist(playlist_id: int):
    """Delete playlist from Powarr and remove the Powarr-owned Plex playlist."""
    result = await playlist_generator.delete_playlist(playlist_id)
    if not result.get("ok") and result.get("message") == "Playlist not found":
        raise HTTPException(status_code=404, detail=result["message"])
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message") or "Delete failed")
    return result


@router.post("/{playlist_id}/approve")
async def approve(playlist_id: int):
    """SP-05 — create the Plex playlist (if needed) and accept pending candidates."""
    result = await playlist_generator.approve_playlist(playlist_id)
    if not result.get("ok") and result.get("message") == "Playlist not found":
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.post("/{playlist_id}/suggest-name")
async def suggest_name(playlist_id: int, db: Session = Depends(get_db)):
    """SP-08 — on-demand LLM playlist name (does not save; PUT title to apply)."""
    pl = db.query(SmartPlaylist).filter_by(id=playlist_id).first()
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found")
    artists = [c.artist_name for c in db.query(SmartPlaylistCandidate).filter_by(
        playlist_id=pl.id).order_by(SmartPlaylistCandidate.created_at.desc()).limit(5).all()]
    name = await playlist_generator.suggest_playlist_name_for(db, pl.genre_tag, artists)
    return {"ok": bool(name), "suggested_title": name, "fallback": f"Powarr · {pl.genre_tag}"}
