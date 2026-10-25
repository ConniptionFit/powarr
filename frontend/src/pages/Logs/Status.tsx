import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, RotateCcw, ShieldAlert } from "lucide-react";
import { systemApi, type BreakerStat, type DepHealth } from "../../lib/api";
import { useToast } from "../../context/ToastContext";
import { Skeleton } from "../../components/Skeleton";

function ago(epochSeconds: number | null): string {
  if (!epochSeconds) return "never";
  const s = Math.max(0, Math.round(Date.now() / 1000 - epochSeconds));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function stateOf(d: DepHealth): { label: string; dot: string; text: string } {
  if (d.breaker_open) return { label: "Paused (breaker open)", dot: "bg-red-400", text: "text-red-300" };
  if (d.ok === false) return { label: "Failing", dot: "bg-amber-400", text: "text-amber-300" };
  if (d.ok === true) return { label: "Healthy", dot: "bg-green-400", text: "text-green-300" };
  return { label: "Not checked yet", dot: "bg-slate-600", text: "text-slate-400" };
}

function Row({ dep, stat, onReset, resetting }: {
  dep: DepHealth; stat?: BreakerStat; onReset: () => void; resetting: boolean;
}) {
  const st = stateOf(dep);
  const rate = stat && stat.calls > 0 ? Math.round((stat.failures / stat.calls) * 100) : null;
  return (
    <li className="bg-surface-raised rounded-xl border border-purple-900/30 p-4">
      <div className="flex items-center gap-3 flex-wrap">
        <span className={`w-2.5 h-2.5 rounded-full ${st.dot}`} aria-hidden />
        <span className="text-white font-medium capitalize">{dep.name}</span>
        <span className={`text-xs ${st.text}`}>{st.label}</span>
        {dep.breaker_open && (
          <span className="text-xs text-slate-400">
            retrying in {Math.ceil(dep.breaker_seconds_remaining / 60)} min
          </span>
        )}
        <span className="ml-auto text-xs text-slate-500">
          checked {ago(dep.checked_at)}{dep.source ? ` (${dep.source})` : ""}
        </span>
        {(dep.breaker_open || (stat?.consecutive_failures ?? 0) > 0) && (
          <button onClick={onReset} disabled={resetting}
            className="flex items-center gap-1 text-xs text-brand-light hover:text-white disabled:opacity-40">
            <RotateCcw size={12} /> Reset breaker
          </button>
        )}
      </div>
      {stat && stat.calls > 0 && (
        <p className="text-xs text-slate-500 mt-2">
          {stat.calls.toLocaleString()} calls since start · {stat.failures.toLocaleString()} failed
          {rate !== null ? ` (${rate}%)` : ""} · {stat.breaker_trips} breaker trip{stat.breaker_trips === 1 ? "" : "s"}
          {" "}· last success {ago(stat.last_success_at)}
        </p>
      )}
      {(dep.last_error || (dep.ok === false && dep.message)) && (
        <p className="text-xs text-red-300/80 mt-1.5 break-words">
          {dep.last_error ?? dep.message}
          {stat?.last_error_at ? <span className="text-slate-600"> · {ago(stat.last_error_at)}</span> : null}
        </p>
      )}
    </li>
  );
}

// Per-integration health with the circuit-breaker state behind it. The cached view is cheap;
// "Check now" runs a live connection test against every enabled integration.
export default function Status() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["dependencies"],
    queryFn: () => systemApi.dependencies(false),
    refetchInterval: 15000,
  });
  const probe = useMutation({
    mutationFn: () => systemApi.dependencies(true),
    onSuccess: res => {
      qc.setQueryData(["dependencies"], res);
      const bad = res.integrations.filter(d => d.ok === false || d.breaker_open).length;
      bad ? toast.error(`${bad} integration${bad === 1 ? "" : "s"} need attention`) : toast.success("All integrations healthy");
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const reset = useMutation({
    mutationFn: (name?: string) => systemApi.resetBreaker(name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["dependencies"] });
      toast.success("Breaker reset");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const stats = new Map((data?.breakers ?? []).map(b => [b.name, b]));
  const deps = data?.integrations ?? [];
  const trouble = deps.filter(d => d.ok === false || d.breaker_open).length;

  return (
    <div className="p-4 sm:p-8 max-w-3xl">
      <div className="flex items-start gap-3 flex-wrap mb-5">
        <div>
          <p className="text-slate-400 text-sm max-w-xl">
            Health of each connected service. After repeated failures Powarr pauses calls to a service
            (a circuit breaker) so one outage doesn't slow everything else.
          </p>
        </div>
        <div className="ml-auto flex gap-2">
          {trouble > 0 && (
            <button onClick={() => reset.mutate(undefined)} disabled={reset.isPending}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-purple-900/40 text-slate-300 hover:text-white text-sm disabled:opacity-40">
              <ShieldAlert size={14} /> Reset all
            </button>
          )}
          <button onClick={() => probe.mutate()} disabled={probe.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-brand text-white text-sm disabled:opacity-40">
            <Activity size={14} className={probe.isPending || isFetching ? "animate-pulse" : ""} />
            {probe.isPending ? "Checking…" : "Check now"}
          </button>
        </div>
      </div>

      {isLoading ? (
        <Skeleton className="h-16 w-full" count={4} />
      ) : deps.length === 0 ? (
        <p className="text-slate-500 text-sm">No integrations are enabled yet. Enable one in Settings, then check here.</p>
      ) : (
        <ul className="space-y-3">
          {deps.map(d => (
            <Row key={d.name} dep={d} stat={stats.get(d.name)} resetting={reset.isPending}
                 onReset={() => reset.mutate(d.name)} />
          ))}
        </ul>
      )}
    </div>
  );
}
