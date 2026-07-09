const BASE = "/api/v1";

async function req<T>(path: string, opts?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (r.status === 401 && !path.startsWith("/auth/")) {
    // Session missing/expired — surface the login modal
    window.dispatchEvent(new Event("powarr:unauthorized"));
  }
  if (!r.ok) {
    let detail = `${r.status} ${r.statusText}`;
    try {
      const body = await r.json();
      if (body?.detail) detail = String(body.detail);
    } catch { /* keep default */ }
    throw new Error(detail);
  }
  if (r.status === 204) return undefined as T;
  return r.json();
}

// --- Media ---
export interface MediaItem {
  id: number;
  plex_rating_key: string;
  title: string;
  year: number | null;
  media_type: string;
  library_section: string | null;
  file_path: string | null;
  file_size: number;
  added_at: string | null;
  release_date: string | null;
  last_watched_at: string | null;
  watch_count: number;
  score: number;
  ignored: boolean;
  parent_title: string | null;
  protected: boolean | null;
  pending_delete_at: string | null;
  llm_rationale: string | null;
  llm_rationale_at: string | null;
  sonarr_id: number | null;
  radarr_id: number | null;
  lidarr_id: number | null;
}

export interface DeletionLogEntry {
  id: number;
  title: string;
  parent_title: string | null;
  media_type: string;
  library_section: string | null;
  file_size: number;
  arr_action: string | null;
  deleted_at: string | null;
}

export interface DeletionStats {
  deleted_30d: number;
  freed_30d_bytes: number;
  deleted_total: number;
  freed_total_bytes: number;
}

export interface MediaStats {
  total_items: number;
  total_size_bytes: number;
  candidates_above_threshold: number;
  potential_savings_bytes: number;
  last_synced: string | null;
}

function downloadCsv(path: string) {
  // Same-origin cookie auth; open as a navigation so the browser saves the file.
  window.location.assign(`/api/v1${path}`);
}

export const mediaApi = {
  list: (params?: Record<string, string | number | boolean>) => {
    const qs = params ? "?" + new URLSearchParams(params as Record<string, string>).toString() : "";
    return req<MediaItem[]>(`/media${qs}`);
  },
  stats: () => req<MediaStats>("/media/stats"),
  ignore: (id: number, ignored: boolean) =>
    req(`/media/${id}/ignore?ignored=${ignored}`, { method: "PATCH" }),
  delete: (id: number) => req(`/media/${id}`, { method: "DELETE" }),
  deleteBatch: (ids: number[]) =>
    req(`/media/batch`, { method: "DELETE", body: JSON.stringify(ids) }),
  libraries: () => req<string[]>("/media/libraries"),
  restore: (id: number) => req<{ id: number; restored: boolean }>(`/media/${id}/restore`, { method: "POST" }),
  explain: (id: number, force = false) =>
    req<{ rationale: string | null; message: string | null; cached: boolean }>(
      `/media/${id}/explain${force ? "?force=true" : ""}`, { method: "POST" }),
  llmRun: (ids?: number[]) =>
    req<{ started: number; total_eligible: number; message: string }>("/media/llm-run", {
      method: "POST", body: JSON.stringify(ids?.length ? { ids } : {}),
    }),
  deletionLog: () => req<DeletionLogEntry[]>("/media/deletion-log"),
  deletionStats: () => req<DeletionStats>("/media/deletion-stats"),
  exportCsv: (params?: Record<string, string | number | boolean>) => {
    const qs = params ? "?" + new URLSearchParams(params as Record<string, string>).toString() : "";
    downloadCsv(`/media/export.csv${qs}`);
  },
  exportDeletionLogCsv: () => downloadCsv("/media/deletion-log/export.csv"),
};

// --- Settings ---
export interface ScoringWeights {
  watch_history_weight: number;
  file_size_weight: number;
  file_age_weight: number;
  release_date_weight: number;
  min_score_threshold: number;
  never_watched_boost: number;
  max_size_gb_reference: number;
  max_age_days_reference: number;
  max_release_age_years_reference: number;
}

export interface ImportMatchingSettings {
  enabled: boolean;
  poll_interval_seconds: number;
  high_confidence_threshold: number;
  low_confidence_floor: number;
  auto_resolve_enabled: boolean;
  grace_period_minutes: number;
  include_stalled: boolean;
  verify_timeout_minutes: number;
  sonarr_enabled: boolean;
  radarr_enabled: boolean;
  lidarr_enabled: boolean;
  readarr_enabled: boolean;
  title_weight: number;
  number_weight: number;
  title_only_cap: number;
  anime_absolute_numbering: boolean;
  orphan_auto_purge: boolean;
  llm_blend_weight: number; // LLM share of the confidence blend (0-1)
  quality_downgrade_auto_reject: boolean;
  suspicious_extensions: string[];
  suspicious_extension_auto_reject: boolean;
  suspicious_extension_delete_from_disk: boolean;
}

export interface OllamaSettings {
  enabled: boolean;
  host: string;
  model: string;
  api_style: string;
  verbosity: string; // minimal | brief | verbose
  model_size: string; // small | medium | large — scales token caps/timeouts
  keep_alive_minutes: number; // ollama keep_alive between calls; 0 = unload after each
  reply_format: string; // json | simple
  confidence_style: string; // numeric | classified
  batch_delay_ms: number; // pause between sequential batch calls; 0 = none
  match_prompt: string;
  explain_prompt: string;
  // Per-task control (v0.27.0) — task models default to `model` when blank
  match_enabled: boolean;
  explain_enabled: boolean;
  match_model: string;
  explain_model: string;
  // Circuit breaker (v0.27.0) — 0 threshold disables
  breaker_threshold: number;
  breaker_cooldown_minutes: number;
  // Inference tuning (v0.29.0) — 0 max_tokens/timeout = model_size defaults
  temperature?: number;
  max_tokens?: number;
  timeout_seconds?: number;
}

export interface LlmStats {
  calls: number;
  successes: number;
  failures: number;
  consecutive_failures: number;
  avg_latency_ms: number | null;
  last_error: string | null;
  last_error_at: number | null; // unix seconds
  last_success_at: number | null;
  breaker_open: boolean;
  breaker_seconds_remaining: number;
  breaker_trips: number;
  breaker_threshold: number;
}

export interface LlmScheduleSettings {
  enabled: boolean;
  mode: string; // "quiet_hours" | "trickle"
  quiet_hours_start: number; // UTC hour 0-23
  quiet_hours_end: number; // UTC hour 0-23
  max_items_per_pass: number;
  scan_imports: boolean;
  scan_media: boolean;
}

export interface BackupSettings {
  enabled: boolean;
  interval_hours: number;
  retention_count: number;
}

export interface BackupFile {
  name: string;
  size: number;
  modified: string;
}

export interface CleanupSettings {
  excluded_libraries: string[];
  soft_delete_days: number;
  protect_requested: boolean;
  protect_other_users: boolean;
  other_user_watch_days: number;
  primary_tautulli_user: string;
}

export interface SyncSettings {
  plex_sync_interval_hours: number;
}

export interface NotificationSettings {
  enabled: boolean;
  ntfy_url: string;
  topic: string;
  public_base_url: string;
  actionable_new_suggestions: boolean;
  actionable_max_per_scan: number;
  digest_enabled: boolean;
  digest_weekday: number;
  digest_hour_utc: number;
}

export const settingsApi = {
  getScoring: () => req<ScoringWeights>("/settings/scoring"),
  updateScoring: (w: ScoringWeights) =>
    req<ScoringWeights>("/settings/scoring", { method: "PUT", body: JSON.stringify(w) }),
  getImportMatching: () => req<ImportMatchingSettings>("/settings/import-matching"),
  updateImportMatching: (s: ImportMatchingSettings) =>
    req<ImportMatchingSettings>("/settings/import-matching", { method: "PUT", body: JSON.stringify(s) }),
  getOllama: () => req<OllamaSettings>("/settings/ollama"),
  updateOllama: (s: OllamaSettings) =>
    req<OllamaSettings>("/settings/ollama", { method: "PUT", body: JSON.stringify(s) }),
  getLlmSchedule: () => req<LlmScheduleSettings>("/settings/llm-schedule"),
  updateLlmSchedule: (s: LlmScheduleSettings) =>
    req<LlmScheduleSettings>("/settings/llm-schedule", { method: "PUT", body: JSON.stringify(s) }),
  getBackup: () => req<BackupSettings>("/settings/backup"),
  updateBackup: (s: BackupSettings) =>
    req<BackupSettings>("/settings/backup", { method: "PUT", body: JSON.stringify(s) }),
  runBackupNow: () => req<{ ok: boolean; path: string | null; message: string }>("/settings/backup/run", { method: "POST" }),
  listBackups: () => req<BackupFile[]>("/settings/backup/list"),
  getCleanup: () => req<CleanupSettings>("/settings/cleanup"),
  updateCleanup: (s: CleanupSettings) =>
    req<CleanupSettings>("/settings/cleanup", { method: "PUT", body: JSON.stringify(s) }),
  getSync: () => req<SyncSettings>("/settings/sync"),
  updateSync: (s: SyncSettings) =>
    req<SyncSettings>("/settings/sync", { method: "PUT", body: JSON.stringify(s) }),
  getNotifications: () => req<NotificationSettings>("/settings/notifications"),
  updateNotifications: (s: NotificationSettings) =>
    req<NotificationSettings>("/settings/notifications", { method: "PUT", body: JSON.stringify(s) }),
  testNotification: () => req<{ ok: boolean; message: string }>("/settings/notifications/test", { method: "POST" }),
  refinePrompt: (draft: string, task: "match" | "explain") =>
    req<{ refined: string }>("/settings/ollama/refine-prompt", {
      method: "POST", body: JSON.stringify({ draft, task }),
    }),
  ollamaContextLength: () =>
    req<{ context_length: number | null; model: string | null }>("/settings/ollama/context-length"),
  ollamaPreview: (task: "match" | "explain", useRealData: boolean) =>
    req<{ output: string | null; latency_ms: number; json_valid: boolean | null; message: string }>(
      "/settings/ollama/preview", { method: "POST", body: JSON.stringify({ task, use_real_data: useRealData }) }),
  llmStats: () => req<LlmStats>("/settings/llm/stats"),
  llmBreakerReset: () => req<LlmStats>("/settings/llm/breaker/reset", { method: "POST" }),
};

// --- Failed imports ---
export interface FailedImport {
  id: number;
  source_app: string;
  queue_item_id: string | null;
  download_id: string | null;
  raw_title: string;
  matched_title: string | null;
  matched_id: number | null;
  confidence: number;
  heuristic_confidence: number | null;
  match_rationale: string | null;
  pack: string | null; // season-pack label ("S03", "S01-S03", "complete series")
  llm_confidence: number | null;
  llm_rationale: string | null;
  llm_agrees: boolean | null; // structured agree/disagree signal (llm_rationale is plain prose, no prefix)
  pack_file_matches: string | null; // JSON: per-file episode suggestions from LLM review
  mapping_overrides: string | null; // JSON: user-corrected per-file episode mappings, keyed by raw path
  quality_downgrade: boolean | null; // every file in the download rejects as "not an upgrade"
  suspicious_files: string | null; // JSON list of filenames matching a suspicious extension
  status: string;
  verified: boolean | null;
  message: string | null;
  created_at: string | null;
  updated_at: string | null;
  resolved_at: string | null;
}

export interface ImportStats {
  suggested: number;
  auto_resolved: number;
  accepted: number;
  rejected: number;
  closed_external: number;
  resolve_failed: number;
  orphan_pending: number;
  orphaned: number;
  by_service: Record<string, number>;
  auto_resolved_7d: number;
  auto_eligible_count: number;
}

export interface AutoEligible {
  enabled: boolean;
  threshold: number;
  count: number;
  ids: number[];
}

export interface ImportFileDetail {
  path: string | null;
  raw_path: string | null; // stable identifier (Sonarr's own absolute path) — used for mapping overrides
  size: number;
  quality: string | null;
  mapped_to: string | null;
  detail: string;
  overridden: boolean;
  rejections: string[];
}

export interface ImportTrends {
  days: number;
  labels: string[];
  new: number[];
  resolved: number[];
}

export const importsApi = {
  list: (status?: string) =>
    req<FailedImport[]>(`/imports${status ? `?status=${status}` : ""}`),
  stats: () => req<ImportStats>("/imports/stats"),
  trends: (days = 30) => req<ImportTrends>(`/imports/trends?days=${days}`),
  exportCsv: (status?: string) =>
    downloadCsv(`/imports/export.csv${status ? `?status=${status}` : ""}`),
  autoEligible: () => req<AutoEligible>("/imports/auto-eligible"),
  scan: () => req<Record<string, unknown>>("/imports/scan", { method: "POST" }),
  accept: (id: number) =>
    req<{ id: number; status: string; ok: boolean; message: string }>(`/imports/${id}/accept`, { method: "POST" }),
  reject: (id: number, removeDownload = false) =>
    req<{ id: number; status: string; download_client?: string[] }>(
      `/imports/${id}/reject?remove_download=${removeDownload}`, { method: "POST" }),
  batch: (ids: number[], action: "accept" | "reject" | "confirm_orphan") =>
    req<{ async?: boolean; task_id?: string; total?: number; results: Array<Record<string, unknown>> }>("/imports/batch", {
      method: "POST", body: JSON.stringify({ ids, action }),
    }),
  confirmOrphan: (id: number) =>
    req<{ id: number; status: string }>(`/imports/${id}/confirm-orphan`, { method: "POST" }),
  keep: (id: number) =>
    req<{ id: number; status: string }>(`/imports/${id}/keep`, { method: "POST" }),
  files: (id: number) =>
    req<{ files: ImportFileDetail[]; message: string | null }>(`/imports/${id}/files`),
  candidates: (id: number, query?: string) =>
    req<{ candidates: Array<{ id: number; title: string; score: number }> }>(
      `/imports/${id}/candidates${query ? `?query=${encodeURIComponent(query)}` : ""}`),
  setMatch: (id: number, matchedId: number, matchedTitle: string) =>
    req<{ id: number; matched_id: number; matched_title: string }>(`/imports/${id}/match`, {
      method: "POST", body: JSON.stringify({ matched_id: matchedId, matched_title: matchedTitle }),
    }),
  llmRun: (ids?: number[]) =>
    req<{ started: number; total_eligible: number; queued: boolean; queue_position?: number; message: string }>("/imports/llm-run", {
      method: "POST", body: JSON.stringify(ids?.length ? { ids } : {}),
    }),
  llmReviewPack: (id: number) =>
    req<{ matches: Array<{ file: string; season: number; episode: number; match_type: string; confidence: string; reason: string }>; file_count?: number; message?: string }>(
      `/imports/${id}/llm-review-pack`, { method: "POST" }),
  episodeOptions: (id: number) =>
    req<{ episodes: Array<{ id: number; season: number; episode: number; title: string }>; message?: string }>(
      `/imports/${id}/episode-options`),
  updateFileMapping: (id: number, path: string, episodeId: number, season: number, episode: number, title: string) =>
    req<{ id: number; overrides: Record<string, unknown> }>(`/imports/${id}/file-mapping`, {
      method: "PUT", body: JSON.stringify({ path, episode_id: episodeId, season, episode, title }),
    }),
};

// --- Active processes (tracked background tasks) ---
export interface TaskProgress {
  id: string;
  kind: "llm_run" | "scan" | "plex_sync" | "deletion" | "import_batch";
  label: string;
  status: "running" | "done" | "failed";
  current: number | null;
  total: number | null;
  message: string | null;
  started_at: number;
}

export const tasksApi = {
  list: () => req<TaskProgress[]>("/tasks"),
};

// --- System ---
export interface ScheduleInfo {
  last_scan_at: string | null;
  next_scan_at: string | null; // null = scanning disabled
  last_synced_at: string | null;
  next_sync_at: string | null; // null = manual sync only
}

export const systemApi = {
  health: () => req<{ status: string; db: string }>("/system/health"),
  logs: (lines = 200) => req<{ lines: string[] }>(`/system/logs?lines=${lines}`),
  schedule: () => req<ScheduleInfo>("/system/schedule"),
};

// --- Auth ---
export interface AuthStatus {
  enabled: boolean;
  totp_enabled: boolean;
  authenticated: boolean;
  bypassed: boolean;
  via: "sso" | "lan" | "session" | null; // how the current request was allowed
  lan_bypass: boolean;
  lan_cidrs: string[];
  sso_enabled: boolean;
  sso_allow_lan_without_sso: boolean;
  sso_trusted_proxies: string[] | null; // null unless the caller can manage
  sso_username_header: string | null;
  username: string | null;
}

export interface SsoConfig {
  sso_enabled?: boolean;
  sso_allow_lan_without_sso?: boolean;
  sso_trusted_proxies?: string[];
  sso_username_header?: string;
}

export const authApi = {
  status: () => req<AuthStatus>("/auth/status"),
  login: (username: string, password: string, totp?: string) =>
    req<{ ok: boolean }>("/auth/login", { method: "POST", body: JSON.stringify({ username, password, totp }) }),
  logout: () => req<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  setup: (username: string, password: string) =>
    req<{ ok: boolean; enabled: boolean }>("/auth/setup", { method: "POST", body: JSON.stringify({ username, password }) }),
  disable: (password: string) =>
    req<{ ok: boolean; enabled: boolean }>("/auth/disable", { method: "POST", body: JSON.stringify({ password }) }),
  changePassword: (current: string, next: string) =>
    req<{ ok: boolean }>("/auth/change-password", { method: "POST", body: JSON.stringify({ current, new: next }) }),
  totpSetup: () => req<{ secret: string; otpauth_uri: string }>("/auth/totp/setup", { method: "POST" }),
  totpEnable: (code: string) =>
    req<{ ok: boolean; totp_enabled: boolean }>("/auth/totp/enable", { method: "POST", body: JSON.stringify({ code }) }),
  totpDisable: (password: string) =>
    req<{ ok: boolean; totp_enabled: boolean }>("/auth/totp/disable", { method: "POST", body: JSON.stringify({ password }) }),
  updateConfig: (lan_bypass: boolean, lan_cidrs: string[]) =>
    req<{ ok: boolean }>("/auth/config", { method: "PUT", body: JSON.stringify({ lan_bypass, lan_cidrs }) }),
  updateSso: (body: SsoConfig) =>
    req<{ ok: boolean; sso_enabled: boolean; sso_allow_lan_without_sso: boolean; sso_trusted_proxies: string[]; sso_username_header: string }>(
      "/auth/sso", { method: "PUT", body: JSON.stringify(body) }),
};

// --- Ollama (optional local-LLM assist) ---
export const ollamaApi = {
  models: () => req<{ ok: boolean; models: string[]; message: string }>("/integrations/ollama/models"),
  test: () => req<{ ok: boolean; message: string; version: string | null }>("/integrations/ollama/test", { method: "POST" }),
};

// --- Integrations ---
export interface IntegrationConfig {
  name: string;
  url: string | null;
  api_key: string | null;   // masked on read — never the stored secret
  api_key_set: boolean;     // a secret is stored (drives the "leave blank to keep" placeholder)
  username: string | null;  // user/pass download clients (qBittorrent) — not a secret
  password: string | null;  // masked on read — never the stored secret
  password_set: boolean;
  enabled: boolean;
  remove_from_monitored_on_delete: boolean;
  delete_from_arr_list: boolean;
}

export const integrationsApi = {
  list: () => req<IntegrationConfig[]>("/integrations"),
  update: (name: string, body: Partial<IntegrationConfig>) =>
    req<IntegrationConfig>(`/integrations/${name}`, { method: "PUT", body: JSON.stringify(body) }),
  test: (name: string) => req<{ ok: boolean; message: string; version: string | null }>(`/integrations/${name}/test`, { method: "POST" }),
  syncPlex: () => req<{ synced: number; protected?: number }>("/integrations/plex/sync", { method: "POST" }),
  syncSeerr: () => req<{ protected: number }>("/integrations/seerr/sync", { method: "POST" }),
};

// --- Formatters ---
export function fmtBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
}

export function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString();
}
