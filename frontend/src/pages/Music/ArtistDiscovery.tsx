import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Compass, Play, RefreshCw, Check, X, Clock, Settings, Sparkles } from "lucide-react";
import { req } from "../../lib/api";

interface Candidate {
  id: number;
  musicbrainz_id: string | null;
  artist_name: string;
  genres: string[];
  mood_tags: string[];
  era: string | null;
  source: "centroid" | "graph";
  similarity_score: number | null;
  associated_seed_mbids: string[];
  seed_artist_name: string | null;
  status: string;
  lidarr_artist_id: number | null;
}

interface ADSettings {
  enabled: boolean;
  qdrant_url: string;
  qdrant_api_key: string;
  qdrant_api_key_set: boolean;
  collection: string;
  ollama_host: string;
  embed_model: string;
  max_candidates_per_run: number;
  related_artists_limit: number;
  auto_add_connection_threshold: number;
  related_artists_refresh_days: number;
  similarity_threshold: number;
  scrobble_lookback_days: number;
  auto_promote: boolean;
  root_folder_path: string;
  quality_profile_id: number;
  metadata_profile_id: number;
  schedule_enabled: boolean;
  schedule_interval_hours: number;
  sync_schedule_enabled: boolean;
  sync_interval_hours: number;
}

interface Stats {
  pending: number;
  accepted: number;
  rejected: number;
  tracked_artists: number | null;
  last_run_at: string | null;
  last_run_message: string | null;
}

interface LidarrProfiles {
  root_folders: { path: string }[];
  quality_profiles: { id: number; name: string }[];
  metadata_profiles: { id: number; name: string }[];
}

const api = {
  settings: () => req<ADSettings>("/artist-discovery/settings"),
  saveSettings: (s: Partial<ADSettings>) =>
    req<ADSettings>("/artist-discovery/settings", { method: "PUT", body: JSON.stringify(s) }),
  stats: () => req<Stats>("/artist-discovery/stats"),
  candidates: (status = "pending") =>
    req<Candidate[]>(`/artist-discovery/candidates?status=${status}`),
  run: () => req<{ ok: boolean; message: string }>("/artist-discovery/run", { method: "POST" }),
  sync: () => req<{ ok: boolean; message: string }>("/artist-discovery/sync", { method: "POST" }),
  accept: (id: number) =>
    req<{ ok: boolean; message: string }>(`/artist-discovery/candidates/${id}/accept`, { method: "POST" }),
  reject: (id: number) =>
    req<{ ok: boolean; message: string }>(`/artist-discovery/candidates/${id}/reject`, { method: "POST" }),
  profiles: () => req<LidarrProfiles>("/artist-discovery/lidarr/profiles"),
};

function formatDate(isoDate: string | null | undefined): string {
  if (!isoDate) return "Never";
  const date = new Date(isoDate);
  const diffMs = Date.now() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  if (diffMins < 1) return "Just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  return `${Math.floor(diffHours / 24)}d ago`;
}

export default function ArtistDiscovery() {
  const qc = useQueryClient();
  const { data: settings } = useQuery({ queryKey: ["ad-settings"], queryFn: api.settings });
  const { data: stats } = useQuery({ queryKey: ["ad-stats"], queryFn: api.stats });
  const { data: candidates = [] } = useQuery({ queryKey: ["ad-candidates"], queryFn: () => api.candidates("pending") });
  const { data: profiles } = useQuery({ queryKey: ["ad-profiles"], queryFn: api.profiles });
  const [draft, setDraft] = useState<Partial<ADSettings> | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const form = draft ?? settings ?? null;

  const saveMut = useMutation({
    mutationFn: () => api.saveSettings(form || {}),
    onSuccess: () => { setDraft(null); setMsg("Settings saved"); qc.invalidateQueries({ queryKey: ["ad-settings"] }); },
    onError: (e: Error) => setMsg(e.message),
  });
  const runMut = useMutation({
    mutationFn: api.run,
    onSuccess: (r) => {
      setMsg(r.message);
      qc.invalidateQueries({ queryKey: ["ad-candidates"] });
      qc.invalidateQueries({ queryKey: ["ad-stats"] });
    },
    onError: (e: Error) => setMsg(e.message),
  });
  const syncMut = useMutation({
    mutationFn: api.sync,
    onSuccess: (r) => { setMsg(r.message); qc.invalidateQueries({ queryKey: ["ad-stats"] }); },
    onError: (e: Error) => setMsg(e.message),
  });
  const actMut = useMutation({
    mutationFn: ({ id, action }: { id: number; action: "accept" | "reject" }) =>
      action === "accept" ? api.accept(id) : api.reject(id),
    onSuccess: (r) => {
      setMsg(r.message);
      qc.invalidateQueries({ queryKey: ["ad-candidates"] });
      qc.invalidateQueries({ queryKey: ["ad-stats"] });
    },
    onError: (e: Error) => setMsg(e.message),
  });

  const set = <K extends keyof ADSettings>(k: K, v: ADSettings[K]) =>
    setDraft(prev => ({ ...(prev ?? settings ?? {}), [k]: v }));

  return (
    <div className="p-4 sm:p-8 max-w-6xl">
      <div className="flex items-center gap-3 mb-6">
        <Compass className="text-brand-light" size={22} />
        <div>
          <h1 className="text-2xl font-bold text-white">Artist Discovery</h1>
          <p className="text-slate-400 text-sm">Last.fm taste mapping → Qdrant similarity + related-artist graph → Lidarr</p>
        </div>
      </div>

      {msg && <p className="mb-4 text-sm text-slate-300">{msg}</p>}

      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
          <div className="bg-surface-raised border border-purple-900/30 rounded-lg p-3">
            <p className="text-xs text-slate-500">Tracked artists</p>
            <p className="text-white text-lg font-semibold">{stats.tracked_artists ?? "—"}</p>
          </div>
          <div className="bg-surface-raised border border-purple-900/30 rounded-lg p-3">
            <p className="text-xs text-slate-500">Pending review</p>
            <p className="text-white text-lg font-semibold">{stats.pending}</p>
          </div>
          <div className="bg-surface-raised border border-purple-900/30 rounded-lg p-3">
            <p className="text-xs text-slate-500">Added to Lidarr</p>
            <p className="text-white text-lg font-semibold">{stats.accepted}</p>
          </div>
          <div className="bg-surface-raised border border-purple-900/30 rounded-lg p-3">
            <p className="text-xs text-slate-500">Last run</p>
            <p className="text-white text-sm font-medium">{formatDate(stats.last_run_at)}</p>
          </div>
        </div>
      )}

      {form && (
        <section className="bg-surface-raised border border-purple-900/30 rounded-xl p-5 mb-6 space-y-4">
          <h2 className="text-white font-semibold text-sm uppercase tracking-wider flex items-center gap-2">
            <Settings size={16} /> Configuration
          </h2>

          <label className="flex items-center gap-2 text-sm text-slate-300">
            <input type="checkbox" checked={!!form.enabled} onChange={e => set("enabled", e.target.checked)} />
            Enabled
          </label>

          <div className="grid sm:grid-cols-2 gap-3">
            <label className="text-xs text-slate-400 block">
              Qdrant URL
              <input className="mt-1 w-full bg-surface border border-purple-900/40 rounded px-3 py-2 text-sm text-white"
                value={form.qdrant_url || ""} onChange={e => set("qdrant_url", e.target.value)} />
            </label>
            <label className="text-xs text-slate-400 block">
              Collection
              <input className="mt-1 w-full bg-surface border border-purple-900/40 rounded px-3 py-2 text-sm text-white"
                value={form.collection || ""} onChange={e => set("collection", e.target.value)} />
            </label>
            <label className="text-xs text-slate-400 block sm:col-span-2">
              Qdrant API key {settings?.qdrant_api_key_set ? "(saved — leave blank to keep)" : ""}
              <input type="password" className="mt-1 w-full bg-surface border border-purple-900/40 rounded px-3 py-2 text-sm text-white"
                value={form.qdrant_api_key || ""} onChange={e => set("qdrant_api_key", e.target.value)}
                placeholder={settings?.qdrant_api_key_set ? "••••••••" : ""} />
            </label>
            <label className="text-xs text-slate-400 block">
              Ollama host <span className="text-slate-600">(blank = reuse LLM Assist host)</span>
              <input className="mt-1 w-full bg-surface border border-purple-900/40 rounded px-3 py-2 text-sm text-white"
                value={form.ollama_host || ""} onChange={e => set("ollama_host", e.target.value)} />
            </label>
            <label className="text-xs text-slate-400 block">
              Embedding model
              <input className="mt-1 w-full bg-surface border border-purple-900/40 rounded px-3 py-2 text-sm text-white"
                value={form.embed_model || ""} onChange={e => set("embed_model", e.target.value)} />
            </label>
          </div>

          <div className="border-t border-purple-900/30 pt-4 grid sm:grid-cols-3 gap-3">
            <label className="text-xs text-slate-400 block">
              Similarity threshold
              <input type="number" step="0.01" min="0" max="1" className="mt-1 w-full bg-surface border border-purple-900/40 rounded px-3 py-2 text-sm text-white"
                value={form.similarity_threshold ?? 0.75} onChange={e => set("similarity_threshold", parseFloat(e.target.value))} />
            </label>
            <label className="text-xs text-slate-400 block">
              Max candidates per run
              <input type="number" className="mt-1 w-full bg-surface border border-purple-900/40 rounded px-3 py-2 text-sm text-white"
                value={form.max_candidates_per_run ?? 5} onChange={e => set("max_candidates_per_run", parseInt(e.target.value))} />
            </label>
            <label className="text-xs text-slate-400 block">
              Related artists per seed
              <input type="number" className="mt-1 w-full bg-surface border border-purple-900/40 rounded px-3 py-2 text-sm text-white"
                value={form.related_artists_limit ?? 3} onChange={e => set("related_artists_limit", parseInt(e.target.value))} />
            </label>
            <label className="text-xs text-slate-400 block">
              Connection threshold (graph)
              <input type="number" className="mt-1 w-full bg-surface border border-purple-900/40 rounded px-3 py-2 text-sm text-white"
                value={form.auto_add_connection_threshold ?? 3} onChange={e => set("auto_add_connection_threshold", parseInt(e.target.value))} />
            </label>
            <label className="text-xs text-slate-400 block">
              Seed re-scan interval (days)
              <input type="number" className="mt-1 w-full bg-surface border border-purple-900/40 rounded px-3 py-2 text-sm text-white"
                value={form.related_artists_refresh_days ?? 30} onChange={e => set("related_artists_refresh_days", parseInt(e.target.value))} />
            </label>
            <label className="text-xs text-slate-400 block">
              Scrobble lookback (days)
              <input type="number" className="mt-1 w-full bg-surface border border-purple-900/40 rounded px-3 py-2 text-sm text-white"
                value={form.scrobble_lookback_days ?? 30} onChange={e => set("scrobble_lookback_days", parseInt(e.target.value))} />
            </label>
          </div>

          <div className="border-t border-purple-900/30 pt-4 grid sm:grid-cols-3 gap-3">
            <label className="text-xs text-slate-400 block">
              Root folder
              <select className="mt-1 w-full bg-surface border border-purple-900/40 rounded px-3 py-2 text-sm text-white"
                value={form.root_folder_path || ""} onChange={e => set("root_folder_path", e.target.value)}>
                <option value="">First available</option>
                {profiles?.root_folders.map(f => <option key={f.path} value={f.path}>{f.path}</option>)}
              </select>
            </label>
            <label className="text-xs text-slate-400 block">
              Quality profile
              <select className="mt-1 w-full bg-surface border border-purple-900/40 rounded px-3 py-2 text-sm text-white"
                value={form.quality_profile_id || 0} onChange={e => set("quality_profile_id", parseInt(e.target.value))}>
                <option value={0}>First available</option>
                {profiles?.quality_profiles.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </label>
            <label className="text-xs text-slate-400 block">
              Metadata profile
              <select className="mt-1 w-full bg-surface border border-purple-900/40 rounded px-3 py-2 text-sm text-white"
                value={form.metadata_profile_id || 0} onChange={e => set("metadata_profile_id", parseInt(e.target.value))}>
                <option value={0}>First available</option>
                {profiles?.metadata_profiles.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </label>
          </div>

          <div className="border-t border-purple-900/30 pt-4 space-y-3">
            <label className="flex items-start gap-2 text-sm text-slate-300">
              <input type="checkbox" className="mt-0.5" checked={!!form.auto_promote} onChange={e => set("auto_promote", e.target.checked)} />
              <span>
                Auto-promote graph candidates to Lidarr
                <span className="block text-xs text-slate-500">
                  Off by default. When on, related-artist candidates that cross the connection
                  threshold skip the review queue and are added to Lidarr automatically. Centroid
                  candidates always land in the review queue regardless of this setting.
                </span>
              </span>
            </label>
          </div>

          <div className="border-t border-purple-900/30 pt-4 space-y-3">
            <h3 className="text-slate-300 font-semibold text-xs uppercase tracking-wider flex items-center gap-2">
              <Clock size={14} /> Scheduling
            </h3>
            <div className="grid sm:grid-cols-2 gap-4">
              <div className="space-y-2">
                <label className="flex items-center gap-2 text-sm text-slate-300">
                  <input type="checkbox" checked={!!form.schedule_enabled} onChange={e => set("schedule_enabled", e.target.checked)} />
                  Discovery cycle (ingest + centroid + graph)
                </label>
                {form.schedule_enabled && (
                  <label className="text-xs text-slate-400 block">
                    Interval (hours)
                    <input type="number" className="mt-1 w-full bg-surface border border-purple-900/40 rounded px-3 py-2 text-sm text-white"
                      value={form.schedule_interval_hours ?? 24} onChange={e => set("schedule_interval_hours", parseInt(e.target.value))} />
                  </label>
                )}
              </div>
              <div className="space-y-2">
                <label className="flex items-center gap-2 text-sm text-slate-300">
                  <input type="checkbox" checked={!!form.sync_schedule_enabled} onChange={e => set("sync_schedule_enabled", e.target.checked)} />
                  Differential sync (Lidarr/Last.fm → Qdrant)
                </label>
                {form.sync_schedule_enabled && (
                  <label className="text-xs text-slate-400 block">
                    Interval (hours)
                    <input type="number" className="mt-1 w-full bg-surface border border-purple-900/40 rounded px-3 py-2 text-sm text-white"
                      value={form.sync_interval_hours ?? 1} onChange={e => set("sync_interval_hours", parseInt(e.target.value))} />
                  </label>
                )}
              </div>
            </div>
          </div>

          <div className="flex flex-wrap gap-2 pt-2">
            <button onClick={() => saveMut.mutate()} disabled={saveMut.isPending}
              className="px-3 py-2 rounded-lg bg-brand/30 text-brand-light text-sm hover:bg-brand/40 disabled:opacity-50">
              Save Settings
            </button>
            <button onClick={() => runMut.mutate()} disabled={runMut.isPending || !form.enabled}
              className="flex items-center gap-2 px-3 py-2 rounded-lg bg-surface border border-purple-900/40 text-slate-300 text-sm hover:text-white disabled:opacity-50">
              <Play size={14} /> {runMut.isPending ? "Running…" : "Run Discovery Now"}
            </button>
            <button onClick={() => syncMut.mutate()} disabled={syncMut.isPending || !form.enabled}
              className="flex items-center gap-2 px-3 py-2 rounded-lg bg-surface border border-purple-900/40 text-slate-300 text-sm hover:text-white disabled:opacity-50">
              <RefreshCw size={14} /> {syncMut.isPending ? "Syncing…" : "Sync Now"}
            </button>
          </div>
        </section>
      )}

      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-white font-semibold text-sm uppercase tracking-wider flex items-center gap-2">
            <Sparkles size={16} /> Pending candidates ({candidates.length})
          </h2>
          <button onClick={() => qc.invalidateQueries({ queryKey: ["ad-candidates"] })}
            className="p-1.5 text-slate-400 hover:text-white" title="Refresh"><RefreshCw size={14} /></button>
        </div>
        {candidates.length === 0 ? (
          <p className="text-slate-500 text-sm">No pending candidates — configure Last.fm/Qdrant/Lidarr and Run Discovery.</p>
        ) : (
          <div className="grid gap-2">
            {candidates.map(c => (
              <div key={c.id} className="bg-surface-raised border border-purple-900/30 rounded-lg p-4">
                <div className="flex items-start justify-between">
                  <div>
                    <p className="text-white text-sm font-medium">{c.artist_name}</p>
                    <div className="flex flex-wrap gap-1 mt-1">
                      {c.genres.slice(0, 5).map(g => (
                        <span key={g} className="text-xs bg-purple-900/40 text-purple-200 px-2 py-0.5 rounded">{g}</span>
                      ))}
                      {c.era && <span className="text-xs bg-surface text-slate-400 px-2 py-0.5 rounded border border-purple-900/40">{c.era}</span>}
                    </div>
                    <p className="text-xs text-slate-500 mt-1.5">
                      {c.source === "centroid"
                        ? `Taste match — ${c.similarity_score != null ? Math.round(c.similarity_score * 100) : "?"}% similarity`
                        : `Related to ${c.seed_artist_name || "monitored artist"} — ${c.associated_seed_mbids.length} connection(s)`}
                    </p>
                  </div>
                  <div className="flex gap-1 shrink-0">
                    <button onClick={() => actMut.mutate({ id: c.id, action: "accept" })} title="Add to Lidarr"
                      className="p-1.5 rounded hover:bg-green-900/40 text-slate-400 hover:text-green-300">
                      <Check size={15} />
                    </button>
                    <button onClick={() => actMut.mutate({ id: c.id, action: "reject" })} title="Reject"
                      className="p-1.5 rounded hover:bg-red-900/40 text-slate-400 hover:text-red-300">
                      <X size={15} />
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
