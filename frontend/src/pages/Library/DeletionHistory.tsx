import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { RotateCcw, Download } from "lucide-react";
import { mediaApi, fmtBytes, fmtDate, type MediaItem } from "../../lib/api";
import { SkeletonTable } from "../../components/Skeleton";
import ScrollFadeX from "../../components/ScrollFadeX";

const ACTION_LABELS: Record<string, string> = {
  none: "No *arr action",
  unmonitored: "Unmonitored in *arr",
  deleted_from_arr: "Deleted from *arr",
};

export default function DeletionHistory() {
  const qc = useQueryClient();

  const { data: pending = [] } = useQuery({
    queryKey: ["media-pending"],
    queryFn: () => mediaApi.list({ pending: true, limit: 500 }),
  });

  const { data: log = [], isLoading } = useQuery({
    queryKey: ["deletion-log"],
    queryFn: mediaApi.deletionLog,
  });

  const { data: stats } = useQuery({
    queryKey: ["deletion-stats"],
    queryFn: mediaApi.deletionStats,
  });

  const restoreMut = useMutation({
    mutationFn: (id: number) => mediaApi.restore(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["media-pending"] });
      qc.invalidateQueries({ queryKey: ["media"] });
    },
  });

  return (
    <div className="p-4 sm:p-8">
      <div className="flex items-center justify-between mb-5 gap-3 flex-wrap">
        <p className="text-slate-400 text-sm">What was deleted, when, and how much space it freed</p>
        <div className="flex items-center gap-3">
          {stats && (
            <p className="text-slate-400 text-sm">
              Last 30 days: <span className="text-white font-medium">{stats.deleted_30d} items, {fmtBytes(stats.freed_30d_bytes)}</span>
              <span className="text-slate-600 mx-2">|</span>
              All time: <span className="text-white font-medium">{stats.deleted_total} items, {fmtBytes(stats.freed_total_bytes)}</span>
            </p>
          )}
          <button
            onClick={() => mediaApi.exportDeletionLogCsv()}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-surface-raised border border-purple-900/40 text-slate-300 hover:text-white text-sm transition-colors"
          >
            <Download size={15} />
            CSV
          </button>
        </div>
      </div>

      {pending.length > 0 && (
        <div className="mb-6">
          <h3 className="text-sm font-semibold text-yellow-300 uppercase tracking-wider mb-2">
            Pending deletion ({pending.length}) — restorable until the soft-delete window ends
          </h3>
          <ScrollFadeX className="bg-surface-raised rounded-xl border border-yellow-900/40 overflow-x-auto">
            <table className="w-full text-sm">
              <tbody className="divide-y divide-purple-900/20">
                {pending.map((item: MediaItem) => (
                  <tr key={item.id} className="hover:bg-white/5 transition-colors">
                    <td className="px-4 py-2.5 text-white">
                      {item.parent_title && <span className="text-slate-500 text-xs block">{item.parent_title}</span>}
                      {item.title}
                    </td>
                    <td className="px-4 py-2.5 text-slate-400 capitalize">{item.media_type}</td>
                    <td className="px-4 py-2.5 text-slate-300">{fmtBytes(item.file_size)}</td>
                    <td className="px-4 py-2.5 text-slate-400">requested {fmtDate(item.pending_delete_at)}</td>
                    <td className="px-4 py-2.5 text-right">
                      <button
                        onClick={() => restoreMut.mutate(item.id)}
                        className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-surface-overlay hover:bg-white/10 text-slate-300 text-xs transition-colors ml-auto"
                      >
                        <RotateCcw size={12} />
                        Restore
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ScrollFadeX>
        </div>
      )}

      {isLoading ? (
        <SkeletonTable rows={8} cols={5} />
      ) : log.length === 0 ? (
        <div className="bg-surface-raised rounded-xl border border-purple-900/30 p-10 text-center">
          <p className="text-slate-400">No deletions recorded yet.</p>
        </div>
      ) : (
        <ScrollFadeX className="bg-surface-raised rounded-xl border border-purple-900/30 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-purple-900/30 text-slate-400 text-xs uppercase tracking-wider">
              <tr>
                <th className="text-left px-4 py-3">Title</th>
                <th className="text-left px-4 py-3">Type</th>
                <th className="text-left px-4 py-3">Library</th>
                <th className="text-left px-4 py-3">Size Freed</th>
                <th className="text-left px-4 py-3">*arr Action</th>
                <th className="text-left px-4 py-3">Deleted</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-purple-900/20">
              {log.map(entry => (
                <tr key={entry.id} className="hover:bg-white/5 transition-colors">
                  <td className="px-4 py-3 text-white">
                    {entry.parent_title && <span className="text-slate-500 text-xs block">{entry.parent_title}</span>}
                    {entry.title}
                  </td>
                  <td className="px-4 py-3 text-slate-400 capitalize">{entry.media_type}</td>
                  <td className="px-4 py-3 text-slate-400">{entry.library_section ?? "—"}</td>
                  <td className="px-4 py-3 text-slate-300">{fmtBytes(entry.file_size)}</td>
                  <td className="px-4 py-3 text-slate-400">{ACTION_LABELS[entry.arr_action ?? "none"] ?? entry.arr_action}</td>
                  <td className="px-4 py-3 text-slate-400">{fmtDate(entry.deleted_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollFadeX>
      )}
    </div>
  );
}
