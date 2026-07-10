import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Compass, Play, RefreshCw, Check, X, Sparkles, Settings, Music2, ChevronDown, ChevronUp } from "lucide-react";
import { Link } from "react-router-dom";
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
  image_url: string | null;
  bio: string | null;
  years_active: string | null;
}

interface Stats {
  pending: number;
  accepted: number;
  rejected: number;
  tracked_artists: number | null;
  last_run_at: string | null;
  last_run_message: string | null;
}

const api = {
  settings: () => req<{ enabled: boolean }>("/artist-discovery/settings"),
  stats: () => req<Stats>("/artist-discovery/stats"),
  candidates: (status = "pending") =>
    req<Candidate[]>(`/artist-discovery/candidates?status=${status}`),
  run: () => req<{ ok: boolean; message: string }>("/artist-discovery/run", { method: "POST" }),
  sync: () => req<{ ok: boolean; message: string }>("/artist-discovery/sync", { method: "POST" }),
  accept: (id: number) =>
    req<{ ok: boolean; message: string }>(`/artist-discovery/candidates/${id}/accept`, { method: "POST" }),
  reject: (id: number) =>
    req<{ ok: boolean; message: string }>(`/artist-discovery/candidates/${id}/reject`, { method: "POST" }),
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

function ArtistAvatar({ url, name }: { url: string | null; name: string }) {
  const [failed, setFailed] = useState(false);
  if (url && !failed) {
    return (
      <img
        src={url}
        alt={name}
        onError={() => setFailed(true)}
        className="w-16 h-16 rounded-lg object-cover shrink-0 bg-surface border border-purple-900/30"
      />
    );
  }
  return (
    <div className="w-16 h-16 rounded-lg shrink-0 bg-surface border border-purple-900/30 flex items-center justify-center">
      <Music2 size={22} className="text-slate-600" />
    </div>
  );
}

function CandidateCard({ c, onAccept, onReject, pending }: {
  c: Candidate;
  onAccept: () => void;
  onReject: () => void;
  pending: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const bio = c.bio || "";
  const isLong = bio.length > 180;

  return (
    <div className="bg-surface-raised border border-purple-900/30 rounded-lg p-4 flex gap-3">
      <ArtistAvatar url={c.image_url} name={c.artist_name} />
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-white text-sm font-medium truncate">{c.artist_name}</p>
            <p className="text-xs text-slate-500">
              {c.source === "centroid"
                ? `Taste match — ${c.similarity_score != null ? Math.round(c.similarity_score * 100) : "?"}% similarity`
                : `Related to ${c.seed_artist_name || "a monitored artist"} — ${c.associated_seed_mbids.length} connection(s)`}
              {c.years_active && <span> · {c.years_active}</span>}
            </p>
          </div>
          <div className="flex gap-1 shrink-0">
            <button onClick={onAccept} disabled={pending} title="Add to Lidarr"
              className="p-1.5 rounded hover:bg-green-900/40 text-slate-400 hover:text-green-300 disabled:opacity-40">
              <Check size={15} />
            </button>
            <button onClick={onReject} disabled={pending} title="Reject"
              className="p-1.5 rounded hover:bg-red-900/40 text-slate-400 hover:text-red-300 disabled:opacity-40">
              <X size={15} />
            </button>
          </div>
        </div>

        {(c.genres.length > 0 || c.era) && (
          <div className="flex flex-wrap gap-1 mt-2">
            {c.genres.slice(0, 5).map(g => (
              <span key={g} className="text-xs bg-purple-900/40 text-purple-200 px-2 py-0.5 rounded">{g}</span>
            ))}
            {c.era && <span className="text-xs bg-surface text-slate-400 px-2 py-0.5 rounded border border-purple-900/40">{c.era}</span>}
          </div>
        )}

        {bio && (
          <div className="mt-2">
            <p className={`text-xs text-slate-400 leading-relaxed ${!expanded && isLong ? "line-clamp-2" : ""}`}>
              {bio}
            </p>
            {isLong && (
              <button onClick={() => setExpanded(e => !e)}
                className="flex items-center gap-1 text-xs text-brand-light hover:underline mt-1">
                {expanded ? <>Show less <ChevronUp size={12} /></> : <>Show more <ChevronDown size={12} /></>}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default function ArtistDiscovery() {
  const qc = useQueryClient();
  const { data: settings } = useQuery({ queryKey: ["ad-settings"], queryFn: api.settings });
  const { data: stats } = useQuery({ queryKey: ["ad-stats"], queryFn: api.stats });
  const { data: candidates = [] } = useQuery({ queryKey: ["ad-candidates"], queryFn: () => api.candidates("pending") });
  const [msg, setMsg] = useState<string | null>(null);

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

  const enabled = !!settings?.enabled;

  return (
    <div className="p-4 sm:p-8 max-w-4xl">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Compass className="text-brand-light" size={22} />
          <div>
            <h1 className="text-2xl font-bold text-white">Artist Discovery</h1>
            <p className="text-slate-400 text-sm">Last.fm taste mapping → Qdrant similarity + related-artist graph → Lidarr</p>
          </div>
        </div>
        <Link to="/settings/music" title="Configure"
          className="p-2 rounded-lg text-slate-400 hover:text-white hover:bg-surface-raised transition-colors">
          <Settings size={18} />
        </Link>
      </div>

      {!enabled && (
        <div className="mb-6 bg-amber-900/20 border border-amber-800/40 rounded-lg px-4 py-3 text-sm text-amber-200 flex items-center justify-between">
          <span>Artist Discovery is disabled.</span>
          <Link to="/settings/music" className="underline hover:text-white">Configure it →</Link>
        </div>
      )}

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

      <div className="flex flex-wrap items-center gap-2 mb-6">
        <button onClick={() => runMut.mutate()} disabled={runMut.isPending || !enabled}
          className="flex items-center gap-2 px-3 py-2 rounded-lg bg-brand/30 text-brand-light text-sm hover:bg-brand/40 disabled:opacity-50">
          <Play size={14} /> {runMut.isPending ? "Running…" : "Run Discovery Now"}
        </button>
        <button onClick={() => syncMut.mutate()} disabled={syncMut.isPending || !enabled}
          className="flex items-center gap-2 px-3 py-2 rounded-lg bg-surface-raised border border-purple-900/40 text-slate-300 text-sm hover:text-white disabled:opacity-50">
          <RefreshCw size={14} /> {syncMut.isPending ? "Syncing…" : "Sync Now"}
        </button>
        {msg && <span className="text-sm text-slate-400">{msg}</span>}
      </div>

      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-white font-semibold text-sm uppercase tracking-wider flex items-center gap-2">
            <Sparkles size={16} /> Pending candidates ({candidates.length})
          </h2>
          <button onClick={() => qc.invalidateQueries({ queryKey: ["ad-candidates"] })}
            className="p-1.5 text-slate-400 hover:text-white" title="Refresh"><RefreshCw size={14} /></button>
        </div>
        {candidates.length === 0 ? (
          <p className="text-slate-500 text-sm">
            No pending candidates — configure Last.fm/Qdrant/Lidarr and Run Discovery.
          </p>
        ) : (
          <div className="grid gap-2">
            {candidates.map(c => (
              <CandidateCard
                key={c.id}
                c={c}
                pending={actMut.isPending}
                onAccept={() => actMut.mutate({ id: c.id, action: "accept" })}
                onReject={() => actMut.mutate({ id: c.id, action: "reject" })}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
