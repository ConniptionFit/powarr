from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime

from app.database import Base


class ArtistAddLog(Base):
    """One row per successful Lidarr add, from either the Artist Discovery
    accept flow or the standalone Related Artists search — both call through
    _add_artist_to_lidarr(), the shared source of truth for this log. Purely
    an activity record (weekly digest, future history views); DiscoveredArtist
    remains the review-queue bookkeeping for the Discovery flow specifically."""
    __tablename__ = "artist_add_log"

    id = Column(Integer, primary_key=True, index=True)
    artist_name = Column(String, nullable=False)
    musicbrainz_id = Column(String, nullable=True)
    source = Column(String, nullable=False)  # discovery | related
    lidarr_artist_id = Column(Integer, nullable=True)
    added_at = Column(DateTime, default=datetime.utcnow, index=True)
