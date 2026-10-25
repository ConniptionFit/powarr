import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, X, ChevronDown, ChevronUp, HelpCircle, CheckCircle2, XCircle } from "lucide-react";
import { importsApi, type FailedImport, type AutoGateStatus } from "../../../lib/api";
import { Skeleton } from "../../../components/Skeleton";

export function GateLeg({ label, leg }: { label: string; leg: AutoGateStatus["algorithm"] }) {
  return (
    <div className={`px-2 py-1 rounded border text-xs ${leg.passes ? "border-green-900/40 bg-green-950/20" : "border-purple-900/20 bg-surface"}`}>
      <span className="text-slate-500">{label}: </span>
      <span className={leg.passes ? "text-green-300 font-semibold" : "text-slate-300"}>
        {leg.value != null ? leg.value.toFixed(2) : "—"}
      </span>
      <span className="text-slate-600"> / {leg.threshold.toFixed(2)}</span>
      {leg.passes
        ? <Check size={11} className="inline ml-1 text-green-400" />
        : <X size={11} className="inline ml-1 text-slate-600" />}
    </div>
  );
}

// FI-07 — "why not auto?" inspector. Read-only: shows the already-computed
// heuristic/LLM signals against the configured thresholds, so a near-miss row
// doesn't require reading logs to understand why it's still sitting in triage.
export function AutoGateInspector({ item }: { item: FailedImport }) {
  const [open, setOpen] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ["import-auto-gate", item.id],
    queryFn: () => importsApi.autoGate(item.id),
    enabled: open,
    staleTime: 30_000,
  });

  return (
    <div className="px-4 py-2 border-t border-purple-900/10">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-200 transition-colors"
      >
        <HelpCircle size={12} />
        Why not auto-import?
        {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
      </button>
      {open && (
        isLoading || !data ? (
          <Skeleton className="h-4 w-40 mt-2" />
        ) : (
          <div className="mt-2 space-y-2">
            <div className="flex flex-wrap items-center gap-3">
              <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-bold ${
                data.would_auto_import ? "bg-green-950/40 text-green-300" : "bg-surface-overlay text-slate-400"
              }`}>
                {data.would_auto_import ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
                {data.would_auto_import ? "Would auto-import" : "Not auto-importing"}
              </span>
              <span className="text-xs text-slate-500">
                mode: <span className="text-slate-300 font-mono">{data.mode}</span>
              </span>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <GateLeg label="Algorithm" leg={data.algorithm} />
              <GateLeg label="LLM" leg={data.llm} />
            </div>
            {data.reasons.length > 0 && (
              <ul className="list-disc list-inside text-xs text-slate-400 space-y-0.5">
                {data.reasons.map((r, i) => <li key={i}>{r}</li>)}
              </ul>
            )}
          </div>
        )
      )}
    </div>
  );
}
