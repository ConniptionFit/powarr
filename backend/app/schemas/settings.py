from pydantic import BaseModel
from typing import Optional


class ScoringWeights(BaseModel):
    watch_history_weight: float = 4.0
    file_size_weight: float = 3.0
    file_age_weight: float = 1.5
    release_date_weight: float = 1.5
    min_score_threshold: float = 40.0
    never_watched_boost: float = 2.0
    max_size_gb_reference: float = 50.0
    max_age_days_reference: float = 1825.0
    max_release_age_years_reference: float = 20.0


class ImportMatchingSettings(BaseModel):
    enabled: bool = True  # read-only queue polling/detection
    poll_interval_seconds: int = 300
    high_confidence_threshold: float = 0.90  # >= this → eligible for auto-resolve
    low_confidence_floor: float = 0.50  # < this → log only, no triage row
    auto_resolve_enabled: bool = False  # writes back to *arr apps — off until explicitly enabled
    grace_period_minutes: int = 10  # skip queue items younger than this — *arr often self-retries
    include_stalled: bool = False  # also flag stalled downloads, not just import failures
    verify_timeout_minutes: int = 30  # resolved rows unverified after this → resolve_failed
    sonarr_enabled: bool = True
    radarr_enabled: bool = True
    lidarr_enabled: bool = True
    readarr_enabled: bool = True
    # Episode-level match weighting (v0.5.0) — read by the scorer AND the LLM prompt scaffold
    title_weight: float = 0.6  # episode-title similarity: heaviest single factor, non-overriding
    number_weight: float = 0.4  # season/episode (or anime absolute) numeric corroboration
    title_only_cap: float = 0.85  # ceiling when no numeric corroboration exists — keeps title-only below auto-resolve
    anime_absolute_numbering: bool = True  # seriesType=anime → absoluteEpisodeNumber is the primary numeric signal
    # Orphan handling (v0.6.1) — confirmed-missing rows prompt for confirmation by default;
    # this skips the prompt and marks them orphaned immediately (same positive-confirmation gate)
    orphan_auto_purge: bool = False
    # LLM share of the confidence blend (v0.12.0, user-confirmed 2026-07-06) —
    # final = (1-w) * deterministic + w * llm. 0 = ignore the LLM entirely.
    llm_blend_weight: float = 0.3
    # Quality-downgrade handling (v0.17.0) — a suggested row is always flagged/badged
    # when every file in the download rejects as "not an upgrade"; this additionally
    # auto-rejects it during the scan instead of leaving it in triage. Off by default —
    # same positive-opt-in pattern as orphan_auto_purge.
    quality_downgrade_auto_reject: bool = False


class OllamaSettings(BaseModel):
    enabled: bool = False
    host: str = ""  # ip:port or http(s)://host:port
    model: str = ""
    api_style: str = "ollama"  # ollama | openai (LM Studio, llama.cpp server, etc.)
    verbosity: str = "brief"  # minimal | brief | verbose — minimal = bare verdict, no prose
    model_size: str = "medium"  # small | medium | large — scales token caps/timeouts to the model
    keep_alive_minutes: int = 10  # ollama keep_alive between calls; 0 = ollama default (unload)
    reply_format: str = "json"  # json | simple — simple = one pipe-separated line, for models that can't do JSON
    confidence_style: str = "numeric"  # numeric (model picks ±0.3 float) | classified (more/less/same → fixed steps)
    batch_delay_ms: int = 0  # optional pause between sequential batch calls — keeps weak hardware from pinning at 100%
    match_prompt: str = ""  # custom template for import matching; "" = built-in default
    explain_prompt: str = ""  # custom template for deletion rationale; "" = built-in default
    pack_prompt: str = ""  # custom template for season pack file matching; "" = built-in default
    verbose_rationales: bool = False  # extended reasoning in rationales (impacts token usage)


class CleanupSettings(BaseModel):
    excluded_libraries: list[str] = []  # library_section names never suggested for deletion
    soft_delete_days: int = 0  # 0 = delete immediately (current behavior); >0 = pending window
    protect_requested: bool = True  # hide Seerr-requested items from suggestions


class SyncSettings(BaseModel):
    plex_sync_interval_hours: int = 0  # 0 = manual sync only


class NotificationSettings(BaseModel):
    enabled: bool = False
    ntfy_url: str = ""  # e.g. http://10.1.1.2:8091
    topic: str = "powarr"


class IntegrationConfig(BaseModel):
    name: str
    url: Optional[str] = None
    api_key: Optional[str] = None
    username: Optional[str] = None  # user/pass download clients (qbittorrent)
    password: Optional[str] = None
    enabled: bool = False
    remove_from_monitored_on_delete: bool = True
    delete_from_arr_list: bool = False

    model_config = {"from_attributes": True}


class IntegrationConfigUpdate(BaseModel):
    url: Optional[str] = None
    api_key: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    enabled: Optional[bool] = None
    remove_from_monitored_on_delete: Optional[bool] = None
    delete_from_arr_list: Optional[bool] = None
