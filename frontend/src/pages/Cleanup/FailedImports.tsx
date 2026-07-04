import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, X, RefreshCw, Bot, ChevronDown, ChevronRight, Trash2 } from "lucide-react";
import { importsApi, fmtDate, fmtBytes, type FailedImport } from "../../lib/api";

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
};

function ConfidenceBadge({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color =
    value >= 0.9 ? "bg-green-900/60 text-green-300" :
    value >= 0.7 ? "bg-yellow-900/60 text-yellow-300" :
    "bg-red-900/60 text-red-300";
  return <span className={`inline-block px-2 py-0.5 rounded text-xs font-bold ${color}`}>{pct}%</span>;
}

function FileDetails({ importId }: { importId: number }) {
  const { data, isLoading } = useQuery({
    queryKey: ["import-files", importId],
    queryFn: () => importsApi.files(importId),
    staleTime: 60_000,
  });
  if (isLoading) return <p className="text-slate-500 text-xs px-4 py-2">Loading file details…</p>;
  if (!data || data.files.length === 0)
    return <p className="text-slate-500 text-xs px-4 py-2">{data?.message ?? "No file details available"}</p>;
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
        {data.files.map((f, i) => (
          <tr key={i}>
            <td className="px-4 py-1.5 max-w-xs truncate" title={f.path ?? ""}>{f.path ?? "—"}</td>
            <td className="px-4 py-1.5">{f.size ? fmtBytes(f.size) : "—"}</td>
            <td className="px-4 py-1.5">{f.quality ?? "—"}</td>
            <td className="px-4 py-1.5">{f.mapped_to ? `${f.mapped_to}${f.detail ? ` (${f.detail})` : ""}` : "unmapped"}</td>
            <td className="px-4 py-1.5 max-w-xs truncate" title={f.rejections.join("; ")}>
              {f.rejections.length ? f.rejections.join("; ") : "—"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const FILTERS = ["suggested", "resolve_failed", "auto_resolved", "accepted", "rejected", ""] as const;

export default function FailedImports() {
  const qc = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<string>("suggested");
  const [scanning, setScanning] = useState(false);
  const [confirmAccept, setConfirmAccept] = useState<number | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [expanded, setExpanded] = useState<number | null>(null);

  const { data: items = [], isLoading } = useQuery({
    queryKey: ["imports", statusFilter],
    queryFn: () => importsApi.list(statusFilter || undefined),
  });

  const { data: stats } = useQuery({
    queryKey: ["import-stats"],
    queryFn: importsApi.stats,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["imports"] });
    qc.invalidateQueries({ queryKey: ["import-stats"] });
    setSelected(new Set());
  };

  // Live updates: the poller publishes an SSE event whenever a scan changes something
  useEffect(() => {
    const es = new EventSource("/api/v1/imports/events");
    es.onmessage = () => {
      qc.invalidateQueries({ queryKey: ["imports"] });
      qc.invalidateQueries({ queryKey: ["import-stats"] });
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
    mutationFn: ({ ids, action }: { ids: number[]; action: "accept" | "reject" }) => importsApi.batch(ids, action),
    onSuccess: r => { setActionMsg(`Batch done: ${r.results.length} item(s) processed`); invalidate(); },
    onError: (e: Error) => setActionMsg(`Batch failed: ${e.message}`),
  });

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
  const allSelectable = items.filter(i => i.status === "suggested" || i.status === "resolve_failed");
  const toggleSelectAll = () => {
    setSelected(prev => prev.size === allSelectable.length && allSelectable.length > 0
      ? new Set() : new Set(allSelectable.map(i => i.id)));
  };

  const filterLabel = (f: string) => f === "" ? "All" : STATUS_META[f]?.label ?? f;
  const filterCount = (f: string): number | null => {
    if (!stats) return null;
    if (f === "") return stats.suggested + stats.auto_resolved + stats.accepted + stats.rejected
      + stats.closed_external + stats.resolve_failed;
    return (stats as unknown as Record<string, number>)[f] ?? null;
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-5">
        <p className="text-slate-400 text-sm">Stuck *arr downloads matched against your library — accept to push the import</p>
        <button
          onClick={handleScan}
          disabled={scanning}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-brand text-white hover:bg-brand-dark text-sm transition-colors disabled:opacity-50"
        >
          <RefreshCw size={15} className={scanning ? "animate-spin" : ""} />
          {scanning ? "Scanning…" : "Scan Now"}
        </button>
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
        {actionMsg && <span className="text-sm text-slate-300 ml-2">{actionMsg}</span>}
      </div>

      {/* Batch action bar */}
      {selected.size > 0 && (
        <div className="flex items-center gap-3 mb-4 px-4 py-2.5 bg-brand/10 border border-brand/30 rounded-lg">
          <span className="text-sm text-brand-light">{selected.size} selected</span>
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
          <button onClick={() => setSelected(new Set())} className="text-xs text-slate-400 hover:text-white ml-auto">
            Clear
          </button>
        </div>
      )}

      {isLoading ? (
        <p className="text-slate-400">Loading…</p>
      ) : items.length === 0 ? (
        <div className="bg-surface-raised rounded-xl border border-purple-900/30 p-10 text-center">
          <p className="text-slate-400">
            {statusFilter === "suggested"
              ? "No stuck imports awaiting review. The background poller checks your *arr queues automatically."
              : "Nothing here yet."}
          </p>
        </div>
      ) : (
        <div className="bg-surface-raised rounded-xl border border-purple-900/30 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="border-b border-purple-900/30 text-slate-400 text-xs uppercase tracking-wider">
              <tr>
                <th className="px-3 py-3 w-8">
                  <input type="checkbox" className="accent-purple-500"
                         checked={selected.size > 0 && selected.size === allSelectable.length}
                         onChange={toggleSelectAll} />
                </th>
                <th className="text-left px-4 py-3">Source</th>
                <th className="text-left px-4 py-3">Release</th>
                <th className="text-left px-4 py-3">Matched To</th>
                <th className="text-left px-4 py-3">Confidence</th>
                <th className="text-left px-4 py-3">Status</th>
                <th className="text-left px-4 py-3">Detected</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-purple-900/20">
              {items.map((item: FailedImport) => {
                const status = STATUS_META[item.status] ?? { label: item.status, cls: "bg-surface-overlay text-slate-300" };
                const actionable = item.status === "suggested" || item.status === "resolve_failed";
                const isExpanded = expanded === item.id;
                return (
                  <>
                    <tr key={item.id} className="hover:bg-white/5 transition-colors">
                      <td className="px-3 py-3">
                        {actionable && (
                          <input type="checkbox" className="accent-purple-500"
                                 checked={selected.has(item.id)} onChange={() => toggleSelect(item.id)} />
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <span className={`px-2 py-0.5 rounded text-xs font-bold text-white ${APP_COLORS[item.source_app] ?? "bg-slate-600"}`}>
                          {item.source_app}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-white font-medium max-w-xs">
                        <button onClick={() => setExpanded(isExpanded ? null : item.id)}
                                className="flex items-center gap-1 hover:text-brand-light transition-colors text-left">
                          {isExpanded ? <ChevronDown size={13} className="flex-shrink-0" /> : <ChevronRight size={13} className="flex-shrink-0" />}
                          <span className="truncate" title={item.raw_title}>{item.raw_title}</span>
                        </button>
                        {item.message && <span className="block text-slate-500 text-xs truncate pl-4" title={item.message}>{item.message}</span>}
                      </td>
                      <td className="px-4 py-3 text-slate-300 max-w-xs">
                        <span className="block truncate" title={item.matched_title ?? ""}>{item.matched_title ?? "—"}</span>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1.5">
                          <ConfidenceBadge value={item.confidence} />
                          {item.llm_confidence !== null && (
                            <span title={`LLM: ${Math.round((item.llm_confidence ?? 0) * 100)}% — ${item.llm_rationale ?? ""}`}>
                              <Bot size={13} className="text-brand-light" />
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`inline-block px-2 py-0.5 rounded text-xs font-bold ${status.cls}`}>{status.label}</span>
                        {item.verified === true && <span className="block text-green-500 text-xs mt-0.5">verified</span>}
                      </td>
                      <td className="px-4 py-3 text-slate-400">{fmtDate(item.created_at)}</td>
                      <td className="px-4 py-3">
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
                              </>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr key={`${item.id}-files`} className="bg-surface/50">
                        <td colSpan={8} className="border-t border-purple-900/10">
                          <FileDetails importId={item.id} />
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
