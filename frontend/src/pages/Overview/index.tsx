import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { HardDrive, Film, Trash2, TrendingDown, RefreshCw, DownloadCloud, CheckCircle, Recycle, Clock, CalendarClock, Activity, AlertTriangle, Shuffle, ChevronRight } from "lucide-react";
import { mediaApi, integrationsApi, importsApi, systemApi, fmtBytes, type DepHealth } from "../../lib/api";
import { SkeletonGrid } from "../../components/Skeleton";

function PipelineChip({ icon: Icon, label, count, color, onClick }: {
  icon: React.ElementType;
  label: string;
  count: number | null;
  color: string;
  onClick?: () => void;
}) {
  const content = (
    <span className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium ${color}`}>
      <Icon size={15} />
      {count === null ? "—" : count} {label}
    </span>
  );
  if (!onClick) return content;
  return (
    <button onClick={onClick} className="transition-transform hover:scale-[1.03] cursor-pointer">
      {content}
    </button>
  );
}

function PipelineFlowCard({ queueCount, reviewCount, autoResolvedCount }: {
  queueCount: number | null;
  reviewCount: number | null;
  autoResolvedCount: number | null;
}) {
  const navigate = useNavigate();
  return (
    <div className="mt-4 bg-surface-raised rounded-xl border border-purple-900/30 p-5">
      <p className="text-slate-400 text-xs uppercase tracking-wider mb-3">Pipeline flow</p>
      <div className="flex items-center flex-wrap gap-2">
        <PipelineChip
          icon={DownloadCloud}
          label="in Import Queue"
          count={queueCount}
          color="bg-amber-900/40 text-amber-300"
          onClick={() => navigate("/imports/queue")}
        />
        <ChevronRight size={16} className="text-slate-600 flex-shrink-0" />
        <PipelineChip
          icon={Shuffle}
          label="awaiting Match Review"
          count={reviewCount}
          color="bg-purple-900/40 text-purple-300"
          onClick={() => navigate("/imports/match-review")}
        />
        <ChevronRight size={16} className="text-slate-600 flex-shrink-0" />
        <PipelineChip
          icon={CheckCircle}
          label="auto-resolved (7d)"
          count={autoResolvedCount}
          color="bg-green-900/40 text-green-300"
        />
      </div>
    </div>
  );
}

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

function StatCard({ icon: Icon, label, value, sub, color, error }: {
  icon: React.ElementType;
  label: string;
  value: string;
  sub?: string;
  color: string;
  error?: boolean;
}) {
  return (
    <div className={`bg-surface-raised rounded-xl border p-5 flex items-start gap-4 ${
      error ? "border-red-700/50" : "border-purple-900/30"
    }`}>
      <div className={`p-3 rounded-lg ${error ? "bg-red-900/60" : color}`}>
        <Icon size={20} className="text-white" />
      </div>
      <div className="min-w-0">
        <p className="text-slate-400 text-xs uppercase tracking-wider mb-1">{label}</p>
        <p className={`text-2xl font-bold ${error ? "text-red-300" : "text-white"}`}>{value}</p>
        {sub && <p className={`text-xs mt-0.5 ${error ? "text-red-400/80" : "text-slate-500"}`}>{sub}</p>}
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

function CountdownStat({ icon, label, color, targetIso, disabledLabel, sub, error }: {
  icon: React.ElementType;
  label: string;
  color: string;
  targetIso: string | null | undefined;
  disabledLabel: string;
  sub?: string;
  error?: boolean;
}) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!targetIso) return;
    const id = setInterval(() => setTick(t => t + 1), 1000);
    return () => clearInterval(id);
  }, [targetIso]);

  const value = error
    ? "—"
    : targetIso
      ? fmtCountdown(new Date(targetIso).getTime() - Date.now())
      : disabledLabel;
  return (
    <StatCard
      icon={icon}
      label={label}
      value={value}
      sub={error ? "schedule unavailable" : sub}
      color={color}
      error={error}
    />
  );
}

function cardValue(error: boolean, loading: boolean, formatted: string): string {
  if (error) return "—";
  if (loading) return "…";
  return formatted;
}

export default function Dashboard() {
  const qc = useQueryClient();
  const { data: stats, isLoading, isError: statsErr, refetch } = useQuery({
    queryKey: ["stats"],
    queryFn: mediaApi.stats,
  });

  const { data: importStats, isError: importErr, isLoading: importLoading } = useQuery({
    queryKey: ["import-stats"],
    queryFn: importsApi.stats,
  });

  const { data: importTrends, isError: trendsErr } = useQuery({
    queryKey: ["import-trends"],
    queryFn: () => importsApi.trends(30),
  });

  const { data: deletionStats, isError: delErr, isLoading: delLoading } = useQuery({
    queryKey: ["deletion-stats"],
    queryFn: mediaApi.deletionStats,
  });

  const { data: schedule, isError: schedErr } = useQuery({
    queryKey: ["schedule"],
    queryFn: systemApi.schedule,
    refetchInterval: 60_000,
  });

  const { data: deps } = useQuery({
    queryKey: ["dependencies"],
    queryFn: () => systemApi.dependencies(false),
    refetchInterval: 60_000,
  });

  const byService = importStats?.by_service ?? {};
  const byServiceLabel = importErr
    ? "unavailable"
    : Object.entries(byService).map(([app, n]) => `${app} ${n}`).join(" · ") || "none pending";

  const downIntegrations = (deps?.integrations ?? []).filter(
    (d: DepHealth) => d.ok === false || d.breaker_open,
  );

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
          <h1 className="text-2xl font-bold text-white">Overview</h1>
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

      {downIntegrations.length > 0 && (
        <div className="mb-4 flex items-start gap-2 rounded-lg border border-amber-700/40 bg-amber-950/30 px-4 py-3 text-sm text-amber-200">
          <AlertTriangle size={16} className="mt-0.5 flex-shrink-0" />
          <div>
            <p className="font-medium">Integration issue{downIntegrations.length > 1 ? "s" : ""}</p>
            <p className="text-amber-200/80 text-xs mt-0.5">
              {downIntegrations.map(d =>
                `${d.name}${d.breaker_open ? " (breaker open)" : ""}: ${d.message || "unreachable"}`
              ).join(" · ")}
            </p>
          </div>
        </div>
      )}

      {isLoading ? (
        <SkeletonGrid cols={4} rows={3} />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
          <StatCard
            icon={Film}
            label="Total Media Items"
            value={cardValue(statsErr, false, stats?.total_items.toLocaleString() ?? "0")}
            color="bg-blue-600"
            error={statsErr}
            sub={statsErr ? "stats unavailable" : undefined}
          />
          <StatCard
            icon={HardDrive}
            label="Total Library Size"
            value={cardValue(statsErr, false, fmtBytes(stats?.total_size_bytes ?? 0))}
            color="bg-indigo-600"
            error={statsErr}
            sub={statsErr ? "stats unavailable" : undefined}
          />
          <StatCard
            icon={Trash2}
            label="Deletion Candidates"
            value={cardValue(statsErr, false, stats?.candidates_above_threshold.toLocaleString() ?? "0")}
            sub={statsErr ? "stats unavailable" : "above score threshold"}
            color="bg-red-700"
            error={statsErr}
          />
          <StatCard
            icon={TrendingDown}
            label="Potential Savings"
            value={cardValue(statsErr, false, fmtBytes(stats?.potential_savings_bytes ?? 0))}
            sub={statsErr ? "stats unavailable" : "if candidates deleted"}
            color="bg-emerald-700"
            error={statsErr}
          />
          <StatCard
            icon={DownloadCloud}
            label="Failed Imports"
            value={cardValue(importErr, importLoading, (importStats?.suggested ?? 0).toLocaleString())}
            sub={byServiceLabel}
            color="bg-purple-700"
            error={importErr}
          />
          <StatCard
            icon={CheckCircle}
            label="Auto-Resolved (7d)"
            value={cardValue(importErr, importLoading, (importStats?.auto_resolved_7d ?? 0).toLocaleString())}
            sub={importErr ? "unavailable" : "imports pushed automatically"}
            color="bg-teal-700"
            error={importErr}
          />
          <StatCard
            icon={Recycle}
            label="Space Freed (30d)"
            value={cardValue(delErr, delLoading, fmtBytes(deletionStats?.freed_30d_bytes ?? 0))}
            sub={delErr ? "unavailable" : `${deletionStats?.deleted_30d ?? 0} items deleted`}
            color="bg-green-800"
            error={delErr}
          />
          <StatCard
            icon={Trash2}
            label="Push Failures"
            value={cardValue(importErr, importLoading, (importStats?.resolve_failed ?? 0).toLocaleString())}
            sub={importErr ? "unavailable" : "imports needing re-triage"}
            color="bg-red-800"
            error={importErr}
          />
          <CountdownStat
            icon={Clock}
            label="Next Import Scan"
            targetIso={schedule?.next_scan_at ?? null}
            disabledLabel="Disabled"
            sub="Failed Import Matching → Detection Enabled"
            color="bg-cyan-700"
            error={schedErr}
          />
          <CountdownStat
            icon={CalendarClock}
            label="Next Plex Sync"
            targetIso={schedule?.next_sync_at ?? null}
            disabledLabel="Manual only"
            sub="Settings → Sync Interval"
            color="bg-violet-700"
            error={schedErr}
          />
          {trendsErr ? (
            <div className="bg-surface-raised rounded-xl border border-red-700/40 p-5 sm:col-span-2">
              <p className="text-red-300 text-sm">Failed import trend unavailable</p>
            </div>
          ) : importTrends ? (
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
          ) : null}
        </div>
      )}

      {!isLoading && (
        <PipelineFlowCard
          queueCount={importErr ? null : (importStats?.suggested ?? 0) + (importStats?.resolve_failed ?? 0) + (importStats?.orphan_pending ?? 0)}
          reviewCount={importErr ? null : (importStats?.suggested ?? 0) + (importStats?.resolve_failed ?? 0) + (importStats?.orphan_pending ?? 0)}
          autoResolvedCount={importErr ? null : importStats?.auto_resolved_7d ?? 0}
        />
      )}
    </div>
  );
}
