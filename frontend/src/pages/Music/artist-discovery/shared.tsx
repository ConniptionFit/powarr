import { Compass, Zap, Network, Radio, Flame } from "lucide-react";
import { req, parseApiDate } from "../../../lib/api";

export interface Candidate {
  id: number;
  musicbrainz_id: string | null;
  artist_name: string;
  genres: string[];
  mood_tags: string[];
  era: string | null;
  source: string; // "direct" | "centroid" | "centroid_recent" | "graph" | "ingest" | "centroid_mood_{slug}" (AD-19)
  similarity_score: number | null;
  associated_seed_mbids: string[];
  seed_artist_name: string | null;
  seed_artist_names: string[];
  status: string;
  lidarr_artist_id: number | null;
  created_at: string | null;
  image_url: string | null;
  bio: string | null;
  years_active: string | null;
  connection_count?: number;
  all_time_connections?: number;
  recent_connections?: number;
  auto_add_progress?: number;
  is_low_metadata?: boolean;
}

// Sort options for the pending-candidates queue
export type CandidateSort = "match" | "newest" | "name" | "connections";

export function connectionCount(c: Candidate): number {
  return c.connection_count ?? Math.max(c.associated_seed_mbids.length, c.seed_artist_names.length);
}

/**
 * ARCHITECTURAL SPECIFICATION: CANDIDATE SORTING BY "BEST MATCH"
 * 
 * "Best Match" sorting MUST always order candidates by how close they are to satisfying
 * the active auto-add criteria, NOT by arbitrary all-time connection counts or legacy match criteria.
 * 
 * Auto-add proximity formula:
 *   recent_ratio = recent_connections / auto_add_recent_threshold (if auto_add_recent_threshold > 0 else 0)
 *   all_time_ratio = all_time_connections / auto_add_all_time_threshold (if auto_add_all_time_threshold > 0 else 0)
 *   auto_add_progress = max(recent_ratio, all_time_ratio)
 * 
 * The server computes `auto_add_progress` and sorts by it.
 * If client-side re-sorting is triggered (e.g. after filtering or re-selecting options), client-side "match" sorting
 * respects `auto_add_progress` descending, then similarity_score descending, then recent_connections descending,
 * then all_time_connections descending.
 * 
 * ANY FUTURE CHANGES TO ARTIST DISCOVERY SORTING MUST MAINTAIN THIS AUTO-ADD PROXIMITY PRIORITY.
 */
export function sortCandidates(list: Candidate[], sortBy: CandidateSort): Candidate[] {
  if (sortBy === "match") {
    const sorted = [...list];
    sorted.sort((a, b) => {
      // 1. auto_add_progress descending (proximity to active auto-add threshold)
      const progDiff = (b.auto_add_progress ?? 0) - (a.auto_add_progress ?? 0);
      if (Math.abs(progDiff) > 1e-6) return progDiff;

      // 2. similarity_score descending (cosine similarity from centroid / vector search)
      const simDiff = (b.similarity_score ?? 0) - (a.similarity_score ?? 0);
      if (Math.abs(simDiff) > 1e-6) return simDiff;

      // 3. recent connections descending
      const recDiff = (b.recent_connections ?? 0) - (a.recent_connections ?? 0);
      if (recDiff !== 0) return recDiff;

      // 4. all-time connections descending
      return (b.all_time_connections ?? connectionCount(b)) - (a.all_time_connections ?? connectionCount(a));
    });
    return sorted;
  }
  const sorted = [...list];
  if (sortBy === "name") {
    sorted.sort((a, b) => a.artist_name.localeCompare(b.artist_name));
  } else if (sortBy === "newest") {
    sorted.sort((a, b) => (parseApiDate(b.created_at ?? "").getTime() || 0) - (parseApiDate(a.created_at ?? "").getTime() || 0));
  } else if (sortBy === "connections") {
    sorted.sort((a, b) => connectionCount(b) - connectionCount(a));
  }
  return sorted;
}

export interface DiscoveryRun {
  id: number;
  run_type: string;
  started_at: string | null;
  finished_at: string | null;
  candidates_found: number;
  candidates_added: number;
  candidates_skipped?: number;
  trigger?: string | null;
  preset?: string | null;
  message: string | null;
}

export interface Decision {
  id?: number;
  run_id?: number | null;
  created_at?: string | null;
  artist_name: string;
  musicbrainz_id: string | null;
  action: "added" | "queued" | "skipped" | "failed" | "would_add" | "would_queue";
  reason: string;
  score: number | null;
  connections: number | null;
  seeds: string[];
  candidate_id: number | null;
  // v0.101.0 ranking detail (present on direct-lane decisions)
  bucket?: Bucket | null;
  tags?: string[];
  listeners?: number | null;
  sources?: string[];
  reason_line?: string;
  breakdown?: Breakdown;
}

export type Bucket = "close" | "branching" | "wildcard";
export type Breakdown = Record<string, { value: number | null; weight: number; points: number }>;

export interface DiscoverCardData {
  id: number;
  artist_name: string;
  musicbrainz_id: string | null;
  image_url: string | null;
  bucket: Bucket | null;
  rank_score: number;
  match: number; // 0..100, relative to the best current candidate
  reason: string;
  blurb: string | null;
  tags: string[];
  seeds: string[];
  listeners: number | null;
  latest_release: { title: string; date: string } | null;
  sources: string[];
  years_active: string | null;
  breakdown: Breakdown;
}

export interface Shelf {
  id: string;
  title: string;
  subtitle: string;
  items: DiscoverCardData[];
}

export interface ShelvesResponse {
  adventure: number;
  total_pending: number;
  shelves: Shelf[];
}

export interface ListeningSource {
  source: string;
  label: string;
  enabled: boolean;
  configured: boolean;
  events: number;
  last_play: string | null;
  last_sync: string | null;
}

export interface ListeningStatus {
  enabled: boolean;
  sources: ListeningSource[];
  plays_in_log: number;
  users: string[];
  counted_users: string[];
}

export interface DedupePreview {
  days: number;
  raw_reports: number;
  real_plays: number;
  duplicates_merged: number;
  seen_by_multiple_sources: number;
  per_source: Record<string, number>;
  examples: { artist: string; track: string; played_at: string; sources: string[]; gap_seconds: number }[];
}

export interface TasteSummary {
  period: string;
  log_events: number;
  top_artists: { name: string; plays: number; excluded: boolean }[];
  top_tags: { tag: string; weight: number }[];
  excluded_artists: string[];
  excluded_tags: string[];
  users: string[];
  counted_users: string[];
}

export interface ScoreRow {
  key: string;
  suggested: number;
  added: number;
  rejected: number;
  hits: number;
  measurable: number;
  hit_rate: number | null;
}

export interface Scorecard {
  days: number;
  window_days: number;
  totals: { suggested: number; added: number; rejected: number; pending: number; hits: number; measurable: number };
  hit_rate: number | null;
  by_bucket: ScoreRow[];
  by_source: ScoreRow[];
  insights: string[];
}

export interface SamplerTrack {
  id: number;
  artist_name: string;
  image_url: string | null;
  bucket: Bucket | null;
  title: string;
  preview: string;
  album_cover: string | null;
}

export interface GenreResult {
  ok: boolean;
  message: string;
  tag?: string;
  results: { artist_name: string; musicbrainz_id: string | null; rank: number; already_triaged: boolean }[];
}

export type FeedbackKind = "more_like_this" | "not_for_me" | "known";

export interface PreviewResult {
  ok: boolean;
  dry_run: boolean;
  preset: string;
  message: string;
  blockers: string[];
  warnings: string[];
  seeds: number;
  candidates_scored: number;
  queued: number;
  added: number;
  failed: number;
  skipped: number;
  skip_counts: Record<string, number>;
  decisions: Decision[];
}

export interface StatusCheck {
  key: string;
  status: "ok" | "warn" | "error";
  label: string;
  detail: string;
}

export interface DiscoveryStatus {
  overall: "ok" | "warn" | "error";
  preset: string;
  checks: StatusCheck[];
  effective: Record<string, string | number | boolean>;
}

export interface RecentAdd {
  id: number;
  artist_name: string;
  musicbrainz_id: string | null;
  source: string; // "discovery" | "related"
  lidarr_artist_id: number | null;
  added_at: string | null;
  discovery_source: string | null;
  similarity_score: number | null;
  seed_artist_name: string | null;
  seed_artist_names: string[];
  associated_seed_mbids: string[];
  genres: string[];
  years_active: string | null;
  image_url: string | null;
  bio: string | null;
}

export interface Stats {
  pending: number;
  accepted: number;
  rejected: number;
  tracked_artists: number | null;
  last_run_at: string | null;
  last_run_message: string | null;
}

export const api = {
  settings: () => req<{ enabled: boolean }>("/artist-discovery/settings"),
  stats: () => req<Stats>("/artist-discovery/stats"),
  candidates: (status = "pending") =>
    req<Candidate[]>(`/artist-discovery/candidates?status=${status}`),
  run: () => req<{ ok: boolean; message: string }>("/artist-discovery/run", { method: "POST" }),
  status: (deep = false) => req<DiscoveryStatus>(`/artist-discovery/status${deep ? "?deep=true" : ""}`),
  preview: () => req<PreviewResult>("/artist-discovery/preview", { method: "POST" }),
  decisions: (runId: number) => req<Decision[]>(`/artist-discovery/decisions?run_id=${runId}&limit=200`),
  runs: () => req<DiscoveryRun[]>("/artist-discovery/runs?limit=10"),
  recentAdds: (limit: number) => req<RecentAdd[]>(`/artist-discovery/recent-adds?limit=${limit}`),
  accept: (id: number) =>
    req<{ ok: boolean; message: string }>(`/artist-discovery/candidates/${id}/accept`, { method: "POST" }),
  reject: (id: number) =>
    req<{ ok: boolean; message: string }>(`/artist-discovery/candidates/${id}/reject`, { method: "POST" }),
  unreject: (id: number) =>
    req<{ ok: boolean; message: string }>(`/artist-discovery/candidates/${id}/unreject`, { method: "POST" }),
  shelves: (adventure?: number) =>
    req<ShelvesResponse>(`/artist-discovery/shelves${adventure === undefined ? "" : `?adventure=${adventure}`}`),
  taste: () => req<TasteSummary>("/artist-discovery/taste"),
  scorecard: () => req<Scorecard>("/artist-discovery/scorecard"),
  sampler: (limit = 10) => req<SamplerTrack[]>(`/artist-discovery/sampler?limit=${limit}`),
  genreExplore: (tag: string, offset = 0) =>
    req<GenreResult>(`/artist-discovery/genre-explore?tag=${encodeURIComponent(tag)}&offset=${offset}`),
  feedback: (id: number, kind: FeedbackKind, reason?: string) =>
    req<{ ok: boolean; message: string }>(`/artist-discovery/candidates/${id}/feedback`, {
      method: "POST", body: JSON.stringify({ kind, reason }),
    }),
  blurb: (id: number) =>
    req<{ ok: boolean; blurb?: string; message?: string }>(`/artist-discovery/candidates/${id}/blurb`, { method: "POST" }),
  listeningStatus: () => req<ListeningStatus>("/artist-discovery/listening/status"),
  listeningSync: () =>
    req<{ ok: boolean; message: string; new: number; merged: number }>("/artist-discovery/listening/sync", { method: "POST" }),
  listeningPreview: (days: number) => req<DedupePreview>(`/artist-discovery/listening/preview?days=${days}`),
  relatedAdd: (artist_name: string, mbid?: string | null) =>
    req<{ ok: boolean; message: string }>("/artist-discovery/related/add", {
      method: "POST", body: JSON.stringify({ artist_name, mbid }),
    }),
  batch: (ids: number[], action: "accept" | "reject") =>
    req<{ results: any[] }>("/artist-discovery/batch", {
      method: "POST",
      body: JSON.stringify({ ids, action }),
    }),
};

export function seedConnectionSuffix(c: Candidate | Pick<Candidate, "seed_artist_name" | "seed_artist_names" | "associated_seed_mbids">): string {
  const names = c.seed_artist_names.length > 0
    ? c.seed_artist_names
    : c.seed_artist_name ? [c.seed_artist_name] : [];
  const conn = (c as Candidate).all_time_connections ?? Math.max(c.associated_seed_mbids.length, names.length);
  const recent = (c as Candidate).recent_connections;
  if (conn === 0) return "";
  const matchStr = recent !== undefined ? `${recent} recent · ${conn} all-time` : `${conn} connection${conn === 1 ? "" : "s"}`;
  if (names.length === 0) {
    return ` — similar to artists in your library (${matchStr})`;
  }
  const listed = names.slice(0, 4).join(", ") + (names.length > 4 ? ` +${names.length - 4} more` : "");
  return ` — similar to ${listed} (${matchStr})`;
}

export function whySuggested(c: Candidate | Pick<Candidate, "source" | "similarity_score" | "seed_artist_name" | "seed_artist_names" | "associated_seed_mbids">): string {
  if (c.source === "centroid" || c.source === "centroid_recent" || c.source.startsWith("centroid_mood_")) {
    const pct = c.similarity_score != null ? `${Math.round(c.similarity_score * 100)}% match to` : "Close match to";
    let profile = "your overall taste profile, built from your most-played artists";
    if (c.source === "centroid_recent") {
      profile = "your recent listening";
    } else if (c.source.startsWith("centroid_mood_")) {
      const mood = c.source.slice("centroid_mood_".length).replace(/_/g, " ");
      profile = `artists tagged "${mood}" in your library`;
    }
    return `${pct} ${profile}${seedConnectionSuffix(c)}`;
  }
  if (c.source === "direct") {
    const seeds = c.seed_artist_names.length > 0 ? c.seed_artist_names : c.seed_artist_name ? [c.seed_artist_name] : [];
    const listed = seeds.slice(0, 4).join(", ") + (seeds.length > 4 ? ` +${seeds.length - 4} more` : "");
    return `Last.fm lists it as similar to ${listed || "artists you play"}`
      + (c.similarity_score != null ? ` (best link ${Math.round(c.similarity_score * 100)}%)` : "");
  }
  const names = c.seed_artist_names.length > 0
    ? c.seed_artist_names
    : c.seed_artist_name ? [c.seed_artist_name] : [];
  const conn = (c as Candidate).all_time_connections ?? Math.max(c.associated_seed_mbids.length, names.length, 1);
  const recent = (c as Candidate).recent_connections;
  const matchStr = recent !== undefined ? `${recent} recent · ${conn} all-time` : `${conn} connection${conn === 1 ? "" : "s"}`;
  if (names.length === 0) {
    return `Similar to artists in your library (${matchStr})`;
  }
  const listed = names.slice(0, 4).join(", ") + (names.length > 4 ? ` +${names.length - 4} more` : "");
  return `Similar to ${listed} (${matchStr})`;
}

export function getLaneBadge(c: Candidate) {
  if (c.source === "centroid") {
    return {
      label: "Taste Centroid",
      className: "bg-purple-950/70 text-purple-300 border border-purple-700/50",
      icon: <Compass size={11} className="text-purple-400" />,
    };
  }
  if (c.source === "centroid_recent") {
    return {
      label: "Recent Taste",
      className: "bg-amber-950/70 text-amber-300 border border-amber-700/50",
      icon: <Zap size={11} className="text-amber-400" />,
    };
  }
  if (c.source.startsWith("centroid_mood_")) {
    const mood = c.source.slice("centroid_mood_".length).replace(/_/g, " ");
    return {
      label: `Mood: ${mood}`,
      className: "bg-rose-950/70 text-rose-300 border border-rose-700/50",
      icon: <Flame size={11} className="text-rose-400" />,
    };
  }
  if (c.source === "graph") {
    return {
      label: "Library Graph",
      className: "bg-cyan-950/70 text-cyan-300 border border-cyan-700/50",
      icon: <Network size={11} className="text-cyan-400" />,
    };
  }
  if (c.source === "ingest") {
    return {
      label: "Scrobble Ingest",
      className: "bg-emerald-950/70 text-emerald-300 border border-emerald-700/50",
      icon: <Radio size={11} className="text-emerald-400" />,
    };
  }
  return {
    label: c.source,
    className: "bg-slate-800 text-slate-300 border border-slate-700",
  };
}

export function getScorePill(c: Candidate) {
  if (c.auto_add_progress !== undefined && c.auto_add_progress > 0) {
    const pct = Math.round(c.auto_add_progress * 100);
    const color =
      pct >= 80
        ? "bg-emerald-950/80 text-emerald-300 border border-emerald-700/50"
        : "bg-purple-950/80 text-purple-300 border border-purple-700/50";
    const allTime = c.all_time_connections ?? connectionCount(c);
    const recent = c.recent_connections ?? 0;
    return {
      label: `${pct}% Match (${recent} Recent · ${allTime} All-Time)`,
      className: color,
    };
  }
  if (c.similarity_score != null) {
    const pct = Math.round(c.similarity_score * 100);
    const color =
      pct >= 80
        ? "bg-emerald-950/80 text-emerald-300 border border-emerald-700/50"
        : "bg-purple-950/80 text-purple-300 border border-purple-700/50";
    return {
      label: `${pct}% Match`,
      className: color,
    };
  }
  const allTime = c.all_time_connections ?? connectionCount(c);
  const recent = c.recent_connections ?? 0;
  const color =
    recent > 0
      ? "bg-emerald-950/80 text-emerald-300 border border-emerald-700/50"
      : "bg-slate-800/80 text-slate-300 border border-slate-700/50";
  return {
    label: `${recent} Recent · ${allTime} All-Time`,
    className: color,
  };
}
