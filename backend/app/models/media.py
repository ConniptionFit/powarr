from sqlalchemy import Column, Integer, String, Float, DateTime, BigInteger, Boolean, Text
from app.database import Base


class MediaItem(Base):
    __tablename__ = "media_items"

    id = Column(Integer, primary_key=True, index=True)
    plex_rating_key = Column(String, unique=True, index=True, nullable=False)
    title = Column(String, nullable=False)
    year = Column(Integer, nullable=True)
    media_type = Column(String, nullable=False)  # movie | show | episode | artist | album | track
    library_section = Column(String, nullable=True)
    file_path = Column(String, nullable=True)
    file_size = Column(BigInteger, default=0)
    added_at = Column(DateTime, nullable=True)
    release_date = Column(DateTime, nullable=True)
    last_watched_at = Column(DateTime, nullable=True)
    watch_count = Column(Integer, default=0)
    score = Column(Float, default=0.0)
    ignored = Column(Boolean, default=False)
    parent_title = Column(String, nullable=True)  # Show name for episodes, artist name for tracks
    protected = Column(Boolean, default=False)  # actively requested in Seerr — hidden from suggestions
    # Watched by another Tautulli user within the protect window (v0.29.0) —
    # separate from Seerr `protected` so Seerr refresh can't wipe it.
    watch_protected = Column(Boolean, default=False)
    # File lives inside a torrent actively seeding in a configured download
    # client (LIB-05, v0.52.0) — opt-in via cleanup.protect_seeding_torrents,
    # refreshed alongside protected/watch_protected during Plex sync.
    seeding_protected = Column(Boolean, default=False)
    # In-progress (started but not finished) per Tautulli watch history, within
    # cleanup.in_progress_min_percent/max_percent (LIB-04, v0.54.0) — opt-in via
    # cleanup.protect_in_progress, refreshed alongside the other protect flags.
    progress_protected = Column(Boolean, default=False)
    pending_delete_at = Column(DateTime, nullable=True)  # soft-delete: when deletion was requested
    # Chosen Sonarr episode delete policy (LIB-02, v0.56.0 — one of
    # deleter.EPISODE_DELETE_MODES), persisted across the soft-delete pending
    # window so the scheduler's later purge honors the mode the user picked
    # rather than falling back to the series-wide default. Null for every
    # other media type and for immediate (non-soft) deletes.
    pending_delete_mode = Column(String, nullable=True)

    # Cached LLM deletion rationale. The key hashes the prompt template, model
    # config, and this item's scoring-relevant fields — any of those changing
    # makes the cache miss, so stale rationales are never served as current.
    llm_rationale = Column(Text, nullable=True)
    llm_rationale_at = Column(DateTime, nullable=True)
    llm_rationale_key = Column(String, nullable=True)

    # LLM-07 (v0.67.0) — independent "risky delete" second opinion, cached the
    # same way as llm_rationale. Bare "KEEP"/"DELETE" verdict; "risky" is derived
    # at read time (KEEP on a scorer-flagged deletion candidate = conflict).
    llm_second_opinion = Column(String, nullable=True)
    llm_second_opinion_at = Column(DateTime, nullable=True)
    llm_second_opinion_key = Column(String, nullable=True)

    # *arr app link IDs (set after matching)
    sonarr_id = Column(Integer, nullable=True)
    radarr_id = Column(Integer, nullable=True)
    lidarr_id = Column(Integer, nullable=True)

    @property
    def risky_delete(self) -> bool:
        """LLM-07 — the second opinion disagreed with a deletion candidacy the
        scorer already flagged (score >= threshold to be in this list at all).
        Advisory only; never blocks or auto-resolves a deletion."""
        return self.llm_second_opinion == "KEEP"
