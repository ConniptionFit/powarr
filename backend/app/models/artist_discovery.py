"""Artist Discovery — native port of the n8n Music Curator (Last.fm → Ollama embeddings →
Qdrant taste-centroid similarity + related-artist graph → Lidarr). See vault
[[Artist Discovery]]."""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, Float
from datetime import datetime

from app.database import Base


class DiscoveredArtist(Base):
    """Review-queue row for a candidate surfaced by centroid search or graph expansion.
    Dedup rule: any existing row (regardless of status) for an mbid/name permanently
    blocks re-creating a pending row for it — a rejected candidate never resurfaces."""
    __tablename__ = "discovered_artists"

    id = Column(Integer, primary_key=True, index=True)
    musicbrainz_id = Column(String, nullable=True, index=True)
    artist_name = Column(String, nullable=False, index=True)
    genres = Column(Text, nullable=True)  # JSON list
    mood_tags = Column(Text, nullable=True)  # JSON list
    era = Column(String, nullable=True)
    source = Column(String, nullable=False, default="centroid")  # centroid|graph
    similarity_score = Column(Float, nullable=True)  # centroid cosine score
    associated_seed_mbids = Column(Text, nullable=True)  # JSON list, graph source
    seed_artist_name = Column(String, nullable=True)  # display convenience, graph source
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

    id = Column(Integer, primary_key=True, index=True)
    run_type = Column(String, nullable=False)  # ingest|centroid|graph|sync|full
    started_at = Column(DateTime, default=datetime.utcnow, index=True)
    finished_at = Column(DateTime, nullable=True)
    candidates_found = Column(Integer, default=0)
    candidates_added = Column(Integer, default=0)
    message = Column(Text, nullable=True)
