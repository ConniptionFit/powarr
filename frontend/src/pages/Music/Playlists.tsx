import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ListMusic, Play, Check, X, RefreshCw, Music, Pencil, Settings } from "lucide-react";
import { Link } from "react-router-dom";
import { req, fmtRelative } from "../../lib/api";

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

const api = {
  settings: () => req<{ enabled: boolean }>("/smart-playlists/settings"),
  list: () => req<Playlist[]>("/smart-playlists"),
  detail: (id: number) => req<PlaylistDetail>(`/smart-playlists/${id}`),
  candidates: (status = "pending") =>
    req<Candidate[]>(`/smart-playlists/candidates?status=${status}`),
  run: () => req<{ ok: boolean; message: string; candidates?: number }>("/smart-playlists/run", { method: "POST", body: "{}" }),
  accept: (id: number) =>
    req<{ ok: boolean; message: string }>(`/smart-playlists/candidates/${id}/accept`, { method: "POST" }),
  reject: (id: number) =>
    req<{ ok: boolean; message: string }>(`/smart-playlists/candidates/${id}/reject`, { method: "POST" }),
  updatePlaylist: (id: number, body: Partial<PlaylistDetail>) =>
    req<PlaylistDetail>(`/smart-playlists/${id}`, { method: "PUT", body: JSON.stringify(body) }),
};

function PlaylistCard({ pl }: { pl: Playlist }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const { data: detail } = useQuery({
    queryKey: ["sp-detail", pl.id], queryFn: () => api.detail(pl.id), enabled: editing,
  });
  const [autoAdd, setAutoAdd] = useState<"" | "on" | "off">("");
  const [maxTracks, setMaxTracks] = useState("");

  const openEdit = () => {
    setAutoAdd(detail?.auto_add_override === true ? "on" : detail?.auto_add_override === false ? "off" : "");
    setMaxTracks(detail?.max_tracks_override != null ? String(detail.max_tracks_override) : "");
    setEditing(true);
  };

  const saveMut = useMutation({
    mutationFn: () => api.updatePlaylist(pl.id, {
      auto_add_override: autoAdd === "" ? null : autoAdd === "on",
      max_tracks_override: maxTracks.trim() === "" ? null : parseInt(maxTracks),
    }),
    onSuccess: () => {
      setEditing(false);
      qc.invalidateQueries({ queryKey: ["sp-list"] });
      qc.invalidateQueries({ queryKey: ["sp-detail", pl.id] });
    },
  });

  return (
    <div className="bg-surface-raised border border-purple-900/30 rounded-lg p-4">
      <div className="flex items-start justify-between mb-2">
        <div>
          <p className="text-white text-sm font-medium">{pl.title}</p>
          <p className="text-slate-500 text-xs">Genre: {pl.genre_tag}</p>
        </div>
        <div className="flex items-center gap-2">
          {pl.plex_playlist_id && (
            <span className="text-xs bg-purple-900/40 text-purple-200 px-2 py-1 rounded">
              Plex #{pl.plex_playlist_id}
            </span>
          )}
          <button onClick={() => (editing ? setEditing(false) : openEdit())}
            className="p-1.5 rounded hover:bg-white/10 text-slate-400 hover:text-white" title="Edit overrides">
            <Pencil size={13} />
          </button>
        </div>
      </div>
      <div className="grid grid-cols-3 gap-2 text-xs text-slate-400 mb-2">
        <span>Tracks: {pl.track_count}</span>
        <span>Pending: {pl.pending_count}</span>
        <span>Last gen: {fmtRelative(pl.last_generated_at)}</span>
      </div>
      {pl.last_run_message && (
        <p className="text-xs text-slate-500 mb-2">Status: {pl.last_run_message}</p>
      )}

      {editing && (
        <div className="border-t border-purple-900/30 mt-2 pt-3 grid sm:grid-cols-2 gap-3">
          <label className="text-xs text-slate-400 block">
            Auto-add override
            <select className="mt-1 w-full bg-surface border border-purple-900/40 rounded px-2 py-1.5 text-sm text-white"
              value={autoAdd} onChange={e => setAutoAdd(e.target.value as "" | "on" | "off")}>
              <option value="">Use global default</option>
              <option value="on">On</option>
              <option value="off">Off</option>
            </select>
          </label>
          <label className="text-xs text-slate-400 block">
            Max tracks override
            <input type="number" placeholder="Use global default"
              className="mt-1 w-full bg-surface border border-purple-900/40 rounded px-2 py-1.5 text-sm text-white placeholder:text-slate-600"
              value={maxTracks} onChange={e => setMaxTracks(e.target.value)} />
          </label>
          <div className="sm:col-span-2">
            <button onClick={() => saveMut.mutate()} disabled={saveMut.isPending}
              className="px-3 py-1.5 rounded-lg bg-brand/30 text-brand-light text-sm hover:bg-brand/40 disabled:opacity-50">
              Save Overrides
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function SmartPlaylists() {
  const qc = useQueryClient();
  const { data: settings } = useQuery({ queryKey: ["sp-settings"], queryFn: api.settings });
  const { data: playlists = [] } = useQuery({ queryKey: ["sp-list"], queryFn: api.list });
  const { data: candidates = [] } = useQuery({ queryKey: ["sp-candidates"], queryFn: () => api.candidates("pending") });
  const [msg, setMsg] = useState<string | null>(null);

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

  const enabled = !!settings?.enabled;

  return (
    <div className="p-4 sm:p-8 max-w-4xl">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <ListMusic className="text-brand-light" size={22} />
          <div>
            <h1 className="text-2xl font-bold text-white">Playlists</h1>
            <p className="text-slate-400 text-sm">Genre playlists from Qdrant → Plex with scheduling & auto-add</p>
          </div>
        </div>
        <Link to="/settings/music" title="Configure"
          className="p-2 rounded-lg text-slate-400 hover:text-white hover:bg-surface-raised transition-colors">
          <Settings size={18} />
        </Link>
      </div>

      {!enabled && (
        <div className="mb-6 bg-amber-900/20 border border-amber-800/40 rounded-lg px-4 py-3 text-sm text-amber-200 flex items-center justify-between">
          <span>Smart Playlists is disabled.</span>
          <Link to="/settings/music" className="underline hover:text-white">Configure it →</Link>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 mb-6">
        <button onClick={() => runMut.mutate()} disabled={runMut.isPending || !enabled}
          className="flex items-center gap-2 px-3 py-2 rounded-lg bg-brand/30 text-brand-light text-sm hover:bg-brand/40 disabled:opacity-50">
          <Play size={14} /> {runMut.isPending ? "Running…" : "Generate Now"}
        </button>
        {msg && <span className="text-sm text-slate-400">{msg}</span>}
      </div>

      <section className="mb-6">
        <h2 className="text-white font-semibold text-sm uppercase tracking-wider mb-3 flex items-center gap-2">
          <Music size={16} /> Playlists ({playlists.length})
        </h2>
        {playlists.length === 0 ? (
          <p className="text-slate-500 text-sm">No genre playlists yet — configure Qdrant and Generate.</p>
        ) : (
          <div className="grid gap-2">
            {playlists.map(pl => <PlaylistCard key={pl.id} pl={pl} />)}
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
