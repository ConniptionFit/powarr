"""Artist Discovery — native port of the n8n Music Curator (Last.fm → Ollama embeddings →
Qdrant taste-centroid similarity + related-artist graph → Lidarr)."""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, Float
from datetime import datetime

from app.database import Base


class DiscoveredArtist(Base):
    """Review-queue row for a candidate surfaced by centroid search or graph expansion.
    Dedup rule: any existing row (regardless of status) for an mbid/name permanently
    blocks re-creating a pending row for it — a rejected candidate never resurfaces."""
    __tablename__ = "discovered_artists"

    id = Column(Integer, primary_key=True)
    musicbrainz_id = Column(String, nullable=True, index=True)
    artist_name = Column(String, nullable=False, index=True)
    genres = Column(Text, nullable=True)  # JSON list
    mood_tags = Column(Text, nullable=True)  # JSON list
    era = Column(String, nullable=True)
    source = Column(String, nullable=False, default="centroid")  # centroid|graph
    similarity_score = Column(Float, nullable=True)  # centroid cosine score
    associated_seed_mbids = Column(Text, nullable=True)  # JSON list, graph source
    seed_artist_name = Column(String, nullable=True)  # display convenience, graph source
    # v0.41.0 — all contributing seed names (associated_seed_mbids resolved back to
    # artist names via their Qdrant points), refreshed when new connections land.
    seed_artist_names = Column(Text, nullable=True)  # JSON list, graph source
    status = Column(String, default="pending", index=True)  # pending|accepted|rejected
    lidarr_artist_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
    # Enrichment (v0.40.0) — Lidarr lookup primary, MusicBrainz (+ Wikipedia via its
    # url-rels) fallback for whichever of image/bio is still missing. Populated once
    # at candidate-creation time, not re-fetched on every read.
    image_url = Column(String, nullable=True)
    bio = Column(Text, nullable=True)
    years_active = Column(String, nullable=True)


class ArtistDiscoveryRun(Base):
    """History/diagnostics for a discovery-cycle or sync run."""
    __tablename__ = "artist_discovery_runs"

    id = Column(Integer, primary_key=True)
    run_type = Column(String, nullable=False)  # ingest|centroid|graph|sync|full
    started_at = Column(DateTime, default=datetime.utcnow, index=True)
    finished_at = Column(DateTime, nullable=True)
    candidates_found = Column(Integer, default=0)
    candidates_added = Column(Integer, default=0)
    message = Column(Text, nullable=True)
    # v2 additions: what started the run, which aggressiveness it ran under, and
    # how many candidates it looked at / skipped (the skips are in the decisions table).
    trigger = Column(String, nullable=True)  # manual | schedule
    preset = Column(String, nullable=True)
    candidates_skipped = Column(Integer, default=0)


class ArtistDiscoveryDecision(Base):
    """One row per candidate the direct lane judged during a run — added, queued,
    skipped or failed — with the reason in plain words. This is the answer to
    "why didn't it add X?". Pruned by the maintenance loop."""
    __tablename__ = "artist_discovery_decisions"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    artist_name = Column(String, nullable=False)
    musicbrainz_id = Column(String, nullable=True)
    action = Column(String, nullable=False, index=True)  # added|queued|skipped|failed
    reason = Column(Text, nullable=True)
    score = Column(Float, nullable=True)
    connections = Column(Integer, nullable=True)
    seeds = Column(Text, nullable=True)  # JSON list of seed names
    candidate_id = Column(Integer, nullable=True)
