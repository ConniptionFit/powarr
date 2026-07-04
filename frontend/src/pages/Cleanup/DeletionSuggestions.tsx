import { useState, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Trash2, EyeOff, Eye, ChevronUp, ChevronDown, RefreshCw } from "lucide-react";
import { mediaApi, integrationsApi, fmtBytes, fmtDate, type MediaItem } from "../../lib/api";

function ScoreBadge({ score }: { score: number }) {
  const color =
    score >= 75 ? "bg-red-900/60 text-red-300" :
    score >= 50 ? "bg-yellow-900/60 text-yellow-300" :
    "bg-green-900/60 text-green-300";
  return <span className={`inline-block px-2 py-0.5 rounded text-xs font-bold ${color}`}>{score.toFixed(0)}</span>;
}

type SortKey = "score" | "file_size" | "watch_count" | "last_watched_at";

interface ShowGroup {
  parent_title: string;
  episodes: MediaItem[];
  total_size: number;
  total_watch_count: number;
  last_watched_at: string | null;
  avg_score: number;
  sonarr_id: number | null;
  ids: number[];
}

function groupByShow(items: MediaItem[]): ShowGroup[] {
  const map = new Map<string, ShowGroup>();
  for (const ep of items) {
    const key = ep.parent_title || ep.title;
    if (!map.has(key)) {
      map.set(key, {
        parent_title: key,
        episodes: [],
        total_size: 0,
        total_watch_count: 0,
        last_watched_at: null,
        avg_score: 0,
        sonarr_id: ep.sonarr_id,
        ids: [],
      });
    }
    const g = map.get(key)!;
    g.episodes.push(ep);
    g.total_size += ep.file_size;
    g.total_watch_count += ep.watch_count;
    g.ids.push(ep.id);
    if (ep.last_watched_at) {
      if (!g.last_watched_at || ep.last_watched_at > g.last_watched_at) {
        g.last_watched_at = ep.last_watched_at;
      }
    }
  }
  for (const g of map.values()) {
    g.avg_score = g.episodes.reduce((s, e) => s + e.score, 0) / g.episodes.length;
  }
  return Array.from(map.values());
}

export default function DeletionSuggestions() {
  const qc = useQueryClient();
  const [minScore, setMinScore] = useState(40);
  const [mediaType, setMediaType] = useState("");
  const [showMode, setShowMode] = useState<"episode" | "show">("show");
  const [sortBy, setSortBy] = useState<SortKey>("score");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [syncing, setSyncing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null); // id or "show:title"

  const fetchType = mediaType || undefined;
  const params: Record<string, string | number | boolean> = {
    min_score: minScore,
    sort_by: sortBy,
    order,
    limit: 500,
    // When show mode is active, always fetch episodes
    ...(showMode === "show" && !mediaType ? { media_type: "episode" } : {}),
    ...(fetchType ? { media_type: fetchType } : {}),
  };

  const { data: rawItems = [], isLoading } = useQuery({
    queryKey: ["media", params],
    queryFn: () => mediaApi.list(params),
  });

  const isShowMode = showMode === "show" && (!mediaType || mediaType === "episode");
  const showGroups = useMemo(() => {
    if (!isShowMode) return [];
    const episodes = rawItems.filter(i => i.media_type === "episode");
    const groups = groupByShow(episodes);
    if (sortBy === "score") groups.sort((a, b) => order === "desc" ? b.avg_score - a.avg_score : a.avg_score - b.avg_score);
    if (sortBy === "file_size") groups.sort((a, b) => order === "desc" ? b.total_size - a.total_size : a.total_size - b.total_size);
    if (sortBy === "watch_count") groups.sort((a, b) => order === "desc" ? b.total_watch_count - a.total_watch_count : a.total_watch_count - b.total_watch_count);
    return groups;
  }, [rawItems, isShowMode, sortBy, order]);

  const displayItems = isShowMode ? [] : rawItems;

  const deleteMut = useMutation({
    mutationFn: (id: number) => mediaApi.delete(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["media"] }); qc.invalidateQueries({ queryKey: ["stats"] }); setConfirmDelete(null); },
  });

  const deleteBatchMut = useMutation({
    mutationFn: (ids: number[]) => mediaApi.deleteBatch(ids),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["media"] }); qc.invalidateQueries({ queryKey: ["stats"] }); setConfirmDelete(null); },
  });

  const ignoreMut = useMutation({
    mutationFn: ({ id, ignored }: { id: number; ignored: boolean }) => mediaApi.ignore(id, ignored),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["media"] }),
  });

  const handleSync = async () => {
    setSyncing(true);
    try {
      const result = await integrationsApi.syncPlex();
      alert(`Synced ${result.synced} items from Plex.`);
      qc.invalidateQueries({ queryKey: ["media"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
    } catch (e: unknown) {
      alert(`Sync failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSyncing(false);
    }
  };

  const toggleSort = (key: SortKey) => {
    if (sortBy === key) setOrder(o => o === "desc" ? "asc" : "desc");
    else { setSortBy(key); setOrder("desc"); }
  };

  const SortIcon = ({ k }: { k: SortKey }) =>
    sortBy === k ? (order === "desc" ? <ChevronDown size={13} /> : <ChevronUp size={13} />) : null;

  const hasEpisodes = rawItems.some(i => i.media_type === "episode") || (!mediaType);

  return (
    <div>
      <div className="flex items-center justify-between mb-5">
        <p className="text-slate-400 text-sm">Deletion candidates sorted by score</p>
        <button
          onClick={handleSync}
          disabled={syncing}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-brand text-white hover:bg-brand-dark text-sm transition-colors disabled:opacity-50"
        >
          <RefreshCw size={15} className={syncing ? "animate-spin" : ""} />
          {syncing ? "Syncing…" : "Sync Plex"}
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-5 items-center">
        <div className="flex items-center gap-2">
          <label className="text-slate-400 text-sm">Min score</label>
          <input
            type="number"
            value={minScore}
            onChange={e => setMinScore(Number(e.target.value))}
            className="w-20 bg-surface-raised border border-purple-900/40 rounded px-2 py-1 text-sm text-white"
          />
        </div>
        <div className="flex items-center gap-2">
          <label className="text-slate-400 text-sm">Type</label>
          <select
            value={mediaType}
            onChange={e => { setMediaType(e.target.value); if (e.target.value && e.target.value !== "episode") setShowMode("episode"); }}
            className="bg-surface-raised border border-purple-900/40 rounded px-2 py-1 text-sm text-white"
          >
            <option value="">All</option>
            <option value="movie">Movies</option>
            <option value="episode">TV Episodes</option>
            <option value="track">Music</option>
          </select>
        </div>

        {/* Show/Episode toggle — only when TV is in scope */}
        {hasEpisodes && (!mediaType || mediaType === "episode") && (
          <div className="flex items-center rounded-lg overflow-hidden border border-purple-900/40 ml-1">
            <button
              onClick={() => setShowMode("show")}
              className={`px-3 py-1.5 text-sm transition-colors ${showMode === "show" ? "bg-brand text-white" : "bg-surface-raised text-slate-400 hover:text-white"}`}
            >
              By Show
            </button>
            <button
              onClick={() => setShowMode("episode")}
              className={`px-3 py-1.5 text-sm transition-colors ${showMode === "episode" ? "bg-brand text-white" : "bg-surface-raised text-slate-400 hover:text-white"}`}
            >
              By Episode
            </button>
          </div>
        )}
      </div>

      {isLoading ? (
        <p className="text-slate-400">Loading…</p>
      ) : (isShowMode ? showGroups : displayItems).length === 0 ? (
        <div className="bg-surface-raised rounded-xl border border-purple-900/30 p-10 text-center">
          <p className="text-slate-400">No candidates found. Try lowering the minimum score or sync Plex first.</p>
        </div>
      ) : (
        <div className="bg-surface-raised rounded-xl border border-purple-900/30 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="border-b border-purple-900/30 text-slate-400 text-xs uppercase tracking-wider">
              <tr>
                <th className="text-left px-4 py-3">{isShowMode ? "Show" : "Title"}</th>
                {!isShowMode && <th className="text-left px-4 py-3">Type</th>}
                <th className="text-left px-4 py-3 cursor-pointer hover:text-white select-none" onClick={() => toggleSort("score")}>
                  <span className="flex items-center gap-1">Score <SortIcon k="score" /></span>
                </th>
                <th className="text-left px-4 py-3 cursor-pointer hover:text-white select-none" onClick={() => toggleSort("file_size")}>
                  <span className="flex items-center gap-1">{isShowMode ? "Total Size" : "Size"} <SortIcon k="file_size" /></span>
                </th>
                <th className="text-left px-4 py-3 cursor-pointer hover:text-white select-none" onClick={() => toggleSort("watch_count")}>
                  <span className="flex items-center gap-1">Plays <SortIcon k="watch_count" /></span>
                </th>
                <th className="text-left px-4 py-3 cursor-pointer hover:text-white select-none" onClick={() => toggleSort("last_watched_at")}>
                  <span className="flex items-center gap-1">Last Watched <SortIcon k="last_watched_at" /></span>
                </th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-purple-900/20">
              {isShowMode
                ? showGroups.map(group => {
                    const key = `show:${group.parent_title}`;
                    return (
                      <tr key={group.parent_title} className="hover:bg-white/5 transition-colors">
                        <td className="px-4 py-3 text-white font-medium">
                          {group.parent_title}
                          <span className="text-slate-500 text-xs ml-2">{group.episodes.length} ep</span>
                        </td>
                        <td className="px-4 py-3"><ScoreBadge score={group.avg_score} /></td>
                        <td className="px-4 py-3 text-slate-300">{fmtBytes(group.total_size)}</td>
                        <td className="px-4 py-3 text-slate-300">{group.total_watch_count}</td>
                        <td className="px-4 py-3 text-slate-400">{fmtDate(group.last_watched_at)}</td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2 justify-end">
                            {confirmDelete === key ? (
                              <div className="flex gap-1">
                                <button onClick={() => deleteBatchMut.mutate(group.ids)} className="px-2 py-1 bg-red-700 hover:bg-red-600 text-white rounded text-xs">Confirm</button>
                                <button onClick={() => setConfirmDelete(null)} className="px-2 py-1 bg-surface-overlay hover:bg-white/10 text-slate-300 rounded text-xs">Cancel</button>
                              </div>
                            ) : (
                              <button onClick={() => setConfirmDelete(key)} title="Delete all episodes" className="p-1.5 rounded hover:bg-red-900/40 text-slate-400 hover:text-red-300 transition-colors">
                                <Trash2 size={15} />
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })
                : displayItems.map((item: MediaItem) => {
                    const key = String(item.id);
                    return (
                      <tr key={item.id} className="hover:bg-white/5 transition-colors">
                        <td className="px-4 py-3 text-white font-medium">
                          {item.parent_title && <span className="text-slate-500 text-xs block">{item.parent_title}</span>}
                          {item.title}
                          {item.year && <span className="text-slate-500 ml-1">({item.year})</span>}
                        </td>
                        <td className="px-4 py-3 text-slate-400 capitalize">{item.media_type}</td>
                        <td className="px-4 py-3"><ScoreBadge score={item.score} /></td>
                        <td className="px-4 py-3 text-slate-300">{fmtBytes(item.file_size)}</td>
                        <td className="px-4 py-3 text-slate-300">{item.watch_count}</td>
                        <td className="px-4 py-3 text-slate-400">{fmtDate(item.last_watched_at)}</td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2 justify-end">
                            <button onClick={() => ignoreMut.mutate({ id: item.id, ignored: !item.ignored })} title={item.ignored ? "Un-ignore" : "Ignore"} className="p-1.5 rounded hover:bg-white/10 text-slate-400 hover:text-white transition-colors">
                              {item.ignored ? <Eye size={15} /> : <EyeOff size={15} />}
                            </button>
                            {confirmDelete === key ? (
                              <div className="flex gap-1">
                                <button onClick={() => deleteMut.mutate(item.id)} className="px-2 py-1 bg-red-700 hover:bg-red-600 text-white rounded text-xs">Confirm</button>
                                <button onClick={() => setConfirmDelete(null)} className="px-2 py-1 bg-surface-overlay hover:bg-white/10 text-slate-300 rounded text-xs">Cancel</button>
                              </div>
                            ) : (
                              <button onClick={() => setConfirmDelete(key)} title="Delete" className="p-1.5 rounded hover:bg-red-900/40 text-slate-400 hover:text-red-300 transition-colors">
                                <Trash2 size={15} />
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
