import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronUp, Eye, XCircle } from "lucide-react";
import { Link } from "react-router-dom";
import { type Decision, type PreviewResult, type StatusCheck, api } from "./shared";

const ACTION_STYLE: Record<string, string> = {
  added: "bg-green-900/40 text-green-300 border-green-700/50",
  would_add: "bg-green-900/40 text-green-300 border-green-700/50",
  queued: "bg-purple-900/40 text-purple-200 border-purple-700/50",
  would_queue: "bg-purple-900/40 text-purple-200 border-purple-700/50",
  skipped: "bg-slate-800/60 text-slate-400 border-slate-700/50",
  failed: "bg-red-900/40 text-red-300 border-red-700/50",
};

const ACTION_LABEL: Record<string, string> = {
  added: "Added", would_add: "Would add", queued: "Queued", would_queue: "Would queue",
  skipped: "Skipped", failed: "Failed",
};

export function DecisionList({ decisions }: { decisions: Decision[] }) {
  if (decisions.length === 0) {
    return <p className="text-slate-500 text-xs">No per-artist decisions were recorded.</p>;
  }
  return (
    <ul className="divide-y divide-purple-900/20">
      {decisions.map((d, i) => (
        <li key={d.id ?? `${d.artist_name}-${i}`} className="py-2 flex items-start gap-3 text-xs">
          <span className={`shrink-0 mt-0.5 px-2 py-0.5 rounded-full border font-medium ${ACTION_STYLE[d.action] ?? ACTION_STYLE.skipped}`}>
            {ACTION_LABEL[d.action] ?? d.action}
          </span>
          <div className="min-w-0">
            <p className="text-white font-medium truncate">
              {d.artist_name}
              {d.score != null && <span className="ml-2 text-slate-500 font-normal">score {d.score.toFixed(2)}</span>}
              {d.connections != null && <span className="ml-2 text-slate-500 font-normal">{d.connections} seed{d.connections === 1 ? "" : "s"}</span>}
            </p>
            <p className="text-slate-400">{d.reason}</p>
            {d.reason_line && <p className="text-slate-500">{d.reason_line}</p>}
            {(d.bucket || (d.tags && d.tags.length > 0)) && (
              <p className="text-slate-600">
                {d.bucket && <span className="mr-2 capitalize">{d.bucket}</span>}
                {d.tags && d.tags.slice(0, 3).join(" · ")}
                {d.listeners != null && <span className="ml-2">{d.listeners.toLocaleString()} listeners</span>}
                {d.sources && d.sources.length > 1 && <span className="ml-2">via {d.sources.join(" + ")}</span>}
              </p>
            )}
            {d.seeds.length > 0 && (
              <p className="text-slate-600 truncate">Similar to {d.seeds.slice(0, 4).join(", ")}{d.seeds.length > 4 ? ` +${d.seeds.length - 4}` : ""}</p>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

function CheckRow({ c }: { c: StatusCheck }) {
  const Icon = c.status === "ok" ? CheckCircle2 : c.status === "warn" ? AlertTriangle : XCircle;
  const color = c.status === "ok" ? "text-green-400" : c.status === "warn" ? "text-amber-400" : "text-red-400";
  return (
    <li className="flex items-start gap-2 text-xs py-1">
      <Icon size={14} className={`shrink-0 mt-0.5 ${color}`} />
      <span><span className="text-white font-medium">{c.label}.</span> <span className="text-slate-400">{c.detail}</span></span>
    </li>
  );
}

/** "Why isn't it adding anything?" checklist. Open by default while something is wrong. */
export function StatusPanel() {
  const [deep, setDeep] = useState(false);
  const [open, setOpen] = useState<boolean | null>(null);
  const { data, isFetching } = useQuery({ queryKey: ["ad-status", deep], queryFn: () => api.status(deep) });
  if (!data) return null;
  const isOpen = open ?? data.overall === "error";
  const tone = data.overall === "error"
    ? "border-red-800/50 bg-red-950/20" : data.overall === "warn"
      ? "border-amber-800/40 bg-amber-900/10" : "border-purple-900/30 bg-surface-raised";
  const problems = data.checks.filter((c) => c.status !== "ok").length;
  return (
    <div className={`mb-6 rounded-xl border px-4 py-3 ${tone}`}>
      <button onClick={() => setOpen(!isOpen)} className="w-full flex items-center justify-between text-sm text-white font-medium">
        <span className="flex items-center gap-2">
          {data.overall === "ok" ? <CheckCircle2 size={16} className="text-green-400" /> : <AlertTriangle size={16} className={data.overall === "error" ? "text-red-400" : "text-amber-400"} />}
          {problems === 0 ? "Discovery is ready to run" : `${problems} thing${problems === 1 ? "" : "s"} limiting discovery`}
          <span className="text-xs text-slate-500 font-normal">preset: {data.preset}</span>
        </span>
        {isOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      </button>
      {isOpen && (
        <div className="mt-2">
          <ul>{data.checks.map((c) => <CheckRow key={c.key} c={c} />)}</ul>
          <div className="flex items-center gap-3 mt-2 text-xs">
            <button onClick={() => setDeep(true)} disabled={isFetching || deep}
              className="text-brand-light hover:underline disabled:opacity-50">
              {deep ? (isFetching ? "Testing connections…" : "Connections tested") : "Test connections"}
            </button>
            <Link to="/settings/music" className="text-brand-light hover:underline">Open settings →</Link>
          </div>
        </div>
      )}
    </div>
  );
}

/** Dry run of the direct lane: shows what a run would do and why, changes nothing. */
export function PreviewButton({ disabled }: { disabled?: boolean }) {
  const qc = useQueryClient();
  const [result, setResult] = useState<PreviewResult | null>(null);
  const mut = useMutation({
    mutationFn: api.preview,
    onSuccess: (r) => { setResult(r); qc.invalidateQueries({ queryKey: ["ad-status"] }); },
  });
  return (
    <>
      <button
        onClick={() => mut.mutate()}
        disabled={mut.isPending || disabled}
        className="flex items-center gap-2 px-4 py-2.5 rounded-xl border border-purple-700/50 text-slate-200 text-sm font-medium hover:bg-surface-raised disabled:opacity-50 transition-all"
        title="Dry run: scores your listening with the saved settings and shows what would be added or queued. Nothing is changed."
      >
        <Eye size={14} /> {mut.isPending ? "Previewing…" : "Preview (dry run)"}
      </button>
      {mut.isError && <span className="text-xs text-red-300">{(mut.error as Error).message}</span>}
      {result && (
        <div className="basis-full mt-2 rounded-xl border border-purple-900/30 bg-surface-raised p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-white text-sm font-semibold">Dry run result</p>
              <p className="text-xs text-slate-400 mt-0.5">{result.message}</p>
            </div>
            <button onClick={() => setResult(null)} className="text-xs text-slate-500 hover:text-white">Close</button>
          </div>
          {result.blockers.map((b) => <p key={b} className="text-xs text-red-300 mt-2">{b}</p>)}
          {result.warnings.map((w) => <p key={w} className="text-xs text-amber-300 mt-1">{w}</p>)}
          {result.decisions.length > 0 && <div className="mt-3 max-h-96 overflow-y-auto"><DecisionList decisions={result.decisions} /></div>}
        </div>
      )}
    </>
  );
}
