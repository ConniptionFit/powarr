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
    # Suspicious file-type detection (v0.19.0) — any file in a download matching one
    # of these (case-insensitive) extensions gets the row flagged/badged, across all
    # *arr apps (unlike quality-downgrade, one bad file is enough — checked with OR,
    # not AND). Archive formats (zip/rar/7z/...) are deliberately excluded from the
    # default list since most legitimate downloads arrive compressed.
    suspicious_extensions: list[str] = [
        ".exe", ".scr", ".bat", ".cmd", ".com", ".pif", ".msi", ".vbs", ".vbe",
        ".js", ".jse", ".wsf", ".wsh", ".ps1", ".jar", ".lnk", ".hta", ".cpl",
    ]
    suspicious_extension_auto_reject: bool = False
    # Also removes the download (every file, not just the flagged one — no download
    # client exposes reliable single-file deletion) via the download client's own
    # delete API. Only meaningful when suspicious_extension_auto_reject is on;
    # user-confirmed 2026-07-07 that whole-download deletion is acceptable here.
    suspicious_extension_delete_from_disk: bool = False


class OllamaSettings(BaseModel):
    enabled: bool = False
    host: str = ""  # ip:port or http(s)://host:port
    model: str = ""
    api_style: str = "ollama"  # ollama | openai (LM Studio, llama.cpp server, etc.)
    verbosity: str = "brief"  # minimal | brief | verbose — minimal = bare verdict, no prose
    model_size: str = "medium"  # small | medium | large — scales token caps/timeouts to the model
    keep_alive_minutes: int = 10  # ollama keep_alive between calls; 0 = ollama default (unload)
    reply_format: str = "json"  # json | simple | markdown — simple = one pipe-separated line for models that
    # can't do JSON; markdown = same JSON shape as "json" but the reason text is asked for in Markdown
    confidence_style: str = "numeric"  # numeric (model picks ±0.3 float) | classified (more/less/same → fixed steps)
    batch_delay_ms: int = 0  # optional pause between sequential batch calls — keeps weak hardware from pinning at 100%
    match_prompt: str = ""  # custom template for import matching; "" = built-in default
    explain_prompt: str = ""  # custom template for deletion rationale; "" = built-in default
    pack_prompt: str = ""  # custom template for season pack file matching; "" = built-in default
    verbose_rationales: bool = False  # extended reasoning in rationales (impacts token usage)
    # Per-task control (v0.27.0, Approved Queue #10) — the global `enabled` stays the
    # master switch; these narrow it per consumer. Task models default to the shared
    # `model` when blank, so existing configs behave exactly as before.
    match_enabled: bool = True    # import-match review + season-pack file matching
    explain_enabled: bool = True  # deletion-candidate rationales
    match_model: str = ""    # "" = use `model`
    explain_model: str = ""  # "" = use `model`
    # Circuit breaker (v0.27.0, Approved Queue #7) — after this many consecutive
    # call failures the assist auto-pauses (every call fails soft to None
    # immediately) for the cooldown, then retries normally. 0 disables the breaker.
    breaker_threshold: int = 5
    breaker_cooldown_minutes: int = 10

    def model_for(self, task: str) -> str:
        override = self.match_model if task == "match" else self.explain_model
        return (override or "").strip() or self.model

    def task_enabled(self, task: str) -> bool:
        """Effective on/off for one consumer ("match" | "explain"): the master
        switch AND the per-task toggle AND a usable host/model."""
        if not (self.enabled and self.host and self.model_for(task)):
            return False
        return self.match_enabled if task == "match" else self.explain_enabled


class CleanupSettings(BaseModel):
    excluded_libraries: list[str] = []  # library_section names never suggested for deletion
    soft_delete_days: int = 0  # 0 = delete immediately (current behavior); >0 = pending window
    protect_requested: bool = True  # hide Seerr-requested items from suggestions


class SyncSettings(BaseModel):
    plex_sync_interval_hours: int = 0  # 0 = manual sync only


class LlmScheduleSettings(BaseModel):
    # Automatic LLM backlog scanning during downtime, on top of the existing
    # on-demand "Run Now" paths (v0.4.0/v0.9.0). Off by default — this is purely
    # additive scheduling around llm_rescore()/llm_media_run(), which already
    # enforce single-flight + batch_delay_ms, so no separate rate limiting is
    # needed here.
    enabled: bool = False
    mode: str = "quiet_hours"  # "quiet_hours" (only within a daily window) | "trickle" (every maintenance cycle)
    quiet_hours_start: int = 0  # UTC hour, 0-23 inclusive — server runs UTC in Docker by default
    quiet_hours_end: int = 6  # UTC hour, 0-23 — window wraps past midnight when <= start
    max_items_per_pass: int = 20  # combined cap across imports + media per maintenance cycle (every 5 min)
    scan_imports: bool = True  # include the Failed Import Matching backlog
    scan_media: bool = True  # include the Cleanup deletion-rationale backlog


class BackupSettings(BaseModel):
    # Scheduled pg_dump (or SQLite file copy) to {data_dir}/backups/, on top of
    # the existing manual `docker exec postgres pg_dump ...` flow documented in
    # Docker & Deployment. Off by default.
    enabled: bool = False
    interval_hours: int = 24
    retention_count: int = 7  # keep the most recent N backup files; 0 = unlimited


class NotificationSettings(BaseModel):
    enabled: bool = False
    ntfy_url: str = ""  # e.g. http://10.1.1.2:8091
    topic: str = "powarr"
    # Click-to-act links (v0.26.0) — Accept/Reject buttons on a new-suggestion
    # ntfy notification, via signed one-time tokens (see action_tokens.py).
    # Needs a reachable public_base_url to build the action URLs; both default
    # off/blank so nothing changes until explicitly configured.
    public_base_url: str = ""  # e.g. https://powarr.pwrs.dev — must be reachable by the ntfy client
    actionable_new_suggestions: bool = False
    actionable_max_per_scan: int = 5  # a scan with more new suggestions than this falls back to the aggregate summary only


class IntegrationConfig(BaseModel):
    name: str
    url: Optional[str] = None
    api_key: Optional[str] = None  # masked on read (SECRET_MASK) — never the stored secret
    api_key_set: bool = False      # a secret is stored (drives the "leave blank to keep" UI)
    username: Optional[str] = None  # not a secret — returned as stored (qbittorrent WebUI user)
    password: Optional[str] = None  # masked on read — never the stored secret
    password_set: bool = False
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
