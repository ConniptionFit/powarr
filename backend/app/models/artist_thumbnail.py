from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime

from app.database import Base


class ArtistThumbnail(Base):
    """AD-21 — persistent thumbnail-URL cache for artists actually in the
    user's library (Lidarr and/or synced Plex music). Populated by the daily
    refresh in services/artist_thumbnails.py, which also deletes rows once an
    artist is no longer in the library. Consumed by the Related Artists
    typeahead, whose Last.fm images are blank/placeholder for most artists.
    A row with image_url NULL records a confirmed miss (all sources checked,
    nothing found) so the refresh doesn't re-search every artist every run."""
    __tablename__ = "artist_thumbnails"

    id = Column(Integer, primary_key=True, index=True)
    name_key = Column(String, unique=True, index=True, nullable=False)  # _norm_artist() form
    artist_name = Column(String, nullable=False)
    image_url = Column(String, nullable=True)
    source = Column(String, nullable=True)  # lidarr | deezer
    musicbrainz_id = Column(String, nullable=True)
    fetched_at = Column(DateTime, default=datetime.utcnow)
