import json

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
    confidence = Column(Float, default=0.0)  # blended/primary score used for thresholds
    heuristic_confidence = Column(Float, nullable=True)  # algorithm-only score, pre-LLM-blend
    llm_confidence = Column(Float, nullable=True)
    llm_rationale = Column(String, nullable=True)
    llm_agrees = Column(Boolean, nullable=True)  # structured agree/disagree signal — llm_rationale is plain prose, no [agrees]/[disagrees] prefix
    pack_file_matches = Column(Text, nullable=True)  # JSON: per-file episode suggestions from LLM review
    mapping_overrides = Column(Text, nullable=True)  # JSON: {raw_path: {episode_id, season, episode, title}} user corrections
    quality_downgrade = Column(Boolean, nullable=True)  # every file rejects as "not an upgrade" — never importable as-is
    # Some files importable + some already covered (gap-fill pack/album) — v0.32.0
    partial_import = Column(Boolean, nullable=True)
    suspicious_files = Column(Text, nullable=True)  # JSON list of filenames matching a suspicious extension (empty/null = clean)
    # suggested | auto_resolved | accepted | rejected | closed_external | resolve_failed
    status = Column(String, default="suggested")
    # True while the download/queue id is still present in the source *arr queue
    # (v0.35.0) — surfaces accepted/rejected/orphaned rows that never left the queue
    still_in_queue = Column(Boolean, nullable=True, index=True)
    verified = Column(Boolean, nullable=True)  # import confirmed in *arr history after resolve
    message = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)

    @property
    def match_rationale(self) -> str | None:
        """Deterministic per-variable scorer readout, stored inside raw_metadata
        (additive-only schema rule — no dedicated column)."""
        try:
            return json.loads(self.raw_metadata or "{}").get("match_rationale")
        except (ValueError, TypeError):
            return None

    @property
    def pack(self) -> str | None:
        """Season-pack label ("S03", "S01-S03", "complete series") when the release
        was detected as a pack — also lives in raw_metadata."""
        try:
            return json.loads(self.raw_metadata or "{}").get("pack")
        except (ValueError, TypeError):
            return None
