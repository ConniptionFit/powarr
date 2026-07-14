import { useEffect, useRef, useState } from "react";
import { Play } from "lucide-react";
import { req } from "../lib/api";

// AD-18, reworked v0.79.0 (user feedback) — the Play button only renders once a
// preview is confirmed to exist on an enabled source (YouTube / Spotify). The
// availability check fires when the card scrolls into view (IntersectionObserver,
// so a 50-card page doesn't fire 50 YouTube searches up front) and the backend
// caches results, so clicking Play just reveals the already-confirmed player.
// No enabled sources, no match, or a failed check all render nothing at all.
interface PreviewSource {
  source: string;
  available: boolean;
  video_id?: string;
  preview_url?: string;
  title?: string;
  message?: string;
}

export default function ArtistPreviewButton({ artistName }: { artistName: string }) {
  const probe = useRef<HTMLSpanElement>(null);
  const [sources, setSources] = useState<PreviewSource[] | null>(null); // null = not checked yet
  const [failed, setFailed] = useState(false);
  const [revealed, setRevealed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        const r = await req<{ sources: PreviewSource[] }>(
          `/artist-discovery/preview?artist=${encodeURIComponent(artistName)}`);
        if (!cancelled) setSources(r.sources);
      } catch {
        if (!cancelled) setFailed(true); // fail-soft: no button, no error text on the card
      }
    };
    const el = probe.current;
    if (!el || typeof IntersectionObserver === "undefined") {
      check();
      return () => { cancelled = true; };
    }
    const obs = new IntersectionObserver(entries => {
      if (entries.some(e => e.isIntersecting)) {
        obs.disconnect();
        check();
      }
    });
    obs.observe(el);
    return () => { cancelled = true; obs.disconnect(); };
  }, [artistName]);

  if (sources === null && !failed) return <span ref={probe} />; // viewport probe while unchecked

  const available = (sources ?? []).filter(s => s.available);
  if (failed || available.length === 0) return null; // no confirmed preview — no button

  if (revealed) {
    return (
      <div className="mt-2 space-y-2">
        {available.map(s => (
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
        ))}
      </div>
    );
  }

  const labels = available
    .map(s => (s.source === "youtube" ? "YouTube" : "Spotify"))
    .join(" and ");
  return (
    <button
      onClick={() => setRevealed(true)}
      title={`Play a short preview of ${artistName} from ${labels}`}
      className="p-1.5 rounded hover:bg-purple-900/40 text-slate-400 hover:text-purple-300"
    >
      <Play size={15} />
    </button>
  );
}
