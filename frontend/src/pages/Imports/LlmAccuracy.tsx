import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { importsApi, type LlmLogGroupStats } from "../../lib/api";
import { SkeletonTable } from "../../components/Skeleton";

// LLM-06 — in-app accuracy dashboard from llm_match_log. CSV export
// (llm-log/export.csv) is raw rows for offline replay; this page is the
// same data pre-aggregated for at-a-glance day-to-day tuning.
function pct(v: number | null): string {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

function GroupTable({ title, groups }: { title: string; groups: LlmLogGroupStats[] }) {
  if (groups.length === 0) return null;
  return (
    <div className="mb-6">
      <h3 className="text-sm font-medium text-slate-300 mb-2">{title}</h3>
      <div className="overflow-x-auto rounded-lg border border-purple-900/40">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-surface-raised text-slate-400 text-left">
              <th className="px-3 py-2 font-medium"></th>
              <th className="px-3 py-2 font-medium text-right">Calls</th>
              <th className="px-3 py-2 font-medium text-right">Parse OK</th>
              <th className="px-3 py-2 font-medium text-right">Agree</th>
              <th className="px-3 py-2 font-medium text-right">Enforced</th>
              <th className="px-3 py-2 font-medium text-right">Avg latency</th>
              <th className="px-3 py-2 font-medium text-right" title="Of calls resolved as accepted/auto-resolved/rejected (NOT orphaned — that means the download's files disappeared, unrelated to match quality): how often the verdict matched what actually happened. A proxy, not ground truth on the LLM's reasoning.">
                Outcome match
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-purple-900/10">
            {groups.map(g => (
              <tr key={g.key} className="hover:bg-white/5 transition-colors">
                <td className="px-3 py-2 text-white font-medium">{g.key}</td>
                <td className="px-3 py-2 text-right text-slate-300">{g.total}</td>
                <td className="px-3 py-2 text-right text-slate-300">{pct(g.parse_ok_rate)}</td>
                <td className="px-3 py-2 text-right text-slate-300">{pct(g.agree_rate)}</td>
                <td className="px-3 py-2 text-right text-slate-300">{pct(g.enforced_rate)}</td>
                <td className="px-3 py-2 text-right text-slate-300">
                  {g.avg_latency_ms == null ? "—" : `${Math.round(g.avg_latency_ms)}ms`}
                </td>
                <td className="px-3 py-2 text-right text-slate-300">
                  {pct(g.outcome_agreement_rate)}
                  {g.outcome_sample_size > 0 && (
                    <span className="text-slate-600 text-xs ml-1">(n={g.outcome_sample_size})</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const DAY_OPTIONS = [
  { label: "Last 7 days", value: 7 },
  { label: "Last 30 days", value: 30 },
  { label: "Last 90 days", value: 90 },
  { label: "All time", value: undefined },
];

export default function LlmAccuracy() {
  const [days, setDays] = useState<number | undefined>(30);
  const { data: stats, isLoading } = useQuery({
    queryKey: ["llm-log-stats", days],
    queryFn: () => importsApi.llmLogStats(days),
  });

  return (
    <div>
      <div className="flex items-center justify-between mb-5 gap-3 flex-wrap">
        <p className="text-slate-400 text-sm">
          Match-review LLM call accuracy — agree/disagree rate, parse success, enforcement flips, and
          latency by source app, model, and prompt version, from every real call logged since v0.41.0.
        </p>
        <div className="flex items-center gap-2">
          <select
            value={days ?? ""}
            onChange={e => setDays(e.target.value ? Number(e.target.value) : undefined)}
            className="px-2.5 py-1.5 bg-surface-raised border border-purple-900/40 rounded-lg text-sm text-slate-300"
          >
            {DAY_OPTIONS.map(o => (
              <option key={o.label} value={o.value ?? ""}>{o.label}</option>
            ))}
          </select>
          <button
            onClick={() => importsApi.exportLlmLogCsv()}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-surface-raised border border-purple-900/40 text-slate-300 hover:text-white text-sm transition-colors"
          >
            <Download size={15} />
            CSV
          </button>
        </div>
      </div>

      {isLoading ? (
        <SkeletonTable rows={5} />
      ) : !stats || stats.overall.total === 0 ? (
        <div className="text-center py-16 text-slate-500">
          No LLM match-review calls logged {days ? `in the last ${days} days` : "yet"}.
        </div>
      ) : (
        <>
          <GroupTable title="Overall" groups={[{ ...stats.overall, key: "All calls" }]} />
          <GroupTable title="By source app" groups={stats.by_source_app} />
          <GroupTable title="By model" groups={stats.by_model} />
          <GroupTable title="By prompt scaffold version" groups={stats.by_scaffold_version} />
        </>
      )}
    </div>
  );
}
