from sqlalchemy import Column, Integer, String, Float, DateTime, BigInteger, Boolean
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
    pending_delete_at = Column(DateTime, nullable=True)  # soft-delete: when deletion was requested

    # *arr app link IDs (set after matching)
    sonarr_id = Column(Integer, nullable=True)
    radarr_id = Column(Integer, nullable=True)
    lidarr_id = Column(Integer, nullable=True)
