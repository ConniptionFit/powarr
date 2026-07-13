import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { X, Link2, Unlink } from "lucide-react";
import { mediaApi, type MediaItem, type ArrCandidate } from "../lib/api";

const APP_LABEL: Record<string, string> = { movie: "Radarr", episode: "Sonarr", track: "Lidarr" };
const ID_FIELD: Record<string, keyof MediaItem> = {
  movie: "radarr_id", episode: "sonarr_id", track: "lidarr_id",
};

// INT-02 — manual override for a bad auto-linked radarr_id/sonarr_id/lidarr_id,
// without full resync gymnastics. The linked id is what deletion propagation
// (services/deleter.py) acts on, so a wrong link here is the kind of mistake
// worth making easy to see and fix directly rather than only via the raw DB.
export default function ArrLinkModal({ item, onClose }: { item: MediaItem; onClose: () => void }) {
  const qc = useQueryClient();
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(query), 250);
    return () => clearTimeout(t);
  }, [query]);

  const appLabel = APP_LABEL[item.media_type] ?? item.media_type;
  const idField = ID_FIELD[item.media_type];
  const currentId = idField ? item[idField] : null;

  const { data, isLoading } = useQuery({
    queryKey: ["arr-candidates", item.id, debounced],
    queryFn: () => mediaApi.arrCandidates(item.id, debounced),
  });

  const save = async (value: number | null) => {
    setSaving(true);
    setError(null);
    try {
      await mediaApi.setArrLink(item.id, value);
      qc.invalidateQueries({ queryKey: ["media"] });
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  if (!idField) return null;

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="bg-surface-raised border border-purple-900/40 rounded-2xl w-full max-w-md shadow-2xl max-h-[80vh] flex flex-col">
        <div className="flex items-center justify-between px-6 py-4 border-b border-purple-900/20">
          <div>
            <h2 className="text-lg font-bold text-white">Fix {appLabel} Link</h2>
            <p className="text-slate-500 text-xs mt-0.5 truncate max-w-xs" title={item.title}>{item.title}</p>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white flex-shrink-0">
            <X size={18} />
          </button>
        </div>

        <div className="px-6 py-4 space-y-3">
          <p className="text-slate-400 text-xs">
            Currently linked: <span className="text-white font-mono">{currentId ?? "none"}</span>
            {currentId != null && (
              <button
                onClick={() => save(null)}
                disabled={saving}
                className="ml-2 inline-flex items-center gap-1 text-red-400 hover:text-red-300 disabled:opacity-50"
              >
                <Unlink size={12} /> Clear
              </button>
            )}
          </p>
          {error && <p className="text-xs text-red-400">{error}</p>}
          <input
            type="text"
            autoFocus
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder={`Search ${appLabel} library…`}
            className="w-full bg-surface border border-purple-900/40 rounded-lg px-3 py-2 text-sm text-white placeholder:text-slate-600"
          />
        </div>

        <div className="px-6 pb-4 overflow-y-auto flex-1">
          {isLoading ? (
            <p className="text-slate-500 text-sm">Loading…</p>
          ) : !data || data.candidates.length === 0 ? (
            <p className="text-slate-500 text-sm">
              {appLabel} not connected, or no matches
              {query ? ` for "${query}"` : ""}.
            </p>
          ) : (
            <div className="space-y-1">
              {data.candidates.map((c: ArrCandidate) => (
                <button
                  key={c.id}
                  onClick={() => save(c.id)}
                  disabled={saving}
                  className={`w-full flex items-center justify-between gap-2 px-3 py-2 rounded-lg text-left text-sm transition-colors disabled:opacity-50 ${
                    c.id === currentId
                      ? "bg-brand/20 border border-brand/40 text-white"
                      : "hover:bg-white/5 text-slate-300"
                  }`}
                >
                  <span className="truncate">
                    {c.title}{c.year ? ` (${c.year})` : ""}
                  </span>
                  <span className="flex items-center gap-1 text-slate-600 text-xs flex-shrink-0">
                    <Link2 size={11} /> {c.id}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
