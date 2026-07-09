import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ListMusic, Play, Check, X, RefreshCw } from "lucide-react";
import { req } from "../lib/api";

interface Playlist {
  id: number;
  genre_tag: string;
  title: string;
  plex_playlist_id: string | null;
  enabled: boolean;
  pending_count: number;
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
}

const api = {
  settings: () => req<SPSettings>("/smart-playlists/settings"),
  saveSettings: (s: Partial<SPSettings>) =>
    req<SPSettings>("/smart-playlists/settings", { method: "PUT", body: JSON.stringify(s) }),
  list: () => req<Playlist[]>("/smart-playlists"),
  candidates: (status = "pending") =>
    req<Candidate[]>(`/smart-playlists/candidates?status=${status}`),
  run: () => req<{ ok: boolean; message: string; candidates?: number }>("/smart-playlists/run", { method: "POST", body: "{}" }),
  accept: (id: number) =>
    req<{ ok: boolean; message: string }>(`/smart-playlists/candidates/${id}/accept`, { method: "POST" }),
  reject: (id: number) =>
    req<{ ok: boolean; message: string }>(`/smart-playlists/candidates/${id}/reject`, { method: "POST" }),
};

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
    <div className="p-4 sm:p-8 max-w-4xl">
      <div className="flex items-center gap-3 mb-6">
        <ListMusic className="text-brand-light" size={22} />
        <div>
          <h1 className="text-2xl font-bold text-white">Smart Playlists</h1>
          <p className="text-slate-400 text-sm">Genre playlists from Qdrant → Plex (read-only vs Qdrant)</p>
        </div>
      </div>

      {msg && <p className="mb-4 text-sm text-slate-300">{msg}</p>}

      {form && (
        <section className="bg-surface-raised border border-purple-900/30 rounded-xl p-5 mb-6 space-y-3">
          <h2 className="text-white font-semibold text-sm uppercase tracking-wider">Settings</h2>
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
          </div>
          <div className="flex gap-2">
            <button onClick={() => saveMut.mutate()} disabled={saveMut.isPending}
              className="px-3 py-2 rounded-lg bg-brand/30 text-brand-light text-sm hover:bg-brand/40 disabled:opacity-50">
              Save
            </button>
            <button onClick={() => runMut.mutate()} disabled={runMut.isPending || !form.enabled}
              className="flex items-center gap-2 px-3 py-2 rounded-lg bg-surface border border-purple-900/40 text-slate-300 text-sm hover:text-white disabled:opacity-50">
              <Play size={14} /> {runMut.isPending ? "Running…" : "Generate Candidates"}
            </button>
          </div>
        </section>
      )}

      <section className="mb-6">
        <h2 className="text-white font-semibold text-sm uppercase tracking-wider mb-3">Playlists</h2>
        {playlists.length === 0 ? (
          <p className="text-slate-500 text-sm">No genre playlists yet — configure Qdrant and Generate.</p>
        ) : (
          <ul className="space-y-2">
            {playlists.map(pl => (
              <li key={pl.id} className="flex items-center justify-between bg-surface-raised border border-purple-900/30 rounded-lg px-4 py-3">
                <div>
                  <p className="text-white text-sm font-medium">{pl.title}</p>
                  <p className="text-slate-500 text-xs">
                    {pl.pending_count} pending
                    {pl.plex_playlist_id ? ` · Plex #${pl.plex_playlist_id}` : " · not created in Plex yet"}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-white font-semibold text-sm uppercase tracking-wider">Pending artists</h2>
          <button onClick={() => qc.invalidateQueries({ queryKey: ["sp-candidates"] })}
            className="p-1.5 text-slate-400 hover:text-white"><RefreshCw size={14} /></button>
        </div>
        {candidates.length === 0 ? (
          <p className="text-slate-500 text-sm">No pending candidates.</p>
        ) : (
          <ul className="space-y-2">
            {candidates.map(c => (
              <li key={c.id} className="flex items-center justify-between bg-surface-raised border border-purple-900/30 rounded-lg px-4 py-2.5">
                <span className="text-slate-200 text-sm">{c.artist_name}</span>
                <div className="flex gap-1">
                  <button onClick={() => actMut.mutate({ id: c.id, action: "accept" })}
                    className="p-1.5 rounded hover:bg-green-900/40 text-slate-400 hover:text-green-300" title="Accept — add tracks to Plex playlist">
                    <Check size={15} />
                  </button>
                  <button onClick={() => actMut.mutate({ id: c.id, action: "reject" })}
                    className="p-1.5 rounded hover:bg-red-900/40 text-slate-400 hover:text-red-300" title="Reject">
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
