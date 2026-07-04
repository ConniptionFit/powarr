import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw, Pause, Play } from "lucide-react";
import { systemApi } from "../../lib/api";

const LEVEL_COLORS: Record<string, string> = {
  ERROR: "text-red-400",
  WARNING: "text-yellow-400",
  INFO: "text-slate-300",
  DEBUG: "text-slate-500",
};

function lineColor(line: string): string {
  for (const [level, cls] of Object.entries(LEVEL_COLORS)) {
    if (line.includes(` ${level} `)) return cls;
  }
  return "text-slate-400";
}

export default function LogsPage() {
  const [autoRefresh, setAutoRefresh] = useState(true);

  const { data, refetch, isFetching } = useQuery({
    queryKey: ["logs"],
    queryFn: () => systemApi.logs(500),
    refetchInterval: autoRefresh ? 5000 : false,
  });

  const lines = data?.lines ?? [];

  return (
    <div className="p-8 flex flex-col h-full">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">Logs</h1>
          <p className="text-slate-400 text-sm mt-1">Recent application log output (in-memory, last 1000 lines)</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setAutoRefresh(a => !a)}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-surface-overlay hover:bg-white/10 text-slate-300 text-sm transition-colors"
          >
            {autoRefresh ? <Pause size={14} /> : <Play size={14} />}
            {autoRefresh ? "Pause" : "Resume"}
          </button>
          <button
            onClick={() => refetch()}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-brand/20 text-brand-light hover:bg-brand/30 text-sm transition-colors"
          >
            <RefreshCw size={14} className={isFetching ? "animate-spin" : ""} />
            Refresh
          </button>
        </div>
      </div>

      <div className="flex-1 bg-surface-raised rounded-xl border border-purple-900/30 p-4 overflow-y-auto font-mono text-xs leading-relaxed">
        {lines.length === 0 ? (
          <p className="text-slate-500">No log output captured yet.</p>
        ) : (
          lines.map((line, i) => (
            <div key={i} className={`whitespace-pre-wrap break-all ${lineColor(line)}`}>{line}</div>
          ))
        )}
      </div>
    </div>
  );
}
