import { useState } from "react";
import { ChevronDown, ChevronUp, Music2, Sparkles, AlertTriangle } from "lucide-react";

// Shared visual body for an artist result card — used by Music -> Artist
// Discovery's candidate queue and Music -> Related Artists' search results.
// Each caller owns its own action buttons/badge (accept+reject vs add/owned)
// and subtitle wording (why-suggested vs match %), passed in as props.

function ArtistAvatar({ url, name }: { url: string | null; name: string }) {
  const [failed, setFailed] = useState(false);
  if (url && !failed) {
    return (
      <img
        src={url}
        alt={name}
        onError={() => setFailed(true)}
        className="w-16 h-16 rounded-xl object-cover shrink-0 bg-surface border border-purple-900/30 shadow-sm"
      />
    );
  }
  return (
    <div className="w-16 h-16 rounded-xl shrink-0 bg-surface border border-purple-900/30 flex items-center justify-center">
      <Music2 size={22} className="text-slate-600" />
    </div>
  );
}

export interface SourceBadge {
  label: string;
  className: string;
}

export default function ArtistCard({
  name,
  yearsActive,
  imageUrl,
  bio,
  genres,
  era,
  subtitle,
  actions,
  sourceBadges,
  preview,
  scorePill,
  laneBadge,
  isLowMetadata,
  onInspect,
  selectable,
  selected,
  onToggleSelect,
}: {
  name: string;
  yearsActive?: string | null;
  imageUrl: string | null;
  bio?: string | null;
  genres?: string[];
  era?: string | null;
  subtitle: string;
  actions: React.ReactNode;
  sourceBadges?: SourceBadge[];
  preview?: React.ReactNode;
  scorePill?: { label: string; className: string };
  laneBadge?: { label: string; className: string; icon?: React.ReactNode };
  isLowMetadata?: boolean;
  onInspect?: () => void;
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const bioText = bio || "";
  const isLong = bioText.length > 180;
  const genreList = genres || [];

  return (
    <div
      className={`bg-surface-raised border rounded-xl p-4 flex gap-3.5 transition-all ${
        selected
          ? "border-brand bg-purple-950/20 shadow-md shadow-purple-950/20"
          : "border-purple-900/30 hover:border-purple-800/50"
      }`}
    >
      {selectable && (
        <div className="flex items-center shrink-0">
          <input
            type="checkbox"
            checked={!!selected}
            onChange={() => onToggleSelect?.()}
            className="w-4 h-4 rounded border-purple-900/50 text-brand bg-surface focus:ring-brand cursor-pointer"
          />
        </div>
      )}

      <ArtistAvatar url={imageUrl} name={name} />

      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-white text-sm font-semibold truncate">
                {name}
                {yearsActive && <span className="text-slate-500 font-normal"> · {yearsActive}</span>}
              </p>

              {scorePill && (
                <span className={`text-[11px] font-bold px-2 py-0.5 rounded-full leading-none ${scorePill.className}`}>
                  {scorePill.label}
                </span>
              )}

              {laneBadge && (
                <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full leading-none flex items-center gap-1 ${laneBadge.className}`}>
                  {laneBadge.icon}
                  {laneBadge.label}
                </span>
              )}

              {isLowMetadata && (
                <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full leading-none bg-amber-950/60 text-amber-300 border border-amber-800/40 flex items-center gap-1" title="Missing MusicBrainz ID and genres">
                  <AlertTriangle size={10} /> Low Metadata
                </span>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-1.5 mt-1">
              <p className="text-xs text-slate-400 leading-snug">{subtitle}</p>
              {sourceBadges && sourceBadges.length > 0 && (
                <span className="flex gap-1">
                  {sourceBadges.map(b => (
                    <span key={b.label} className={`text-[10px] px-1.5 py-0.5 rounded-full leading-none ${b.className}`}>
                      {b.label}
                    </span>
                  ))}
                </span>
              )}
            </div>
          </div>

          <div className="flex items-center gap-1 shrink-0">
            {onInspect && (
              <button
                onClick={onInspect}
                title="View Suggestion Insights"
                aria-label="View Suggestion Insights"
                className="p-1.5 rounded-lg hover:bg-purple-900/40 text-purple-400 hover:text-purple-200 transition-colors"
              >
                <Sparkles size={15} />
              </button>
            )}
            {actions}
          </div>
        </div>

        {(genreList.length > 0 || era) && (
          <div className="flex flex-wrap gap-1 mt-2">
            {genreList.slice(0, 5).map(g => (
              <span key={g} className="text-xs bg-purple-900/40 text-purple-200 px-2 py-0.5 rounded-lg">{g}</span>
            ))}
            {genreList.length > 5 && (
              <span className="text-xs text-slate-500 px-1 py-0.5">+{genreList.length - 5}</span>
            )}
            {era && <span className="text-xs bg-surface text-slate-400 px-2 py-0.5 rounded-lg border border-purple-900/40">{era}</span>}
          </div>
        )}

        {bioText && (
          <div className="mt-2">
            <p className={`text-xs text-slate-400 leading-relaxed ${!expanded && isLong ? "line-clamp-2" : ""}`}>
              {bioText}
            </p>
            {isLong && (
              <button onClick={() => setExpanded(e => !e)}
                className="flex items-center gap-1 text-xs text-brand-light hover:underline mt-1">
                {expanded ? <>Show less <ChevronUp size={12} /></> : <>Show more <ChevronDown size={12} /></>}
              </button>
            )}
          </div>
        )}

        {preview}
      </div>
    </div>
  );
}

