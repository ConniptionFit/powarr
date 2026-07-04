const BASE = "/api/v1";

async function req<T>(path: string, opts?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
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
  explain: (id: number) =>
    req<{ rationale: string | null; message: string | null }>(`/media/${id}/explain`, { method: "POST" }),
  deletionLog: () => req<DeletionLogEntry[]>("/media/deletion-log"),
  deletionStats: () => req<DeletionStats>("/media/deletion-stats"),
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
}

export interface OllamaSettings {
  enabled: boolean;
  host: string;
  model: string;
  api_style: string;
}

export interface CleanupSettings {
  excluded_libraries: string[];
  soft_delete_days: number;
  protect_requested: boolean;
}

export interface SyncSettings {
  plex_sync_interval_hours: number;
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
  getCleanup: () => req<CleanupSettings>("/settings/cleanup"),
  updateCleanup: (s: CleanupSettings) =>
    req<CleanupSettings>("/settings/cleanup", { method: "PUT", body: JSON.stringify(s) }),
  getSync: () => req<SyncSettings>("/settings/sync"),
  updateSync: (s: SyncSettings) =>
    req<SyncSettings>("/settings/sync", { method: "PUT", body: JSON.stringify(s) }),
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
  llm_confidence: number | null;
  llm_rationale: string | null;
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
  by_service: Record<string, number>;
  auto_resolved_7d: number;
}

export interface ImportFileDetail {
  path: string | null;
  size: number;
  quality: string | null;
  mapped_to: string | null;
  detail: string;
  rejections: string[];
}

export const importsApi = {
  list: (status?: string) =>
    req<FailedImport[]>(`/imports${status ? `?status=${status}` : ""}`),
  stats: () => req<ImportStats>("/imports/stats"),
  scan: () => req<Record<string, unknown>>("/imports/scan", { method: "POST" }),
  accept: (id: number) =>
    req<{ id: number; status: string; ok: boolean; message: string }>(`/imports/${id}/accept`, { method: "POST" }),
  reject: (id: number, removeDownload = false) =>
    req<{ id: number; status: string; download_client?: string[] }>(
      `/imports/${id}/reject?remove_download=${removeDownload}`, { method: "POST" }),
  batch: (ids: number[], action: "accept" | "reject") =>
    req<{ results: Array<Record<string, unknown>> }>("/imports/batch", {
      method: "POST", body: JSON.stringify({ ids, action }),
    }),
  files: (id: number) =>
    req<{ files: ImportFileDetail[]; message: string | null }>(`/imports/${id}/files`),
};

// --- System ---
export const systemApi = {
  health: () => req<{ status: string; db: string }>("/system/health"),
  logs: (lines = 200) => req<{ lines: string[] }>(`/system/logs?lines=${lines}`),
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
  api_key: string | null;
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
