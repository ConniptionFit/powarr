"""Artist Discovery API — native port of the n8n Music Curator. See vault
[[Artist Discovery]]. POST /run stays synchronous (request/response) but is
tray-tracked as of v0.48.0 (AD-02, artist_discovery task kind) — the tray
card appears via SSE independent of the request's own lifecycle, same as
POST /imports/scan. Smart Playlists' own generate/sync endpoints remain
untracked (its own module's precedent for now)."""
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
    seed_artist_names: list[str] = []
    status: str
    lidarr_artist_id: Optional[int] = None
    created_at: Optional[datetime] = None
    image_url: Optional[str] = None
    bio: Optional[str] = None
    years_active: Optional[str] = None


def _match_rating_key(row: DiscoveredArtist) -> tuple[bool, float, int]:
    """Best-match-first ranking. Centroid rows carry a real 0-1 similarity score
    (the % shown in the UI) and always outrank graph rows, which have no
    comparable score — sorted among themselves by connection count instead of
    inventing a cross-scale conversion. `sort(reverse=True)` on this tuple wants
    True > False and higher numbers first, which lines up with "best match" for
    every field, so a single sort call handles both groups."""
    connections = len(json.loads(row.associated_seed_mbids)) if row.associated_seed_mbids else 0
    return (row.similarity_score is not None, row.similarity_score or 0.0, connections)


def _candidate_out(row: DiscoveredArtist) -> CandidateOut:
    # clean_tags/clean_era also run at candidate creation — re-applying here
    # covers rows stored before the placeholder filtering existed (AD-06).
    return CandidateOut(
        id=row.id, musicbrainz_id=row.musicbrainz_id, artist_name=row.artist_name,
        genres=service.clean_tags(json.loads(row.genres) if row.genres else []),
        mood_tags=service.clean_tags(json.loads(row.mood_tags) if row.mood_tags else []),
        era=service.clean_era(row.era), source=row.source, similarity_score=row.similarity_score,
        associated_seed_mbids=json.loads(row.associated_seed_mbids) if row.associated_seed_mbids else [],
        seed_artist_name=row.seed_artist_name,
        seed_artist_names=json.loads(row.seed_artist_names) if row.seed_artist_names else [],
        status=row.status,
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
    return await service.run_discovery(db)


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
    # created_at desc first so _match_rating_key's stable sort breaks ties
    # (equal score, or equal connection count) by newest-first, same as before.
    rows = q.order_by(DiscoveredArtist.created_at.desc()).limit(500).all()
    rows.sort(key=_match_rating_key, reverse=True)
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


@router.post("/candidates/re-enrich")
async def re_enrich(db: Session = Depends(get_db)):
    """Backfill image/bio/years/seed-names on pending candidates missing them."""
    return await service.re_enrich_missing(db)


class RunOut(BaseModel):
    id: int
    run_type: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    candidates_found: int = 0
    candidates_added: int = 0
    message: Optional[str] = None


@router.get("/runs", response_model=list[RunOut])
def list_runs(limit: int = Query(20, le=100), db: Session = Depends(get_db)):
    from app.models.artist_discovery import ArtistDiscoveryRun
    rows = (db.query(ArtistDiscoveryRun)
            .order_by(ArtistDiscoveryRun.started_at.desc()).limit(limit).all())
    return [RunOut(id=r.id, run_type=r.run_type, started_at=r.started_at,
                   finished_at=r.finished_at, candidates_found=r.candidates_found or 0,
                   candidates_added=r.candidates_added or 0, message=r.message)
            for r in rows]


@router.get("/lidarr/profiles")
async def lidarr_profiles(db: Session = Depends(get_db)):
    return await service.get_lidarr_profiles(db)


class RelatedArtistOut(BaseModel):
    musicbrainz_id: Optional[str] = None
    artist_name: str
    match_score: float = 0.0
    already_owned: bool = False
    image_url: Optional[str] = None
    bio: Optional[str] = None
    genres: list[str] = []
    years_active: Optional[str] = None
    similarity_sources: list[str] = ["lastfm"]  # AD-14: "lastfm" | "plex_sonic" | "plex_similar"


class RelatedSearchOut(BaseModel):
    ok: bool
    message: str
    results: list[RelatedArtistOut] = []


@router.get("/related", response_model=RelatedSearchOut)
async def related(
    artist: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Ad-hoc "who's similar to X" search — read-only, no Qdrant writes, no
    DiscoveredArtist rows. Independent of the taste-model pipeline."""
    return await service.search_related_artists(db, artist, limit=limit)


@router.post("/related/add")
async def related_add(body: dict = Body(...), db: Session = Depends(get_db)):
    """Add a Related Artists search result straight to Lidarr, bypassing the
    review queue entirely. Body: {mbid?, artist_name}."""
    artist_name = (body.get("artist_name") or "").strip()
    if not artist_name:
        raise HTTPException(status_code=400, detail="Body: {mbid?, artist_name}")
    return await service.add_related_artist(db, body.get("mbid"), artist_name)
