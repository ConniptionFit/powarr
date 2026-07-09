"""Smart Playlists (MOD-01, v0.34.0) — genre playlists from Qdrant → Plex."""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey
from datetime import datetime

from app.database import Base


class SmartPlaylist(Base):
    __tablename__ = "smart_playlists"

    id = Column(Integer, primary_key=True, index=True)
    genre_tag = Column(String, nullable=False, unique=True, index=True)
    title = Column(String, nullable=False)
    plex_playlist_id = Column(String, nullable=True)  # set only after Powarr creates it
    auto_add = Column(Boolean, nullable=True)  # None = use global default
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SmartPlaylistCandidate(Base):
    __tablename__ = "smart_playlist_candidates"

    id = Column(Integer, primary_key=True, index=True)
    playlist_id = Column(Integer, ForeignKey("smart_playlists.id"), nullable=False, index=True)
    artist_name = Column(String, nullable=False)
    musicbrainz_id = Column(String, nullable=True)
    status = Column(String, default="pending", index=True)  # pending|accepted|rejected
    source_payload = Column(Text, nullable=True)  # JSON snippet from Qdrant
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
