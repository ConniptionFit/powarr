import { X } from "lucide-react";
import { type DiscoverCardData } from "./shared";
import { BUCKET_STYLE } from "./DiscoverCard";

const SIGNAL_LABEL: Record<string, string> = {
  similarity: "Similar to what you play",
  agreement: "Several sources agree",
  tag_fit: "Fits your usual tags",
  obscurity: "Under the radar",
  freshness: "Recent release",
  feedback: "Your feedback",
};
const SOURCE_LABEL: Record<string, string> = { lastfm: "Last.fm", deezer: "Deezer", listenbrainz: "ListenBrainz" };

/** Slide-over that shows exactly why an artist was suggested: one bar per signal,
 * the sources that agree, and the seeds behind it. This is what makes tuning legible. */
export function ExplainDrawer({ c, onClose }: { c: DiscoverCardData; onClose: () => void }) {
  const entries = Object.entries(c.breakdown);
  const maxAbs = Math.max(0.1, ...entries.map(([, v]) => Math.abs(v.points)));
  const bucket = c.bucket ? BUCKET_STYLE[c.bucket] : null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50" onClick={onClose}>
      <aside className="w-full max-w-sm h-full overflow-y-auto bg-surface-raised border-l border-purple-900/40 shadow-2xl p-5 space-y-4"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-white font-bold text-lg leading-tight">{c.artist_name}</h3>
            {bucket && <span className={`inline-block mt-1 text-[10px] px-2 py-0.5 rounded-full border ${bucket.cls}`}>{bucket.label}</span>}
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-white" aria-label="Close"><X size={18} /></button>
        </div>

        <p className="text-sm text-slate-300">{c.reason}</p>
        {c.blurb && <p className="text-sm text-slate-400 italic">{c.blurb}</p>}

        <section>
          <h4 className="text-xs uppercase tracking-wider text-slate-500 mb-2">Score breakdown</h4>
          <ul className="space-y-2">
            {entries.map(([k, v]) => (
              <li key={k} className="text-xs">
                <div className="flex justify-between text-slate-300">
                  <span>{SIGNAL_LABEL[k] ?? k}</span>
                  <span className={v.points < 0 ? "text-red-300" : "text-slate-400"}>
                    {v.points >= 0 ? "+" : ""}{v.points.toFixed(2)}
                  </span>
                </div>
                <div className="h-1.5 rounded-full bg-surface mt-1 overflow-hidden">
                  <div className={`h-full rounded-full ${v.points < 0 ? "bg-red-500/70" : "bg-brand"}`}
                    style={{ width: `${Math.min(100, (Math.abs(v.points) / maxAbs) * 100)}%` }} />
                </div>
                {v.value === null && <p className="text-[10px] text-slate-600 mt-0.5">No data for this signal, so it did not count.</p>}
              </li>
            ))}
          </ul>
          <p className="text-xs text-slate-500 mt-2">Final score {c.rank_score.toFixed(2)}. Weights are set in Settings, Music.</p>
        </section>

        <section>
          <h4 className="text-xs uppercase tracking-wider text-slate-500 mb-2">Suggested by</h4>
          <div className="flex flex-wrap gap-1.5">
            {c.sources.map((s) => (
              <span key={s} className="text-xs px-2 py-0.5 rounded-full bg-surface text-slate-300">{SOURCE_LABEL[s] ?? s}</span>
            ))}
          </div>
        </section>

        {c.seeds.length > 0 && (
          <section>
            <h4 className="text-xs uppercase tracking-wider text-slate-500 mb-2">Linked to artists you play</h4>
            <p className="text-sm text-slate-300">{c.seeds.join(", ")}</p>
          </section>
        )}

        <section className="text-xs text-slate-400 space-y-1">
          {c.tags.length > 0 && <p>Tags: {c.tags.join(", ")}</p>}
          {c.listeners != null && <p>Last.fm listeners: {c.listeners.toLocaleString()}</p>}
          {c.latest_release && <p>Latest release: {c.latest_release.title} ({c.latest_release.date})</p>}
          {c.years_active && <p>Active: {c.years_active}</p>}
        </section>
      </aside>
    </div>
  );
}
