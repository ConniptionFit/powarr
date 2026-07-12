from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean
from datetime import datetime

from app.database import Base


class MalformedImportFlag(Base):
    """FI-10: a pack release whose download already left the *arr queue (Sonarr
    considers it imported) but whose current on-disk episode coverage looks
    incomplete when re-checked against the same absolute/season scope FI-08/
    FI-02 already use — e.g. a double-segment pack that only ever imported
    half its claimed episodes. Notify-and-triage only: nothing here ever
    rewrites the library. See services/malformed_audit.py."""
    __tablename__ = "malformed_import_flags"

    id = Column(Integer, primary_key=True, index=True)
    source_app = Column(String, nullable=False)
    matched_id = Column(Integer, nullable=True)
    matched_title = Column(String, nullable=True)
    download_id = Column(String, nullable=False, index=True)
    source_title = Column(String, nullable=False)
    pack_label = Column(String, nullable=True)
    mapped_episodes = Column(Integer, nullable=True)
    total_episodes = Column(Integer, nullable=True)
    coverage_ratio = Column(Float, nullable=True)
    flagged_at = Column(DateTime, default=datetime.utcnow)
    dismissed = Column(Boolean, default=False)
