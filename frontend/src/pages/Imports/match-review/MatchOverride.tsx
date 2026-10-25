import { useState, useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { importsApi, type FailedImport } from "../../../lib/api";

export function MatchOverride({ item, onDone }: { item: FailedImport; onDone: () => void }) {
  const [query, setQuery] = useState(item.raw_title);
  const [candidates, setCandidates] = useState<Array<{ id: number; title: string; score: number }> | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const search = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await importsApi.candidates(item.id, query);
      setCandidates(r.candidates);
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const use = async (c: { id: number; title: string }) => {
    setBusy(true);
    try {
      await importsApi.setMatch(item.id, c.id, c.title);
      onDone();
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <div className="px-4 py-2 border-t border-purple-900/10">
      <div className="flex items-center gap-2">
        <span className="text-xs text-slate-500 uppercase tracking-wider">Change match:</span>
        <input
          type="text"
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === "Enter" && search()}
          className="flex-1 max-w-md bg-surface border border-purple-900/40 rounded px-2 py-1 text-xs text-white"
        />
        <button onClick={search} disabled={busy}
                className="flex items-center gap-1 px-2 py-1 rounded bg-surface-overlay hover:bg-white/10 text-slate-300 text-xs transition-colors disabled:opacity-40">
          <Search size={11} /> Search Library
        </button>
        {msg && <span className="text-xs text-red-400">{msg}</span>}
      </div>
      {candidates && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {candidates.filter(c => c.score > 0).map(c => (
            <button key={c.id} onClick={() => use(c)} disabled={busy}
                    className="px-2 py-1 rounded bg-brand/10 border border-brand/30 text-brand-light text-xs hover:bg-brand/20 transition-colors disabled:opacity-40">
              {c.title} <span className="opacity-60">{Math.round(c.score * 100)}%</span>
            </button>
          ))}
          {candidates.filter(c => c.score > 0).length === 0 && (
            <span className="text-xs text-slate-500">No candidates found — try a shorter query.</span>
          )}
        </div>
      )}
    </div>
  );
}

export interface EpisodeOption { id: number; season: number; episode: number; title: string; }

// Type-to-filter episode combobox anchored below the Mapped To cell it was
// opened from. Defaults its scroll position to the file's currently-mapped
// episode so the common case (nudging a wrong guess) doesn't require typing.
export function EpisodePicker({ importId, currentSeason, currentEpisode, onSelect, onClose }: {
  importId: number;
  currentSeason: number | null;
  currentEpisode: number | null;
  onSelect: (ep: EpisodeOption) => void;
  onClose: () => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["episode-options", importId],
    queryFn: () => importsApi.episodeOptions(importId),
    staleTime: 5 * 60_000,
  });
  const [query, setQuery] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const activeRef = useRef<HTMLButtonElement>(null);
  const episodes = data?.episodes ?? [];
  const q = query.trim().toLowerCase().replace(/\s+/g, "");
  const filtered = q
    ? episodes.filter(e =>
        `s${e.season.toString().padStart(2, "0")}e${e.episode.toString().padStart(2, "0")}`.includes(q) ||
        String(e.episode).includes(q) ||
        e.title.toLowerCase().includes(query.trim().toLowerCase()))
    : episodes;

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "center" });
  }, [data]);

  useEffect(() => {
    const onMouseDown = (ev: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(ev.target as Node)) onClose();
    };
    window.addEventListener("mousedown", onMouseDown);
    return () => window.removeEventListener("mousedown", onMouseDown);
  }, [onClose]);

  return (
    <div ref={containerRef} className="absolute z-30 mt-1 w-72 bg-surface-raised border border-purple-900/40 rounded-lg shadow-xl">
      <input
        autoFocus
        type="text"
        value={query}
        onChange={e => setQuery(e.target.value)}
        onKeyDown={e => e.key === "Escape" && onClose()}
        placeholder="Type to filter episodes…"
        className="w-full px-2 py-1.5 bg-surface border-b border-purple-900/40 text-xs text-white focus:outline-none rounded-t-lg"
      />
      <div className="max-h-56 overflow-y-auto py-1">
        {isLoading && <p className="text-xs text-slate-500 px-3 py-2">Loading episodes…</p>}
        {data?.message && <p className="text-xs text-slate-500 px-3 py-2">{data.message}</p>}
        {!isLoading && !data?.message && filtered.length === 0 && (
          <p className="text-xs text-slate-500 px-3 py-2">No matches</p>
        )}
        {filtered.map(e => {
          const isCurrent = e.season === currentSeason && e.episode === currentEpisode;
          return (
            <button
              key={e.id}
              ref={isCurrent ? activeRef : undefined}
              onClick={() => onSelect(e)}
              className={`block w-full text-left px-3 py-1.5 text-xs hover:bg-white/10 transition-colors ${
                isCurrent ? "bg-brand/10 text-brand-light" : "text-slate-300"
              }`}
            >
              S{e.season.toString().padStart(2, "0")}E{e.episode.toString().padStart(2, "0")}
              {e.title && <span className="text-slate-500"> — {e.title}</span>}
            </button>
          );
        })}
      </div>
    </div>
  );
}
