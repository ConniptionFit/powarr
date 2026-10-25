import { useState, useEffect, useMemo, useRef, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, X, RefreshCw, Bot, ChevronDown, ChevronRight, ChevronUp, Trash2, Search, Columns3, ThumbsUp, ThumbsDown, ListEnd, Download, Rows3, Split, LayoutGrid, Table2 } from "lucide-react";
import { importsApi, settingsApi, fmtDate, type FailedImport } from "../../lib/api";
import { usePersistedState } from "../../lib/usePersistedState";
import { subscribeEvents } from "../../lib/eventBus";
import SavedViews from "../../components/SavedViews";
import { DENSITY_CLASSES, DENSITY_STORAGE_KEY, type TableDensity } from "../../lib/tableDensity";
import ClampedText from "../../components/ClampedText";
import BotState from "../../components/BotState";
import ConfidenceBadge from "../../components/ConfidenceBadge";
import { PlatformBadge, PLATFORM_META, PLATFORM_ORDER, type PlatformName } from "../../components/PlatformIcon";
import { SkeletonTable } from "../../components/Skeleton";
import Kbd from "../../components/Kbd";
import ScrollFadeX from "../../components/ScrollFadeX";
import { type ColKey, type ColDef, COLUMNS, LS_VISIBLE, LS_WIDTHS, STATUS_META, loadVisible, loadWidths, isInteractiveTarget, stripMd } from "./match-review/shared";
import { MatchOverride } from "./match-review/MatchOverride";
import { FileDetails } from "./match-review/FileDetails";
import { AutoGateInspector } from "./match-review/AutoGateInspector";
import { PackReviewButton } from "./match-review/PackReviewButton";
const FILTERS = ["suggested", "resolve_failed", "orphan_pending", "auto_resolved", "accepted", "rejected", "orphaned", ""] as const;

const PAGE_SIZE = 200;

export default function MatchReview() {
  const qc = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  // Filter/sort choices persist per-browser (v0.27.0, Approved Queue #11) —
  // column layout below already did since v0.4.0.
  const [statusFilter, setStatusFilter] = usePersistedState<string>("powarr.failedImports.statusFilter", "suggested");
  const [viewMode, setViewMode] = usePersistedState<"cards" | "table">("powarr.matchreview.view", "cards");
  const [scanning, setScanning] = useState(false);
  const [confirmAccept, setConfirmAccept] = useState<number | null>(null);
  const [acceptingIds, setAcceptingIds] = useState<Set<number>>(new Set()); // per-item Pushing… (v0.33.0)
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [llmQueued, setLlmQueued] = useState<number | null>(null); // queue position, null = not queued
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [expanded, setExpanded] = useState<number | null>(null);
  // UX-05 — row the keyboard shortcuts act on (ring-highlighted in both views).
  const [focusedId, setFocusedId] = useState<number | null>(null);
  // Large queues render in pages of PAGE_SIZE rows so thousands of expandable rows never mount at once.
  const [shown, setShown] = useState(PAGE_SIZE);

  // table view state (persisted per-browser)
  const [visibleCols, setVisibleCols] = useState<Set<ColKey>>(loadVisible);
  const [widths, setWidths] = useState<Record<string, number>>(loadWidths);
  const [showColMenu, setShowColMenu] = useState(false);
  const [sortBy, setSortBy] = usePersistedState<keyof FailedImport>("powarr.failedImports.sortBy", "created_at");
  const [sortDir, setSortDir] = usePersistedState<"asc" | "desc">("powarr.failedImports.sortDir", "desc");
  const [packReviewLoading, setPackReviewLoading] = useState(new Set<number>());
  const [packReviewResults, setPackReviewResults] = useState<Record<string, Array<{ file: string; season: number; episode: number; match_type: string; confidence: string; reason: string }>>>({});
  const [downgradeOnly, setDowngradeOnly] = usePersistedState<boolean>("powarr.failedImports.downgradeOnly", false);
  const [suspiciousOnly, setSuspiciousOnly] = usePersistedState<boolean>("powarr.failedImports.suspiciousOnly", false);
  const [packFilter, setPackFilter] = usePersistedState<"all" | "packs" | "singles">("powarr.failedImports.packFilter", "all");
  const [search, setSearch] = usePersistedState("powarr.failedImports.search", "");
  const [platformFilter, setPlatformFilter] = usePersistedState<PlatformName | "">("powarr.failedImports.platformFilter", "");
  const [rootCauseFilter, setRootCauseFilter] = usePersistedState<string>("powarr.failedImports.rootCauseFilter", "");
  const [density, setDensity] = usePersistedState<TableDensity>(DENSITY_STORAGE_KEY, "comfortable");
  const d = DENSITY_CLASSES[density];
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

  // Deep-link from Import Queue's "Send to Match Review →" (?focus=<id>) —
  // switch to the All filter if needed, expand the row, and scroll to it.
  useEffect(() => {
    const focusId = searchParams.get("focus");
    if (!focusId || isLoading) return;
    const id = Number(focusId);
    if (!items.some(i => i.id === id)) {
      if (statusFilter !== "") setStatusFilter("");
      return;
    }
    setExpanded(id);
    setSearchParams(prev => { const next = new URLSearchParams(prev); next.delete("focus"); return next; }, { replace: true });
    requestAnimationFrame(() => {
      document.getElementById(`import-item-${id}`)?.scrollIntoView({ block: "center", behavior: "smooth" });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, items, isLoading]);

  const { data: stats } = useQuery({
    queryKey: ["import-stats"],
    queryFn: importsApi.stats,
  });

  const { data: matchSettings } = useQuery({
    queryKey: ["import-matching"],
    queryFn: settingsApi.getImportMatching,
  });

  const { data: autoEligible } = useQuery({
    queryKey: ["import-auto-eligible"],
    queryFn: importsApi.autoEligible,
    // Only poll when auto-resolve is on — otherwise the button is hidden
    enabled: !!matchSettings?.auto_resolve_enabled,
    refetchInterval: matchSettings?.auto_resolve_enabled ? 15_000 : false,
  });

  const enabledPlatforms = useMemo(() => {
    if (!matchSettings) return PLATFORM_ORDER.slice();
    return PLATFORM_ORDER.filter(p => matchSettings[`${p}_enabled` as keyof typeof matchSettings] !== false);
  }, [matchSettings]);

  const sortedItems = useMemo(() => {
    let arr = items;
    if (downgradeOnly) arr = arr.filter(i => i.quality_downgrade);
    if (suspiciousOnly) arr = arr.filter(i => i.suspicious_files);
    if (packFilter === "packs") arr = arr.filter(i => i.pack);
    if (packFilter === "singles") arr = arr.filter(i => !i.pack);
    if (platformFilter) arr = arr.filter(i => i.source_app === platformFilter);
    if (rootCauseFilter) arr = arr.filter(i => i.root_cause_code === rootCauseFilter);
    const q = search.trim().toLowerCase();
    if (q) {
      arr = arr.filter(i =>
        (i.raw_title || "").toLowerCase().includes(q) ||
        (i.matched_title || "").toLowerCase().includes(q) ||
        (i.message || "").toLowerCase().includes(q) ||
        (i.match_rationale || "").toLowerCase().includes(q)
      );
    }
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
  }, [items, sortBy, sortDir, downgradeOnly, suspiciousOnly, packFilter, platformFilter, rootCauseFilter, search]);

  // FI-06 — root causes actually present in the current result set, with
  // counts, in a fixed priority order (matches services/root_cause.py).
  const ROOT_CAUSE_ORDER = ["missing_files", "year_mismatch", "not_an_upgrade", "suspicious_file",
    "pack_partial", "scene_name_junk", "no_match", "llm_disagrees", "weak_numeric_match",
    "low_confidence", "unclassified"];
  const rootCauseCounts = useMemo(() => {
    const counts = new Map<string, { label: string; count: number }>();
    for (const i of items) {
      if (!i.root_cause_code) continue;
      const entry = counts.get(i.root_cause_code);
      if (entry) entry.count++;
      else counts.set(i.root_cause_code, { label: i.root_cause_label || i.root_cause_code, count: 1 });
    }
    return [...counts.entries()].sort(
      (a, b) => ROOT_CAUSE_ORDER.indexOf(a[0]) - ROOT_CAUSE_ORDER.indexOf(b[0])
    );
  }, [items]);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["imports"] });
    qc.invalidateQueries({ queryKey: ["import-stats"] });
    qc.invalidateQueries({ queryKey: ["import-auto-eligible"] });
    setSelected(new Set());
  };

  // Live updates: poller scans + on-demand LLM runs + async batch accepts
  useEffect(() => {
    return subscribeEvents(data => {
      qc.invalidateQueries({ queryKey: ["imports"] });
      qc.invalidateQueries({ queryKey: ["import-stats"] });
      qc.invalidateQueries({ queryKey: ["import-auto-eligible"] });
      if (!data) return;
      if (data.type === "llm_run") setActionMsg(`LLM run finished: ${data.scored} scored, ${data.skipped} skipped${data.auto_queued ? `, ${data.auto_queued} queued for auto-import` : ""}`);
      if (data.type === "llm_run_started") setLlmQueued(null);
      if (data.type === "rescore") setActionMsg(`Rescore finished: ${data.rescored} rescored, ${data.skipped} skipped${data.auto_queued ? `, ${data.auto_queued} queued for auto-import` : ""}`);
      if (data.type === "import_batch") {
        const parts = [`${data.ok ?? 0} imported`];
        if (data.orphaned) parts.push(`${data.orphaned} gone (orphaned)`);
        if (data.failed) parts.push(`${data.failed} failed`);
        setActionMsg(`Import finished: ${parts.join(", ")}`);
        setSelected(new Set());
        setAcceptingIds(new Set());
        setConfirmAccept(null);
      }
    });
  }, [qc]);

  const acceptMut = useMutation({
    mutationFn: (id: number) => importsApi.accept(id),
    onMutate: (id) => {
      setAcceptingIds(prev => new Set(prev).add(id));
      setConfirmAccept(null);
    },
    onSuccess: (r, id) => {
      if (r.async) {
        setActionMsg(
          r.coalesced
            ? `Queued — now importing ${r.total ?? "?"} item(s) (Active Processes)`
            : `Importing ${r.total ?? 1} item(s) — progress in Active Processes`,
        );
        // Keep acceptingIds until import_batch SSE clears them
        return;
      }
      setAcceptingIds(prev => { const n = new Set(prev); n.delete(id); return n; });
      setActionMsg(r.ok ? `Import pushed: ${r.message}` : `Push failed: ${r.message}`);
      invalidate();
    },
    onError: (e: Error, id) => {
      setAcceptingIds(prev => { const n = new Set(prev); n.delete(id); return n; });
      setActionMsg(`Accept failed: ${e.message}`);
    },
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
    onMutate: (vars) => {
      if (vars.action === "accept") {
        setAcceptingIds(prev => {
          const n = new Set(prev);
          for (const id of vars.ids) n.add(id);
          return n;
        });
      }
    },
    onSuccess: (r, vars) => {
      if (r.async && r.task_id) {
        setActionMsg(
          r.coalesced
            ? `Queued — now importing ${r.total ?? vars.ids.length} item(s) (Active Processes)`
            : `Importing ${r.total ?? vars.ids.length} item(s) — progress in Active Processes`,
        );
        // Selection / acceptingIds cleared when the import_batch SSE event arrives
        return;
      }
      setAcceptingIds(prev => {
        const n = new Set(prev);
        for (const id of vars.ids) n.delete(id);
        return n;
      });
      setActionMsg(`Batch done: ${r.results.length} item(s) processed`);
      invalidate();
    },
    onError: (e: Error, vars) => {
      setAcceptingIds(prev => {
        const n = new Set(prev);
        for (const id of vars.ids) n.delete(id);
        return n;
      });
      setActionMsg(`Batch failed: ${e.message}`);
    },
  });

  const processEligible = () => {
    const ids = autoEligible?.ids ?? [];
    if (!ids.length) return;
    batchMut.mutate({ ids, action: "accept" });
  };

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

  const rescoreMut = useMutation({
    mutationFn: (ids?: number[]) => importsApi.rescore(ids),
    onSuccess: r => setActionMsg(r.message),
    onError: (e: Error) => setActionMsg(`Rescore failed: ${e.message}`),
  });

  const llmRunMut = useMutation({
    mutationFn: (ids?: number[]) => importsApi.llmRun(ids),
    onSuccess: r => {
      setActionMsg(r.message);
      setLlmQueued(r.queued ? (r.queue_position ?? 1) : null);
      if (!r.queued) setSelected(new Set());
    },
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
  const visibleItems = sortedItems.slice(0, shown);
  // Select-all only covers rows actually on screen, so bulk actions never touch unseen items.
  const allSelectable = visibleItems.filter(i =>
    i.status === "suggested" || i.status === "resolve_failed" || i.status === "orphan_pending");
  // Orphan-pending rows take confirm/keep, not accept/reject — the batch bar
  // switches wholesale when the selection is entirely orphan-pending.
  const selectedOrphanCount = sortedItems.filter(i => selected.has(i.id) && i.status === "orphan_pending").length;
  const orphanBatch = selected.size > 0 && selectedOrphanCount === selected.size;
  const toggleSelectAll = () => {
    setSelected(prev => prev.size === allSelectable.length && allSelectable.length > 0
      ? new Set() : new Set(allSelectable.map(i => i.id)));
  };

  // UX-05 — keyboard-first triage over the currently filtered/sorted list:
  // j/k move focus, x selects, e expands, y accepts (two-step — the first y
  // arms the same Confirm Import step the button uses, the second fires it),
  // n rejects (never the reject-and-remove-download variant: that one stays
  // click-only), Esc backs out of the confirm then clears focus. Ignored
  // while typing in any input/select and when a modifier is held.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t?.closest("input, textarea, select, [contenteditable=true]")) return;
      if (!["j", "k", "x", "y", "n", "e", "Escape"].includes(e.key)) return;
      if (e.key === "Escape") {
        if (confirmAccept !== null) setConfirmAccept(null);
        else setFocusedId(null);
        return;
      }
      if (sortedItems.length === 0) return;
      const idx = sortedItems.findIndex(i => i.id === focusedId);
      if (e.key === "j" || e.key === "k") {
        e.preventDefault();
        const nextIdx = idx === -1 ? 0
          : e.key === "j" ? Math.min(idx + 1, sortedItems.length - 1)
          : Math.max(idx - 1, 0);
        const next = sortedItems[nextIdx];
        if (nextIdx >= shown) setShown(n => n + PAGE_SIZE);
        setFocusedId(next.id);
        document.getElementById(`import-item-${next.id}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
        return;
      }
      const item = idx >= 0 ? sortedItems[idx] : undefined;
      if (!item) return;
      const actionable = item.status === "suggested" || item.status === "resolve_failed";
      if (e.key === "x" && (actionable || item.status === "orphan_pending")) toggleSelect(item.id);
      if (e.key === "e") setExpanded(prev => (prev === item.id ? null : item.id));
      if (e.key === "y" && actionable && item.matched_id && !acceptingIds.has(item.id)) {
        if (confirmAccept === item.id) acceptMut.mutate(item.id);
        else setConfirmAccept(item.id);
      }
      if (e.key === "n" && actionable) rejectMut.mutate({ id: item.id, remove: false });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sortedItems, focusedId, confirmAccept, acceptingIds]);

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

  // Shared between table and card views — orphan-pending confirm/keep, or the
  // full actionable accept/reject/LLM/pack-review row.
  const renderActions = (item: FailedImport) => {
    const actionable = item.status === "suggested" || item.status === "resolve_failed";
    const orphanPending = item.status === "orphan_pending";
    return (
      <>
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
            {acceptingIds.has(item.id) ? (
              <span className="px-2 py-1 text-green-400 text-xs tabular-nums">Pushing…</span>
            ) : confirmAccept === item.id ? (
              <div className="flex gap-1">
                <button
                  onClick={() => acceptMut.mutate(item.id)}
                  className="px-2 py-1 bg-green-700 hover:bg-green-600 text-white rounded text-xs"
                >
                  {item.partial_import
                    ? "Import Missing/Upgrades"
                    : "Confirm Import"}
                </button>
                <button onClick={() => setConfirmAccept(null)} className="px-2 py-1 bg-surface-overlay hover:bg-white/10 text-slate-300 rounded text-xs">Cancel</button>
              </div>
            ) : (
              <>
                <button
                  onClick={() => setConfirmAccept(item.id)}
                  disabled={!item.matched_id}
                  title={item.matched_id
                    ? (item.partial_import
                      ? "Accept — import only missing/upgrade files; already-covered files are skipped"
                      : "Accept — push import to the *arr app")
                    : "No match to import"}
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
                  title="Score this item with the local LLM (most in-depth reasoning available, regardless of the configured verbosity)"
                  className="p-1.5 rounded hover:bg-indigo-900/40 text-slate-400 hover:text-indigo-300 transition-colors disabled:opacity-30"
                >
                  {llmRunMut.isPending ? <BotState variant="thinking" size={15} /> : <Bot size={15} />}
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
      </>
    );
  };

  const renderCell = (item: FailedImport, key: ColKey, isExpanded: boolean) => {
    switch (key) {
      case "source":
        return <PlatformBadge app={item.source_app} />;
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
          <span title={stripMd(item.match_rationale)}>
            <ConfidenceBadge value={item.heuristic_confidence ?? item.confidence} />
          </span>
        );
      case "match_notes":
        return item.match_rationale ? (
          <ClampedText text={item.match_rationale} markdown />
        ) : <span className="text-slate-600 text-xs">—</span>;
      case "llm_pct":
        return item.llm_confidence !== null ? (
          <span className="flex items-center gap-1">
            <span title={stripMd(item.llm_rationale)}><ConfidenceBadge value={item.llm_confidence} /></span>
            {item.llm_agrees === true && (
              <span title="LLM agrees with the match"><ThumbsUp size={12} className="text-green-400" /></span>
            )}
            {item.llm_agrees === false && (
              <span title="LLM disagrees with the match"><ThumbsDown size={12} className="text-red-400" /></span>
            )}
          </span>
        ) : <span className="text-slate-600 text-xs">—</span>;
      case "llm_notes":
        return item.llm_rationale ? (
          <ClampedText text={item.llm_rationale} markdown />
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
                Covered
              </span>
            )}
            {item.partial_import && !item.quality_downgrade && (
              <span
                className="inline-flex items-center gap-1 mt-0.5 px-1.5 py-0.5 rounded bg-sky-900/40 text-sky-300 text-[10px] font-bold uppercase tracking-wide w-fit"
                title="Gap-fill: some files are missing/upgrades (will import) and some are already covered (skipped). Expand the row for green/red per file."
              >
                <Split size={10} /> Partial
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
            {/* FI-06 — only for causes not already surfaced by a badge above,
                so this never duplicates Covered/Partial/Suspicious. */}
            {item.root_cause_code && item.root_cause_label &&
              !["not_an_upgrade", "suspicious_file", "pack_partial"].includes(item.root_cause_code) &&
              item.root_cause_code !== "unclassified" && (
              <span
                className="block mt-0.5 px-1.5 py-0.5 rounded bg-purple-900/40 text-purple-300 text-[10px] font-bold uppercase tracking-wide w-fit"
                title={item.root_cause_action ?? undefined}
              >
                {item.root_cause_label}
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
    <div className="p-4 sm:p-8">
      <div className="flex items-center gap-2 mb-1">
        <BotState variant="available" size={22} />
        <h2 className="text-white font-semibold text-sm">Match Review</h2>
      </div>
      <div className="flex items-center justify-between mb-5 gap-3 flex-wrap">
        <div>
          <p className="text-slate-400 text-sm">Stuck *arr downloads matched against your library — accept to push the import</p>
          {/* UX-05 — pointer-only devices never see the shortcut hint */}
          <p className="hidden md:block text-slate-600 text-xs mt-0.5">
            Keyboard: <Kbd>j</Kbd>/<Kbd>k</Kbd> navigate · <Kbd>x</Kbd> select · <Kbd>e</Kbd> details · <Kbd>y</Kbd> accept (twice to confirm) · <Kbd>n</Kbd> reject
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setViewMode(m => m === "cards" ? "table" : "cards")}
            title={viewMode === "cards" ? "Switch to table view" : "Switch to card view"}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-surface-raised border border-purple-900/40 text-slate-300 hover:text-white text-sm transition-colors"
          >
            {viewMode === "cards" ? <Table2 size={15} /> : <LayoutGrid size={15} />}
            {viewMode === "cards" ? "Table view" : "Card view"}
          </button>
          {viewMode === "table" && (
            <button
              onClick={() => setDensity(density === "comfortable" ? "compact" : "comfortable")}
              title={density === "comfortable" ? "Switch to compact rows" : "Switch to comfortable rows"}
              className="flex items-center gap-2 px-3 py-2 rounded-lg bg-surface-raised border border-purple-900/40 text-slate-300 hover:text-white text-sm transition-colors"
            >
              <Rows3 size={15} />
              {density === "comfortable" ? "Compact" : "Comfortable"}
            </button>
          )}
          <button
            onClick={() => importsApi.exportCsv(statusFilter === "all" ? undefined : statusFilter)}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-surface-raised border border-purple-900/40 text-slate-300 hover:text-white text-sm transition-colors"
          >
            <Download size={15} />
            CSV
          </button>
          {viewMode === "table" && (
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
          )}
          <button
            onClick={handleScan}
            disabled={scanning}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-brand text-white hover:bg-brand-dark text-sm transition-colors disabled:opacity-50"
          >
            <RefreshCw size={15} className={scanning ? "animate-spin" : ""} />
            {scanning ? "Scanning…" : "Scan Now"}
          </button>
          <button
            onClick={() => rescoreMut.mutate(undefined)}
            disabled={rescoreMut.isPending}
            title="Re-run the deterministic matcher on all open Lidarr/Readarr rows — instant, no LLM"
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-surface-raised border border-purple-900/40 text-slate-300 hover:text-white text-sm transition-colors disabled:opacity-50"
          >
            <RefreshCw size={15} className={rescoreMut.isPending ? "animate-spin" : ""} />
            Rescore Candidates
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
        {/* Independent boolean toggles (combine freely) — pill-shaped with a
            checkmark when on, to read as a distinct control type from the
            mutually-exclusive status filters (rounded-lg) to their left. */}
        <div className="w-px h-5 bg-purple-900/40 mx-0.5" />
        <button
          onClick={() => setDowngradeOnly(v => !v)}
          title="Show only items where every file rejects as not an upgrade over an existing library file"
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm transition-colors border ${
            downgradeOnly ? "bg-orange-700 border-orange-700 text-white" : "bg-surface-raised text-slate-400 hover:text-white border-purple-900/40"
          }`}
        >
          {downgradeOnly && <Check size={13} />}
          Covered only
        </button>
        <button
          onClick={() => setSuspiciousOnly(v => !v)}
          title="Show only items with a suspicious file type (e.g. .exe) in the download"
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm transition-colors border ${
            suspiciousOnly ? "bg-red-700 border-red-700 text-white" : "bg-surface-raised text-slate-400 hover:text-white border-purple-900/40"
          }`}
        >
          {suspiciousOnly && <Check size={13} />}
          Suspicious only
        </button>
        <button
          onClick={() => setPackFilter(f => f === "packs" ? "all" : "packs")}
          title="Show only season-pack downloads"
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm transition-colors border ${
            packFilter === "packs" ? "bg-brand border-brand text-white" : "bg-surface-raised text-slate-400 hover:text-white border-purple-900/40"
          }`}
        >
          {packFilter === "packs" && <Check size={13} />}
          Packs only
        </button>
        <button
          onClick={() => setPackFilter(f => f === "singles" ? "all" : "singles")}
          title="Show only single-episode (non-pack) downloads"
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm transition-colors border ${
            packFilter === "singles" ? "bg-brand border-brand text-white" : "bg-surface-raised text-slate-400 hover:text-white border-purple-900/40"
          }`}
        >
          {packFilter === "singles" && <Check size={13} />}
          Singles only
        </button>
        {llmQueued !== null && (
          <span className="flex items-center gap-1.5 px-2 py-1 rounded bg-indigo-900/40 text-indigo-300 text-xs">
            <ListEnd size={13} /> LLM run queued (position {llmQueued})
          </span>
        )}
        {actionMsg && <span className="text-sm text-slate-300 ml-2">{actionMsg}</span>}
      </div>

      {/* Search + platform filters (v0.28.0) */}
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        <div className="relative">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            type="search"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search title / release…"
            className="pl-8 pr-3 py-1.5 w-56 bg-surface-raised border border-purple-900/40 rounded-lg text-sm text-white placeholder:text-slate-500"
          />
        </div>
        <SavedViews
          scope="matchReview"
          current={{ statusFilter, sortBy, sortDir, downgradeOnly, suspiciousOnly, packFilter, search, platformFilter, rootCauseFilter }}
          apply={v => {
            setStatusFilter(v.statusFilter); setSortBy(v.sortBy); setSortDir(v.sortDir);
            setDowngradeOnly(v.downgradeOnly); setSuspiciousOnly(v.suspiciousOnly); setPackFilter(v.packFilter);
            setSearch(v.search); setPlatformFilter(v.platformFilter); setRootCauseFilter(v.rootCauseFilter);
          }}
        />
        {rootCauseCounts.length > 0 && (
          <select
            value={rootCauseFilter}
            onChange={e => setRootCauseFilter(e.target.value)}
            title="Filter by root cause (FI-06) — tagged from the same signals already driving the confidence/badges above"
            className="px-2.5 py-1.5 bg-surface-raised border border-purple-900/40 rounded-lg text-sm text-slate-300"
          >
            <option value="">Root cause: all</option>
            {rootCauseCounts.map(([code, { label, count }]) => (
              <option key={code} value={code}>{label} ({count})</option>
            ))}
          </select>
        )}
        {enabledPlatforms.map(p => {
          const meta = PLATFORM_META[p];
          const Icon = meta.Icon;
          const active = platformFilter === p;
          return (
            <button
              key={p}
              onClick={() => setPlatformFilter(active ? "" : p)}
              title={`Filter by ${meta.label}`}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition-colors border ${
                active
                  ? `${meta.badge} border-transparent text-white`
                  : `bg-surface-raised hover:text-white border-purple-900/40 ${meta.chip}`
              }`}
            >
              <Icon size={13} />
              {meta.label}
            </button>
          );
        })}
        {matchSettings?.auto_resolve_enabled && (autoEligible?.count ?? stats?.auto_eligible_count ?? 0) > 0 && (
          <button
            onClick={processEligible}
            disabled={batchMut.isPending}
            title={`Batch-import ${autoEligible?.count ?? stats?.auto_eligible_count} item(s) passing the auto-import gate (${matchSettings.auto_import_mode ?? "either"}: algorithm ≥ ${Math.round((matchSettings.high_confidence_threshold ?? 0.9) * 100)}%, LLM ≥ ${Math.round((matchSettings.llm_auto_threshold ?? 0.8) * 100)}%)`}
            className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm bg-green-700 hover:bg-green-600 text-white transition-colors disabled:opacity-50"
          >
            <Check size={14} />
            Process {autoEligible?.count ?? stats?.auto_eligible_count} Items
          </button>
        )}
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
                title="Score the selected items with the local LLM (most in-depth reasoning available, regardless of the configured verbosity)"
                className="flex items-center gap-1.5 px-3 py-1 bg-indigo-700 hover:bg-indigo-600 text-white rounded text-xs disabled:opacity-50"
              >
                {llmRunMut.isPending ? <BotState variant="thinking" size={12} /> : <Bot size={12} />} Run LLM on Selected
              </button>
              <button
                onClick={() => rescoreMut.mutate([...selected])}
                disabled={rescoreMut.isPending}
                title="Re-run the deterministic matcher on the selected music/book items — instant, no LLM"
                className="flex items-center gap-1.5 px-3 py-1 bg-sky-700 hover:bg-sky-600 text-white rounded text-xs disabled:opacity-50"
              >
                <RefreshCw size={12} className={rescoreMut.isPending ? "animate-spin" : ""} /> Rescore Selected
              </button>
            </>
          )}
          <button onClick={() => setSelected(new Set())} className="text-xs text-slate-400 hover:text-white ml-auto">
            Clear
          </button>
        </div>
      )}

      {isLoading ? (
        <SkeletonTable rows={8} cols={9} />
      ) : sortedItems.length === 0 ? (
        <div className="bg-surface-raised rounded-xl border border-purple-900/30 p-10 text-center">
          <p className="text-slate-400">
            {statusFilter === "suggested"
              ? "No stuck imports awaiting review. The background poller checks your *arr queues automatically."
              : "Nothing here yet."}
          </p>
        </div>
      ) : viewMode === "table" ? (
        <ScrollFadeX className="bg-surface-raised rounded-xl border border-purple-900/30 overflow-x-auto">
          <table
            className={`text-sm ${density === "compact" ? "[&_td]:!py-1.5 [&_th]:!py-1.5 [&_td]:text-xs" : ""}`}
            style={{ tableLayout: "fixed", width: "100%", minWidth: totalWidth }}
          >
            <thead className="border-b border-purple-900/30 text-slate-400 text-xs uppercase tracking-wider">
              <tr>
                <th className={`${d.head} !px-3`} style={{ width: 40 }}>
                  <input type="checkbox" className="accent-purple-500"
                         checked={selected.size > 0 && selected.size === allSelectable.length}
                         onChange={toggleSelectAll} />
                </th>
                {cols.map(c => (
                  <th key={c.key} className={`text-left ${d.head} relative select-none group`}
                      style={{ width: colW(c) }}
                      title={c.tooltip}>
                    <button
                      onClick={() => c.sortField && toggleSort(c.sortField)}
                      className={`flex items-center gap-1 uppercase ${c.sortField ? "cursor-pointer hover:text-white" : "cursor-default"}`}
                      title={c.tooltip}
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
              {visibleItems.map((item: FailedImport) => {
                const actionable = item.status === "suggested" || item.status === "resolve_failed";
                const orphanPending = item.status === "orphan_pending";
                const selectable = actionable || orphanPending;
                const isExpanded = expanded === item.id;
                return (
                  <>
                    <tr
                      key={item.id}
                      id={`import-item-${item.id}`}
                      className={`hover:bg-white/5 transition-colors ${selectable ? "cursor-pointer" : ""} ${selected.has(item.id) ? "bg-brand/5" : ""} ${focusedId === item.id ? "ring-1 ring-inset ring-brand/60" : ""}`}
                      onClick={e => {
                        setFocusedId(item.id);
                        if (!selectable || isInteractiveTarget(e.target)) return;
                        toggleSelect(item.id);
                      }}
                    >
                      <td className="px-3 py-3" onClick={e => e.stopPropagation()}>
                        {selectable && (
                          <input type="checkbox" className="accent-purple-500"
                                 checked={selected.has(item.id)} onChange={() => toggleSelect(item.id)} />
                        )}
                      </td>
                      {cols.map(c => (
                        <td key={c.key} className="px-4 py-3 overflow-hidden" style={{ width: colW(c) }}>
                          {renderCell(item, c.key, isExpanded)}
                        </td>
                      ))}
                      <td className="px-4 py-3" onClick={e => e.stopPropagation()}>
                        {renderActions(item)}
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr key={`${item.id}-files`} className="bg-surface/50">
                        <td colSpan={cols.length + 2} className="border-t border-purple-900/10">
                          <FileDetails importId={item.id} sourceApp={item.source_app} matchedId={item.matched_id} packFileMatches={item.pack_file_matches} />
                          {actionable && <AutoGateInspector item={item} />}
                          {actionable && <MatchOverride item={item} onDone={invalidate} />}
                        </td>
                      </tr>
                    )}
                  </>
                );
              })}
            </tbody>
          </table>
        </ScrollFadeX>
      ) : (
        <div className="space-y-3">
          {visibleItems.map((item: FailedImport) => {
            const actionable = item.status === "suggested" || item.status === "resolve_failed";
            const orphanPending = item.status === "orphan_pending";
            const selectable = actionable || orphanPending;
            const isExpanded = expanded === item.id;
            const status = STATUS_META[item.status] ?? { label: item.status, cls: "bg-surface-overlay text-slate-300" };
            return (
              <div
                key={item.id}
                id={`import-item-${item.id}`}
                className={`bg-surface-raised rounded-xl border p-4 transition-colors ${
                  selected.has(item.id) ? "border-brand/50 bg-brand/5" : "border-purple-900/30"
                } ${focusedId === item.id ? "ring-1 ring-brand/60" : ""}`}
                onClick={() => setFocusedId(item.id)}
              >
                <div className="flex items-start justify-between gap-3 mb-3">
                  <div className="flex items-start gap-2 min-w-0 flex-1">
                    {selectable && (
                      <input
                        type="checkbox"
                        className="accent-purple-500 mt-1 flex-shrink-0"
                        checked={selected.has(item.id)}
                        onChange={() => toggleSelect(item.id)}
                      />
                    )}
                    <div className="min-w-0 flex-1">
                      {renderCell(item, "release", isExpanded)}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0">
                    <PlatformBadge app={item.source_app} />
                    <span className={`inline-block px-2 py-0.5 rounded text-xs font-bold ${status.cls}`}>{status.label}</span>
                  </div>
                </div>

                <div className="text-sm text-slate-300 mb-3">
                  <span className="text-slate-500 text-xs uppercase tracking-wider mr-2">Matched</span>
                  {renderCell(item, "matched", isExpanded)}
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-3">
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-slate-500 text-xs uppercase tracking-wider">Match</span>
                      {renderCell(item, "match_pct", isExpanded)}
                    </div>
                    {renderCell(item, "match_notes", isExpanded)}
                  </div>
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <Bot size={12} className="text-brand-light" />
                      <span className="text-slate-500 text-xs uppercase tracking-wider">LLM</span>
                      {renderCell(item, "llm_pct", isExpanded)}
                    </div>
                    {renderCell(item, "llm_notes", isExpanded)}
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-slate-500 text-xs">{renderCell(item, "detected", isExpanded)}</span>
                  {renderActions(item)}
                </div>

                {isExpanded && (
                  <div className="mt-3 -mx-4 -mb-4 bg-surface/50 rounded-b-xl border-t border-purple-900/10">
                    <FileDetails importId={item.id} sourceApp={item.source_app} matchedId={item.matched_id} packFileMatches={item.pack_file_matches} />
                    {actionable && <AutoGateInspector item={item} />}
                    {actionable && <MatchOverride item={item} onDone={invalidate} />}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
      {sortedItems.length > shown && (
        <div className="mt-4 flex justify-center">
          <button
            onClick={() => setShown(n => n + PAGE_SIZE)}
            className="px-4 py-2 rounded-lg border border-purple-900/40 bg-surface-raised text-sm text-slate-300 hover:text-white"
          >
            Show {Math.min(PAGE_SIZE, sortedItems.length - shown)} more ({sortedItems.length - shown} hidden)
          </button>
        </div>
      )}
    </div>
  );
}
