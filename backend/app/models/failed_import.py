from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean
from datetime import datetime

from app.database import Base


class FailedImport(Base):
    __tablename__ = "failed_imports"

    id = Column(Integer, primary_key=True, index=True)
    source_app = Column(String, nullable=False)  # sonarr | radarr | lidarr
    queue_item_id = Column(String, nullable=True)  # queue record id in the source app
    download_id = Column(String, index=True, nullable=True)  # download client hash — stable dedupe key
    raw_title = Column(String, nullable=False)
    raw_metadata = Column(Text, nullable=True)  # JSON: queue status messages, output path, protocol
    matched_title = Column(String, nullable=True)
    matched_id = Column(Integer, nullable=True)  # series/movie/artist id in the source app
    matched_metadata = Column(Text, nullable=True)  # JSON: candidate details
    confidence = Column(Float, default=0.0)
    llm_confidence = Column(Float, nullable=True)
    llm_rationale = Column(String, nullable=True)
    # suggested | auto_resolved | accepted | rejected | closed_external | resolve_failed
    status = Column(String, default="suggested")
    verified = Column(Boolean, nullable=True)  # import confirmed in *arr history after resolve
    message = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
