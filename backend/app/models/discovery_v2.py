"""Discovery v2 storage: the unified listen log, cached ranking evidence, and
explicit feedback. Additive tables only (created by Base.metadata.create_all)."""
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text

from app.database import Base


class ListenEvent(Base):
    """One real play of one track. Several raw reports of the same play (Last.fm
    scrobble plus the Plex play that caused it) share a canonical_id; only the
    canonical row (is_primary) counts toward the taste profile."""
    __tablename__ = "listen_events"

    id = Column(Integer, primary_key=True)
    source = Column(String, nullable=False)  # lastfm | plex | tautulli | listenbrainz
    source_user = Column(String, nullable=True)
    source_event_id = Column(String, nullable=True)  # stable id from the source, when it has one
    artist = Column(String, nullable=False)
    artist_key = Column(String, nullable=False, index=True)
    track = Column(String, nullable=False)
    track_key = Column(String, nullable=False)
    album = Column(String, nullable=True)
    mbid = Column(String, nullable=True)
    played_at = Column(DateTime, nullable=False, index=True)
    duration_s = Column(Integer, nullable=True)
    canonical_id = Column(Integer, nullable=True, index=True)  # id of the primary row of the group
    is_primary = Column(Integer, default=1)  # 1 = counts, 0 = merged into another event
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (Index("ix_listen_events_src_evt", "source", "source_event_id"),)


class DiscoveryEvidence(Base):
    """Cached per-candidate signal (tags, listeners, deezer related, latest
    release...). Gathering is slow and rate limited, ranking is instant, so the
    ranker reads these instead of refetching when a setting changes."""
    __tablename__ = "artist_discovery_evidence"

    id = Column(Integer, primary_key=True)
    artist_key = Column(String, nullable=False, index=True)
    artist_name = Column(String, nullable=False)
    source = Column(String, nullable=False)  # lastfm | deezer | musicbrainz | listenbrainz
    signal = Column(String, nullable=False)  # tags | listeners | related | release | similar
    value = Column(Float, nullable=True)
    detail = Column(Text, nullable=True)  # JSON
    fetched_at = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (Index("ix_ad_evidence_key_sig", "artist_key", "source", "signal"),)


class DiscoveryFeedback(Base):
    """More like this / Not for me / Already know them. Soft preference signal
    (tags and seeds get boosted or penalised); never deletes anything."""
    __tablename__ = "artist_discovery_feedback"

    id = Column(Integer, primary_key=True)
    artist_key = Column(String, nullable=False, index=True)
    artist_name = Column(String, nullable=False)
    kind = Column(String, nullable=False)  # more_like_this | not_for_me | known
    reason = Column(String, nullable=True)  # genre | known | dislike | other
    tags = Column(Text, nullable=True)  # JSON list copied from the candidate
    seeds = Column(Text, nullable=True)  # JSON list copied from the candidate
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
