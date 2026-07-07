import { useState, useEffect, useMemo, useRef, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, X, RefreshCw, Bot, ChevronDown, ChevronRight, ChevronUp, Trash2, Search, Columns3, Sparkles, Lightbulb, Brain } from "lucide-react";
import { importsApi, fmtDate, fmtBytes, type FailedImport } from "../../lib/api";
import ClampedText from "../../components/ClampedText";

const APP_COLORS: Record<string, string> = {
  sonarr: "bg-teal-600",
  radarr: "bg-amber-600",
  lidarr: "bg-pink-600",
  readarr: "bg-orange-700",
};

const STATUS_META: Record<string, { label: string; cls: string }> = {
  suggested: { label: "Suggested", cls: "bg-yellow-900/60 text-yellow-300" },
  auto_resolved: { label: "Auto-resolved", cls: "bg-green-900/60 text-green-300" },
  accepted: { label: "Accepted", cls: "bg-green-900/60 text-green-300" },
  rejected: { label: "Rejected", cls: "bg-red-900/60 text-red-300" },
  closed_external: { label: "Self-resolved", cls: "bg-surface-overlay text-slate-300" },
  resolve_failed: { label: "Push failed", cls: "bg-red-900/60 text-red-300" },
  orphan_pending: { label: "Confirm orphan", cls: "bg-orange-900/60 text-orange-300" },
  orphaned: { label: "Orphaned", cls: "bg-surface-overlay text-slate-300" },
};

function ConfidenceBadge({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color =
    value >= 0.9 ? "bg-green-900/60 text-green-300" :
    value >= 0.7 ? "bg-yellow-900/60 text-yellow-300" :
    "bg-red-900/60 text-red-300";
  return <span className={`inline-block px-2 py-0.5 rounded text-xs font-bold ${color}`}>{pct}%</span>;
}

// --- Column system: visibility + widths persisted per-browser (localStorage) ---

type ColKey = "source" | "release" | "matched" | "match_pct" | "match_notes" | "llm_pct" | "llm_notes" | "status" | "detected";

interface ColDef {
  key: ColKey;
  label: string;
  width: number; // default px
  sortField?: keyof FailedImport;
}

const COLUMNS: ColDef[] = [
  { key: "source", label: "Source", width: 90, sortField: "source_app" },
  { key: "release", label: "Release", width: 340, sortField: "raw_title" },
  { key: "matched", label: "Matched To", width: 240, sortField: "matched_title" },
  { key: "match_pct", label: "Match", width: 90, sortField: "heuristic_confidence" },
  { key: "match_notes", label: "Match Notes", width: 260, sortField: "match_rationale" },
  { key: "llm_pct", label: "LLM", width: 90, sortField: "llm_confidence" },
  { key: "llm_notes", label: "LLM Notes", width: 260, sortField: "llm_rationale" },
  { key: "status", label: "Status", width: 120, sortField: "status" },
  { key: "detected", label: "Detected", width: 110, sortField: "created_at" },
];

const LS_VISIBLE = "powarr.failedImports.visibleCols";
const LS_WIDTHS = "powarr.failedImports.colWidths";
const LS_MATCH_NOTES_SEEN = "powarr.failedImports.matchNotesIntroduced"; // v0.5.0 one-time column surfacing

function loadVisible(): Set<ColKey> {
  try {
    const raw = localStorage.getItem(LS_VISIBLE);
    if (raw) {
      const set = new Set(JSON.parse(raw) as ColKey[]);
      // Layouts saved before v0.5.0 predate the Match Notes column — show it once;
      // hiding it again afterwards sticks.
      if (!localStorage.getItem(LS_MATCH_NOTES_SEEN)) {
        set.add("match_notes");
        localStorage.setItem(LS_MATCH_NOTES_SEEN, "1");
      }
      return set;
    }
  } catch { /* fall through */ }
  return new Set(COLUMNS.map(c => c.key));
}

function loadWidths(): Record<string, number> {
  try {
    const raw = localStorage.getItem(LS_WIDTHS);
    if (raw) return JSON.parse(raw);
  } catch { /* fall through */ }
  return {};
}

function MatchOverride({ item, onDone }: { item: FailedImport; onDone: () => void }) {
  const [query, setQuery] = useState(item.raw_title);
  const [candidates, setCandidates] = useState<Array<{ id: number; title: string; score: number }> | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const search = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await importsApi.candidates(item.id, query);
      setCandidates(r.candidates);
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const use = async (c: { id: number; title: string }) => {
    setBusy(true);
    try {
      await importsApi.setMatch(item.id, c.id, c.title);
      onDone();
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <div className="px-4 py-2 border-t border-purple-900/10">
      <div className="flex items-center gap-2">
        <span className="text-xs text-slate-500 uppercase tracking-wider">Change match:</span>
        <input
          type="text"
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === "Enter" && search()}
          className="flex-1 max-w-md bg-surface border border-purple-900/40 rounded px-2 py-1 text-xs text-white"
        />
        <button onClick={search} disabled={busy}
                className="flex items-center gap-1 px-2 py-1 rounded bg-surface-overlay hover:bg-white/10 text-slate-300 text-xs transition-colors disabled:opacity-40">
          <Search size={11} /> Search Library
        </button>
        {msg && <span className="text-xs text-red-400">{msg}</span>}
      </div>
      {candidates && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {candidates.filter(c => c.score > 0).map(c => (
            <button key={c.id} onClick={() => use(c)} disabled={busy}
                    className="px-2 py-1 rounded bg-brand/10 border border-brand/30 text-brand-light text-xs hover:bg-brand/20 transition-colors disabled:opacity-40">
              {c.title} <span className="opacity-60">{Math.round(c.score * 100)}%</span>
            </button>
          ))}
          {candidates.filter(c => c.score > 0).length === 0 && (
            <span className="text-xs text-slate-500">No candidates found — try a shorter query.</span>
          )}
        </div>
      )}
    </div>
  );
}

interface EpisodeOption { id: number; season: number; episode: number; title: string; }

// Type-to-filter episode combobox anchored below the Mapped To cell it was
// opened from. Defaults its scroll position to the file's currently-mapped
// episode so the common case (nudging a wrong guess) doesn't require typing.
function EpisodePicker({ importId, currentSeason, currentEpisode, onSelect, onClose }: {
  importId: number;
  currentSeason: number | null;
  currentEpisode: number | null;
  onSelect: (ep: EpisodeOption) => void;
  onClose: () => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["episode-options", importId],
    queryFn: () => importsApi.episodeOptions(importId),
    staleTime: 5 * 60_000,
  });
  const [query, setQuery] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const activeRef = useRef<HTMLButtonElement>(null);
  const episodes = data?.episodes ?? [];
  const q = query.trim().toLowerCase().replace(/\s+/g, "");
  const filtered = q
    ? episodes.filter(e =>
        `s${e.season.toString().padStart(2, "0")}e${e.episode.toString().padStart(2, "0")}`.includes(q) ||
        String(e.episode).includes(q) ||
        e.title.toLowerCase().includes(query.trim().toLowerCase()))
    : episodes;

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "center" });
  }, [data]);

  useEffect(() => {
    const onMouseDown = (ev: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(ev.target as Node)) onClose();
    };
    window.addEventListener("mousedown", onMouseDown);
    return () => window.removeEventListener("mousedown", onMouseDown);
  }, [onClose]);

  return (
    <div ref={containerRef} className="absolute z-30 mt-1 w-72 bg-surface-raised border border-purple-900/40 rounded-lg shadow-xl">
      <input
        autoFocus
        type="text"
        value={query}
        onChange={e => setQuery(e.target.value)}
        onKeyDown={e => e.key === "Escape" && onClose()}
        placeholder="Type to filter episodes…"
        className="w-full px-2 py-1.5 bg-surface border-b border-purple-900/40 text-xs text-white focus:outline-none rounded-t-lg"
      />
      <div className="max-h-56 overflow-y-auto py-1">
        {isLoading && <p className="text-xs text-slate-500 px-3 py-2">Loading episodes…</p>}
        {data?.message && <p className="text-xs text-slate-500 px-3 py-2">{data.message}</p>}
        {!isLoading && !data?.message && filtered.length === 0 && (
          <p className="text-xs text-slate-500 px-3 py-2">No matches</p>
        )}
        {filtered.map(e => {
          const isCurrent = e.season === currentSeason && e.episode === currentEpisode;
          return (
            <button
              key={e.id}
              ref={isCurrent ? activeRef : undefined}
              onClick={() => onSelect(e)}
              className={`block w-full text-left px-3 py-1.5 text-xs hover:bg-white/10 transition-colors ${
                isCurrent ? "bg-brand/10 text-brand-light" : "text-slate-300"
              }`}
            >
              S{e.season.toString().padStart(2, "0")}E{e.episode.toString().padStart(2, "0")}
              {e.title && <span className="text-slate-500"> — {e.title}</span>}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function FileDetails({ importId, sourceApp, matchedId }: { importId: number; sourceApp: string; matchedId: number | null }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["import-files", importId],
    queryFn: () => importsApi.files(importId),
    staleTime: 60_000,
  });
  const [editingPath, setEditingPath] = useState<string | null>(null);

  const mappingMut = useMutation({
    mutationFn: ({ path, ep }: { path: string; ep: EpisodeOption }) =>
      importsApi.updateFileMapping(importId, path, ep.id, ep.season, ep.episode, ep.title),
    onSuccess: () => {
      setEditingPath(null);
      qc.invalidateQueries({ queryKey: ["import-files", importId] });
      qc.invalidateQueries({ queryKey: ["imports"] });
    },
  });

  const editable = sourceApp === "sonarr" && !!matchedId;

  if (isLoading) return <p className="text-slate-500 text-xs px-4 py-2">Loading file details…</p>;
  if (!data || data.files.length === 0)
    return <p className="text-slate-500 text-xs px-4 py-2">{data?.message ?? "No file details available"}</p>;

  const parseCurrent = (detail: string): { season: number | null; episode: number | null } => {
    const m = detail.match(/S(\d+)E(\d+)/i);
    return m ? { season: Number(m[1]), episode: Number(m[2]) } : { season: null, episode: null };
  };

  return (
    <table className="w-full text-xs">
      <thead className="text-slate-500 uppercase tracking-wider">
        <tr>
          <th className="text-left px-4 py-1.5">File</th>
          <th className="text-left px-4 py-1.5">Size</th>
          <th className="text-left px-4 py-1.5">Quality</th>
          <th className="text-left px-4 py-1.5">Mapped To</th>
          <th className="text-left px-4 py-1.5">Rejections</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-purple-900/10 text-slate-400">
        {data.files.map((f, i) => {
          const current = parseCurrent(f.detail);
          const isEditing = editable && !!f.raw_path && editingPath === f.raw_path;
          const label = f.mapped_to ? `${f.mapped_to}${f.detail ? ` (${f.detail})` : ""}` : "unmapped";
          return (
            <tr key={i}>
              <td className="px-4 py-1.5 max-w-xs truncate" title={f.path ?? ""}>{f.path ?? "—"}</td>
              <td className="px-4 py-1.5">{f.size ? fmtBytes(f.size) : "—"}</td>
              <td className="px-4 py-1.5">{f.quality ?? "—"}</td>
              <td className="px-4 py-1.5 relative">
                {editable && f.raw_path ? (
                  <button
                    onClick={() => setEditingPath(isEditing ? null : (f.raw_path as string))}
                    title="Click to adjust the episode mapping for this file"
                    className="text-left hover:text-brand-light transition-colors underline decoration-dotted underline-offset-2"
                  >
                    {label}
                  </button>
                ) : (
                  <span>{label}</span>
                )}
                {f.overridden && (
                  <span className="ml-1.5 text-green-400 text-[10px] font-bold uppercase align-middle">Overridden</span>
                )}
                {isEditing && f.raw_path && (
                  <EpisodePicker
                    importId={importId}
                    currentSeason={current.season}
                    currentEpisode={current.episode}
                    onSelect={ep => mappingMut.mutate({ path: f.raw_path as string, ep })}
                    onClose={() => setEditingPath(null)}
                  />
                )}
              </td>
              <td className="px-4 py-1.5 max-w-xs truncate" title={f.rejections.join("; ")}>
                {f.rejections.length ? f.rejections.join("; ") : "—"}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

const FILTERS = ["suggested", "resolve_failed", "orphan_pending", "auto_resolved", "accepted", "rejected", "orphaned", ""] as const;

export default function FailedImports() {
  const qc = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<string>("suggested");
  const [scanning, setScanning] = useState(false);
  const [confirmAccept, setConfirmAccept] = useState<number | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [expanded, setExpanded] = useState<number | null>(null);

  // table view state (persisted per-browser)
  const [visibleCols, setVisibleCols] = useState<Set<ColKey>>(loadVisible);
  const [widths, setWidths] = useState<Record<string, number>>(loadWidths);
  const [showColMenu, setShowColMenu] = useState(false);
  const [sortBy, setSortBy] = useState<keyof FailedImport>("created_at");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [packReviewLoading, setPackReviewLoading] = useState(new Set<number>());
  const [packReviewResults, setPackReviewResults] = useState<Record<string, Array<{ file: string; season: number; episode: number; confidence: string; reason: string }>>>({});
  const [downgradeOnly, setDowngradeOnly] = useState(false);
  const [suspiciousOnly, setSuspiciousOnly] = useState(false);
  const resizing = useRef<{ key: string; startX: number; startW: number } | null>(null);

  useEffect(() => {
    localStorage.setItem(LS_VISIBLE, JSON.stringify([...visibleCols]));
  }, [visibleCols]);

  const persistWidths = useCallback((w: Record<string, number>) => {
    localStorage.setItem(LS_WIDTHS, JSON.stringify(w));
  }, []);

  const startResize = (key: string, defaultW: number) => (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    resizing.current = { key, startX: e.clientX, startW: widths[key] ?? defaultW };
    const onMove = (ev: MouseEvent) => {
      if (!resizing.current) return;
      const delta = ev.clientX - resizing.current.startX;
      const w = Math.max(60, resizing.current.startW + delta);
      setWidths(prev => ({ ...prev, [resizing.current!.key]: w }));
    };
    const onUp = () => {
      resizing.current = null;
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      setWidths(prev => { persistWidths(prev); return prev; });
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const { data: items = [], isLoading } = useQuery({
    queryKey: ["imports", statusFilter],
    queryFn: () => importsApi.list(statusFilter || undefined),
  });

  const { data: stats } = useQuery({
    queryKey: ["import-stats"],
    queryFn: importsApi.stats,
  });

  const sortedItems = useMemo(() => {
    let arr = items;
    if (downgradeOnly) arr = arr.filter(i => i.quality_downgrade);
    if (suspiciousOnly) arr = arr.filter(i => i.suspicious_files);
    arr = [...arr];
    arr.sort((a, b) => {
      const av = a[sortBy], bv = b[sortBy];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      const cmp = typeof av === "number" && typeof bv === "number"
        ? av - bv
        : String(av).localeCompare(String(bv));
      return sortDir === "asc" ? cmp : -cmp;
    });
    return arr;
  }, [items, sortBy, sortDir, downgradeOnly, suspiciousOnly]);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["imports"] });
    qc.invalidateQueries({ queryKey: ["import-stats"] });
    setSelected(new Set());
  };

  // Live updates: poller scans + on-demand LLM runs both publish SSE events
  useEffect(() => {
    const es = new EventSource("/api/v1/imports/events");
    es.onmessage = ev => {
      qc.invalidateQueries({ queryKey: ["imports"] });
      qc.invalidateQueries({ queryKey: ["import-stats"] });
      try {
        const data = JSON.parse(ev.data);
        if (data.type === "llm_run") setActionMsg(`LLM run finished: ${data.scored} scored, ${data.skipped} skipped`);
      } catch { /* keepalive */ }
    };
    return () => es.close();
  }, [qc]);

  const acceptMut = useMutation({
    mutationFn: (id: number) => importsApi.accept(id),
    onSuccess: r => { setActionMsg(r.ok ? `Import pushed: ${r.message}` : `Push failed: ${r.message}`); setConfirmAccept(null); invalidate(); },
    onError: (e: Error) => { setActionMsg(`Accept failed: ${e.message}`); setConfirmAccept(null); },
  });

  const rejectMut = useMutation({
    mutationFn: ({ id, remove }: { id: number; remove: boolean }) => importsApi.reject(id, remove),
    onSuccess: r => {
      if (r.download_client) setActionMsg(r.download_client.join("; "));
      invalidate();
    },
  });

  const batchMut = useMutation({
    mutationFn: ({ ids, action }: { ids: number[]; action: "accept" | "reject" | "confirm_orphan" }) => importsApi.batch(ids, action),
    onSuccess: r => { setActionMsg(`Batch done: ${r.results.length} item(s) processed`); invalidate(); },
    onError: (e: Error) => setActionMsg(`Batch failed: ${e.message}`),
  });

  const confirmOrphanMut = useMutation({
    mutationFn: (id: number) => importsApi.confirmOrphan(id),
    onSuccess: () => invalidate(),
    onError: (e: Error) => setActionMsg(`Confirm failed: ${e.message}`),
  });

  const keepMut = useMutation({
    mutationFn: (id: number) => importsApi.keep(id),
    onSuccess: () => { setActionMsg("Kept in triage — the next scan re-checks it"); invalidate(); },
    onError: (e: Error) => setActionMsg(`Keep failed: ${e.message}`),
  });

  const llmRunMut = useMutation({
    mutationFn: (ids?: number[]) => importsApi.llmRun(ids),
    onSuccess: r => { setActionMsg(r.message); setSelected(new Set()); },
    onError: (e: Error) => setActionMsg(`LLM run failed: ${e.message}`),
  });

  const handlePackReviewClick = async (itemId: number) => {
    if (packReviewLoading.has(itemId)) return; // already loading
    setPackReviewLoading((prev: Set<number>) => new Set(prev).add(itemId));
    try {
      const result = await importsApi.llmReviewPack(itemId);
      if (result.matches && result.matches.length > 0) {
        setPackReviewResults((prev: Record<string, any>) => ({ ...prev, [itemId.toString()]: result.matches }));
      } else {
        setActionMsg(result.message || "No matches found for pack review");
      }
    } catch (e: unknown) {
      setActionMsg(`Pack review failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setPackReviewLoading((prev: Set<number>) => { const next = new Set(prev); next.delete(itemId); return next; });
    }
  };

  const handleScan = async () => {
    setScanning(true);
    setActionMsg(null);
    try {
      await importsApi.scan();
      invalidate();
    } catch (e: unknown) {
      setActionMsg(`Scan failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setScanning(false);
    }
  };

  const toggleSelect = (id: number) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const allSelectable = sortedItems.filter(i =>
    i.status === "suggested" || i.status === "resolve_failed" || i.status === "orphan_pending");
  // Orphan-pending rows take confirm/keep, not accept/reject — the batch bar
  // switches wholesale when the selection is entirely orphan-pending.
  const selectedOrphanCount = sortedItems.filter(i => selected.has(i.id) && i.status === "orphan_pending").length;
  const orphanBatch = selected.size > 0 && selectedOrphanCount === selected.size;
  const toggleSelectAll = () => {
    setSelected(prev => prev.size === allSelectable.length && allSelectable.length > 0
      ? new Set() : new Set(allSelectable.map(i => i.id)));
  };

  const toggleSort = (field: keyof FailedImport) => {
    if (sortBy === field) setSortDir(d => d === "desc" ? "asc" : "desc");
    else { setSortBy(field); setSortDir("desc"); }
  };

  const filterLabel = (f: string) => f === "" ? "All" : STATUS_META[f]?.label ?? f;
  const filterCount = (f: string): number | null => {
    if (!stats) return null;
    if (f === "") return stats.suggested + stats.auto_resolved + stats.accepted + stats.rejected
      + stats.closed_external + stats.resolve_failed + stats.orphan_pending + stats.orphaned;
    return (stats as unknown as Record<string, number>)[f] ?? null;
  };

  const cols = COLUMNS.filter(c => visibleCols.has(c.key));
  const colW = (c: ColDef) => widths[c.key] ?? c.width;
  const totalWidth = 40 + cols.reduce((s, c) => s + colW(c), 0) + 130; // checkbox + cols + actions

  const renderCell = (item: FailedImport, key: ColKey, isExpanded: boolean) => {
    switch (key) {
      case "source":
        return <span className={`px-2 py-0.5 rounded text-xs font-bold text-white ${APP_COLORS[item.source_app] ?? "bg-slate-600"}`}>{item.source_app}</span>;
      case "release":
        return (
          <>
            <button onClick={() => setExpanded(isExpanded ? null : item.id)}
                    className="flex items-center gap-1 hover:text-brand-light transition-colors text-left w-full min-w-0 text-white font-medium">
              {isExpanded ? <ChevronDown size={13} className="flex-shrink-0" /> : <ChevronRight size={13} className="flex-shrink-0" />}
              <span className="truncate min-w-0" title={item.raw_title}>{item.raw_title}</span>
              {item.pack && (
                <span className="flex-shrink-0 px-1.5 py-0.5 rounded bg-brand/20 text-brand-light text-[10px] font-bold uppercase tracking-wide"
                      title={`Season pack (${item.pack}) — accepting imports every mappable file`}>
                  {item.pack === "complete series" ? "Complete" : `Pack ${item.pack}`}
                </span>
              )}
            </button>
            {item.message && <span className="block text-slate-500 text-xs truncate pl-4" title={item.message}>{item.message}</span>}
          </>
        );
      case "matched":
        return <span className="block truncate text-slate-300" title={item.matched_title ?? ""}>{item.matched_title ?? "—"}</span>;
      case "match_pct":
        return (
          <span title={item.match_rationale ?? ""}>
            <ConfidenceBadge value={item.heuristic_confidence ?? item.confidence} />
          </span>
        );
      case "match_notes":
        return item.match_rationale ? (
          <ClampedText text={item.match_rationale} />
        ) : <span className="text-slate-600 text-xs">—</span>;
      case "llm_pct":
        return item.llm_confidence !== null ? (
          <span title={item.llm_rationale ?? ""}><ConfidenceBadge value={item.llm_confidence} /></span>
        ) : <span className="text-slate-600 text-xs">—</span>;
      case "llm_notes":
        return item.llm_rationale ? (
          <ClampedText text={item.llm_rationale} />
        ) : <span className="text-slate-600 text-xs">—</span>;
      case "status": {
        const status = STATUS_META[item.status] ?? { label: item.status, cls: "bg-surface-overlay text-slate-300" };
        return (
          <>
            <span className={`inline-block px-2 py-0.5 rounded text-xs font-bold ${status.cls}`}>{status.label}</span>
            {item.verified === true && <span className="block text-green-500 text-xs mt-0.5">verified</span>}
            {item.quality_downgrade && (
              <span
                className="block mt-0.5 px-1.5 py-0.5 rounded bg-orange-900/40 text-orange-300 text-[10px] font-bold uppercase tracking-wide w-fit"
                title="Every file in this download rejects as not an upgrade over an existing library file — will never import as-is"
              >
                Downgrade
              </span>
            )}
            {item.suspicious_files && (
              <span
                className="block mt-0.5 px-1.5 py-0.5 rounded bg-red-900/60 text-red-300 text-[10px] font-bold uppercase tracking-wide w-fit"
                title={`Suspicious file type(s): ${JSON.parse(item.suspicious_files).join(", ")}`}
              >
                Suspicious
              </span>
            )}
          </>
        );
      }
      case "detected":
        return <span className="text-slate-400">{fmtDate(item.created_at)}</span>;
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-5">
        <p className="text-slate-400 text-sm">Stuck *arr downloads matched against your library — accept to push the import</p>
        <div className="flex items-center gap-2">
          <div className="relative">
            <button
              onClick={() => setShowColMenu(s => !s)}
              className="flex items-center gap-2 px-3 py-2 rounded-lg bg-surface-raised border border-purple-900/40 text-slate-300 hover:text-white text-sm transition-colors"
            >
              <Columns3 size={15} />
              Columns
            </button>
            {showColMenu && (
              <div className="absolute right-0 mt-1 z-20 bg-surface-raised border border-purple-900/40 rounded-lg shadow-xl p-3 w-48">
                {COLUMNS.map(c => (
                  <label key={c.key} className="flex items-center gap-2 py-1 text-sm text-slate-300 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={visibleCols.has(c.key)}
                      onChange={() => setVisibleCols(prev => {
                        const next = new Set(prev);
                        if (next.has(c.key)) { if (next.size > 1) next.delete(c.key); }
                        else next.add(c.key);
                        return next;
                      })}
                      className="accent-purple-500"
                    />
                    {c.label}
                  </label>
                ))}
                <button
                  onClick={() => { setWidths({}); persistWidths({}); }}
                  className="mt-2 w-full px-2 py-1 rounded bg-surface-overlay hover:bg-white/10 text-slate-400 text-xs transition-colors"
                >
                  Reset column widths
                </button>
              </div>
            )}
          </div>
          <button
            onClick={handleScan}
            disabled={scanning}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-brand text-white hover:bg-brand-dark text-sm transition-colors disabled:opacity-50"
          >
            <RefreshCw size={15} className={scanning ? "animate-spin" : ""} />
            {scanning ? "Scanning…" : "Scan Now"}
          </button>
        </div>
      </div>

      {/* Status filter chips */}
      <div className="flex items-center gap-2 mb-5 flex-wrap">
        {FILTERS.map(f => (
          <button
            key={f}
            onClick={() => { setStatusFilter(f); setSelected(new Set()); setExpanded(null); }}
            className={`px-3 py-1.5 rounded-lg text-sm transition-colors ${
              statusFilter === f ? "bg-brand text-white" : "bg-surface-raised text-slate-400 hover:text-white border border-purple-900/40"
            }`}
          >
            {filterLabel(f)}
            {filterCount(f) !== null && <span className="ml-1.5 text-xs opacity-70">{filterCount(f)}</span>}
          </button>
        ))}
        <button
          onClick={() => setDowngradeOnly(v => !v)}
          title="Show only items where every file rejects as not an upgrade over an existing library file"
          className={`px-3 py-1.5 rounded-lg text-sm transition-colors border ${
            downgradeOnly ? "bg-orange-700 border-orange-700 text-white" : "bg-surface-raised text-slate-400 hover:text-white border-purple-900/40"
          }`}
        >
          Downgrades only
        </button>
        <button
          onClick={() => setSuspiciousOnly(v => !v)}
          title="Show only items with a suspicious file type (e.g. .exe) in the download"
          className={`px-3 py-1.5 rounded-lg text-sm transition-colors border ${
            suspiciousOnly ? "bg-red-700 border-red-700 text-white" : "bg-surface-raised text-slate-400 hover:text-white border-purple-900/40"
          }`}
        >
          Suspicious only
        </button>
        {actionMsg && <span className="text-sm text-slate-300 ml-2">{actionMsg}</span>}
      </div>

      {/* Batch action bar */}
      {selected.size > 0 && (
        <div className="flex items-center gap-3 mb-4 px-4 py-2.5 bg-brand/10 border border-brand/30 rounded-lg">
          <span className="text-sm text-brand-light">{selected.size} selected</span>
          {orphanBatch ? (
            <button
              onClick={() => batchMut.mutate({ ids: [...selected], action: "confirm_orphan" })}
              disabled={batchMut.isPending}
              title="Confirm these downloads are gone — mark them orphaned"
              className="px-3 py-1 bg-orange-700 hover:bg-orange-600 text-white rounded text-xs disabled:opacity-50"
            >
              Confirm Orphans Selected
            </button>
          ) : (
            <>
              <button
                onClick={() => batchMut.mutate({ ids: [...selected], action: "accept" })}
                disabled={batchMut.isPending}
                className="px-3 py-1 bg-green-700 hover:bg-green-600 text-white rounded text-xs disabled:opacity-50"
              >
                Accept Selected
              </button>
              <button
                onClick={() => batchMut.mutate({ ids: [...selected], action: "reject" })}
                disabled={batchMut.isPending}
                className="px-3 py-1 bg-red-700 hover:bg-red-600 text-white rounded text-xs disabled:opacity-50"
              >
                Reject Selected
              </button>
              <button
                onClick={() => llmRunMut.mutate([...selected])}
                disabled={llmRunMut.isPending}
                title="Score the selected items with the local LLM"
                className="flex items-center gap-1.5 px-3 py-1 bg-indigo-700 hover:bg-indigo-600 text-white rounded text-xs disabled:opacity-50"
              >
                <Bot size={12} /> Run LLM on Selected
              </button>
            </>
          )}
          <button onClick={() => setSelected(new Set())} className="text-xs text-slate-400 hover:text-white ml-auto">
            Clear
          </button>
        </div>
      )}

      {isLoading ? (
        <p className="text-slate-400">Loading…</p>
      ) : sortedItems.length === 0 ? (
        <div className="bg-surface-raised rounded-xl border border-purple-900/30 p-10 text-center">
          <p className="text-slate-400">
            {statusFilter === "suggested"
              ? "No stuck imports awaiting review. The background poller checks your *arr queues automatically."
              : "Nothing here yet."}
          </p>
        </div>
      ) : (
        <div className="bg-surface-raised rounded-xl border border-purple-900/30 overflow-x-auto">
          <table className="text-sm" style={{ tableLayout: "fixed", width: "100%", minWidth: totalWidth }}>
            <thead className="border-b border-purple-900/30 text-slate-400 text-xs uppercase tracking-wider">
              <tr>
                <th className="px-3 py-3" style={{ width: 40 }}>
                  <input type="checkbox" className="accent-purple-500"
                         checked={selected.size > 0 && selected.size === allSelectable.length}
                         onChange={toggleSelectAll} />
                </th>
                {cols.map(c => (
                  <th key={c.key} className="text-left px-4 py-3 relative select-none group"
                      style={{ width: colW(c) }}>
                    <button
                      onClick={() => c.sortField && toggleSort(c.sortField)}
                      className={`flex items-center gap-1 uppercase ${c.sortField ? "cursor-pointer hover:text-white" : "cursor-default"}`}
                    >
                      {c.key === "llm_pct" && <Bot size={13} className="text-brand-light" />}
                      {c.label}
                      {c.sortField && sortBy === c.sortField && (
                        sortDir === "desc" ? <ChevronDown size={13} /> : <ChevronUp size={13} />
                      )}
                    </button>
                    <span
                      onMouseDown={startResize(c.key, c.width)}
                      className="absolute right-0 top-0 h-full w-1.5 cursor-col-resize opacity-0 group-hover:opacity-100 bg-purple-500/40"
                      title="Drag to resize"
                    />
                  </th>
                ))}
                <th className="px-4 py-3" style={{ width: 130 }} />
              </tr>
            </thead>
            <tbody className="divide-y divide-purple-900/20">
              {sortedItems.map((item: FailedImport) => {
                const actionable = item.status === "suggested" || item.status === "resolve_failed";
                const orphanPending = item.status === "orphan_pending";
                const isExpanded = expanded === item.id;
                return (
                  <>
                    <tr key={item.id} className="hover:bg-white/5 transition-colors">
                      <td className="px-3 py-3">
                        {(actionable || orphanPending) && (
                          <input type="checkbox" className="accent-purple-500"
                                 checked={selected.has(item.id)} onChange={() => toggleSelect(item.id)} />
                        )}
                      </td>
                      {cols.map(c => (
                        <td key={c.key} className="px-4 py-3 overflow-hidden" style={{ width: colW(c) }}>
                          {renderCell(item, c.key, isExpanded)}
                        </td>
                      ))}
                      <td className="px-4 py-3">
                        {orphanPending && (
                          <div className="flex items-center gap-1.5 justify-end">
                            <button
                              onClick={() => confirmOrphanMut.mutate(item.id)}
                              disabled={confirmOrphanMut.isPending}
                              title="Confirm — the download is gone from every client and disk; mark orphaned"
                              className="px-2 py-1 bg-orange-700 hover:bg-orange-600 text-white rounded text-xs disabled:opacity-50"
                            >
                              Confirm Orphan
                            </button>
                            <button
                              onClick={() => keepMut.mutate(item.id)}
                              disabled={keepMut.isPending}
                              title="Not an orphan — put it back in triage (the next scan re-checks it)"
                              className="px-2 py-1 bg-surface-overlay hover:bg-white/10 text-slate-300 rounded text-xs disabled:opacity-50"
                            >
                              Keep
                            </button>
                          </div>
                        )}
                        {actionable && (
                          <div className="flex items-center gap-1.5 justify-end">
                            {confirmAccept === item.id ? (
                              <div className="flex gap-1">
                                <button
                                  onClick={() => acceptMut.mutate(item.id)}
                                  disabled={acceptMut.isPending}
                                  className="px-2 py-1 bg-green-700 hover:bg-green-600 text-white rounded text-xs disabled:opacity-50"
                                >
                                  {acceptMut.isPending ? "Pushing…" : "Confirm Import"}
                                </button>
                                <button onClick={() => setConfirmAccept(null)} className="px-2 py-1 bg-surface-overlay hover:bg-white/10 text-slate-300 rounded text-xs">Cancel</button>
                              </div>
                            ) : (
                              <>
                                <button
                                  onClick={() => setConfirmAccept(item.id)}
                                  disabled={!item.matched_id}
                                  title={item.matched_id ? "Accept — push import to the *arr app" : "No match to import"}
                                  className="p-1.5 rounded hover:bg-green-900/40 text-slate-400 hover:text-green-300 transition-colors disabled:opacity-30"
                                >
                                  <Check size={15} />
                                </button>
                                <button
                                  onClick={() => rejectMut.mutate({ id: item.id, remove: false })}
                                  title="Reject — stop suggesting this download"
                                  className="p-1.5 rounded hover:bg-red-900/40 text-slate-400 hover:text-red-300 transition-colors"
                                >
                                  <X size={15} />
                                </button>
                                <button
                                  onClick={() => rejectMut.mutate({ id: item.id, remove: true })}
                                  title="Reject & remove the download from the torrent client (requires qBittorrent/Transmission integration)"
                                  className="p-1.5 rounded hover:bg-red-900/40 text-slate-400 hover:text-red-300 transition-colors"
                                >
                                  <Trash2 size={15} />
                                </button>
                                <button
                                  onClick={() => llmRunMut.mutate([item.id])}
                                  disabled={llmRunMut.isPending}
                                  title="Score this item with the local LLM"
                                  className="p-1.5 rounded hover:bg-indigo-900/40 text-slate-400 hover:text-indigo-300 transition-colors disabled:opacity-30"
                                >
                                  <Sparkles size={15} />
                                </button>
                                {item.pack && item.matched_id && (
                                  <PackReviewButton
                                    itemId={item.id}
                                    isLoading={packReviewLoading.has(item.id)}
                                    hasResults={!!packReviewResults[item.id.toString()]}
                                    results={packReviewResults[item.id.toString()]}
                                    onClick={() => handlePackReviewClick(item.id)}
                                  />
                                )}
                              </>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr key={`${item.id}-files`} className="bg-surface/50">
                        <td colSpan={cols.length + 2} className="border-t border-purple-900/10">
                          <FileDetails importId={item.id} sourceApp={item.source_app} matchedId={item.matched_id} />
                          {actionable && <MatchOverride item={item} onDone={invalidate} />}
                        </td>
                      </tr>
                    )}
                  </>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function PackReviewButton({
  itemId,
  isLoading,
  hasResults,
  results,
  onClick,
}: {
  itemId: number;
  isLoading: boolean;
  hasResults: boolean;
  results?: Array<{ file: string; season: number; episode: number; confidence: string; reason: string }>;
  onClick: () => void;
}) {
  const [showTooltip, setShowTooltip] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);

  return (
    <div className="relative">
      <button
        ref={buttonRef}
        onClick={onClick}
        disabled={isLoading}
        onMouseEnter={() => { if (hasResults) setShowTooltip(true); }}
        onMouseLeave={() => setShowTooltip(false)}
        className="p-1.5 rounded hover:bg-cyan-900/40 text-slate-400 hover:text-cyan-300 transition-colors disabled:opacity-30"
        title={isLoading ? "Analyzing..." : hasResults ? "Hover to see results" : "Review files in pack with LLM"}
      >
        {isLoading ? (
          <Brain size={15} className="animate-pulse" />
        ) : hasResults ? (
          <Lightbulb size={15} className="text-cyan-300" />
        ) : (
          <Bot size={15} />
        )}
      </button>

      {showTooltip && hasResults && results && (
        <div className="absolute bottom-full right-0 mb-2 bg-surface-raised border border-purple-900/40 rounded-lg shadow-xl p-3 w-64 z-50">
          <div className="max-h-60 overflow-y-auto space-y-2">
            {results.map((m, i) => (
              <div key={i} className="bg-surface/50 border border-purple-900/20 rounded p-2">
                <div className="text-xs font-bold text-brand-light">
                  S{m.season.toString().padStart(2, "0")}E{m.episode.toString().padStart(2, "0")}
                </div>
                <div className="text-xs text-slate-300 truncate" title={m.file}>{m.file}</div>
                {m.reason && <div className="text-xs text-slate-400 mt-1">{m.reason}</div>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
