import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { HardDrive, Film, Trash2, TrendingDown, RefreshCw } from "lucide-react";
import { mediaApi, integrationsApi, fmtBytes } from "../../lib/api";

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

export default function Dashboard() {
  const qc = useQueryClient();
  const { data: stats, isLoading, refetch } = useQuery({
    queryKey: ["stats"],
    queryFn: mediaApi.stats,
  });

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
    <div className="p-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white">Dashboard</h1>
          <p className="text-slate-400 text-sm mt-1">Media library overview</p>
        </div>
        <div className="flex items-center gap-3">
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
        </div>
      )}

      <div className="mt-10 bg-surface-raised rounded-xl border border-purple-900/30 p-6">
        <h2 className="text-lg font-semibold text-white mb-2">Getting Started</h2>
        <ol className="text-slate-400 text-sm space-y-2 list-decimal list-inside">
          <li>Configure your Plex connection in <span className="text-brand-light">Integrations</span></li>
          <li>Optionally enable Tautulli for enriched watch history</li>
          <li>Connect Sonarr / Radarr / Lidarr for deletion control</li>
          <li>Run a Plex sync, then visit <span className="text-brand-light">Cleanup</span> to review candidates</li>
          <li>Tune scoring weights in <span className="text-brand-light">Settings</span></li>
        </ol>
      </div>
    </div>
  );
}
