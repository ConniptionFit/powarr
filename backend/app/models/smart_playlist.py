"""Smart Playlists (MOD-01, v0.35+) — genre playlists from Qdrant → Plex with scheduling."""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey
from datetime import datetime

from app.database import Base


class SmartPlaylist(Base):
    __tablename__ = "smart_playlists"

    id = Column(Integer, primary_key=True, index=True)
    genre_tag = Column(String, nullable=False, unique=True, index=True)
    title = Column(String, nullable=False)
    plex_playlist_id = Column(String, nullable=True)  # set only after Powarr creates it
    plex_created_at = Column(DateTime, nullable=True)  # when plex_playlist_id was first set
    auto_add = Column(Boolean, nullable=True)  # None = use global default
    enabled = Column(Boolean, default=True)
    # Metadata for playlist curation (foundation for mood/era filtering in future)
    mood = Column(String, nullable=True)
    era = Column(String, nullable=True)
    # Track counts and timing
    track_count = Column(Integer, default=0)
    last_generated_at = Column(DateTime, nullable=True)
    last_run_message = Column(String, nullable=True)
    # Per-playlist overrides (optional)
    auto_add_override = Column(Boolean, nullable=True)  # None = use global default
    max_tracks_override = Column(Integer, nullable=True)  # None = use global default
    # SP-12 — True when genre_tag names a configured template (SmartPlaylistSettings.
    # playlist_templates) generated from the UNION of several genres, not a single
    # real genre. Distinguishes "Workout" the template from a genuine genre that
    # happens to be named the same.
    is_template = Column(Boolean, default=False)
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


class SmartPlaylistRun(Base):
    """Track playlist generation runs for history and diagnostics."""
    __tablename__ = "smart_playlist_runs"

    id = Column(Integer, primary_key=True, index=True)
    playlist_id = Column(Integer, ForeignKey("smart_playlists.id"), nullable=True, index=True)
    started_at = Column(DateTime, default=datetime.utcnow, index=True)
    completed_at = Column(DateTime, nullable=True)
    status = Column(String, default="pending", index=True)  # pending|success|failed|partial
    message = Column(String, nullable=True)  # summary or error
    candidates_found = Column(Integer, default=0)
    candidates_accepted = Column(Integer, default=0)
    tracks_added = Column(Integer, default=0)
    error_details = Column(Text, nullable=True)  # detailed error info if failed


class SmartPlaylistTrack(Base):
    """Track actual track additions to playlists for dedup and lifecycle management."""
    __tablename__ = "smart_playlist_tracks"

    id = Column(Integer, primary_key=True, index=True)
    playlist_id = Column(Integer, ForeignKey("smart_playlists.id"), nullable=False, index=True)
    plex_key = Column(String, nullable=False, index=True)  # plex_rating_key
    artist_name = Column(String, nullable=False, index=True)
    track_title = Column(String, nullable=True)
    added_at = Column(DateTime, default=datetime.utcnow, index=True)
    # Metadata snapshot for tracking/dedup logic
    plex_metadata = Column(Text, nullable=True)  # JSON snapshot of track at add time
