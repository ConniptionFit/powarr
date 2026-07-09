import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ListMusic, Play, Check, X, RefreshCw, Clock, Settings, Music } from "lucide-react";
import { req } from "../lib/api";

interface Playlist {
  id: number;
  genre_tag: string;
  title: string;
  plex_playlist_id: string | null;
  enabled: boolean;
  track_count: number;
  pending_count: number;
  last_generated_at: string | null;
  last_run_message: string | null;
}

interface PlaylistDetail extends Playlist {
  mood?: string | null;
  era?: string | null;
  auto_add_override?: boolean | null;
  max_tracks_override?: number | null;
}

interface Candidate {
  id: number;
  playlist_id: number;
  artist_name: string;
  musicbrainz_id: string | null;
  status: string;
}

interface SPSettings {
  enabled: boolean;
  qdrant_url: string;
  qdrant_api_key: string;
  qdrant_api_key_set: boolean;
  collection: string;
  auto_create_playlists: boolean;
  auto_add_tracks_default: boolean;
  min_artists_per_genre: number;
  excluded_genres: string[];
  max_tracks_per_playlist: number;
  schedule_enabled: boolean;
  schedule_interval_hours: number;
}

const api = {
  settings: () => req<SPSettings>("/smart-playlists/settings"),
  saveSettings: (s: Partial<SPSettings>) =>
    req<SPSettings>("/smart-playlists/settings", { method: "PUT", body: JSON.stringify(s) }),
  list: () => req<Playlist[]>("/smart-playlists"),
  detail: (id: number) => req<PlaylistDetail>(`/smart-playlists/${id}`),
  candidates: (status = "pending") =>
    req<Candidate[]>(`/smart-playlists/candidates?status=${status}`),
  run: () => req<{ ok: boolean; message: string; candidates?: number }>("/smart-playlists/run", { method: "POST", body: "{}" }),
  accept: (id: number) =>
    req<{ ok: boolean; message: string }>(`/smart-playlists/candidates/${id}/accept`, { method: "POST" }),
  reject: (id: number) =>
    req<{ ok: boolean; message: string }>(`/smart-playlists/candidates/${id}/reject`, { method: "POST" }),
};

function formatDate(isoDate: string | null | undefined): string {
  if (!isoDate) return "Never";
  const date = new Date(isoDate);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  if (diffMins < 1) return "Just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}

export default function SmartPlaylists() {
  const qc = useQueryClient();
  const { data: settings } = useQuery({ queryKey: ["sp-settings"], queryFn: api.settings });
  const { data: playlists = [] } = useQuery({ queryKey: ["sp-list"], queryFn: api.list });
  const { data: candidates = [] } = useQuery({ queryKey: ["sp-candidates"], queryFn: () => api.candidates("pending") });
  const [draft, setDraft] = useState<Partial<SPSettings> | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const form = draft ?? settings ?? null;

  const saveMut = useMutation({
    mutationFn: () => api.saveSettings(form || {}),
    onSuccess: () => { setDraft(null); setMsg("Settings saved"); qc.invalidateQueries({ queryKey: ["sp-settings"] }); },
    onError: (e: Error) => setMsg(e.message),
  });
  const runMut = useMutation({
    mutationFn: api.run,
    onSuccess: (r) => {
      setMsg(r.ok ? `Generated — ${r.candidates ?? 0} new candidate(s)` : r.message);
      qc.invalidateQueries({ queryKey: ["sp-list"] });
      qc.invalidateQueries({ queryKey: ["sp-candidates"] });
    },
    onError: (e: Error) => setMsg(e.message),
  });
  const actMut = useMutation({
    mutationFn: ({ id, action }: { id: number; action: "accept" | "reject" }) =>
      action === "accept" ? api.accept(id) : api.reject(id),
    onSuccess: (r) => {
      setMsg(r.message);
      qc.invalidateQueries({ queryKey: ["sp-candidates"] });
      qc.invalidateQueries({ queryKey: ["sp-list"] });
    },
    onError: (e: Error) => setMsg(e.message),
  });

  const set = <K extends keyof SPSettings>(k: K, v: SPSettings[K]) =>
    setDraft(prev => ({ ...(prev ?? settings ?? {}), [k]: v }));

  return (
    <div className="p-4 sm:p-8 max-w-6xl">
      <div className="flex items-center gap-3 mb-6">
        <ListMusic className="text-brand-light" size={22} />
        <div>
          <h1 className="text-2xl font-bold text-white">Smart Playlists</h1>
          <p className="text-slate-400 text-sm">Genre playlists from Qdrant → Plex with scheduling & auto-add</p>
        </div>
      </div>

      {msg && <p className="mb-4 text-sm text-slate-300">{msg}</p>}

      {form && (
        <section className="bg-surface-raised border border-purple-900/30 rounded-xl p-5 mb-6 space-y-4">
          <h2 className="text-white font-semibold text-sm uppercase tracking-wider flex items-center gap-2">
            <Settings size={16} /> Configuration
          </h2>

          <div className="space-y-3">
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
              <label className="text-xs text-slate-400 block">
                Min artists per genre
                <input type="number" className="mt-1 w-full bg-surface border border-purple-900/40 rounded px-3 py-2 text-sm text-white"
                  value={form.min_artists_per_genre || 3} onChange={e => set("min_artists_per_genre", parseInt(e.target.value))} />
              </label>
              <label className="text-xs text-slate-400 block">
                Max tracks per playlist
                <input type="number" className="mt-1 w-full bg-surface border border-purple-900/40 rounded px-3 py-2 text-sm text-white"
                  value={form.max_tracks_per_playlist || 200} onChange={e => set("max_tracks_per_playlist", parseInt(e.target.value))} />
              </label>
              <label className="text-xs text-slate-400 block sm:col-span-2">
                Qdrant API key {settings?.qdrant_api_key_set ? "(saved — leave blank to keep)" : ""}
                <input type="password" className="mt-1 w-full bg-surface border border-purple-900/40 rounded px-3 py-2 text-sm text-white"
                  value={form.qdrant_api_key || ""} onChange={e => set("qdrant_api_key", e.target.value)}
                  placeholder={settings?.qdrant_api_key_set ? "••••••••" : ""} />
              </label>
            </div>
          </div>

          <div className="border-t border-purple-900/30 pt-4 space-y-3">
            <h3 className="text-slate-300 font-semibold text-xs uppercase tracking-wider flex items-center gap-2">
              <Clock size={14} /> Scheduling
            </h3>
            <label className="flex items-center gap-2 text-sm text-slate-300">
              <input type="checkbox" checked={!!form.schedule_enabled} onChange={e => set("schedule_enabled", e.target.checked)} />
              Enable scheduled generation
            </label>
            {form.schedule_enabled && (
              <label className="text-xs text-slate-400 block">
                Interval (hours)
                <input type="number" className="mt-1 w-full bg-surface border border-purple-900/40 rounded px-3 py-2 text-sm text-white"
                  value={form.schedule_interval_hours || 24} onChange={e => set("schedule_interval_hours", parseInt(e.target.value))} />
              </label>
            )}
            <label className="flex items-center gap-2 text-sm text-slate-300">
              <input type="checkbox" checked={!!form.auto_add_tracks_default} onChange={e => set("auto_add_tracks_default", e.target.checked)} />
              Auto-add tracks to playlists (default)
            </label>
          </div>

          <div className="flex gap-2 pt-2">
            <button onClick={() => saveMut.mutate()} disabled={saveMut.isPending}
              className="px-3 py-2 rounded-lg bg-brand/30 text-brand-light text-sm hover:bg-brand/40 disabled:opacity-50">
              Save Settings
            </button>
            <button onClick={() => runMut.mutate()} disabled={runMut.isPending || !form.enabled}
              className="flex items-center gap-2 px-3 py-2 rounded-lg bg-surface border border-purple-900/40 text-slate-300 text-sm hover:text-white disabled:opacity-50">
              <Play size={14} /> {runMut.isPending ? "Running…" : "Generate Now"}
            </button>
          </div>
        </section>
      )}

      <section className="mb-6">
        <h2 className="text-white font-semibold text-sm uppercase tracking-wider mb-3 flex items-center gap-2">
          <Music size={16} /> Playlists ({playlists.length})
        </h2>
        {playlists.length === 0 ? (
          <p className="text-slate-500 text-sm">No genre playlists yet — configure Qdrant and Generate.</p>
        ) : (
          <div className="grid gap-2">
            {playlists.map(pl => (
              <div key={pl.id} className="bg-surface-raised border border-purple-900/30 rounded-lg p-4">
                <div className="flex items-start justify-between mb-2">
                  <div>
                    <p className="text-white text-sm font-medium">{pl.title}</p>
                    <p className="text-slate-500 text-xs">Genre: {pl.genre_tag}</p>
                  </div>
                  {pl.plex_playlist_id && (
                    <span className="text-xs bg-purple-900/40 text-purple-200 px-2 py-1 rounded">
                      Plex #{pl.plex_playlist_id}
                    </span>
                  )}
                </div>
                <div className="grid grid-cols-3 gap-2 text-xs text-slate-400 mb-2">
                  <span>Tracks: {pl.track_count}</span>
                  <span>Pending: {pl.pending_count}</span>
                  <span>Last gen: {formatDate(pl.last_generated_at)}</span>
                </div>
                {pl.last_run_message && (
                  <p className="text-xs text-slate-500 mb-2">Status: {pl.last_run_message}</p>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-white font-semibold text-sm uppercase tracking-wider">Pending artist candidates</h2>
          <button onClick={() => qc.invalidateQueries({ queryKey: ["sp-candidates"] })}
            className="p-1.5 text-slate-400 hover:text-white" title="Refresh"><RefreshCw size={14} /></button>
        </div>
        {candidates.length === 0 ? (
          <p className="text-slate-500 text-sm">No pending candidates.</p>
        ) : (
          <ul className="space-y-2">
            {candidates.map(c => (
              <li key={c.id} className="flex items-center justify-between bg-surface-raised border border-purple-900/30 rounded-lg px-4 py-2.5">
                <span className="text-slate-200 text-sm">{c.artist_name}</span>
                <div className="flex gap-1">
                  <button onClick={() => actMut.mutate({ id: c.id, action: "accept" })} title="Accept"
                    className="p-1.5 rounded hover:bg-green-900/40 text-slate-400 hover:text-green-300">
                    <Check size={15} />
                  </button>
                  <button onClick={() => actMut.mutate({ id: c.id, action: "reject" })} title="Reject"
                    className="p-1.5 rounded hover:bg-red-900/40 text-slate-400 hover:text-red-300">
                    <X size={15} />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
