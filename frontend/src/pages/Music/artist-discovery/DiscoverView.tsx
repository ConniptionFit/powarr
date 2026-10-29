import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Headphones, Pause, Play, RefreshCw, Search, SkipForward } from "lucide-react";
import { usePersistedState } from "../../../lib/usePersistedState";
import { useToast } from "../../../context/ToastContext";
import { DiscoverCard } from "./DiscoverCard";
import { ExplainDrawer } from "./ExplainDrawer";
import { type DiscoverCardData, type FeedbackKind, type SamplerTrack, api } from "./shared";

const ADVENTURE_LABEL = (v: number) => (v < 0.2 ? "Familiar" : v < 0.5 ? "Mostly familiar" : v < 0.8 ? "Adventurous" : "Very adventurous");

/** Plays one 30-second clip per top candidate back to back. */
function Sampler() {
  const [open, setOpen] = useState(false);
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(false);
  const audio = useRef<HTMLAudioElement>(null);
  const { data: tracks = [], isFetching } = useQuery({
    queryKey: ["ad-sampler"], queryFn: () => api.sampler(10), enabled: open, staleTime: 5 * 60_000,
  });
  const cur: SamplerTrack | undefined = tracks[i];
  useEffect(() => {
    if (open && cur && audio.current) {
      audio.current.src = cur.preview;
      audio.current.play().then(() => setPlaying(true)).catch(() => setPlaying(false));
    }
  }, [open, cur]);
  const next = () => setI((n) => (tracks.length ? (n + 1) % tracks.length : 0));
  if (!open) {
    return (
      <button onClick={() => { setOpen(true); setI(0); }}
        className="flex items-center gap-2 px-3 py-2 rounded-xl border border-purple-700/50 text-slate-200 text-sm hover:bg-surface-raised"
        title="Listen to a 30 second clip from each of your top suggestions, back to back">
        <Headphones size={14} /> Sampler
      </button>
    );
  }
  return (
    <div className="basis-full rounded-xl border border-purple-900/30 bg-surface-raised p-3 flex items-center gap-3">
      <audio ref={audio} onEnded={next} onPause={() => setPlaying(false)} onPlay={() => setPlaying(true)} />
      {cur?.album_cover || cur?.image_url ? (
        <img src={(cur.album_cover || cur.image_url)!} alt="" className="w-12 h-12 rounded object-cover" />
      ) : <div className="w-12 h-12 rounded bg-surface" />}
      <div className="min-w-0 flex-1">
        {isFetching && <p className="text-xs text-slate-400">Finding clips…</p>}
        {!isFetching && !cur && <p className="text-xs text-slate-400">No clips available for your current suggestions.</p>}
        {cur && (
          <>
            <p className="text-sm text-white font-medium truncate">{cur.artist_name}</p>
            <p className="text-xs text-slate-400 truncate">{cur.title}  ·  {i + 1} of {tracks.length}</p>
          </>
        )}
      </div>
      {cur && (
        <>
          <button onClick={() => (playing ? audio.current?.pause() : audio.current?.play())}
            className="p-2 rounded-lg hover:bg-white/10 text-white" aria-label={playing ? "Pause" : "Play"}>
            {playing ? <Pause size={16} /> : <Play size={16} />}
          </button>
          <button onClick={next} className="p-2 rounded-lg hover:bg-white/10 text-white" aria-label="Next clip"><SkipForward size={16} /></button>
        </>
      )}
      <button onClick={() => { audio.current?.pause(); setOpen(false); }} className="text-xs text-slate-500 hover:text-white">Close</button>
    </div>
  );
}

/** Pick a genre, see strong names you have not heard or triaged. */
function GenreExplorer() {
  const [tag, setTag] = useState("");
  const [asked, setAsked] = useState<{ tag: string; offset: number } | null>(null);
  const toast = useToast();
  const { data, isFetching } = useQuery({
    queryKey: ["ad-genre", asked?.tag, asked?.offset],
    queryFn: () => api.genreExplore(asked!.tag, asked!.offset),
    enabled: !!asked,
  });
  const add = useMutation({
    mutationFn: (r: { artist_name: string; musicbrainz_id: string | null }) => api.relatedAdd(r.artist_name, r.musicbrainz_id),
    onSuccess: (r) => toast.success(r.message || "Added"),
    onError: (e: Error) => toast.error(e.message),
  });
  return (
    <section className="rounded-xl border border-purple-900/30 bg-surface-raised p-4 space-y-3">
      <div>
        <h3 className="text-white font-semibold text-sm">Explore a genre</h3>
        <p className="text-xs text-slate-500">Top artists for a tag, minus anything you own, have played, or already reviewed.</p>
      </div>
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (tag.trim()) setAsked({ tag: tag.trim(), offset: 0 }); }}>
        <div className="relative flex-1">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input value={tag} onChange={(e) => setTag(e.target.value)} placeholder="shoegaze, trip hop, math rock…"
            className="w-full pl-9 pr-3 py-2 bg-surface border border-purple-900/30 rounded-xl text-xs text-white placeholder:text-slate-500 focus:outline-none focus:border-brand" />
        </div>
        <button type="submit" className="px-4 py-2 rounded-xl bg-brand text-white text-xs font-medium hover:bg-brand-light">Explore</button>
      </form>
      {isFetching && <p className="text-xs text-slate-400">Looking…</p>}
      {data && !isFetching && (
        <>
          {data.message && <p className="text-xs text-slate-400">{data.message}</p>}
          <ul className="divide-y divide-purple-900/20">
            {data.results.map((r) => (
              <li key={r.artist_name} className="py-2 flex items-center gap-3 text-sm">
                <span className="text-slate-600 text-xs w-6 text-right">{r.rank}</span>
                <span className="flex-1 text-white truncate">{r.artist_name}</span>
                {r.already_triaged && <span className="text-[10px] text-slate-500">already reviewed</span>}
                <button onClick={() => add.mutate(r)} disabled={add.isPending}
                  className="text-xs px-2.5 py-1 rounded-lg text-green-300 hover:bg-green-900/30 disabled:opacity-40">Add to Lidarr</button>
              </li>
            ))}
          </ul>
          {asked && data.results.length > 0 && (
            <button onClick={() => setAsked({ tag: asked.tag, offset: asked.offset + 30 })}
              className="text-xs text-brand-light hover:underline">Dig deeper (less obvious names)</button>
          )}
        </>
      )}
    </section>
  );
}

/** The Discover tab: shelves with a reason, a Familiar to Adventurous slider, a sampler. */
export function DiscoverView({ enabled, onRun, running }: { enabled: boolean; onRun: () => void; running: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [stored, setStored] = usePersistedState<number | null>("powarr.artistDiscovery.adventure", null);
  const [draft, setDraft] = useState<number | null>(null);
  const [explain, setExplain] = useState<DiscoverCardData | null>(null);
  const adventure = stored ?? undefined;
  const { data, isLoading } = useQuery({ queryKey: ["ad-shelves", adventure], queryFn: () => api.shelves(adventure) });
  const shown = draft ?? stored ?? data?.adventure ?? 0.3;

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["ad-shelves"] });
    qc.invalidateQueries({ queryKey: ["ad-candidates"] });
    qc.invalidateQueries({ queryKey: ["ad-stats"] });
  };
  const add = useMutation({
    mutationFn: (id: number) => api.accept(id),
    onSuccess: (r) => { toast.success(r.message || "Added to Lidarr"); refresh(); qc.invalidateQueries({ queryKey: ["ad-recent-adds"] }); },
    onError: (e: Error) => toast.error(e.message),
  });
  const feedback = useMutation({
    mutationFn: (v: { id: number; kind: FeedbackKind; reason?: string }) => api.feedback(v.id, v.kind, v.reason),
    onSuccess: (r) => { toast.info(r.message); refresh(); },
    onError: (e: Error) => toast.error(e.message),
  });
  const busy = add.isPending || feedback.isPending;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex-1 min-w-[220px] rounded-xl border border-purple-900/30 bg-surface-raised px-4 py-2.5">
          <div className="flex justify-between text-xs mb-1">
            <span className="text-slate-400">Familiar</span>
            <span className="text-white font-medium">{ADVENTURE_LABEL(shown)}</span>
            <span className="text-slate-400">Adventurous</span>
          </div>
          <input type="range" min={0} max={1} step={0.05} value={shown} className="w-full accent-purple-500"
            aria-label="Familiar to adventurous"
            onChange={(e) => setDraft(parseFloat(e.target.value))}
            onMouseUp={() => { if (draft !== null) { setStored(draft); setDraft(null); } }}
            onTouchEnd={() => { if (draft !== null) { setStored(draft); setDraft(null); } }}
            onKeyUp={() => { if (draft !== null) { setStored(draft); setDraft(null); } }} />
        </div>
        <button onClick={onRun} disabled={running || !enabled}
          className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-brand text-white text-sm font-medium hover:bg-brand-light disabled:opacity-50"
          title="Find new artists now">
          <RefreshCw size={14} className={running ? "animate-spin" : ""} /> {running ? "Finding…" : "Find new artists"}
        </button>
        <Sampler />
      </div>

      {isLoading && <p className="text-sm text-slate-400">Loading suggestions…</p>}
      {data && data.shelves.length === 0 && (
        <div className="rounded-xl border border-purple-900/20 bg-surface-raised/40 p-8 text-center space-y-1">
          <p className="text-slate-300 text-sm">Nothing to show yet.</p>
          <p className="text-slate-500 text-xs">Run discovery to fill these shelves, or use Preview on the Review queue tab to see what it would find.</p>
        </div>
      )}
      {data?.shelves.map((s) => (
        <section key={s.id} className="space-y-2">
          <div>
            <h2 className="text-white font-bold text-base">{s.title}</h2>
            <p className="text-xs text-slate-500">{s.subtitle}</p>
          </div>
          <div className="flex gap-3 overflow-x-auto snap-x pb-2 -mx-1 px-1">
            {s.items.map((c) => (
              <DiscoverCard key={c.id} c={c} busy={busy}
                onAdd={() => add.mutate(c.id)}
                onFeedback={(kind, reason) => feedback.mutate({ id: c.id, kind, reason })}
                onExplain={() => setExplain(c)} />
            ))}
          </div>
        </section>
      ))}

      <GenreExplorer />
      {explain && <ExplainDrawer c={explain} onClose={() => setExplain(null)} />}
    </div>
  );
}
