import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Compass, Play, Check, X, Sparkles, Settings, ChevronDown, ChevronUp, History, UserPlus } from "lucide-react";
import { Link } from "react-router-dom";
import { req, fmtRelative, parseApiDate } from "../../lib/api";
import ArtistCard from "../../components/ArtistCard";
import ArtistPreviewButton from "../../components/ArtistPreviewButton";
import ScrollFadeX from "../../components/ScrollFadeX";

interface Candidate {
  id: number;
  musicbrainz_id: string | null;
  artist_name: string;
  genres: string[];
  mood_tags: string[];
  era: string | null;
  source: string; // "centroid" | "centroid_recent" | "graph" | "centroid_mood_{slug}" (AD-19)
  similarity_score: number | null;
  associated_seed_mbids: string[];
  seed_artist_name: string | null;
  seed_artist_names: string[];
  status: string;
  lidarr_artist_id: number | null;
  image_url: string | null;
  bio: string | null;
  years_active: string | null;
}

interface DiscoveryRun {
  id: number;
  run_type: string;
  started_at: string | null;
  finished_at: string | null;
  candidates_found: number;
  candidates_added: number;
  message: string | null;
}

// AD-22 — one genuine Lidarr add (ArtistAddLog row) with enough of its
// DiscoveredArtist enrichment to reuse whySuggested()'s wording.
interface RecentAdd {
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
  runs: () => req<DiscoveryRun[]>("/artist-discovery/runs?limit=10"),
  recentAdds: (limit: number) => req<RecentAdd[]>(`/artist-discovery/recent-adds?limit=${limit}`),
  accept: (id: number) =>
    req<{ ok: boolean; message: string }>(`/artist-discovery/candidates/${id}/accept`, { method: "POST" }),
  reject: (id: number) =>
    req<{ ok: boolean; message: string }>(`/artist-discovery/candidates/${id}/reject`, { method: "POST" }),
};

function whySuggested(c: Pick<Candidate, "source" | "similarity_score" | "seed_artist_name" | "seed_artist_names" | "associated_seed_mbids">): string {
  if (c.source === "centroid" || c.source === "centroid_recent" || c.source.startsWith("centroid_mood_")) {
    const pct = c.similarity_score != null ? `${Math.round(c.similarity_score * 100)}% match to` : "Close match to";
    // AD-17 — a second discovery lane seeded from what you've actually been
    // listening to lately, distinct from the all-time most-played centroid.
    // AD-19 — a third kind of lane, one per configured mood tag (SP-15).
    let profile = "your overall taste profile, built from your most-played artists";
    if (c.source === "centroid_recent") {
      profile = "your recent listening";
    } else if (c.source.startsWith("centroid_mood_")) {
      const mood = c.source.slice("centroid_mood_".length).replace(/_/g, " ");
      profile = `artists tagged "${mood}" in your library`;
    }
    return `${pct} ${profile}`;
  }
  const names = c.seed_artist_names.length > 0
    ? c.seed_artist_names
    : c.seed_artist_name ? [c.seed_artist_name] : [];
  const conn = Math.max(c.associated_seed_mbids.length, names.length, 1);
  if (names.length === 0) {
    return `Similar to ${conn} artist${conn === 1 ? "" : "s"} already in your library`;
  }
  const listed = names.slice(0, 4).join(", ") + (names.length > 4 ? ` +${names.length - 4} more` : "");
  return `Similar to ${listed} — ${conn} connection${conn === 1 ? "" : "s"} to artists in your library`;
}

function CandidateCard({ c, onAccept, onReject, pending }: {
  c: Candidate;
  onAccept: () => void;
  onReject: () => void;
  pending: boolean;
}) {
  return (
    <ArtistCard
      name={c.artist_name}
      yearsActive={c.years_active}
      imageUrl={c.image_url}
      bio={c.bio}
      genres={c.genres}
      era={c.era}
      subtitle={whySuggested(c)}
      actions={
        <>
          <button onClick={onAccept} disabled={pending} title="Add to Lidarr" aria-label="Add to Lidarr"
            className="p-1.5 rounded hover:bg-green-900/40 text-slate-400 hover:text-green-300 disabled:opacity-40">
            <Check size={15} />
          </button>
          <button onClick={onReject} disabled={pending} title="Reject" aria-label="Reject"
            className="p-1.5 rounded hover:bg-red-900/40 text-slate-400 hover:text-red-300 disabled:opacity-40">
            <X size={15} />
          </button>
        </>
      }
      preview={<ArtistPreviewButton artistName={c.artist_name} />}
    />
  );
}

function runDuration(r: DiscoveryRun): string {
  if (!r.started_at || !r.finished_at) return "—";
  const secs = Math.max(0, Math.round((parseApiDate(r.finished_at).getTime() - parseApiDate(r.started_at).getTime()) / 1000));
  return secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m ${secs % 60}s`;
}

// AD-22 — the last N artists actually added to Lidarr (discovery accepts +
// Related Artists adds), with when, why, thumbnail, and bio. Reuses the shared
// ArtistCard shell; the added-time rides in the actions slot since these rows
// have no buttons.
function addReason(a: RecentAdd): string {
  if (a.source === "related") return "Added from a Related Artists search";
  if (a.discovery_source) {
    return `Accepted from the discovery queue — ${whySuggested({
      source: a.discovery_source,
      similarity_score: a.similarity_score,
      seed_artist_name: a.seed_artist_name,
      seed_artist_names: a.seed_artist_names,
      associated_seed_mbids: a.associated_seed_mbids,
    })}`;
  }
  return "Accepted from the discovery queue";
}

function RecentlyAdded() {
  const [open, setOpen] = useState(true);
  const [limit, setLimit] = useState(10);
  const { data: adds = [] } = useQuery({
    queryKey: ["ad-recent-adds", limit],
    queryFn: () => api.recentAdds(limit),
    enabled: open,
  });

  return (
    <section className="mt-8">
      <div className="flex items-center gap-3 mb-3">
        <button onClick={() => setOpen(o => !o)}
          className="flex items-center gap-2 text-white font-semibold text-sm uppercase tracking-wider">
          <UserPlus size={16} /> Recently added
          {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
        {open && (
          <select value={limit} onChange={e => setLimit(Number(e.target.value))}
            title="How many recent adds to show"
            className="bg-surface-raised border border-purple-900/40 rounded px-2 py-1 text-xs text-slate-300">
            <option value={10}>Last 10</option>
            <option value={25}>Last 25</option>
            <option value={50}>Last 50</option>
          </select>
        )}
      </div>
      {open && (
        adds.length === 0 ? (
          <p className="text-slate-500 text-sm">No artists added yet — accept a discovery candidate or add one from Related Artists.</p>
        ) : (
          <div className="grid gap-2">
            {adds.map(a => (
              <ArtistCard
                key={a.id}
                name={a.artist_name}
                yearsActive={a.years_active}
                imageUrl={a.image_url}
                bio={a.bio}
                genres={a.genres}
                subtitle={addReason(a)}
                actions={
                  <span className="text-xs text-slate-500 whitespace-nowrap" title={a.added_at ? parseApiDate(a.added_at).toLocaleString() : undefined}>
                    {fmtRelative(a.added_at)}
                  </span>
                }
              />
            ))}
          </div>
        )
      )}
    </section>
  );
}

function RecentRuns() {
  const [open, setOpen] = useState(false);
  const { data: runs = [] } = useQuery({ queryKey: ["ad-runs"], queryFn: api.runs, enabled: open });

  return (
    <section className="mt-8">
      <button onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 text-white font-semibold text-sm uppercase tracking-wider mb-3">
        <History size={16} /> Recent runs
        {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      </button>
      {open && (
        runs.length === 0 ? (
          <p className="text-slate-500 text-sm">No runs recorded yet.</p>
        ) : (
          <ScrollFadeX className="overflow-x-auto" fadeFrom="from-[#1a1a2e]">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-slate-500 border-b border-purple-900/30">
                  <th className="py-2 pr-4 font-medium">Type</th>
                  <th className="py-2 pr-4 font-medium">Started</th>
                  <th className="py-2 pr-4 font-medium">Duration</th>
                  <th className="py-2 pr-4 font-medium">Found</th>
                  <th className="py-2 pr-4 font-medium">Added</th>
                  <th className="py-2 font-medium">Result</th>
                </tr>
              </thead>
              <tbody>
                {runs.map(r => (
                  <tr key={r.id} className="border-b border-purple-900/20 text-slate-300">
                    <td className="py-2 pr-4 capitalize">{r.run_type}</td>
                    <td className="py-2 pr-4 whitespace-nowrap">{fmtRelative(r.started_at)}</td>
                    <td className="py-2 pr-4">{runDuration(r)}</td>
                    <td className="py-2 pr-4">{r.candidates_found}</td>
                    <td className="py-2 pr-4">{r.candidates_added}</td>
                    <td className="py-2 text-slate-400">{r.message || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollFadeX>
        )
      )}
    </section>
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
      qc.invalidateQueries({ queryKey: ["ad-runs"] });
    },
    onError: (e: Error) => setMsg(e.message),
  });
  const actMut = useMutation({
    mutationFn: ({ id, action }: { id: number; action: "accept" | "reject" }) =>
      action === "accept" ? api.accept(id) : api.reject(id),
    onSuccess: (r) => {
      setMsg(r.message);
      qc.invalidateQueries({ queryKey: ["ad-candidates"] });
      qc.invalidateQueries({ queryKey: ["ad-stats"] });
      qc.invalidateQueries({ queryKey: ["ad-recent-adds"] });
    },
    onError: (e: Error) => setMsg(e.message),
  });

  // Live updates: keeps stats/candidates current whether discovery was
  // triggered by the button or the background scheduler, and surfaces the
  // finished/failed message here too (in addition to the tray card).
  useEffect(() => {
    const es = new EventSource("/api/v1/imports/events");
    es.onmessage = ev => {
      try {
        const data = JSON.parse(ev.data);
        if (data.type === "task_update" && data.task?.kind === "artist_discovery") {
          qc.invalidateQueries({ queryKey: ["ad-stats"] });
          qc.invalidateQueries({ queryKey: ["ad-candidates"] });
          qc.invalidateQueries({ queryKey: ["ad-runs"] });
          if (data.task.status === "done" || data.task.status === "failed") {
            setMsg(data.task.message || null);
          }
        }
      } catch { /* keepalive */ }
    };
    return () => es.close();
  }, [qc]);

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
          <div className="bg-surface-raised border border-purple-900/30 rounded-lg p-3"
            title="Total artists in Powarr's shared taste vector space — your monitored artists plus every related artist ever discovered. Never shrinks (points are soft-deleted, not removed).">
            <p className="text-xs text-slate-500">Taste model size</p>
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
            <p className="text-white text-sm font-medium">{fmtRelative(stats.last_run_at)}</p>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 mb-6">
        <button onClick={() => runMut.mutate()} disabled={runMut.isPending || !enabled}
          className="flex items-center gap-2 px-3 py-2 rounded-lg bg-brand/30 text-brand-light text-sm hover:bg-brand/40 disabled:opacity-50">
          <Play size={14} /> {runMut.isPending ? "Running…" : "Run Discovery Now"}
        </button>
        {msg && <span className="text-sm text-slate-400">{msg}</span>}
      </div>

      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-white font-semibold text-sm uppercase tracking-wider flex items-center gap-2">
            <Sparkles size={16} /> Pending candidates ({candidates.length})
          </h2>
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

      <RecentlyAdded />

      <RecentRuns />
    </div>
  );
}
