from sqlalchemy import Column, Integer, String, Boolean, Text
from app.database import Base


class Integration(Base):
    __tablename__ = "integrations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)  # plex | tautulli | sonarr | radarr | lidarr
    url = Column(String, nullable=True)
    api_key = Column(String, nullable=True)
    enabled = Column(Boolean, default=False)
    extra_config = Column(Text, nullable=True)  # JSON string for per-integration extras
