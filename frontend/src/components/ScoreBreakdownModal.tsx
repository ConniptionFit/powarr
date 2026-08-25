import { useQuery } from "@tanstack/react-query";
import { X, Calculator } from "lucide-react";
import { mediaApi, type MediaItem } from "../lib/api";

// LIB-08 — the deterministic answer to "why is this scored 87?".
//
// score_breakdown() has always computed these numbers, but the only consumer
// was the LLM explain prompt, so the sole user-facing "why" required a working
// Ollama. This is the same arithmetic the scorer already ran: free, instant,
// and it cannot be unavailable. Bars are drawn against each factor's
// max_contribution rather than against 100, so a factor scoring full strength
// on a small weight reads as the minor influence it actually is instead of
// looking maxed out.

function FactorBar({ label, factor, contribution, max }: {
  label: string; factor: number; contribution: number; max: number;
}) {
  const pct = max > 0 ? (contribution / max) * 100 : 0;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3 mb-1">
        <span className="text-sm text-slate-300">{label}</span>
        <span className="text-xs text-slate-400 tabular-nums">
          <span className="text-white font-medium">{contribution.toFixed(1)}</span>
          <span className="text-slate-500"> / {max.toFixed(1)} pts</span>
        </span>
      </div>
      <div className="h-2 rounded-full bg-black/40 overflow-hidden">
        <div
          className="h-full rounded-full bg-brand-light/80 transition-[width]"
          style={{ width: `${Math.max(0, Math.min(100, pct))}%` }}
        />
      </div>
      <p className="mt-1 text-[11px] text-slate-500">
        signal strength {Math.round(factor * 100)}% of this factor's maximum
      </p>
    </div>
  );
}

export default function ScoreBreakdownModal({ item, onClose }: {
  item: MediaItem; onClose: () => void;
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["score-breakdown", item.id],
    queryFn: () => mediaApi.scoreBreakdown(item.id),
  });

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg bg-surface-raised border border-purple-900/40 rounded-xl shadow-xl"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-purple-900/40">
          <div className="min-w-0">
            <p className="flex items-center gap-2 text-xs uppercase tracking-wider text-slate-400">
              <Calculator size={13} /> Score breakdown
            </p>
            <h2 className="mt-1 text-white font-medium truncate">{item.title}</h2>
          </div>
          <button
            onClick={onClose}
            aria-label="Close score breakdown"
            className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/5 flex-shrink-0"
          >
            <X size={16} />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          {isLoading && <p className="text-sm text-slate-400">Calculating…</p>}
          {error && (
            <p className="text-sm text-amber-400/90">
              Couldn't load the breakdown: {error instanceof Error ? error.message : String(error)}
            </p>
          )}
          {data && (
            <>
              <div className="flex items-baseline gap-2">
                <span className="text-3xl font-bold text-white tabular-nums">
                  {data.score.toFixed(1)}
                </span>
                <span className="text-sm text-slate-400">
                  deletion score — higher means a stronger candidate
                </span>
              </div>

              <div className="space-y-3.5">
                {data.factors.map(f => (
                  <FactorBar
                    key={f.key}
                    label={f.label}
                    factor={f.factor}
                    contribution={f.contribution}
                    max={f.max_contribution}
                  />
                ))}
              </div>

              <div className="pt-1 space-y-1 text-[11px] text-slate-500 border-t border-purple-900/30">
                <p className="pt-2">
                  Factors with a weight of zero are switched off in Settings → Scoring
                  and aren't listed.
                </p>
                {data.series_watched && (
                  <p>
                    Counted as watched because another episode of this show has been
                    watched, even though this item hasn't.
                  </p>
                )}
                {data.profile_applied && (
                  <p>
                    Uses the per-library weight overlay for{" "}
                    <span className="text-slate-400">{data.library_section}</span>, not the
                    global defaults.
                  </p>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
