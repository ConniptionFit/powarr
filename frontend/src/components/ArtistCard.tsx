import { useState } from "react";
import { ChevronDown, ChevronUp, Music2 } from "lucide-react";

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
        className="w-16 h-16 rounded-lg object-cover shrink-0 bg-surface border border-purple-900/30"
      />
    );
  }
  return (
    <div className="w-16 h-16 rounded-lg shrink-0 bg-surface border border-purple-900/30 flex items-center justify-center">
      <Music2 size={22} className="text-slate-600" />
    </div>
  );
}

export interface SourceBadge {
  label: string;
  className: string;
}

export default function ArtistCard({ name, yearsActive, imageUrl, bio, genres, era, subtitle, actions, sourceBadges }: {
  name: string;
  yearsActive?: string | null;
  imageUrl: string | null;
  bio?: string | null;
  genres?: string[];
  era?: string | null;
  subtitle: string;
  actions: React.ReactNode;
  sourceBadges?: SourceBadge[];
}) {
  const [expanded, setExpanded] = useState(false);
  const bioText = bio || "";
  const isLong = bioText.length > 180;
  const genreList = genres || [];

  return (
    <div className="bg-surface-raised border border-purple-900/30 rounded-lg p-4 flex gap-3">
      <ArtistAvatar url={imageUrl} name={name} />
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-white text-sm font-medium truncate">
              {name}
              {yearsActive && <span className="text-slate-500 font-normal"> · {yearsActive}</span>}
            </p>
            <div className="flex flex-wrap items-center gap-1.5">
              <p className="text-xs text-slate-500">{subtitle}</p>
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
          <div className="flex gap-1 shrink-0">{actions}</div>
        </div>

        {(genreList.length > 0 || era) && (
          <div className="flex flex-wrap gap-1 mt-2">
            {genreList.slice(0, 5).map(g => (
              <span key={g} className="text-xs bg-purple-900/40 text-purple-200 px-2 py-0.5 rounded">{g}</span>
            ))}
            {era && <span className="text-xs bg-surface text-slate-400 px-2 py-0.5 rounded border border-purple-900/40">{era}</span>}
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
      </div>
    </div>
  );
}
