import { Check, X } from "lucide-react";
import ArtistCard from "../../../components/ArtistCard";
import ArtistPreviewButton from "../../../components/ArtistPreviewButton";
import { type Candidate, whySuggested, getLaneBadge, getScorePill } from "./shared";

export function CandidateCard({
  c,
  onAccept,
  onReject,
  onInspect,
  pending,
  selectable,
  selected,
  onToggleSelect,
}: {
  c: Candidate;
  onAccept: () => void;
  onReject: () => void;
  onInspect: () => void;
  pending: boolean;
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: () => void;
}) {
  return (
    <ArtistCard
      name={c.artist_name}
      yearsActive={c.years_active}
      imageUrl={c.image_url}
      bio={c.bio}
      genres={c.genres}
      era={c.era}
      subtitle={whySuggested(c)}
      scorePill={getScorePill(c)}
      laneBadge={getLaneBadge(c)}
      isLowMetadata={c.is_low_metadata}
      onInspect={onInspect}
      selectable={selectable}
      selected={selected}
      onToggleSelect={onToggleSelect}
      actions={
        <>
          <button
            onClick={onAccept}
            disabled={pending}
            title="Add to Lidarr"
            aria-label="Add to Lidarr"
            className="p-1.5 rounded-lg hover:bg-green-900/40 text-slate-400 hover:text-green-300 disabled:opacity-40 transition-colors"
          >
            <Check size={15} />
          </button>
          <button
            onClick={onReject}
            disabled={pending}
            title="Reject"
            aria-label="Reject"
            className="p-1.5 rounded-lg hover:bg-red-900/40 text-slate-400 hover:text-red-300 disabled:opacity-40 transition-colors"
          >
            <X size={15} />
          </button>
        </>
      }
      preview={<ArtistPreviewButton artistName={c.artist_name} />}
    />
  );
}
