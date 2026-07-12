import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ListMusic, Play, RefreshCw, Music, Pencil, Settings, Sparkles, Upload, Trash2,
  Ban, Library,
} from "lucide-react";
import { Link } from "react-router-dom";
import { req, fmtRelative } from "../../lib/api";

interface Playlist {
  id: number;
  genre_tag: string;
  title: string;
  plex_playlist_id: string | null;
  enabled: boolean;
  track_count: number;
  artist_count: number;
  pending_count: number;
  last_generated_at: string | null;
  last_run_message: string | null;
  is_template: boolean; // SP-12 — genre_tag names a configured template, not a real genre
}

interface PlaylistDetail extends Playlist {
  mood?: string | null;
  era?: string | null;
  auto_add_override?: boolean | null;
  max_tracks_override?: number | null;
}

interface SPSettings {
  enabled: boolean;
  blacklisted_artists: string[];
  auto_create_playlists: boolean;
  auto_update_playlists: boolean;
  llm_playlist_names: boolean;
}

const api = {
  settings: () => req<SPSettings>("/smart-playlists/settings"),
  list: () => req<Playlist[]>("/smart-playlists"),
  detail: (id: number) => req<PlaylistDetail>(`/smart-playlists/${id}`),
  run: () => req<{
    ok: boolean; message: string; playlists_created?: number; tracks_added?: number;
  }>("/smart-playlists/run", { method: "POST", body: "{}" }),
  updatePlaylist: (id: number, body: Partial<PlaylistDetail>) =>
    req<PlaylistDetail>(`/smart-playlists/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deletePlaylist: (id: number) =>
    req<{ ok: boolean; message: string }>(`/smart-playlists/${id}`, { method: "DELETE" }),
  approve: (id: number) =>
    req<{ ok: boolean; message: string }>(`/smart-playlists/${id}/approve`, { method: "POST" }),
  suggestName: (id: number) =>
    req<{ ok: boolean; suggested_title: string | null; fallback: string }>(
      `/smart-playlists/${id}/suggest-name`, { method: "POST" }),
  saveBlacklist: (artists: string[]) =>
    req<{ ok: boolean; blacklisted_artists: string[] }>("/smart-playlists/blacklist", {
      method: "PUT", body: JSON.stringify({ blacklisted_artists: artists }),
    }),
};

function PlaylistCard({ pl, onMsg, suggested }: { pl: Playlist; onMsg: (m: string) => void; suggested?: boolean }) {
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
      onMsg(pl.plex_playlist_id && titleDraft.trim() !== pl.title ? "Saved — Plex title updated" : "Saved");
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
    },
    onError: (e: Error) => onMsg(e.message),
  });

  const approveMut = useMutation({
    mutationFn: () => api.approve(pl.id),
    onSuccess: (r) => {
      onMsg(r.message);
      qc.invalidateQueries({ queryKey: ["sp-list"] });
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
      <div className="flex items-start justify-between mb-2 gap-2">
        <div className="min-w-0">
          <p className="text-white text-sm font-medium truncate">
            {pl.title}
            {pl.is_template && (
              <span className="ml-2 px-1.5 py-0.5 rounded bg-indigo-900/40 text-indigo-300 text-[10px] font-bold uppercase tracking-wide align-middle">
                Template
              </span>
            )}
          </p>
          <p className="text-slate-500 text-xs">{pl.is_template ? "Template" : "Genre"}: {pl.genre_tag}</p>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {suggested ? (
            <button onClick={() => approveMut.mutate()} disabled={approveMut.isPending}
              className="flex items-center gap-1 px-2 py-1 rounded-lg bg-brand/30 text-brand-light text-xs hover:bg-brand/40 disabled:opacity-50"
              title="Approve & push to Plex">
              <Upload size={12} />
              {approveMut.isPending ? "…" : "Approve"}
            </button>
          ) : (
            <span className="text-xs bg-purple-900/40 text-purple-200 px-2 py-1 rounded">
              Plex
            </span>
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
        <span>Artists: {pl.artist_count || "—"}</span>
        <span>Last: {fmtRelative(pl.last_generated_at)}</span>
      </div>
      {pl.last_run_message && (
        <p className="text-xs text-slate-500 mb-2">{pl.last_run_message}</p>
      )}

      {confirmDelete && (
        <div className="border-t border-red-900/40 mt-2 pt-3 space-y-2">
          <p className="text-sm text-red-200">
            Delete <span className="font-medium text-white">{pl.title}</span>?
            {pl.plex_playlist_id
              ? " This also removes the playlist from Plex."
              : " Suggested only — nothing on Plex."}
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
          {!suggested && (
            <label className="text-xs text-slate-400 block">
              Auto-update override
              <select className="mt-1 w-full bg-surface border border-purple-900/40 rounded px-2 py-1.5 text-sm text-white"
                value={autoAdd} onChange={e => setAutoAdd(e.target.value as "" | "on" | "off")}>
                <option value="">Use global default</option>
                <option value="on">On</option>
                <option value="off">Off</option>
              </select>
            </label>
          )}
          <label className={`text-xs text-slate-400 block ${suggested ? "sm:col-span-2" : ""}`}>
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

function BlacklistSection({ onMsg }: { onMsg: (m: string) => void }) {
  const qc = useQueryClient();
  const { data: settings } = useQuery({ queryKey: ["sp-settings"], queryFn: api.settings });
  const [draft, setDraft] = useState<string | null>(null);
  const [newArtist, setNewArtist] = useState("");

  const artists = draft != null
    ? draft.split("\n").map(s => s.trim()).filter(Boolean)
    : (settings?.blacklisted_artists || []);

  const textValue = draft ?? (settings?.blacklisted_artists || []).join("\n");

  const saveMut = useMutation({
    mutationFn: () => api.saveBlacklist(artists),
    onSuccess: (r) => {
      setDraft(null);
      onMsg(`Blacklist saved (${r.blacklisted_artists.length})`);
      qc.invalidateQueries({ queryKey: ["sp-settings"] });
    },
    onError: (e: Error) => onMsg(e.message),
  });

  const addArtist = () => {
    const name = newArtist.trim();
    if (!name) return;
    const next = [...artists];
    if (!next.some(a => a.toLowerCase() === name.toLowerCase())) next.push(name);
    setDraft(next.join("\n"));
    setNewArtist("");
  };

  const removeArtist = (name: string) => {
    setDraft(artists.filter(a => a !== name).join("\n"));
  };

  return (
    <section className="mb-6">
      <h2 className="text-white font-semibold text-sm uppercase tracking-wider mb-1 flex items-center gap-2">
        <Ban size={16} /> Artist blacklist
      </h2>
      <p className="text-xs text-slate-500 mb-3">
        All monitored artists are included in playlists unless listed here.
      </p>
      <div className="bg-surface-raised border border-purple-900/30 rounded-lg p-4 space-y-3">
        <div className="flex gap-2">
          <input
            className="flex-1 bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white placeholder:text-slate-600"
            placeholder="Add artist name…"
            value={newArtist}
            onChange={e => setNewArtist(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); addArtist(); } }}
          />
          <button onClick={addArtist}
            className="px-3 py-1.5 rounded-lg bg-brand/30 text-brand-light text-sm hover:bg-brand/40">
            Add
          </button>
        </div>
        {artists.length === 0 ? (
          <p className="text-slate-500 text-sm">No blacklisted artists.</p>
        ) : (
          <ul className="flex flex-wrap gap-2">
            {artists.map(a => (
              <li key={a} className="flex items-center gap-1.5 text-sm bg-surface border border-purple-900/40 rounded-lg px-2.5 py-1 text-slate-200">
                {a}
                <button onClick={() => removeArtist(a)} className="text-slate-500 hover:text-red-300" title="Remove">×</button>
              </li>
            ))}
          </ul>
        )}
        <textarea
          className="w-full bg-surface border border-purple-900/40 rounded px-3 py-2 text-xs text-slate-400 font-mono min-h-[72px]"
          value={textValue}
          onChange={e => setDraft(e.target.value)}
          placeholder="One artist per line (optional bulk edit)"
        />
        <button onClick={() => saveMut.mutate()} disabled={saveMut.isPending || draft == null}
          className="px-3 py-1.5 rounded-lg bg-brand hover:bg-brand-dark text-white text-sm disabled:opacity-50">
          {saveMut.isPending ? "Saving…" : "Save blacklist"}
        </button>
      </div>
    </section>
  );
}

export default function SmartPlaylists() {
  const qc = useQueryClient();
  const { data: settings } = useQuery({ queryKey: ["sp-settings"], queryFn: api.settings });
  const { data: playlists = [] } = useQuery({ queryKey: ["sp-list"], queryFn: api.list });
  const [msg, setMsg] = useState<string | null>(null);

  const suggested = playlists.filter(p => !p.plex_playlist_id);
  const managed = playlists.filter(p => !!p.plex_playlist_id);

  const runMut = useMutation({
    mutationFn: api.run,
    onSuccess: (r) => {
      const parts = [];
      if (r.playlists_created) parts.push(`${r.playlists_created} suggested`);
      if (r.tracks_added) parts.push(`+${r.tracks_added} tracks`);
      setMsg(r.ok ? (parts.length ? `Generated — ${parts.join(", ")}` : "Generated") : r.message);
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
            <p className="text-slate-400 text-sm">
              Auto-includes artists unless blacklisted — approve Suggested playlists to push to Plex
            </p>
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
        <button onClick={() => qc.invalidateQueries({ queryKey: ["sp-list"] })}
          className="p-2 text-slate-400 hover:text-white" title="Refresh">
          <RefreshCw size={14} />
        </button>
        {msg && <span className="text-sm text-slate-400">{msg}</span>}
      </div>

      <section className="mb-6">
        <h2 className="text-white font-semibold text-sm uppercase tracking-wider mb-1 flex items-center gap-2">
          <Music size={16} /> Suggested playlists ({suggested.length})
        </h2>
        <p className="text-xs text-slate-500 mb-3">
          Newly generated genre playlists not yet on Plex — Approve to create and fill them.
        </p>
        {suggested.length === 0 ? (
          <p className="text-slate-500 text-sm">No suggested playlists — run Generate to discover genres.</p>
        ) : (
          <div className="grid gap-2">
            {suggested.map(pl => <PlaylistCard key={pl.id} pl={pl} onMsg={setMsg} suggested />)}
          </div>
        )}
      </section>

      <section className="mb-6">
        <h2 className="text-white font-semibold text-sm uppercase tracking-wider mb-1 flex items-center gap-2">
          <Library size={16} /> Managed by Powarr ({managed.length})
        </h2>
        <p className="text-xs text-slate-500 mb-3">
          Playlists Powarr owns on Plex — auto-updated with eligible artists on Generate.
        </p>
        {managed.length === 0 ? (
          <p className="text-slate-500 text-sm">No managed playlists yet — approve a Suggested playlist.</p>
        ) : (
          <div className="grid gap-2">
            {managed.map(pl => <PlaylistCard key={pl.id} pl={pl} onMsg={setMsg} />)}
          </div>
        )}
      </section>

      <BlacklistSection onMsg={setMsg} />
    </div>
  );
}
