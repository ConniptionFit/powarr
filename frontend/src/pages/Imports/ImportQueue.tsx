import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, ArrowRight } from "lucide-react";
import { importsApi, fmtDate, type FailedImport } from "../../lib/api";
import { usePersistedState } from "../../lib/usePersistedState";
import { PlatformBadge } from "../../components/PlatformIcon";
import { SkeletonTable } from "../../components/Skeleton";

const STATUS_META: Record<string, { label: string; cls: string }> = {
  suggested: { label: "Suggested", cls: "bg-yellow-900/60 text-yellow-300" },
  resolve_failed: { label: "Push failed", cls: "bg-red-900/60 text-red-300" },
  orphan_pending: { label: "Confirm orphan", cls: "bg-orange-900/60 text-orange-300" },
};

// Actionable/undecided statuses — the items still sitting in the detection
// queue awaiting a match-review decision. Match Review (and its full status
// tab row) is where the terminal statuses (accepted/rejected/orphaned/etc.)
// live.
const QUEUE_FILTERS = ["suggested", "resolve_failed", "orphan_pending", ""] as const;

export default function ImportQueue() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [statusFilter, setStatusFilter] = usePersistedState<string>("powarr.importQueue.statusFilter", "");
  const [scanning, setScanning] = useState(false);
  const [actionMsg, setActionMsg] = useState<string | null>(null);

  const { data: items = [], isLoading } = useQuery({
    queryKey: ["imports", "queue", statusFilter],
    queryFn: () => importsApi.list(statusFilter || undefined),
    select: (all: FailedImport[]) =>
      statusFilter ? all : all.filter(i => QUEUE_FILTERS.includes(i.status as (typeof QUEUE_FILTERS)[number])),
  });

  const { data: stats } = useQuery({
    queryKey: ["import-stats"],
    queryFn: importsApi.stats,
  });

  const filterLabel = (f: string) => f === "" ? "All queued" : STATUS_META[f]?.label ?? f;
  const filterCount = (f: string): number | null => {
    if (!stats) return null;
    if (f === "") return stats.suggested + stats.resolve_failed + stats.orphan_pending;
    return (stats as unknown as Record<string, number>)[f] ?? null;
  };

  const handleScan = async () => {
    setScanning(true);
    setActionMsg(null);
    try {
      await importsApi.scan();
      qc.invalidateQueries({ queryKey: ["imports"] });
      qc.invalidateQueries({ queryKey: ["import-stats"] });
    } catch (e: unknown) {
      setActionMsg(`Scan failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setScanning(false);
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-5 gap-3 flex-wrap">
        <div>
          <p className="text-slate-400 text-sm">Stuck *arr downloads detected by the background poller — send to Match Review to score and accept</p>
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

      <div className="flex items-center gap-2 mb-5 flex-wrap">
        {QUEUE_FILTERS.map(f => (
          <button
            key={f}
            onClick={() => setStatusFilter(f)}
            className={`px-3 py-1.5 rounded-lg text-sm transition-colors ${
              statusFilter === f ? "bg-brand text-white" : "bg-surface-raised text-slate-400 hover:text-white border border-purple-900/40"
            }`}
          >
            {filterLabel(f)}
            {filterCount(f) !== null && <span className="ml-1.5 text-xs opacity-70">{filterCount(f)}</span>}
          </button>
        ))}
        {actionMsg && <span className="text-sm text-red-400 ml-2">{actionMsg}</span>}
      </div>

      {isLoading ? (
        <SkeletonTable rows={8} cols={4} />
      ) : items.length === 0 ? (
        <div className="bg-surface-raised rounded-xl border border-purple-900/30 p-10 text-center">
          <p className="text-slate-400">No stuck imports awaiting review. The background poller checks your *arr queues automatically.</p>
        </div>
      ) : (
        <div className="bg-surface-raised rounded-xl border border-purple-900/30 divide-y divide-purple-900/20">
          {items.map(item => {
            const status = STATUS_META[item.status] ?? { label: item.status, cls: "bg-surface-overlay text-slate-300" };
            return (
              <div key={item.id} className="flex items-center gap-4 px-4 py-3">
                <PlatformBadge app={item.source_app} />
                <div className="flex-1 min-w-0">
                  <p className="text-white font-medium truncate" title={item.raw_title}>{item.raw_title}</p>
                  <p className="text-slate-500 text-xs">detected {fmtDate(item.created_at)}</p>
                </div>
                <span className={`inline-block px-2 py-0.5 rounded text-xs font-bold flex-shrink-0 ${status.cls}`}>{status.label}</span>
                <button
                  onClick={() => navigate(`/imports/match-review?focus=${item.id}`)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-brand/20 text-brand-light hover:bg-brand/30 text-xs font-medium transition-colors flex-shrink-0"
                >
                  Send to Match Review <ArrowRight size={13} />
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
