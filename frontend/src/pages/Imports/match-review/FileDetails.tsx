import { useState, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { importsApi, fmtBytes } from "../../../lib/api";
import { EpisodePicker, type EpisodeOption } from "./MatchOverride";

// Concise LLM pack-match reasoning badges — mirrors the closed vocabulary the
// backend prompt asks for (llm_assist.PACK_MATCH_TYPES). Strongest evidence
// (green) to weakest (red), so a glance at color tells you what needs review.
export const MATCH_TYPE_STYLES: Record<string, string> = {
  "Exact Match": "bg-green-900/40 text-green-300",
  "Title Match": "bg-teal-900/40 text-teal-300",
  "Number Match": "bg-blue-900/40 text-blue-300",
  "Absolute Number Match": "bg-indigo-900/40 text-indigo-300",
  "Multi-Episode File": "bg-purple-900/40 text-purple-300",
  "Sequence Match": "bg-orange-900/40 text-orange-300",
  "Low Confidence": "bg-red-900/40 text-red-300",
};

export const basename = (p: string) => p.split("/").pop() ?? p;

export function FileDetails({ importId, sourceApp, matchedId, packFileMatches }: {
  importId: number; sourceApp: string; matchedId: number | null; packFileMatches: string | null;
}) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["import-files", importId],
    queryFn: () => importsApi.files(importId),
    staleTime: 60_000,
  });
  const [editingPath, setEditingPath] = useState<string | null>(null);

  const matchByFile = useMemo(() => {
    const map = new Map<string, { match_type: string; confidence: string; reason: string }>();
    if (!packFileMatches) return map;
    try {
      const parsed: Array<{ file: string; match_type?: string; confidence: string; reason: string }> = JSON.parse(packFileMatches);
      for (const m of parsed) map.set(basename(m.file), { match_type: m.match_type ?? "Low Confidence", confidence: m.confidence, reason: m.reason });
    } catch { /* malformed — ignore, badge just won't show */ }
    return map;
  }, [packFileMatches]);

  const mappingMut = useMutation({
    mutationFn: ({ path, ep }: { path: string; ep: EpisodeOption }) =>
      importsApi.updateFileMapping(importId, path, ep.id, ep.season, ep.episode, ep.title),
    onSuccess: () => {
      setEditingPath(null);
      qc.invalidateQueries({ queryKey: ["import-files", importId] });
      qc.invalidateQueries({ queryKey: ["imports"] });
    },
  });

  const editable = sourceApp === "sonarr" && !!matchedId;

  if (isLoading) return <p className="text-slate-500 text-xs px-4 py-2">Loading file details…</p>;
  if (!data || data.files.length === 0)
    return <p className="text-slate-500 text-xs px-4 py-2">{data?.message ?? "No file details available"}</p>;

  const parseCurrent = (detail: string): { season: number | null; episode: number | null } => {
    const m = detail.match(/S(\d+)E(\d+)/i);
    return m ? { season: Number(m[1]), episode: Number(m[2]) } : { season: null, episode: null };
  };

  return (
    <table className="w-full text-xs">
      <thead className="text-slate-500 uppercase tracking-wider">
        <tr>
          <th className="text-left px-4 py-1.5">File</th>
          <th className="text-left px-4 py-1.5">Size</th>
          <th className="text-left px-4 py-1.5">Quality</th>
          <th className="text-left px-4 py-1.5">Mapped To</th>
          <th className="text-left px-4 py-1.5">Match Reasoning</th>
          <th className="text-left px-4 py-1.5">Rejections</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-purple-900/10 text-slate-400">
        {data.files.map((f, i) => {
          const current = parseCurrent(f.detail);
          const isEditing = editable && !!f.raw_path && editingPath === f.raw_path;
          const label = f.mapped_to ? `${f.mapped_to}${f.detail ? ` (${f.detail})` : ""}` : "unmapped";
          const match = f.path ? matchByFile.get(basename(f.path)) : undefined;
          const status = f.import_status ?? (f.rejections.length ? "blocked" : "ok");
          const rowCls = status === "covered"
            ? "bg-red-950/25 text-red-200/90"
            : status === "ok"
              ? "bg-green-950/20"
              : "";
          const statusTitle = status === "covered"
            ? "Already in library at equal-or-better quality — skipped on import"
            : status === "ok"
              ? "Will import (missing or upgrade)"
              : f.rejections.join("; ") || "Blocked by *arr rejections";
          return (
            <tr key={i} className={rowCls} title={statusTitle}>
              <td className="px-4 py-1.5 max-w-xs truncate" title={f.path ?? ""}>
                <span className="inline-flex items-center gap-1.5">
                  {status === "covered" && (
                    <span className="w-1.5 h-1.5 rounded-full bg-red-400 flex-shrink-0" title={statusTitle} />
                  )}
                  {status === "ok" && (
                    <span className="w-1.5 h-1.5 rounded-full bg-green-400 flex-shrink-0" title={statusTitle} />
                  )}
                  {f.path ?? "—"}
                </span>
              </td>
              <td className="px-4 py-1.5">{f.size ? fmtBytes(f.size) : "—"}</td>
              <td className="px-4 py-1.5">{f.quality ?? "—"}</td>
              <td className="px-4 py-1.5 relative">
                {editable && f.raw_path ? (
                  <button
                    onClick={() => setEditingPath(isEditing ? null : (f.raw_path as string))}
                    title="Click to adjust the episode mapping for this file"
                    className="text-left hover:text-brand-light transition-colors underline decoration-dotted underline-offset-2"
                  >
                    {label}
                  </button>
                ) : (
                  <span>{label}</span>
                )}
                {f.overridden && (
                  <span className="ml-1.5 text-green-400 text-[10px] font-bold uppercase align-middle">Overridden</span>
                )}
                {isEditing && f.raw_path && (
                  <EpisodePicker
                    importId={importId}
                    currentSeason={current.season}
                    currentEpisode={current.episode}
                    onSelect={ep => mappingMut.mutate({ path: f.raw_path as string, ep })}
                    onClose={() => setEditingPath(null)}
                  />
                )}
              </td>
              <td className="px-4 py-1.5">
                {match ? (
                  <span
                    title={`${match.confidence} confidence${match.reason ? ` — ${match.reason}` : ""}`}
                    className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-wide ${MATCH_TYPE_STYLES[match.match_type] ?? "bg-surface-overlay text-slate-400"}`}
                  >
                    {match.match_type}
                  </span>
                ) : "—"}
              </td>
              <td className="px-4 py-1.5 max-w-xs truncate" title={f.rejections.join("; ")}>
                {f.rejections.length ? f.rejections.join("; ") : "—"}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
