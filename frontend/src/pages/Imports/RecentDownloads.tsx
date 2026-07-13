import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Search, RotateCcw, AlertTriangle, X } from "lucide-react";
import { importsApi, fmtDate } from "../../lib/api";
import { PLATFORM_META, PLATFORM_ORDER, type PlatformName } from "../../components/PlatformIcon";
import { SkeletonTable } from "../../components/Skeleton";

// FI-10 — flags from the nightly malformed-import audit (settled Sonarr
// packs whose current on-disk coverage looks incomplete). Notify-and-triage
// only; re-import reuses the same forceReimport action as the table below.
function MalformedAuditPanel({ onReimport }: {
  onReimport: (sourceApp: string, downloadId: string, matchedId: number) => void;
}) {
  const qc = useQueryClient();
  const { data: flags = [] } = useQuery({
    queryKey: ["malformed-audit"],
    queryFn: () => importsApi.malformedAudit(),
  });
  const dismissMut = useMutation({
    mutationFn: (id: number) => importsApi.malformedAuditDismiss(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["malformed-audit"] }),
  });

  if (flags.length === 0) return null;

  return (
    <div className="mb-5 rounded-lg border border-orange-900/50 bg-orange-950/20 p-4">
      <div className="flex items-center gap-2 mb-3 text-orange-300 text-sm font-medium">
        <AlertTriangle size={15} />
        {flags.length} possibly-malformed import{flags.length === 1 ? "" : "s"} — coverage looks incomplete
      </div>
      <div className="space-y-1.5">
        {flags.map(f => (
          <div key={f.id} className="flex items-center gap-3 px-3 py-2 rounded-md bg-surface text-sm">
            <span className="text-slate-300 flex-1 truncate" title={f.source_title}>
              {f.matched_title ?? f.source_title}
            </span>
            {f.pack_label && <span className="text-slate-500 text-xs">{f.pack_label}</span>}
            <span className="text-orange-300 text-xs">
              {f.mapped_episodes}/{f.total_episodes} episodes
              {f.coverage_ratio != null && ` (${Math.round(f.coverage_ratio * 100)}%)`}
            </span>
            {f.matched_id != null && (
              <button
                onClick={() => onReimport(f.source_app, f.download_id, f.matched_id!)}
                title="Force a re-import of this download"
                className="flex items-center gap-1 px-2 py-1 rounded bg-surface-overlay hover:bg-white/10 text-slate-300 text-xs transition-colors"
              >
                <RotateCcw size={11} /> Re-import
              </button>
            )}
            <button
              onClick={() => dismissMut.mutate(f.id)}
              title="Dismiss"
              className="p-1 rounded hover:bg-white/10 text-slate-500 hover:text-slate-300 transition-colors"
            >
              <X size={13} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

// FI-09 — browse/search recently grabbed downloads across all enabled *arr
// apps and force a one-shot re-import, independent of stuck-import
// detection (Scan Now only surfaces items the queue/history heuristics flag
// as stuck). Re-import goes through the exact same push_import_command()
// path as Accept elsewhere in the app — this page just supplies a different
// source for download_id/matched_id, chosen from the app's own grab
// history rather than a triage row.
export default function RecentDownloads() {
  const qc = useQueryClient();
  const [platformFilter, setPlatformFilter] = useState<PlatformName | "">("");
  const [search, setSearch] = useState("");
  const [msg, setMsg] = useState<Record<string, string>>({});

  const { data: rows = [], isLoading, isFetching } = useQuery({
    queryKey: ["recent-downloads", platformFilter, search],
    queryFn: () => importsApi.recentDownloads({
      source_app: platformFilter || undefined,
      search: search.trim() || undefined,
    }),
  });

  const reimportMut = useMutation({
    mutationFn: ({ sourceApp, downloadId, matchedId }: { sourceApp: string; downloadId: string; matchedId: number }) =>
      importsApi.forceReimport(sourceApp, downloadId, matchedId),
    onSuccess: (result, vars) => {
      setMsg(prev => ({ ...prev, [vars.downloadId]: result.message }));
      qc.invalidateQueries({ queryKey: ["imports"] });
    },
    onError: (err: Error, vars) => {
      setMsg(prev => ({ ...prev, [vars.downloadId]: err.message }));
    },
  });

  return (
    <div>
      <p className="text-slate-400 text-sm mb-5">
        Browse recent grabs from every enabled *arr app and force a re-import — distinct from Scan Now,
        which only surfaces items already flagged as stuck.
      </p>

      <MalformedAuditPanel
        onReimport={(sourceApp, downloadId, matchedId) =>
          reimportMut.mutate({ sourceApp, downloadId, matchedId })}
      />

      <div className="flex items-center gap-2 mb-5 flex-wrap">
        <div className="relative">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            type="search"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search title…"
            className="pl-8 pr-3 py-1.5 w-56 bg-surface-raised border border-purple-900/40 rounded-lg text-sm text-white placeholder:text-slate-500"
          />
        </div>
        {PLATFORM_ORDER.map(p => {
          const meta = PLATFORM_META[p];
          const Icon = meta.Icon;
          const active = platformFilter === p;
          return (
            <button
              key={p}
              onClick={() => setPlatformFilter(active ? "" : p)}
              title={`Filter by ${meta.label}`}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition-colors border ${
                active
                  ? `${meta.badge} border-transparent text-white`
                  : `bg-surface-raised hover:text-white border-purple-900/40 ${meta.chip}`
              }`}
            >
              <Icon size={13} />
              {meta.label}
            </button>
          );
        })}
        {isFetching && !isLoading && <span className="text-slate-500 text-xs">refreshing…</span>}
      </div>

      {isLoading ? (
        <SkeletonTable rows={8} />
      ) : rows.length === 0 ? (
        <div className="text-center py-16 text-slate-500">No recent grabs found for this filter.</div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-purple-900/40">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-surface-raised text-slate-400 text-left">
                <th className="px-4 py-2.5 font-medium">Source</th>
                <th className="px-4 py-2.5 font-medium">Release</th>
                <th className="px-4 py-2.5 font-medium">Library Match</th>
                <th className="px-4 py-2.5 font-medium">Grabbed</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
                <th className="px-4 py-2.5 font-medium"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-purple-900/10">
              {rows.map(row => {
                const meta = PLATFORM_META[row.source_app as PlatformName];
                const Icon = meta?.Icon;
                const rowMsg = msg[row.download_id];
                const pending = reimportMut.isPending && reimportMut.variables?.downloadId === row.download_id;
                return (
                  <tr key={`${row.source_app}-${row.download_id}`} className="hover:bg-white/5 transition-colors">
                    <td className="px-4 py-3">
                      <span className="flex items-center gap-1.5 text-slate-300">
                        {Icon && <Icon size={13} />}
                        {meta?.label ?? row.source_app}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-300 max-w-xs truncate" title={row.source_title}>
                      {row.source_title}
                    </td>
                    <td className="px-4 py-3 text-slate-300">
                      {row.matched_title ?? <span className="text-slate-600">—</span>}
                    </td>
                    <td className="px-4 py-3 text-slate-500 text-xs">{fmtDate(row.event_date)}</td>
                    <td className="px-4 py-3">
                      {row.still_in_queue ? (
                        <span className="px-2 py-0.5 rounded text-xs font-bold bg-orange-900/60 text-orange-300">
                          Still in queue
                        </span>
                      ) : (
                        <span className="px-2 py-0.5 rounded text-xs font-bold bg-surface-overlay text-slate-400">
                          Left queue
                        </span>
                      )}
                      {rowMsg && <div className="text-xs text-slate-400 mt-1 max-w-xs">{rowMsg}</div>}
                    </td>
                    <td className="px-4 py-3">
                      {row.matched_id != null ? (
                        <button
                          onClick={() => reimportMut.mutate({
                            sourceApp: row.source_app, downloadId: row.download_id, matchedId: row.matched_id!,
                          })}
                          disabled={pending}
                          title="Force a re-import of this download via ManualImport"
                          className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-surface-overlay hover:bg-white/10 text-slate-300 text-xs transition-colors disabled:opacity-50"
                        >
                          <RotateCcw size={12} className={pending ? "animate-spin" : ""} />
                          Force re-import
                        </button>
                      ) : (
                        <span className="text-slate-600 text-xs" title="No library match on this grab — nothing to re-import against">
                          No match
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
