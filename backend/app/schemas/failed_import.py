from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class FailedImportOut(BaseModel):
    id: int
    source_app: str
    queue_item_id: Optional[str] = None
    download_id: Optional[str] = None
    raw_title: str
    matched_title: Optional[str] = None
    matched_id: Optional[int] = None
    confidence: float
    heuristic_confidence: Optional[float] = None
    match_rationale: Optional[str] = None  # deterministic per-variable scorer readout
    pack: Optional[str] = None  # season-pack label ("S03", "S01-S03", "complete series")
    llm_confidence: Optional[float] = None
    llm_rationale: Optional[str] = None
    pack_file_matches: Optional[str] = None  # JSON: per-file episode suggestions from LLM review
    mapping_overrides: Optional[str] = None  # JSON: user-corrected per-file episode mappings
    quality_downgrade: Optional[bool] = None  # every file rejects as "not an upgrade"
    status: str
    verified: Optional[bool] = None
    message: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ImportStats(BaseModel):
    suggested: int = 0
    auto_resolved: int = 0
    accepted: int = 0
    rejected: int = 0
    closed_external: int = 0
    resolve_failed: int = 0
    orphan_pending: int = 0
    orphaned: int = 0
    by_service: dict[str, int] = {}  # suggested count per source app
    auto_resolved_7d: int = 0
