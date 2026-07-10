"""Artist Discovery API — native port of the n8n Music Curator. See vault
[[Artist Discovery]]. Kept synchronous (request/response), no Active Processes Tray
wiring — matches Smart Playlists' existing precedent."""
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.artist_discovery import DiscoveredArtist
from app.schemas.settings import ArtistDiscoverySettings
from app.services import artist_discovery as service
from pydantic import BaseModel

router = APIRouter(prefix="/artist-discovery", tags=["artist-discovery"])


class CandidateOut(BaseModel):
    id: int
    musicbrainz_id: Optional[str] = None
    artist_name: str
    genres: list[str] = []
    mood_tags: list[str] = []
    era: Optional[str] = None
    source: str
    similarity_score: Optional[float] = None
    associated_seed_mbids: list[str] = []
    seed_artist_name: Optional[str] = None
    status: str
    lidarr_artist_id: Optional[int] = None
    created_at: Optional[datetime] = None
    image_url: Optional[str] = None
    bio: Optional[str] = None
    years_active: Optional[str] = None


def _candidate_out(row: DiscoveredArtist) -> CandidateOut:
    return CandidateOut(
        id=row.id, musicbrainz_id=row.musicbrainz_id, artist_name=row.artist_name,
        genres=json.loads(row.genres) if row.genres else [],
        mood_tags=json.loads(row.mood_tags) if row.mood_tags else [],
        era=row.era, source=row.source, similarity_score=row.similarity_score,
        associated_seed_mbids=json.loads(row.associated_seed_mbids) if row.associated_seed_mbids else [],
        seed_artist_name=row.seed_artist_name, status=row.status,
        lidarr_artist_id=row.lidarr_artist_id, created_at=row.created_at,
        image_url=row.image_url, bio=row.bio, years_active=row.years_active,
    )


@router.get("/settings", response_model=ArtistDiscoverySettings)
def get_settings(db: Session = Depends(get_db)):
    return service.load_settings(db)


@router.put("/settings", response_model=ArtistDiscoverySettings)
def put_settings(body: ArtistDiscoverySettings, db: Session = Depends(get_db)):
    service.save_settings(db, body)
    return body


@router.get("/stats")
async def stats(db: Session = Depends(get_db)):
    return await service.get_stats(db)


@router.post("/run")
async def run_discovery(db: Session = Depends(get_db)):
    return await service.run_full_discovery_cycle(db)


@router.post("/sync")
async def run_sync(db: Session = Depends(get_db)):
    return await service.run_differential_sync(db)


@router.get("/candidates", response_model=list[CandidateOut])
def list_candidates(status: str = Query("pending"),
                    source: Optional[str] = None,
                    db: Session = Depends(get_db)):
    q = db.query(DiscoveredArtist).filter_by(status=status)
    if source:
        q = q.filter_by(source=source)
    rows = q.order_by(DiscoveredArtist.created_at.desc()).limit(500).all()
    return [_candidate_out(r) for r in rows]


@router.get("/candidates/{candidate_id}", response_model=CandidateOut)
def get_candidate(candidate_id: int, db: Session = Depends(get_db)):
    row = db.query(DiscoveredArtist).filter_by(id=candidate_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return _candidate_out(row)


@router.post("/candidates/{candidate_id}/accept")
async def accept(candidate_id: int, db: Session = Depends(get_db)):
    result = await service.add_to_lidarr(db, candidate_id)
    if not result.get("ok") and result.get("message") == "Candidate not found":
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.post("/candidates/{candidate_id}/reject")
def reject(candidate_id: int, db: Session = Depends(get_db)):
    result = service.reject_candidate(db, candidate_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("message"))
    return result


@router.post("/batch")
async def batch(body: dict = Body(...), db: Session = Depends(get_db)):
    ids = body.get("ids") or []
    action = body.get("action")
    if action not in ("accept", "reject") or not ids:
        raise HTTPException(status_code=400, detail="Body: {ids, action: accept|reject}")
    results = []
    for cid in ids:
        if action == "accept":
            results.append(await service.add_to_lidarr(db, cid))
        else:
            results.append(service.reject_candidate(db, cid))
    return {"results": results}


@router.get("/lidarr/profiles")
async def lidarr_profiles(db: Session = Depends(get_db)):
    return await service.get_lidarr_profiles(db)
