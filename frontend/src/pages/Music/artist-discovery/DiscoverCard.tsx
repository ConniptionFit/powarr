import { useState } from "react";
import { Check, ChevronDown, Info, ThumbsDown, ThumbsUp, X } from "lucide-react";
import ArtistPreviewButton from "../../../components/ArtistPreviewButton";
import { type Bucket, type DiscoverCardData, type FeedbackKind } from "./shared";

export const BUCKET_STYLE: Record<Bucket, { label: string; cls: string }> = {
  close: { label: "Close", cls: "bg-green-950/60 text-green-300 border-green-700/40" },
  branching: { label: "Branching out", cls: "bg-surface text-purple-300 border-purple-700/40" },
  wildcard: { label: "Wildcard", cls: "bg-orange-950/60 text-orange-300 border-orange-700/40" },
};

const NOT_FOR_ME_REASONS: [string, string][] = [
  ["genre", "Not my genre"],
  ["known", "I already know them"],
  ["dislike", "I don't like them"],
  ["other", "Just not now"],
];

function MatchMeter({ value }: { value: number }) {
  return (
    <div className="flex items-center gap-2" title={`Match strength ${value}/100, compared with your other suggestions`}>
      <div className="h-1.5 flex-1 rounded-full bg-surface overflow-hidden">
        <div className="h-full rounded-full bg-brand" style={{ width: `${Math.max(4, value)}%` }} />
      </div>
      <span className="text-[10px] text-slate-500 w-7 text-right">{value}</span>
    </div>
  );
}

/** One artist tile: who, why, how strong, and the four things you can do about it. */
export function DiscoverCard({ c, busy, onAdd, onFeedback, onExplain }: {
  c: DiscoverCardData;
  busy: boolean;
  onAdd: () => void;
  onFeedback: (kind: FeedbackKind, reason?: string) => void;
  onExplain: () => void;
}) {
  const [menu, setMenu] = useState(false);
  const bucket = c.bucket ? BUCKET_STYLE[c.bucket] : null;
  return (
    <div className="w-60 shrink-0 snap-start rounded-xl border border-purple-900/30 bg-surface-raised overflow-hidden flex flex-col">
      <div className="relative h-32 bg-surface">
        {c.image_url ? (
          <img src={c.image_url} alt="" loading="lazy" className="w-full h-full object-cover" />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-3xl text-slate-500 font-bold">
            {c.artist_name.slice(0, 1).toUpperCase()}
          </div>
        )}
        {bucket && (
          <span className={`absolute top-2 left-2 text-[10px] px-2 py-0.5 rounded-full border ${bucket.cls}`}>
            {bucket.label}
          </span>
        )}
      </div>
      <div className="p-3 flex-1 flex flex-col gap-2">
        <div>
          <p className="text-white font-semibold text-sm leading-tight truncate" title={c.artist_name}>{c.artist_name}</p>
          {c.latest_release && (
            <p className="text-[11px] text-amber-300 truncate">New: {c.latest_release.title} ({c.latest_release.date.slice(0, 4)})</p>
          )}
        </div>
        <MatchMeter value={c.match} />
        <p className="text-xs text-slate-300 leading-snug line-clamp-3" title={c.blurb ?? c.reason}>{c.blurb ?? c.reason}</p>
        {c.blurb && <p className="text-[10px] text-slate-600 line-clamp-2" title={c.reason}>{c.reason}</p>}
        {c.tags.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {c.tags.slice(0, 3).map((t) => (
              <span key={t} className="text-[10px] px-1.5 py-0.5 rounded bg-surface border border-purple-900/20 text-slate-400">{t}</span>
            ))}
          </div>
        )}
        <ArtistPreviewButton artistName={c.artist_name} />
      </div>
      <div className="flex items-center justify-between gap-1 border-t border-purple-900/20 px-2 py-1.5 relative">
        <button onClick={onAdd} disabled={busy} title="Add to Lidarr"
          className="flex items-center gap-1 px-2 py-1 rounded-lg text-xs text-green-300 hover:bg-green-900/30 disabled:opacity-40">
          <Check size={14} /> Add
        </button>
        <button onClick={() => onFeedback("more_like_this")} disabled={busy} title="More like this: similar artists rank higher"
          className="p-1.5 rounded-lg text-slate-400 hover:text-purple-300 hover:bg-purple-900/30 disabled:opacity-40">
          <ThumbsUp size={14} />
        </button>
        <div className="relative">
          <button onClick={() => setMenu(!menu)} disabled={busy} title="Not for me"
            className="flex items-center gap-0.5 p-1.5 rounded-lg text-slate-400 hover:text-red-300 hover:bg-red-900/30 disabled:opacity-40">
            <ThumbsDown size={14} /><ChevronDown size={10} />
          </button>
          {menu && (
            <div className="absolute bottom-full right-0 mb-1 z-20 w-44 rounded-lg border border-purple-800/50 bg-surface-raised shadow-xl p-1">
              {NOT_FOR_ME_REASONS.map(([r, label]) => (
                <button key={r} onClick={() => { setMenu(false); onFeedback(r === "known" ? "known" : "not_for_me", r); }}
                  className="w-full text-left text-xs text-slate-300 hover:bg-white/5 rounded px-2 py-1.5 flex items-center gap-1.5">
                  <X size={11} className="text-slate-500" /> {label}
                </button>
              ))}
            </div>
          )}
        </div>
        <button onClick={onExplain} title="Why this was suggested"
          className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/5">
          <Info size={14} />
        </button>
      </div>
    </div>
  );
}
