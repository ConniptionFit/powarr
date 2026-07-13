import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Search, Users, Plus, CircleCheck, Music2 } from "lucide-react";
import { req } from "../../lib/api";
import ArtistCard, { SourceBadge } from "../../components/ArtistCard";
import ArtistPreviewButton from "../../components/ArtistPreviewButton";

interface ArtistNameMatch {
  artist_name: string;
  musicbrainz_id: string | null;
  image_url: string | null;
  genres: string[];
}

interface RelatedArtist {
  musicbrainz_id: string | null;
  artist_name: string;
  match_score: number;
  already_owned: boolean;
  image_url: string | null;
  bio: string | null;
  genres: string[];
  years_active: string | null;
  similarity_sources: string[];
}

// AD-14: one badge per contributing source, each result gets 1-3 of these.
const SOURCE_BADGES: Record<string, SourceBadge> = {
  lastfm: { label: "Last.fm", className: "bg-red-900/40 text-red-300" },
  plex_sonic: { label: "Sonically Similar", className: "bg-amber-900/40 text-amber-300" },
  plex_similar: { label: "Plex Similar", className: "bg-teal-900/40 text-teal-300" },
};

interface SearchResult {
  ok: boolean;
  message: string;
  results: RelatedArtist[];
}

const PAGE_SIZE = 50;

const api = {
  search: (artist: string, limit: number) =>
    req<SearchResult>(`/artist-discovery/related?artist=${encodeURIComponent(artist)}&limit=${limit}`),
  add: (mbid: string | null, artist_name: string) =>
    req<{ ok: boolean; message: string }>("/artist-discovery/related/add", {
      method: "POST",
      body: JSON.stringify({ mbid, artist_name }),
    }),
  searchNames: (q: string) =>
    req<{ ok: boolean; message: string; results: ArtistNameMatch[] }>(
      `/artist-discovery/related/search-names?q=${encodeURIComponent(q)}&limit=8`),
};

// Debounce a fast-changing value (typeahead input) so we don't fire a
// network request on every keystroke.
function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(t);
  }, [value, delayMs]);
  return debounced;
}

function RelatedArtistCard({ a, onAdd, adding, added }: {
  a: RelatedArtist;
  onAdd: () => void;
  adding: boolean;
  added: boolean;
}) {
  const owned = a.already_owned || added;
  return (
    <ArtistCard
      name={a.artist_name}
      yearsActive={a.years_active}
      imageUrl={a.image_url}
      bio={a.bio}
      genres={a.genres}
      subtitle={a.match_score > 0 ? `${Math.round(a.match_score * 100)}% sonic/tag match` : "Related artist"}
      sourceBadges={(a.similarity_sources || []).map(s => SOURCE_BADGES[s]).filter(Boolean)}
      actions={
        owned ? (
          <span className="flex items-center gap-1 text-xs text-slate-500 px-2 py-1" title="Already in your library">
            <CircleCheck size={14} className="text-green-500" /> In your library
          </span>
        ) : (
          <button onClick={onAdd} disabled={adding} title="Add to Lidarr"
            className="flex items-center gap-1 px-2 py-1.5 rounded hover:bg-green-900/40 text-slate-400 hover:text-green-300 disabled:opacity-40 text-xs">
            <Plus size={15} /> {adding ? "Adding…" : "Add"}
          </button>
        )
      }
      preview={<ArtistPreviewButton artistName={a.artist_name} />}
    />
  );
}

export default function RelatedArtists() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<RelatedArtist[] | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [addingKey, setAddingKey] = useState<string | null>(null);
  const [addedKeys, setAddedKeys] = useState<Set<string>>(new Set());
  const [hideOwned, setHideOwned] = useState(true);
  const [limit, setLimit] = useState(PAGE_SIZE);

  // Typeahead — pick the right artist before running the slower similar-
  // artists search below. Closes on selection, blur, or Escape.
  const [showSuggestions, setShowSuggestions] = useState(false);
  const debouncedQuery = useDebounced(query, 300);
  const searchBoxRef = useRef<HTMLDivElement>(null);
  const { data: nameMatches } = useQuery({
    queryKey: ["related-artist-typeahead", debouncedQuery],
    queryFn: () => api.searchNames(debouncedQuery.trim()),
    enabled: debouncedQuery.trim().length >= 2 && showSuggestions,
  });
  const suggestions = nameMatches?.results ?? [];

  useEffect(() => {
    const onClickOutside = (e: MouseEvent) => {
      if (searchBoxRef.current && !searchBoxRef.current.contains(e.target as Node)) {
        setShowSuggestions(false);
      }
    };
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  const searchMut = useMutation({
    mutationFn: ({ name, nextLimit }: { name: string; nextLimit: number }) => api.search(name, nextLimit),
    onSuccess: (r) => {
      setMsg(r.message);
      setResults(r.results);
    },
    onError: (e: Error) => { setMsg(e.message); setResults(null); },
  });

  const addMut = useMutation({
    mutationFn: ({ mbid, name }: { mbid: string | null; name: string }) => api.add(mbid, name),
    onSuccess: (r, vars) => {
      setMsg(r.message);
      if (r.ok) setAddedKeys(prev => new Set(prev).add(vars.mbid || vars.name));
      setAddingKey(null);
    },
    onError: (e: Error) => { setMsg(e.message); setAddingKey(null); },
  });

  const submit = (name?: string) => {
    const target = (name ?? query).trim();
    if (!target) return;
    setShowSuggestions(false);
    if (name !== undefined) setQuery(name);
    setMsg(null);
    setLimit(PAGE_SIZE);
    searchMut.mutate({ name: target, nextLimit: PAGE_SIZE });
  };

  const loadMore = () => {
    const nextLimit = limit + PAGE_SIZE;
    setLimit(nextLimit);
    searchMut.mutate({ name: query.trim(), nextLimit });
  };

  const visible = (results || []).filter(a => !hideOwned || !a.already_owned);
  const canLoadMore = (results?.length ?? 0) >= limit;

  return (
    <div className="p-4 sm:p-8 max-w-4xl">
      <div className="flex items-center gap-3 mb-6">
        <Users className="text-brand-light" size={22} />
        <div>
          <h1 className="text-2xl font-bold text-white">Related Artists</h1>
          <p className="text-slate-400 text-sm">
            Search any artist to see who's related — bypasses the Discovery queue, add straight to Lidarr.
          </p>
        </div>
      </div>

      <div className="flex gap-2 mb-6 relative" ref={searchBoxRef}>
        <div className="flex-1 relative">
          <input
            value={query}
            onChange={e => { setQuery(e.target.value); setShowSuggestions(true); }}
            onFocus={() => setShowSuggestions(true)}
            onKeyDown={e => { if (e.key === "Enter") submit(); if (e.key === "Escape") setShowSuggestions(false); }}
            placeholder="Search for an artist…"
            autoComplete="off"
            className="w-full bg-surface-raised border border-purple-900/40 rounded-lg px-3 py-2 text-sm text-white placeholder:text-slate-500 focus:outline-none focus:border-brand-light"
          />
          {showSuggestions && suggestions.length > 0 && (
            <div className="absolute z-20 top-full mt-1 w-full max-h-72 overflow-y-auto bg-surface-raised border border-purple-900/40 rounded-lg shadow-xl">
              {suggestions.map(s => (
                <button
                  key={s.musicbrainz_id || s.artist_name}
                  onClick={() => submit(s.artist_name)}
                  className="w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-white/5 transition-colors border-b border-purple-900/10 last:border-0"
                >
                  {s.image_url ? (
                    <img src={s.image_url} alt="" className="w-8 h-8 rounded object-cover shrink-0 bg-surface" />
                  ) : (
                    <div className="w-8 h-8 rounded shrink-0 bg-surface flex items-center justify-center">
                      <Music2 size={14} className="text-slate-600" />
                    </div>
                  )}
                  <div className="min-w-0">
                    <p className="text-sm text-white truncate">{s.artist_name}</p>
                    {s.genres.length > 0 && (
                      <p className="text-xs text-slate-500 truncate">{s.genres.join(", ")}</p>
                    )}
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
        <button onClick={() => submit()} disabled={searchMut.isPending || !query.trim()}
          className="flex items-center gap-2 px-3 py-2 rounded-lg bg-brand/30 text-brand-light text-sm hover:bg-brand/40 disabled:opacity-50">
          <Search size={14} /> {searchMut.isPending ? "Searching…" : "Search"}
        </button>
      </div>

      {msg && <p className="text-sm text-slate-400 mb-4">{msg}</p>}

      {results && results.length > 0 && (
        <>
          <label className="flex items-center gap-2 mb-4 text-sm text-slate-400 select-none cursor-pointer">
            <input
              type="checkbox"
              checked={hideOwned}
              onChange={e => setHideOwned(e.target.checked)}
              className="rounded border-purple-900/40 bg-surface-raised"
            />
            Hide artists already in your library
          </label>

          <div className="grid gap-2">
            {visible.map(a => {
              const key = a.musicbrainz_id || a.artist_name;
              return (
                <RelatedArtistCard
                  key={key}
                  a={a}
                  adding={addingKey === key}
                  added={addedKeys.has(key)}
                  onAdd={() => {
                    setAddingKey(key);
                    addMut.mutate({ mbid: a.musicbrainz_id, name: a.artist_name });
                  }}
                />
              );
            })}
          </div>

          {visible.length === 0 && (
            <p className="text-sm text-slate-500 mt-2">
              All results are already in your library — uncheck the toggle above to see them.
            </p>
          )}

          {canLoadMore && (
            <button
              onClick={loadMore}
              disabled={searchMut.isPending}
              className="mt-4 w-full py-2 rounded-lg border border-purple-900/40 text-sm text-slate-400 hover:text-brand-light hover:border-brand-light disabled:opacity-50"
            >
              {searchMut.isPending ? "Loading…" : "Load more"}
            </button>
          )}
        </>
      )}
    </div>
  );
}
