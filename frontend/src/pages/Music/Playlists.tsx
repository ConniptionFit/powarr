import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ListMusic, Play, Check, X, RefreshCw, Music, Pencil, Settings, Sparkles, Upload, Trash2 } from "lucide-react";
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
  deletePlaylist: (id: number) =>
    req<{ ok: boolean; message: string }>(`/smart-playlists/${id}`, { method: "DELETE" }),
  approve: (id: number) =>
    req<{ ok: boolean; message: string }>(`/smart-playlists/${id}/approve`, { method: "POST" }),
  suggestName: (id: number) =>
    req<{ ok: boolean; suggested_title: string | null; fallback: string }>(
      `/smart-playlists/${id}/suggest-name`, { method: "POST" }),
};

function PlaylistCard({ pl, onMsg }: { pl: Playlist; onMsg: (m: string) => void }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const { data: detail } = useQuery({
    queryKey: ["sp-detail", pl.id], queryFn: () => api.detail(pl.id), enabled: editing,
  });
  const [autoAdd, setAutoAdd] = useState<"" | "on" | "off">("");
  const [maxTracks, setMaxTracks] = useState("");
  const [titleDraft, setTitleDraft] = useState(pl.title);

  const openEdit = () => {
    setAutoAdd(detail?.auto_add_override === true ? "on" : detail?.auto_add_override === false ? "off" : "");
    setMaxTracks(detail?.max_tracks_override != null ? String(detail.max_tracks_override) : "");
    setTitleDraft(pl.title);
    setConfirmDelete(false);
    setEditing(true);
  };

  const saveMut = useMutation({
    mutationFn: () => api.updatePlaylist(pl.id, {
      auto_add_override: autoAdd === "" ? null : autoAdd === "on",
      max_tracks_override: maxTracks.trim() === "" ? null : parseInt(maxTracks),
      title: titleDraft.trim() || pl.title,
    }),
    onSuccess: () => {
      setEditing(false);
      onMsg(pl.plex_playlist_id && titleDraft.trim() !== pl.title
        ? "Saved — Plex title updated"
        : "Saved");
      qc.invalidateQueries({ queryKey: ["sp-list"] });
      qc.invalidateQueries({ queryKey: ["sp-detail", pl.id] });
    },
    onError: (e: Error) => onMsg(e.message),
  });

  const deleteMut = useMutation({
    mutationFn: () => api.deletePlaylist(pl.id),
    onSuccess: (r) => {
      onMsg(r.message);
      setConfirmDelete(false);
      qc.invalidateQueries({ queryKey: ["sp-list"] });
      qc.invalidateQueries({ queryKey: ["sp-candidates"] });
    },
    onError: (e: Error) => onMsg(e.message),
  });

  const approveMut = useMutation({
    mutationFn: () => api.approve(pl.id),
    onSuccess: (r) => {
      onMsg(r.message);
      qc.invalidateQueries({ queryKey: ["sp-list"] });
      qc.invalidateQueries({ queryKey: ["sp-candidates"] });
    },
    onError: (e: Error) => onMsg(e.message),
  });

  const suggestMut = useMutation({
    mutationFn: () => api.suggestName(pl.id),
    onSuccess: (r) => {
      if (r.suggested_title) {
        setTitleDraft(r.suggested_title);
        setEditing(true);
        onMsg(`Suggested: ${r.suggested_title}`);
      } else {
        onMsg(`LLM unavailable — fallback ${r.fallback}`);
      }
    },
    onError: (e: Error) => onMsg(e.message),
  });

  return (
    <div className="bg-surface-raised border border-purple-900/30 rounded-lg p-4">
      <div className="flex items-start justify-between mb-2">
        <div>
          <p className="text-white text-sm font-medium">{pl.title}</p>
          <p className="text-slate-500 text-xs">Genre: {pl.genre_tag}</p>
        </div>
        <div className="flex items-center gap-1">
          {pl.plex_playlist_id ? (
            <span className="text-xs bg-purple-900/40 text-purple-200 px-2 py-1 rounded">
              Plex #{pl.plex_playlist_id}
            </span>
          ) : (
            <span className="text-xs bg-amber-900/30 text-amber-200 px-2 py-1 rounded">Draft</span>
          )}
          {!pl.plex_playlist_id && (
            <button onClick={() => approveMut.mutate()} disabled={approveMut.isPending}
              className="p-1.5 rounded hover:bg-green-900/40 text-slate-400 hover:text-green-300"
              title="Approve & push to Plex">
              <Upload size={13} />
            </button>
          )}
          <button onClick={() => suggestMut.mutate()} disabled={suggestMut.isPending}
            className="p-1.5 rounded hover:bg-white/10 text-slate-400 hover:text-brand-light"
            title="Suggest LLM name">
            <Sparkles size={13} />
          </button>
          <button onClick={() => (editing ? setEditing(false) : openEdit())}
            className="p-1.5 rounded hover:bg-white/10 text-slate-400 hover:text-white" title="Rename / edit">
            <Pencil size={13} />
          </button>
          <button onClick={() => { setConfirmDelete(v => !v); setEditing(false); }}
            className="p-1.5 rounded hover:bg-red-900/40 text-slate-400 hover:text-red-300"
            title="Delete playlist">
            <Trash2 size={13} />
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

      {confirmDelete && (
        <div className="border-t border-red-900/40 mt-2 pt-3 space-y-2">
          <p className="text-sm text-red-200">
            Delete <span className="font-medium text-white">{pl.title}</span>?
            {pl.plex_playlist_id
              ? " This also removes the playlist from Plex."
              : " Draft only — nothing on Plex."}
          </p>
          <div className="flex gap-2">
            <button onClick={() => deleteMut.mutate()} disabled={deleteMut.isPending}
              className="px-3 py-1.5 rounded-lg bg-red-900/50 text-red-100 text-sm hover:bg-red-900/70 disabled:opacity-50">
              {deleteMut.isPending ? "Deleting…" : "Delete"}
            </button>
            <button onClick={() => setConfirmDelete(false)}
              className="px-3 py-1.5 rounded-lg text-slate-400 text-sm hover:text-white">
              Cancel
            </button>
          </div>
        </div>
      )}

      {editing && (
        <div className="border-t border-purple-900/30 mt-2 pt-3 grid sm:grid-cols-2 gap-3">
          <label className="text-xs text-slate-400 block sm:col-span-2">
            Title {pl.plex_playlist_id && <span className="text-slate-600">(synced to Plex on save)</span>}
            <input className="mt-1 w-full bg-surface border border-purple-900/40 rounded px-2 py-1.5 text-sm text-white"
              value={titleDraft} onChange={e => setTitleDraft(e.target.value)} />
          </label>
          <label className="text-xs text-slate-400 block">
            Auto-update override
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
              Save
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
            <p className="text-slate-400 text-sm">Genre playlists from Qdrant → Plex — approve drafts, auto-update approved</p>
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
            {playlists.map(pl => <PlaylistCard key={pl.id} pl={pl} onMsg={setMsg} />)}
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
