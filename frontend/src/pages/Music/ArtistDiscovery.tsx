import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Compass, Play, Check, X, Sparkles, Settings, Search, Zap, Network, CheckSquare, Square, AlertTriangle } from "lucide-react";
import { Link } from "react-router-dom";
import { fmtRelative } from "../../lib/api";
import { usePersistedState } from "../../lib/usePersistedState";
import { subscribeEvents } from "../../lib/eventBus";
import ArtistSuggestionModal from "../../components/ArtistSuggestionModal";
import { useToast } from "../../context/ToastContext";
import { type Candidate, type CandidateSort, sortCandidates, type Stats, api } from "./artist-discovery/shared";
import { CandidateCard } from "./artist-discovery/CandidateCard";
import { RecentlyAdded, RecentRuns } from "./artist-discovery/RecentPanels";
import { PreviewButton, StatusPanel } from "./artist-discovery/DiscoveryTools";
import { DiscoverView } from "./artist-discovery/DiscoverView";
import { InsightsView } from "./artist-discovery/InsightsView";

type Tab = "discover" | "queue" | "insights";

export default function ArtistDiscovery() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data: settings } = useQuery({ queryKey: ["ad-settings"], queryFn: api.settings });
  const { data: stats } = useQuery({ queryKey: ["ad-stats"], queryFn: api.stats });
  const { data: candidates = [] } = useQuery({
    queryKey: ["ad-candidates"],
    queryFn: () => api.candidates("pending"),
  });

  const [msg, setMsg] = useState<string | null>(null);
  const [tab, setTab] = usePersistedState<Tab>("powarr.artistDiscovery.tab", "discover");
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
      qc.invalidateQueries({ queryKey: ["ad-shelves"] });
    },
    onError: (e: Error) => setMsg(e.message),
  });

  const actMut = useMutation({
    mutationFn: ({ id, action }: { id: number; action: "accept" | "reject" }) =>
      action === "accept" ? api.accept(id) : api.reject(id),
    onSuccess: (r, v) => {
      if (v.action === "reject") {
        toast.undoable("Candidate rejected", () => {
          api.unreject(v.id)
            .then(() => {
              qc.invalidateQueries({ queryKey: ["ad-candidates"] });
              qc.invalidateQueries({ queryKey: ["ad-stats"] });
            })
            .catch((e: Error) => toast.error(e.message));
        });
      } else {
        setMsg(r.message);
      }
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
    return subscribeEvents((data) => {
      if (data?.type === "task_update" && data.task?.kind === "artist_discovery") {
        qc.invalidateQueries({ queryKey: ["ad-stats"] });
        qc.invalidateQueries({ queryKey: ["ad-candidates"] });
        qc.invalidateQueries({ queryKey: ["ad-runs"] });
        if (data.task.status === "done" || data.task.status === "failed") {
          setMsg(data.task.message || null);
        }
      }
    });
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
    if (laneFilter === "direct") return c.source === "direct";
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
  const countDirect = candidates.filter((c) => c.source === "direct").length;
  const countLowMeta = candidates.filter((c) => c.is_low_metadata).length;

  return (
    <div className="p-4 sm:p-8 max-w-5xl pb-24">
      {/* Page Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Compass className="text-brand-light" size={24} />
          <div>
            <h1 className="text-2xl font-bold text-white">Artist Discovery</h1>
            <p className="text-slate-400 text-sm">
              Your listening (Last.fm, Plex, ListenBrainz) → similar artists, scored → shelves, review queue or Lidarr
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

      <StatusPanel />

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

      {/* Tabs */}
      <div className="flex gap-1 mb-6 border-b border-purple-900/30" role="tablist">
        {([["discover", "Discover"], ["queue", `Review queue (${candidates.length})`], ["insights", "Insights"]] as [Tab, string][]).map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
            className={`px-4 py-2 text-sm font-medium -mb-px border-b-2 transition-colors ${
              tab === id ? "text-white border-brand" : "text-slate-400 border-transparent hover:text-white"}`}>
            {label}
          </button>
        ))}
      </div>

      {msg && tab !== "queue" && <p className="text-xs text-slate-300 font-medium mb-4">{msg}</p>}
      {tab === "discover" && <DiscoverView enabled={enabled} onRun={() => runMut.mutate()} running={runMut.isPending} />}
      {tab === "insights" && <InsightsView />}

      {tab === "queue" && (<>
      {/* Action Bar */}
      <div className="flex flex-wrap items-center gap-3 mb-6">
        <button
          onClick={() => runMut.mutate()}
          disabled={runMut.isPending || !enabled}
          className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-brand text-white text-sm font-medium hover:bg-brand-light shadow-md shadow-purple-950/40 disabled:opacity-50 transition-all"
        >
          <Play size={14} /> {runMut.isPending ? "Running discovery cycle…" : "Run Discovery Now"}
        </button>
        <PreviewButton disabled={!enabled} />
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

              {countDirect > 0 && (
                <button
                  onClick={() => setLaneFilter("direct")}
                  className={`px-3 py-1 rounded-full border flex items-center gap-1.5 transition-all ${
                    laneFilter === "direct"
                      ? "bg-green-900/70 text-green-200 border-green-600 font-semibold"
                      : "bg-surface text-slate-400 border-purple-900/30 hover:border-purple-800"
                  }`}
                >
                  <Sparkles size={12} className="text-green-400" />
                  Direct ({countDirect})
                </button>
              )}

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
            No pending candidates. Try Preview (dry run) to see what a run would do and why.
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
      </>)}

      {/* Floating Sticky Batch Action Bar */}
      {tab === "queue" && selectedIds.size > 0 && (
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

