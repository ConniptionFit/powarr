import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { HardDrive, Film, Trash2, TrendingDown, RefreshCw, DownloadCloud, CheckCircle, Recycle, Clock, CalendarClock, Activity } from "lucide-react";
import { mediaApi, integrationsApi, importsApi, systemApi, fmtBytes } from "../../lib/api";

function Sparkline({ values, color = "#a78bfa" }: { values: number[]; color?: string }) {
  const w = 160, h = 36, pad = 2;
  if (!values.length) return null;
  const max = Math.max(...values, 1);
  const step = values.length > 1 ? (w - pad * 2) / (values.length - 1) : 0;
  const pts = values.map((v, i) => {
    const x = pad + i * step;
    const y = h - pad - (v / max) * (h - pad * 2);
    return `${x},${y}`;
  }).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-9" preserveAspectRatio="none">
      <polyline fill="none" stroke={color} strokeWidth="1.5" points={pts} />
    </svg>
  );
}

function StatCard({ icon: Icon, label, value, sub, color }: {
  icon: React.ElementType;
  label: string;
  value: string;
  sub?: string;
  color: string;
}) {
  return (
    <div className="bg-surface-raised rounded-xl border border-purple-900/30 p-5 flex items-start gap-4">
      <div className={`p-3 rounded-lg ${color}`}>
        <Icon size={20} className="text-white" />
      </div>
      <div>
        <p className="text-slate-400 text-xs uppercase tracking-wider mb-1">{label}</p>
        <p className="text-2xl font-bold text-white">{value}</p>
        {sub && <p className="text-slate-500 text-xs mt-0.5">{sub}</p>}
      </div>
    </div>
  );
}

function fmtCountdown(ms: number): string {
  if (ms <= 0) return "Due now";
  const totalSec = Math.floor(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

// Ticks its own display every second without re-rendering the rest of the
// dashboard — the target time comes from the server (next_scan_at etc.),
// this just formats "time remaining" and re-formats on each tick.
function CountdownStat({ icon, label, color, targetIso, disabledLabel, sub }: {
  icon: React.ElementType;
  label: string;
  color: string;
  targetIso: string | null;
  disabledLabel: string;
  sub?: string;
}) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!targetIso) return;
    const id = setInterval(() => setTick(t => t + 1), 1000);
    return () => clearInterval(id);
  }, [targetIso]);

  const value = targetIso ? fmtCountdown(new Date(targetIso).getTime() - Date.now()) : disabledLabel;
  return <StatCard icon={icon} label={label} value={value} sub={sub} color={color} />;
}

export default function Dashboard() {
  const qc = useQueryClient();
  const { data: stats, isLoading, refetch } = useQuery({
    queryKey: ["stats"],
    queryFn: mediaApi.stats,
  });

  const { data: importStats } = useQuery({
    queryKey: ["import-stats"],
    queryFn: importsApi.stats,
  });

  const { data: importTrends } = useQuery({
    queryKey: ["import-trends"],
    queryFn: () => importsApi.trends(30),
  });

  const { data: deletionStats } = useQuery({
    queryKey: ["deletion-stats"],
    queryFn: mediaApi.deletionStats,
  });

  const { data: schedule } = useQuery({
    queryKey: ["schedule"],
    queryFn: systemApi.schedule,
    refetchInterval: 60_000, // periodically resync with the server in case a scan/sync just ran elsewhere
  });

  const byService = importStats?.by_service ?? {};
  const byServiceLabel = Object.entries(byService)
    .map(([app, n]) => `${app} ${n}`)
    .join(" · ") || "none pending";

  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);

  const handleSync = async () => {
    setSyncing(true);
    setSyncMsg(null);
    try {
      const result = await integrationsApi.syncPlex();
      setSyncMsg(`Synced ${result.synced} items`);
      qc.invalidateQueries({ queryKey: ["stats"] });
      qc.invalidateQueries({ queryKey: ["media"] });
    } catch (e: unknown) {
      setSyncMsg(`Sync failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className="p-4 sm:p-8">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white">Dashboard</h1>
          <p className="text-slate-400 text-sm mt-1">
            Media library overview
            {stats?.last_synced && (
              <span className="text-slate-500"> — last synced {new Date(stats.last_synced).toLocaleString()}</span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          {syncMsg && (
            <span className={`text-sm ${syncMsg.startsWith("Sync failed") ? "text-red-400" : "text-green-400"}`}>
              {syncMsg}
            </span>
          )}
          <button
            onClick={handleSync}
            disabled={syncing}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-yellow-700 hover:bg-yellow-600 text-white text-sm transition-colors disabled:opacity-50"
          >
            <RefreshCw size={15} className={syncing ? "animate-spin" : ""} />
            {syncing ? "Syncing…" : "Sync Library"}
          </button>
          <button
            onClick={() => refetch()}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-brand/20 text-brand-light hover:bg-brand/30 text-sm transition-colors"
          >
            <RefreshCw size={15} />
            Refresh
          </button>
        </div>
      </div>

      {isLoading ? (
        <p className="text-slate-400">Loading...</p>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
          <StatCard
            icon={Film}
            label="Total Media Items"
            value={stats?.total_items.toLocaleString() ?? "0"}
            color="bg-blue-600"
          />
          <StatCard
            icon={HardDrive}
            label="Total Library Size"
            value={fmtBytes(stats?.total_size_bytes ?? 0)}
            color="bg-indigo-600"
          />
          <StatCard
            icon={Trash2}
            label="Deletion Candidates"
            value={stats?.candidates_above_threshold.toLocaleString() ?? "0"}
            sub="above score threshold"
            color="bg-red-700"
          />
          <StatCard
            icon={TrendingDown}
            label="Potential Savings"
            value={fmtBytes(stats?.potential_savings_bytes ?? 0)}
            sub="if candidates deleted"
            color="bg-emerald-700"
          />
          <StatCard
            icon={DownloadCloud}
            label="Failed Imports"
            value={(importStats?.suggested ?? 0).toLocaleString()}
            sub={byServiceLabel}
            color="bg-purple-700"
          />
          <StatCard
            icon={CheckCircle}
            label="Auto-Resolved (7d)"
            value={(importStats?.auto_resolved_7d ?? 0).toLocaleString()}
            sub="imports pushed automatically"
            color="bg-teal-700"
          />
          <StatCard
            icon={Recycle}
            label="Space Freed (30d)"
            value={fmtBytes(deletionStats?.freed_30d_bytes ?? 0)}
            sub={`${deletionStats?.deleted_30d ?? 0} items deleted`}
            color="bg-green-800"
          />
          <StatCard
            icon={Trash2}
            label="Push Failures"
            value={(importStats?.resolve_failed ?? 0).toLocaleString()}
            sub="imports needing re-triage"
            color="bg-red-800"
          />
          <CountdownStat
            icon={Clock}
            label="Next Import Scan"
            targetIso={schedule?.next_scan_at ?? null}
            disabledLabel="Disabled"
            sub="Failed Import Matching → Detection Enabled"
            color="bg-cyan-700"
          />
          <CountdownStat
            icon={CalendarClock}
            label="Next Plex Sync"
            targetIso={schedule?.next_sync_at ?? null}
            disabledLabel="Manual only"
            sub="Settings → Sync Interval"
            color="bg-violet-700"
          />
          {importTrends && (
            <div className="bg-surface-raised rounded-xl border border-purple-900/30 p-5 sm:col-span-2">
              <div className="flex items-start gap-4">
                <div className="p-3 rounded-lg bg-fuchsia-800">
                  <Activity size={20} className="text-white" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-slate-400 text-xs uppercase tracking-wider mb-1">Failed Import Trend (30d)</p>
                  <div className="flex items-end gap-6">
                    <div className="flex-1 min-w-0">
                      <p className="text-xs text-violet-300 mb-1">New</p>
                      <Sparkline values={importTrends.new} color="#a78bfa" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-xs text-teal-300 mb-1">Resolved</p>
                      <Sparkline values={importTrends.resolved} color="#2dd4bf" />
                    </div>
                    <div className="text-right shrink-0">
                      <p className="text-2xl font-bold text-white">
                        {importTrends.new.reduce((a, b) => a + b, 0)}
                      </p>
                      <p className="text-slate-500 text-xs">new · {importTrends.resolved.reduce((a, b) => a + b, 0)} resolved</p>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
