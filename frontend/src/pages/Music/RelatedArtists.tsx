import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Search, Users, Plus, CircleCheck } from "lucide-react";
import { req } from "../../lib/api";
import ArtistCard from "../../components/ArtistCard";

interface RelatedArtist {
  musicbrainz_id: string | null;
  artist_name: string;
  match_score: number;
  already_owned: boolean;
  image_url: string | null;
  bio: string | null;
  genres: string[];
  years_active: string | null;
}

interface SearchResult {
  ok: boolean;
  message: string;
  results: RelatedArtist[];
}

const api = {
  search: (artist: string) =>
    req<SearchResult>(`/artist-discovery/related?artist=${encodeURIComponent(artist)}`),
  add: (mbid: string | null, artist_name: string) =>
    req<{ ok: boolean; message: string }>("/artist-discovery/related/add", {
      method: "POST",
      body: JSON.stringify({ mbid, artist_name }),
    }),
};

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
    />
  );
}

export default function RelatedArtists() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<RelatedArtist[] | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [addingKey, setAddingKey] = useState<string | null>(null);
  const [addedKeys, setAddedKeys] = useState<Set<string>>(new Set());

  const searchMut = useMutation({
    mutationFn: () => api.search(query.trim()),
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

  const submit = () => {
    if (!query.trim()) return;
    setMsg(null);
    searchMut.mutate();
  };

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

      <div className="flex gap-2 mb-6">
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") submit(); }}
          placeholder="Search for an artist…"
          className="flex-1 bg-surface-raised border border-purple-900/40 rounded-lg px-3 py-2 text-sm text-white placeholder:text-slate-500 focus:outline-none focus:border-brand-light"
        />
        <button onClick={submit} disabled={searchMut.isPending || !query.trim()}
          className="flex items-center gap-2 px-3 py-2 rounded-lg bg-brand/30 text-brand-light text-sm hover:bg-brand/40 disabled:opacity-50">
          <Search size={14} /> {searchMut.isPending ? "Searching…" : "Search"}
        </button>
      </div>

      {msg && <p className="text-sm text-slate-400 mb-4">{msg}</p>}

      {results && results.length > 0 && (
        <div className="grid gap-2">
          {results.map(a => {
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
      )}
    </div>
  );
}
