import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Compass,
  Play,
  Check,
  X,
  Sparkles,
  Settings,
  ChevronDown,
  ChevronUp,
  History,
  UserPlus,
  Search,
  Zap,
  Network,
  Radio,
  Flame,
  CheckSquare,
  Square,
  AlertTriangle,
} from "lucide-react";
import { Link } from "react-router-dom";
import { req, fmtRelative, parseApiDate } from "../../lib/api";
import { usePersistedState } from "../../lib/usePersistedState";
import ArtistCard from "../../components/ArtistCard";
import ArtistPreviewButton from "../../components/ArtistPreviewButton";
import ScrollFadeX from "../../components/ScrollFadeX";
import ArtistSuggestionModal from "../../components/ArtistSuggestionModal";

interface Candidate {
  id: number;
  musicbrainz_id: string | null;
  artist_name: string;
  genres: string[];
  mood_tags: string[];
  era: string | null;
  source: string; // "centroid" | "centroid_recent" | "graph" | "ingest" | "centroid_mood_{slug}" (AD-19)
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
  is_low_metadata?: boolean;
}

// Sort options for the pending-candidates queue
type CandidateSort = "match" | "newest" | "name" | "connections";

function connectionCount(c: Candidate): number {
  return c.connection_count ?? Math.max(c.associated_seed_mbids.length, c.seed_artist_names.length);
}

function sortCandidates(list: Candidate[], sortBy: CandidateSort): Candidate[] {
  if (sortBy === "match") return list; // server order, left as-is
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

interface DiscoveryRun {
  id: number;
  run_type: string;
  started_at: string | null;
  finished_at: string | null;
  candidates_found: number;
  candidates_added: number;
  message: string | null;
}

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
  batch: (ids: number[], action: "accept" | "reject") =>
    req<{ results: any[] }>("/artist-discovery/batch", {
      method: "POST",
      body: JSON.stringify({ ids, action }),
    }),
};

function seedConnectionSuffix(c: Candidate | Pick<Candidate, "seed_artist_name" | "seed_artist_names" | "associated_seed_mbids">): string {
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

function whySuggested(c: Candidate | Pick<Candidate, "source" | "similarity_score" | "seed_artist_name" | "seed_artist_names" | "associated_seed_mbids">): string {
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

function getLaneBadge(c: Candidate) {
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

function getScorePill(c: Candidate) {
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

function CandidateCard({
  c,
  onAccept,
  onReject,
  onInspect,
  pending,
  selectable,
  selected,
  onToggleSelect,
}: {
  c: Candidate;
  onAccept: () => void;
  onReject: () => void;
  onInspect: () => void;
  pending: boolean;
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: () => void;
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
      scorePill={getScorePill(c)}
      laneBadge={getLaneBadge(c)}
      isLowMetadata={c.is_low_metadata}
      onInspect={onInspect}
      selectable={selectable}
      selected={selected}
      onToggleSelect={onToggleSelect}
      actions={
        <>
          <button
            onClick={onAccept}
            disabled={pending}
            title="Add to Lidarr"
            aria-label="Add to Lidarr"
            className="p-1.5 rounded-lg hover:bg-green-900/40 text-slate-400 hover:text-green-300 disabled:opacity-40 transition-colors"
          >
            <Check size={15} />
          </button>
          <button
            onClick={onReject}
            disabled={pending}
            title="Reject"
            aria-label="Reject"
            className="p-1.5 rounded-lg hover:bg-red-900/40 text-slate-400 hover:text-red-300 disabled:opacity-40 transition-colors"
          >
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
        <button
          onClick={() => setOpen((o) => !o)}
          className="flex items-center gap-2 text-white font-semibold text-sm uppercase tracking-wider"
        >
          <UserPlus size={16} /> Recently added
          {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
        {open && (
          <select
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            title="How many recent adds to show"
            className="bg-surface-raised border border-purple-900/40 rounded-lg px-2 py-1 text-xs text-slate-300"
          >
            <option value={10}>Last 10</option>
            <option value={25}>Last 25</option>
            <option value={50}>Last 50</option>
          </select>
        )}
      </div>
      {open &&
        (adds.length === 0 ? (
          <p className="text-slate-500 text-sm">
            No artists added yet — accept a discovery candidate or add one from Related Artists.
          </p>
        ) : (
          <div className="grid gap-2">
            {adds.map((a) => (
              <ArtistCard
                key={a.id}
                name={a.artist_name}
                yearsActive={a.years_active}
                imageUrl={a.image_url}
                bio={a.bio}
                genres={a.genres}
                subtitle={addReason(a)}
                actions={
                  <span
                    className="text-xs text-slate-500 whitespace-nowrap"
                    title={a.added_at ? parseApiDate(a.added_at).toLocaleString() : undefined}
                  >
                    {fmtRelative(a.added_at)}
                  </span>
                }
              />
            ))}
          </div>
        ))}
    </section>
  );
}

function RecentRuns() {
  const [open, setOpen] = useState(false);
  const { data: runs = [] } = useQuery({ queryKey: ["ad-runs"], queryFn: api.runs, enabled: open });

  return (
    <section className="mt-8">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 text-white font-semibold text-sm uppercase tracking-wider mb-3"
      >
        <History size={16} /> Recent runs
        {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      </button>
      {open &&
        (runs.length === 0 ? (
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
                {runs.map((r) => (
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
        ))}
    </section>
  );
}

export default function ArtistDiscovery() {
  const qc = useQueryClient();
  const { data: settings } = useQuery({ queryKey: ["ad-settings"], queryFn: api.settings });
  const { data: stats } = useQuery({ queryKey: ["ad-stats"], queryFn: api.stats });
  const { data: candidates = [] } = useQuery({
    queryKey: ["ad-candidates"],
    queryFn: () => api.candidates("pending"),
  });

  const [msg, setMsg] = useState<string | null>(null);
  const [candidateSort, setCandidateSort] = usePersistedState<CandidateSort>(
    "powarr.artistDiscovery.candidateSort",
    "match"
  );
  const [searchQuery, setSearchQuery] = useState("");
  const [laneFilter, setLaneFilter] = usePersistedState<string>(
    "powarr.artistDiscovery.laneFilter",
    "all"
  );
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [inspectCandidate, setInspectCandidate] = useState<Candidate | null>(null);

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

  const batchMut = useMutation({
    mutationFn: ({ ids, action }: { ids: number[]; action: "accept" | "reject" }) =>
      api.batch(ids, action),
    onSuccess: (r) => {
      setSelectedIds(new Set());
      setMsg(`Processed ${r.results.length} candidate(s)`);
      qc.invalidateQueries({ queryKey: ["ad-candidates"] });
      qc.invalidateQueries({ queryKey: ["ad-stats"] });
      qc.invalidateQueries({ queryKey: ["ad-recent-adds"] });
    },
    onError: (e: Error) => setMsg(e.message),
  });

  // Live SSE updates
  useEffect(() => {
    const es = new EventSource("/api/v1/imports/events");
    es.onmessage = (ev) => {
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
      } catch {
        /* keepalive */
      }
    };
    return () => es.close();
  }, [qc]);

  const enabled = !!settings?.enabled;

  // Filter candidates
  const filteredCandidates = candidates.filter((c) => {
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase().trim();
      const matchName = c.artist_name.toLowerCase().includes(q);
      const matchGenre = c.genres.some((g) => g.toLowerCase().includes(q));
      const matchSeed = c.seed_artist_names.some((s) => s.toLowerCase().includes(q));
      if (!matchName && !matchGenre && !matchSeed) return false;
    }
    if (laneFilter === "centroid") return c.source === "centroid";
    if (laneFilter === "recent") return c.source === "centroid_recent";
    if (laneFilter === "graph_ingest") return c.source === "graph" || c.source === "ingest";
    if (laneFilter === "low_metadata") return !!c.is_low_metadata;
    return true;
  });

  const sortedCandidates = sortCandidates(filteredCandidates, candidateSort);

  // Selection helpers
  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const allVisibleSelected =
    sortedCandidates.length > 0 &&
    sortedCandidates.every((c) => selectedIds.has(c.id));

  const toggleSelectAllVisible = () => {
    if (allVisibleSelected) {
      setSelectedIds((prev) => {
        const next = new Set(prev);
        sortedCandidates.forEach((c) => next.delete(c.id));
        return next;
      });
    } else {
      setSelectedIds((prev) => {
        const next = new Set(prev);
        sortedCandidates.forEach((c) => next.add(c.id));
        return next;
      });
    }
  };

  // Counts for filter chips
  const countCentroid = candidates.filter((c) => c.source === "centroid").length;
  const countRecent = candidates.filter((c) => c.source === "centroid_recent").length;
  const countGraphIngest = candidates.filter((c) => c.source === "graph" || c.source === "ingest").length;
  const countLowMeta = candidates.filter((c) => c.is_low_metadata).length;

  return (
    <div className="p-4 sm:p-8 max-w-4xl pb-24">
      {/* Page Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Compass className="text-brand-light" size={24} />
          <div>
            <h1 className="text-2xl font-bold text-white">Artist Discovery</h1>
            <p className="text-slate-400 text-sm">
              Last.fm taste mapping → Qdrant similarity + related-artist graph → Lidarr
            </p>
          </div>
        </div>
        <Link
          to="/settings/music"
          title="Configure"
          className="p-2 rounded-xl text-slate-400 hover:text-white hover:bg-surface-raised transition-colors border border-purple-900/30"
        >
          <Settings size={18} />
        </Link>
      </div>

      {!enabled && (
        <div className="mb-6 bg-amber-900/20 border border-amber-800/40 rounded-xl px-4 py-3 text-sm text-amber-200 flex items-center justify-between">
          <span>Artist Discovery is disabled.</span>
          <Link to="/settings/music" className="underline hover:text-white font-medium">
            Configure it →
          </Link>
        </div>
      )}

      {/* KPI Stats Grid */}
      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
          <div
            className="bg-surface-raised border border-purple-900/30 rounded-xl p-3.5 shadow-sm"
            title="Total artists in Powarr's shared taste vector space — your monitored artists plus every related artist ever discovered. Never shrinks (points are soft-deleted, not removed)."
          >
            <p className="text-xs text-slate-500 font-medium">Taste model size</p>
            <p className="text-white text-lg font-bold mt-0.5">{stats.tracked_artists ?? "—"}</p>
          </div>
          <div className="bg-surface-raised border border-purple-900/30 rounded-xl p-3.5 shadow-sm">
            <p className="text-xs text-slate-500 font-medium">Pending review</p>
            <p className="text-white text-lg font-bold mt-0.5">{stats.pending}</p>
          </div>
          <div className="bg-surface-raised border border-purple-900/30 rounded-xl p-3.5 shadow-sm">
            <p className="text-xs text-slate-500 font-medium">Added to Lidarr</p>
            <p className="text-white text-lg font-bold mt-0.5">{stats.accepted}</p>
          </div>
          <div className="bg-surface-raised border border-purple-900/30 rounded-xl p-3.5 shadow-sm">
            <p className="text-xs text-slate-500 font-medium">Last run</p>
            <p className="text-white text-sm font-semibold mt-1 truncate">
              {fmtRelative(stats.last_run_at)}
            </p>
          </div>
        </div>
      )}

      {/* Action Bar */}
      <div className="flex flex-wrap items-center gap-3 mb-6">
        <button
          onClick={() => runMut.mutate()}
          disabled={runMut.isPending || !enabled}
          className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-brand text-white text-sm font-medium hover:bg-brand-light shadow-md shadow-purple-950/40 disabled:opacity-50 transition-all"
        >
          <Play size={14} /> {runMut.isPending ? "Running discovery cycle…" : "Run Discovery Now"}
        </button>
        {msg && <span className="text-xs text-slate-300 font-medium">{msg}</span>}
      </div>

      {/* Queue Section */}
      <section className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-white font-bold text-sm uppercase tracking-wider flex items-center gap-2">
            <Sparkles size={16} className="text-purple-400" /> Pending candidates ({candidates.length})
          </h2>

          <div className="flex flex-wrap items-center gap-2">
            {candidates.length > 0 && (
              <button
                onClick={toggleSelectAllVisible}
                className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white px-2.5 py-1.5 rounded-lg border border-purple-900/40 bg-surface-raised transition-colors"
                title={allVisibleSelected ? "Deselect all visible" : "Select all visible"}
              >
                {allVisibleSelected ? <CheckSquare size={14} className="text-brand" /> : <Square size={14} />}
                <span>Select All</span>
              </button>
            )}

            {candidates.length > 1 && (
              <select
                value={candidateSort}
                onChange={(e) => setCandidateSort(e.target.value as CandidateSort)}
                title="Sort pending candidates"
                className="bg-surface-raised border border-purple-900/40 rounded-xl px-2.5 py-1.5 text-xs text-slate-300 focus:outline-none focus:border-brand"
              >
                <option value="match">Best match</option>
                <option value="connections">Most connections</option>
                <option value="newest">Newest first</option>
                <option value="name">Name A–Z</option>
              </select>
            )}
          </div>
        </div>

        {/* Search & Filter Toolbar */}
        {candidates.length > 0 && (
          <div className="space-y-2.5">
            <div className="relative">
              <Search size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-500" />
              <input
                type="text"
                placeholder="Search candidates by artist name, genre, or seed..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-10 pr-4 py-2 bg-surface-raised border border-purple-900/30 rounded-xl text-xs text-white placeholder:text-slate-500 focus:outline-none focus:border-brand transition-colors"
              />
              {searchQuery && (
                <button
                  onClick={() => setSearchQuery("")}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-white text-xs p-1"
                >
                  <X size={13} />
                </button>
              )}
            </div>

            {/* Lane Filter Chips */}
            <div className="flex flex-wrap items-center gap-1.5 text-xs">
              <button
                onClick={() => setLaneFilter("all")}
                className={`px-3 py-1 rounded-full border transition-all ${
                  laneFilter === "all"
                    ? "bg-brand text-white border-brand shadow-sm font-semibold"
                    : "bg-surface text-slate-400 border-purple-900/30 hover:border-purple-800"
                }`}
              >
                All ({candidates.length})
              </button>

              {countCentroid > 0 && (
                <button
                  onClick={() => setLaneFilter("centroid")}
                  className={`px-3 py-1 rounded-full border flex items-center gap-1.5 transition-all ${
                    laneFilter === "centroid"
                      ? "bg-purple-900/70 text-purple-200 border-purple-600 font-semibold"
                      : "bg-surface text-slate-400 border-purple-900/30 hover:border-purple-800"
                  }`}
                >
                  <Compass size={12} className="text-purple-400" />
                  Taste Centroid ({countCentroid})
                </button>
              )}

              {countRecent > 0 && (
                <button
                  onClick={() => setLaneFilter("recent")}
                  className={`px-3 py-1 rounded-full border flex items-center gap-1.5 transition-all ${
                    laneFilter === "recent"
                      ? "bg-amber-900/70 text-amber-200 border-amber-600 font-semibold"
                      : "bg-surface text-slate-400 border-purple-900/30 hover:border-purple-800"
                  }`}
                >
                  <Zap size={12} className="text-amber-400" />
                  Recent Taste ({countRecent})
                </button>
              )}

              {countGraphIngest > 0 && (
                <button
                  onClick={() => setLaneFilter("graph_ingest")}
                  className={`px-3 py-1 rounded-full border flex items-center gap-1.5 transition-all ${
                    laneFilter === "graph_ingest"
                      ? "bg-cyan-900/70 text-cyan-200 border-cyan-600 font-semibold"
                      : "bg-surface text-slate-400 border-purple-900/30 hover:border-purple-800"
                  }`}
                >
                  <Network size={12} className="text-cyan-400" />
                  Graph & Ingest ({countGraphIngest})
                </button>
              )}

              {countLowMeta > 0 && (
                <button
                  onClick={() => setLaneFilter("low_metadata")}
                  className={`px-3 py-1 rounded-full border flex items-center gap-1.5 transition-all ${
                    laneFilter === "low_metadata"
                      ? "bg-amber-950/80 text-amber-300 border-amber-600 font-semibold"
                      : "bg-surface text-amber-400/80 border-amber-900/40 hover:border-amber-700"
                  }`}
                >
                  <AlertTriangle size={12} />
                  Low Metadata ({countLowMeta})
                </button>
              )}
            </div>
          </div>
        )}

        {/* Candidate List */}
        {candidates.length === 0 ? (
          <p className="text-slate-500 text-sm">
            No pending candidates — configure Last.fm/Qdrant/Lidarr and Run Discovery.
          </p>
        ) : sortedCandidates.length === 0 ? (
          <div className="p-8 text-center bg-surface-raised/40 border border-purple-900/20 rounded-xl space-y-2">
            <p className="text-slate-400 text-sm">No candidates match the current filter or search query.</p>
            <button
              onClick={() => {
                setSearchQuery("");
                setLaneFilter("all");
              }}
              className="text-xs text-brand-light hover:underline font-medium"
            >
              Reset filters
            </button>
          </div>
        ) : (
          <div className="grid gap-2.5">
            {sortedCandidates.map((c) => (
              <CandidateCard
                key={c.id}
                c={c}
                pending={actMut.isPending || batchMut.isPending}
                selectable={true}
                selected={selectedIds.has(c.id)}
                onToggleSelect={() => toggleSelect(c.id)}
                onInspect={() => setInspectCandidate(c)}
                onAccept={() => actMut.mutate({ id: c.id, action: "accept" })}
                onReject={() => actMut.mutate({ id: c.id, action: "reject" })}
              />
            ))}
          </div>
        )}
      </section>

      {/* Floating Sticky Batch Action Bar */}
      {selectedIds.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 bg-[#141424]/95 border border-purple-700/60 shadow-2xl rounded-2xl px-5 py-3.5 flex items-center gap-4 backdrop-blur-md animate-in fade-in slide-in-from-bottom-3 duration-200">
          <span className="text-xs font-semibold text-white">
            {selectedIds.size} artist{selectedIds.size === 1 ? "" : "s"} selected
          </span>
          <button
            onClick={() => setSelectedIds(new Set())}
            className="text-xs text-slate-400 hover:text-white underline transition-colors"
          >
            Clear
          </button>
          <div className="h-4 w-px bg-purple-900/50" />
          <button
            onClick={() =>
              batchMut.mutate({ ids: Array.from(selectedIds), action: "reject" })
            }
            disabled={batchMut.isPending}
            className="px-3.5 py-1.5 rounded-xl text-xs font-medium text-red-300 bg-red-950/50 border border-red-800/50 hover:bg-red-900/60 disabled:opacity-50 transition-colors flex items-center gap-1.5"
          >
            <X size={14} /> Reject Selected
          </button>
          <button
            onClick={() =>
              batchMut.mutate({ ids: Array.from(selectedIds), action: "accept" })
            }
            disabled={batchMut.isPending}
            className="px-4 py-1.5 rounded-xl text-xs font-medium text-white bg-brand hover:bg-brand-light shadow-lg shadow-purple-950/60 disabled:opacity-50 transition-colors flex items-center gap-1.5"
          >
            <Check size={14} /> Accept Selected
          </button>
        </div>
      )}

      {/* Suggestion Insights Modal */}
      {inspectCandidate && (
        <ArtistSuggestionModal
          candidateId={inspectCandidate.id}
          artistName={inspectCandidate.artist_name}
          onClose={() => setInspectCandidate(null)}
          onAccept={() => actMut.mutate({ id: inspectCandidate.id, action: "accept" })}
          onReject={() => actMut.mutate({ id: inspectCandidate.id, action: "reject" })}
          actionPending={actMut.isPending}
        />
      )}

      <RecentlyAdded />

      <RecentRuns />
    </div>
  );
}

