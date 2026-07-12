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
    # Days until a watched item's watch-factor approaches ~0.63 of max (v0.30.0).
    # Pre-v0.30 used a hard linear /365 ramp — see [[Scoring System — Pre-v0.30 Backup]].
    watch_half_life_days: float = 365.0


class ScoringProfiles(BaseModel):
    """Per-Plex-library partial overlays on ScoringWeights (v0.30.0, AQ #16).

    Keys are `MediaItem.library_section` names. Each value is a partial dict of
    ScoringWeights fields — only listed keys override the global default.
    """
    by_library: dict[str, dict] = {}


class ImportMatchingSettings(BaseModel):
    enabled: bool = True  # read-only queue polling/detection
    poll_interval_seconds: int = 300
    high_confidence_threshold: float = 0.90  # algorithm (heuristic) leg of the auto-import gate (0-1)
    low_confidence_floor: float = 0.50  # < this → log only, no triage row
    auto_resolve_enabled: bool = True  # writes back to *arr apps — on by default (user can disable)
    # Auto-import gating (v0.44.0, user-confirmed 2026-07-11) — which signal(s)
    # must clear their own threshold before a matched row auto-imports.
    #   algorithm: heuristic_confidence >= high_confidence_threshold
    #   llm:       llm_confidence       >= llm_auto_threshold
    #   either (default) / both: OR / AND of the two legs.
    # Rows with no LLM signal simply fail the LLM leg — "either" degrades to
    # algorithm-only; "llm" and "both" never pass without an LLM score.
    auto_import_mode: str = "either"  # llm | algorithm | either | both
    llm_auto_threshold: float = 0.80
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
    # FI-08, opt-in/off by default (confirmation-gated: this can raise pack
    # confidence for anime batch releases enough to newly qualify for
    # auto-resolve, per the Non-negotiable Principles' scoring-change rule).
    # When on, a seriesType=anime pack with no season marker at all ("001-100")
    # scopes its coverage denominator to that absolute-episode range instead
    # of the whole aired show, so a genuinely complete absolute-numbered pack
    # doesn't read stuck at a tiny fraction forever.
    anime_absolute_pack_coverage: bool = False
    # FI-10, opt-in/off by default — a new proactive notification channel the
    # user hasn't asked for by default, even though it never writes to the
    # library. Nightly (self-gated in scheduler.py) re-check of Sonarr pack
    # grabs whose download already left the queue: flags ones whose current
    # on-disk coverage (same scope logic as FI-02/FI-08's pack_scope_episodes)
    # looks incomplete, so a double-segment pack that quietly only imported
    # half doesn't go unnoticed once it's no longer in active triage.
    malformed_audit_enabled: bool = False
    malformed_audit_interval_hours: int = 24
    malformed_audit_lookback_days: int = 7
    malformed_audit_threshold: float = 0.9  # below this coverage ratio → flagged
    # Orphan handling (v0.6.1) — confirmed-missing rows prompt for confirmation by default;
    # this skips the prompt and marks them orphaned immediately (same positive-confirmation gate)
    orphan_auto_purge: bool = False
    # LLM share of the confidence blend (v0.12.0, user-confirmed 2026-07-06) —
    # final = (1-w) * deterministic + w * llm. 0 = ignore the LLM entirely.
    llm_blend_weight: float = 0.3
    # Equal-or-better library coverage (v0.17.0 Sonarr; v0.29.0 all *arr incl. Lidarr) —
    # a suggested row is always flagged/badged when every file rejects as "not an
    # upgrade" / "album already imported" (library already has equal-or-better
    # quality). This additionally auto-rejects during the scan instead of leaving
    # it in triage. Off by default — same positive-opt-in pattern as orphan_auto_purge.
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
    verbose_rationales: bool = False  # legacy unused — verbosity tier replaced it
    # Forbid chain-of-thought / <think> in the scaffold (v0.30.0) — on by default.
    # Models should answer with verdict + bullets only; stripping still runs as a backstop.
    forbid_thinking: bool = True
    # Inject a compact structured det_summary instead of the full prose rationale
    # (v0.30.0) — smaller context, same signal. On by default.
    compact_det_summary: bool = True
    # Reply format is fixed to rich-text-capable JSON (reason may use Markdown
    # bullets/bold). The Settings UI no longer exposes reply_format; the field
    # remains for API/back-compat and is forced to "markdown" at call sites.
    # Per-task control (v0.27.0, Approved Queue #10) — the global `enabled` stays the
    # master switch; these narrow it per consumer. Task models default to the shared
    # `model` when blank, so existing configs behave exactly as before.
    match_enabled: bool = True    # import-match review + season-pack file matching
    explain_enabled: bool = True  # deletion-candidate rationales
    match_model: str = ""    # "" = use `model`
    explain_model: str = ""  # "" = use `model`
    # LLM-07 (v0.67.0) — independent "risky delete" second opinion on deletion
    # candidates: same model call as explain (minimal verbosity KEEP/DELETE), run
    # against a summary that now names protection flags/watch-progress by name, so
    # a KEEP verdict on an item the scorer flagged for deletion is a real conflict
    # signal, not a coin flip. Advisory only — never blocks or auto-resolves a
    # deletion. Off by default: a new proactive judgment-affecting signal, opt-in
    # like every other feature in this category this session.
    second_opinion_enabled: bool = False
    second_opinion_model: str = ""  # "" = use `model`
    # Circuit breaker (v0.27.0, Approved Queue #7) — after this many consecutive
    # call failures the assist auto-pauses (every call fails soft to None
    # immediately) for the cooldown, then retries normally. 0 disables the breaker.
    breaker_threshold: int = 5
    breaker_cooldown_minutes: int = 10
    # Inference tuning (v0.29.0, Approved Queue #13) — 0 for max_tokens/timeout
    # means "use the model_size profile defaults from _limits()".
    temperature: float = 0.0
    max_tokens: int = 0
    timeout_seconds: int = 0

    def model_for(self, task: str) -> str:
        override = {"match": self.match_model, "explain": self.explain_model,
                    "second_opinion": self.second_opinion_model}.get(task, "")
        return (override or "").strip() or self.model

    def task_enabled(self, task: str) -> bool:
        """Effective on/off for one consumer ("match" | "explain" |
        "second_opinion"): the master switch AND the per-task toggle AND a
        usable host/model."""
        if not (self.enabled and self.host and self.model_for(task)):
            return False
        return {"match": self.match_enabled, "explain": self.explain_enabled,
                "second_opinion": self.second_opinion_enabled}.get(task, False)


class LlmPolicies(BaseModel):
    """Per-source_app and per-Plex-library partial overlays on LLM behavior
    (v0.68.0, LLM-08) — extends the global per-task toggles (v0.27.0) and mirrors
    ScoringProfiles' by_library overlay shape (v0.30.0). User-confirmed 2026-07-12:
    empty by default, so every app/library keeps today's global behavior
    (including the existing 0.3 llm_blend_weight) until a row is explicitly added.

    `by_app` keys are FailedImport.source_app values (sonarr/radarr/lidarr/readarr).
    Overlay dict values: `match_enabled` (bool), `match_model` (str),
    `llm_blend_weight` (float). Match review + confidence blending only ever
    operate on FailedImport rows, which carry no library_section — source_app is
    the only axis that makes sense for them.

    `by_library` keys are MediaItem.library_section names. Overlay dict values:
    `explain_enabled` (bool), `explain_model` (str). Deletion rationale only ever
    operates on MediaItem rows, which carry no source_app — library is the only
    axis that makes sense for it. (Does not extend to the LLM-07 second-opinion
    task — out of LLM-08's stated scope.)
    """
    by_app: dict[str, dict] = {}
    by_library: dict[str, dict] = {}


class CleanupSettings(BaseModel):
    excluded_libraries: list[str] = []  # library_section names never suggested for deletion
    soft_delete_days: int = 0  # 0 = delete immediately (current behavior); >0 = pending window
    protect_requested: bool = True  # hide Seerr-requested items from suggestions
    # Tautulli multi-user protection (v0.29.0, Approved Queue #12) — hide items
    # another household user watched within N days. Separate from Seerr `protected`
    # (that flag is wiped on every Seerr refresh). Off by default.
    protect_other_users: bool = False
    other_user_watch_days: int = 30
    primary_tautulli_user: str = ""  # friendly_name whose watches do NOT protect (your own)
    # LIB-05 (v0.52.0) — hide items whose file lives inside a torrent actively
    # seeding in a configured download client (qBittorrent/Transmission). Off by
    # default (opt-in, same pattern as protect_other_users). Refreshed during
    # Plex sync; fail-soft — an unreachable download client never clears
    # protection, it just skips that cycle's refresh.
    protect_seeding_torrents: bool = False
    # LIB-04 (v0.54.0) — hide items that are in-progress (started but not
    # finished) per Tautulli watch history, so a show/movie someone is midway
    # through doesn't get suggested for deletion. Off by default (opt-in,
    # requires Tautulli). min/max bound the "in progress" band — below min is
    # barely-started (not worth protecting), at/above max is essentially
    # finished (already scored appropriately by watch history, no separate
    # protection needed).
    protect_in_progress: bool = False
    in_progress_min_percent: float = 5.0
    in_progress_max_percent: float = 90.0
    in_progress_lookback_days: int = 30


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


class QdrantSettings(BaseModel):
    """Shared Qdrant connection (v0.40.0) — single source of truth for every module
    that talks to the `music_affinity_space` collection (Smart Playlists, Artist
    Discovery). Configured once on Settings -> Integrations; consumers load this
    instead of keeping their own copy of the connection details."""
    url: str = ""
    api_key: str = ""
    collection: str = "music_affinity_space"


class SmartPlaylistSettings(BaseModel):
    """MOD-01 Smart Playlists (v0.34.0) — read-only Qdrant → Plex genre playlists.
    Qdrant connection lives in [[QdrantSettings]] (Settings -> Integrations), not here."""
    enabled: bool = False
    # SP-05 — new Plex playlists require approval (manual Accept / Approve) unless
    # this is on. Off by default so scheduled runs never push unreviewed playlists.
    auto_create_playlists: bool = False
    # SP-05 — once a playlist has been pushed to Plex (plex_playlist_id set),
    # scheduled runs auto-add eligible tracks. On by default.
    auto_update_playlists: bool = True
    # Legacy alias for auto_update_playlists / per-playlist override fallback.
    # Prefer auto_update_playlists for new code; kept for existing UI + overrides.
    auto_add_tracks_default: bool = True
    min_artists_per_genre: int = 3
    excluded_genres: list[str] = []
    # SP-07 / SP-03 — blacklist-only eligibility. Normalized names matched case-
    # insensitively; all other monitored artists remain eligible.
    blacklisted_artists: list[str] = []
    max_tracks_per_playlist: int = 200
    schedule_enabled: bool = False
    schedule_interval_hours: int = 24
    # SP-04 / SP-08 — LLM names at create time and on-demand rename. Fails soft
    # to "Powarr · {genre}" when the LLM is disabled/unreachable.
    llm_playlist_names: bool = False
    # SP-02 — bias per-artist track selection toward tracks sonically close to the
    # playlist's most recently added track, via Plex's own Sonic Analysis
    # (PlexIntegration.sonically_similar_keys). Pure re-ranking on top of the
    # existing Qdrant genre/artist eligibility, never a filter. Off by default:
    # requires Plex Pass + sonic analysis having been run on the library; fails
    # soft to the prior insertion-order pick whenever analysis data is missing.
    sonic_similarity_enabled: bool = False
    # SP-14 — case/punctuation/whitespace genre-label variants (e.g. "Hip-Hop" /
    # "Hip Hop" / "hip_hop") always merge automatically into one playlist. This
    # map additionally merges genuinely different-looking labels the user
    # considers equivalent (e.g. {"Rap": "Hip-Hop"}) — a taxonomy judgment call
    # left to the user rather than a hardcoded opinion. Keys/values matched
    # case-insensitively; the value is the canonical display label.
    genre_aliases: dict[str, str] = {}
    # SP-12 — named intent templates (e.g. "Workout": ["Rock", "Electronic",
    # "Hip-Hop"]), each a playlist generated from the UNION of its listed
    # genres instead of a single genre. Empty by default — which genres mean
    # "workout" vs "focus" is a taste judgment left entirely to the user, same
    # precedent as genre_aliases above, not a hardcoded opinion. The item's
    # other ask (weighting by Sonic Analysis energy/valence) isn't
    # implementable as literally described — Plex's public API exposes Sonic
    # Analysis only via nearest-neighbor similarity search (already used by
    # sonic_similarity_enabled above), not queryable raw energy/valence
    # values — so template playlists get whatever sonic bias that existing
    # setting already provides, same as any other playlist.
    playlist_templates: dict[str, list[str]] = {}
    # SP-13 — playlists were add-only before this: a track never leaves once
    # added, even if its artist gets blacklisted later or the track itself
    # leaves Plex (deleted, moved libraries). Opt-in/off by default — unlike
    # SP-12/AD-17 above, this *removes* content from a playlist the user
    # curated, so it needs an explicit opt-in rather than defaulting on.
    prune_stale_tracks_enabled: bool = False
    # SP-09 — related-artist axis: a playlist seeded from one owned artist plus
    # whichever of its Last.fm-similar artists are also monitored. Map of
    # playlist name -> seed artist name, empty by default (same "taxonomy left
    # to the user" precedent as genre_aliases/playlist_templates). The item's
    # other two named axes (mood, era) aren't implemented — see the note in
    # [[Smart Playlists]]: the Qdrant payload has mood_tags/era fields, but
    # nothing in the current pipeline ever populates them with real data, so
    # there's nothing to generate from yet without a new tagging pipeline.
    related_artist_seeds: dict[str, str] = {}


class ArtistDiscoverySettings(BaseModel):
    """Artist Discovery — native port of the n8n Music Curator (Last.fm scrobbles →
    Ollama embeddings → Qdrant taste-centroid similarity + related-artist graph →
    Lidarr). Writes to the same `music_affinity_space` collection Smart Playlists
    reads (soft-delete semantics — never deletes points, only flips flags). Qdrant
    connection lives in [[QdrantSettings]] (Settings -> Integrations), not here."""
    enabled: bool = False
    # Standalone Ollama connection for embeddings — deliberately independent of the
    # Local LLM Assist Ollama settings (no fallback/reuse), even if both happen to
    # point at the same host in practice.
    ollama_host: str = ""
    embed_model: str = "all-minilm"
    max_candidates_per_run: int = 5
    related_artists_limit: int = 3  # top-N similar artists kept per seed (graph sync)
    # AD-07 — dual thresholds on *recently-listened* seed connection count only
    # (Qdrant associated_seed_mbids filtered by scrobble_lookback_days). Example
    # band: suggest=3, auto_add=5 — numbers are configurable; auto_add=0 disables.
    suggest_connection_threshold: int = 3
    auto_add_connection_threshold: int = 0  # 0 = auto-add disabled (safe default)
    related_artists_refresh_days: int = 30  # re-scan a seed's similar artists after this many days
    similarity_threshold: float = 0.75  # cosine score floor for centroid-search candidates
    scrobble_lookback_days: int = 30  # AD-07 — window for "recently listened" seed filter
    # Legacy master switch — when True and auto_add_connection_threshold is 0,
    # load_settings migrates auto_add to suggest_connection_threshold. Prefer the
    # numeric auto_add threshold going forward.
    auto_promote: bool = False
    root_folder_path: str = ""  # "" = use Lidarr's first available root folder
    quality_profile_id: int = 0  # 0 = use Lidarr's first available quality profile
    metadata_profile_id: int = 0  # 0 = use Lidarr's first available metadata profile
    schedule_enabled: bool = False
    schedule_interval_hours: int = 24  # full discovery cycle: ingest + centroid + graph
    sync_schedule_enabled: bool = False
    sync_interval_hours: int = 1  # differential sync: Lidarr/Last.fm stats -> Qdrant
    # AD-08 — purge image_url on accepted rows after this many days (0 = never).
    thumbnail_retention_days: int = 30
    # AD-17 — second discovery lane seeded from recently-listened artists
    # (reuses scrobble_lookback_days, the same "recent" window AD-07 already
    # established) alongside the existing all-time most-played centroid, so
    # results aren't dominated by one blended average. Purely additive (finds
    # more candidates, never removes any) — default on, unlike scoring/
    # auto-resolve-affecting toggles elsewhere in the app.
    recent_taste_lane_enabled: bool = True


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
    # Weekly digest (v0.29.0, Approved Queue #15) — one ntfy summary per week.
    digest_enabled: bool = False
    digest_weekday: int = 0  # 0=Monday … 6=Sunday (datetime.weekday())
    digest_hour_utc: int = 9
    # Per-section toggles (v0.50.0) — all default on so existing digests keep
    # their current content; new sections (artists/playlists) are included by
    # default too since the digest itself is already opt-in via digest_enabled.
    digest_include_imports: bool = True
    digest_include_artists: bool = True
    digest_include_playlists: bool = True
    digest_include_cleanup: bool = True


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
