"""LLM match-review call log (LLM-LOG-01) — one row per real match-review LLM
reply (scan-time and on-demand rescore), for offline prompt engineering: replay
the exact inputs against a new scaffold/model and compare verdicts against the
ground-truth `resolution` backfilled when the failed-import row closes. Additive
table; pruned on a retention schedule (see services/llm_match_log.py)."""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, Float
from datetime import datetime

from app.database import Base


class LlmMatchLog(Base):
    __tablename__ = "llm_match_log"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    failed_import_id = Column(Integer, nullable=True, index=True)
    site = Column(String, nullable=True)  # scan | rescore
    source_app = Column(String, nullable=True)
    model = Column(String, nullable=True)
    scaffold_version = Column(String, nullable=True)
    prompt_hash = Column(String, nullable=True)  # sha256[:16] of the full prompt
    # The prompt's variable inputs — enough to rebuild the call with any scaffold.
    release_title = Column(Text, nullable=True)
    candidate_title = Column(Text, nullable=True)
    context = Column(Text, nullable=True)
    det_summary = Column(Text, nullable=True)
    # Deterministic App-check flags (lidarr only; null elsewhere).
    evidence_artist_ok = Column(Boolean, nullable=True)
    evidence_album_ok = Column(Boolean, nullable=True)
    raw_reply = Column(Text, nullable=True)
    parse_ok = Column(Boolean, nullable=True)
    agrees = Column(Boolean, nullable=True)  # post-enforcement verdict
    confidence_adjustment = Column(Float, nullable=True)
    enforced = Column(Boolean, nullable=True)  # enforce_music_evidence flipped the verdict
    latency_ms = Column(Integer, nullable=True)
    # Ground-truth label: the failed-import row's terminal status (accepted /
    # rejected / auto_resolved / orphaned / closed_external), backfilled by the
    # scheduler once the row closes.
    resolution = Column(String, nullable=True, index=True)
    resolved_at = Column(DateTime, nullable=True)
