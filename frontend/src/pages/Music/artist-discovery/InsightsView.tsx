import { useQuery } from "@tanstack/react-query";
import { type ScoreRow, api } from "./shared";

const pct = (v: number | null) => (v === null ? "n/a" : `${Math.round(v * 100)}%`);

function ScoreTable({ title, rows }: { title: string; rows: ScoreRow[] }) {
  if (rows.length === 0) return null;
  return (
    <div>
      <h4 className="text-xs uppercase tracking-wider text-slate-500 mb-1.5">{title}</h4>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-slate-500 text-left">
            <th className="font-normal py-1">Group</th><th className="font-normal">Suggested</th>
            <th className="font-normal">Added</th><th className="font-normal">Rejected</th><th className="font-normal">Played</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="border-t border-purple-900/20 text-slate-300">
              <td className="py-1.5 capitalize">{r.key}</td><td>{r.suggested}</td><td>{r.added}</td><td>{r.rejected}</td>
              <td title={`${r.hits} of ${r.measurable} measurable`}>{pct(r.hit_rate)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Insights: what the taste profile looks like, and whether suggestions became listening. */
export function InsightsView() {
  const { data: card } = useQuery({ queryKey: ["ad-scorecard"], queryFn: api.scorecard });
  const { data: taste } = useQuery({ queryKey: ["ad-taste"], queryFn: api.taste });
  const maxTag = Math.max(0.01, ...(taste?.top_tags.map((t) => t.weight) ?? [0.01]));
  return (
    <div className="space-y-6">
      <section className="rounded-xl border border-purple-900/30 bg-surface-raised p-4 space-y-3">
        <div>
          <h3 className="text-white font-semibold text-sm">Scorecard</h3>
          <p className="text-xs text-slate-500">
            A suggestion counts as played when you listen to the artist (from any source, including Last.fm scrobbles
            from other apps) within {card?.window_days ?? 30} days after it was added or queued.
          </p>
        </div>
        {card && card.totals.suggested === 0 && <p className="text-xs text-slate-400">No suggestions in the last {card.days} days yet.</p>}
        {card && card.totals.suggested > 0 && (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {[
                ["Suggested", card.totals.suggested], ["Added", card.totals.added],
                ["Rejected", card.totals.rejected], ["Played", pct(card.hit_rate)],
              ].map(([label, v]) => (
                <div key={label as string} className="rounded-lg bg-surface p-3">
                  <p className="text-[11px] text-slate-500">{label}</p>
                  <p className="text-white text-lg font-bold">{v}</p>
                </div>
              ))}
            </div>
            {card.insights.map((i) => <p key={i} className="text-xs text-brand-light">{i}</p>)}
            <div className="grid sm:grid-cols-2 gap-6">
              <ScoreTable title="By familiarity" rows={card.by_bucket} />
              <ScoreTable title="By source" rows={card.by_source} />
            </div>
          </>
        )}
      </section>

      <section className="rounded-xl border border-purple-900/30 bg-surface-raised p-4 space-y-3">
        <div>
          <h3 className="text-white font-semibold text-sm">Your taste right now</h3>
          <p className="text-xs text-slate-500">
            Built from {taste?.log_events.toLocaleString() ?? 0} de-duplicated plays across the enabled sources
            (period: {taste?.period}). Change what counts under Settings, Music.
          </p>
        </div>
        {taste && taste.log_events === 0 && (
          <p className="text-xs text-amber-300">The listening log is empty. Enable a source in Settings and run a sync, or discovery falls back to Last.fm top artists.</p>
        )}
        <div className="grid sm:grid-cols-2 gap-6">
          <div>
            <h4 className="text-xs uppercase tracking-wider text-slate-500 mb-1.5">Top artists</h4>
            <ul className="space-y-1">
              {taste?.top_artists.map((a) => (
                <li key={a.name} className={`flex justify-between text-xs ${a.excluded ? "text-slate-600 line-through" : "text-slate-300"}`}>
                  <span className="truncate">{a.name}</span><span className="text-slate-500">{a.plays}</span>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h4 className="text-xs uppercase tracking-wider text-slate-500 mb-1.5">Top tags</h4>
            <ul className="space-y-1.5">
              {taste?.top_tags.map((t) => (
                <li key={t.tag} className="text-xs">
                  <div className="flex justify-between text-slate-300"><span>{t.tag}</span></div>
                  <div className="h-1 rounded-full bg-surface mt-0.5"><div className="h-full rounded-full bg-brand" style={{ width: `${(t.weight / maxTag) * 100}%` }} /></div>
                </li>
              ))}
              {taste && taste.top_tags.length === 0 && <li className="text-xs text-slate-500">Tags appear after the first discovery run.</li>}
            </ul>
          </div>
        </div>
      </section>
    </div>
  );
}
