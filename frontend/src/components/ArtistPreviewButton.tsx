import { useState } from "react";
import { Play } from "lucide-react";
import { req } from "../lib/api";

// AD-18 — "listen before you add" on Discovery/Related Artists cards. On-demand
// only (never automatic): one click resolves whichever of YouTube/Spotify are
// configured+enabled server-side, then renders whatever came back. Neither
// source guarantees a hit — no configured source, no match, or (Spotify)
// no preview_url all render the same "nothing available" message rather than
// an error, since none of those are actually failures.
interface PreviewSource {
  source: string;
  available: boolean;
  video_id?: string;
  preview_url?: string;
  title?: string;
  message?: string;
}

export default function ArtistPreviewButton({ artistName }: { artistName: string }) {
  const [state, setState] = useState<{ loading: boolean; sources: PreviewSource[] | null; error: string | null }>({
    loading: false, sources: null, error: null,
  });

  if (state.sources !== null) {
    const available = state.sources.filter(s => s.available);
    return (
      <div className="mt-2 space-y-2">
        {available.length === 0 ? (
          <p className="text-xs text-slate-500">No preview available for "{artistName}".</p>
        ) : (
          available.map(s => (
            <div key={s.source}>
              {s.source === "youtube" && s.video_id && (
                <iframe
                  width="100%"
                  height="160"
                  src={`https://www.youtube.com/embed/${s.video_id}`}
                  title={s.title || artistName}
                  allow="autoplay; encrypted-media"
                  className="rounded border border-purple-900/30"
                />
              )}
              {s.source === "spotify" && s.preview_url && (
                <audio controls src={s.preview_url} className="w-full h-8">
                  Your browser doesn't support audio playback.
                </audio>
              )}
            </div>
          ))
        )}
      </div>
    );
  }

  if (state.error) {
    return <p className="text-xs text-red-400 mt-2">{state.error}</p>;
  }

  return (
    <button
      onClick={async () => {
        setState({ loading: true, sources: null, error: null });
        try {
          const r = await req<{ sources: PreviewSource[] }>(
            `/artist-discovery/preview?artist=${encodeURIComponent(artistName)}`);
          setState({ loading: false, sources: r.sources, error: null });
        } catch (e: unknown) {
          setState({ loading: false, sources: null, error: e instanceof Error ? e.message : String(e) });
        }
      }}
      disabled={state.loading}
      title="Listen before you add (AD-18)"
      className="p-1.5 rounded hover:bg-purple-900/40 text-slate-400 hover:text-purple-300 disabled:opacity-40"
    >
      <Play size={15} />
    </button>
  );
}
