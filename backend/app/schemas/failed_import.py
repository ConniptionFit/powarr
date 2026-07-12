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
    llm_agrees: Optional[bool] = None  # structured agree/disagree signal (llm_rationale is plain prose)
    pack_file_matches: Optional[str] = None  # JSON: per-file episode suggestions from LLM review
    mapping_overrides: Optional[str] = None  # JSON: user-corrected per-file episode mappings
    quality_downgrade: Optional[bool] = None  # every file rejects as "not an upgrade"
    partial_import: Optional[bool] = None  # some importable + some already covered (gap-fill)
    suspicious_files: Optional[str] = None  # JSON list of filenames matching a suspicious extension
    status: str
    still_in_queue: Optional[bool] = None  # download still present in the *arr queue (v0.35.0)
    verified: Optional[bool] = None
    message: Optional[str] = None
    root_cause_code: Optional[str] = None  # FI-06 — plain-language root-cause tag
    root_cause_label: Optional[str] = None
    root_cause_action: Optional[str] = None
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
    still_in_queue: int = 0  # rows whose download is still in the *arr queue (v0.35.0)
    by_service: dict[str, int] = {}  # suggested count per source app
    auto_resolved_7d: int = 0
    # Process N Items (v0.28.0) — 0 when auto_resolve is off
    auto_eligible_count: int = 0
    # suggested + resolve_failed + orphan_pending (v0.35.0 Needs attention view)
    needs_attention: int = 0


class AutoEligibleOut(BaseModel):
    enabled: bool
    threshold: float
    count: int
    ids: list[int] = []


class RecentDownloadOut(BaseModel):
    """FI-09 — one row per distinct downloadId recently grabbed by an *arr
    app, independent of stuck-import detection."""
    source_app: str
    source_title: str
    download_id: str
    matched_id: Optional[int] = None
    matched_title: Optional[str] = None
    event_date: Optional[str] = None
    still_in_queue: bool = False


class MalformedImportFlagOut(BaseModel):
    """FI-10 — a pack whose download already left the queue but whose current
    on-disk coverage looks incomplete when re-checked."""
    id: int
    source_app: str
    matched_id: Optional[int] = None
    matched_title: Optional[str] = None
    download_id: str
    source_title: str
    pack_label: Optional[str] = None
    mapped_episodes: Optional[int] = None
    total_episodes: Optional[int] = None
    coverage_ratio: Optional[float] = None
    flagged_at: Optional[datetime] = None
    dismissed: bool = False

    model_config = {"from_attributes": True}
