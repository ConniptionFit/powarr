import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ShieldAlert, X } from "lucide-react";
import { mediaApi, fmtBytes } from "../lib/api";

// LIB-01 — non-destructive dry-run shown before any delete (single or batch)
// commits: projected GB freed, *arr cascade (an episode/track delete can
// unmonitor or delete an entire series/artist in Sonarr/Lidarr), current
// protection flags, and the soft-delete window. Nothing here writes anything;
// the real delete only fires when the user clicks Confirm.
export default function DeletionPreviewModal({
  ids,
  onCancel,
  onConfirm,
  confirming,
}: {
  ids: number[];
  onCancel: () => void;
  onConfirm: () => void;
  confirming: boolean;
}) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["deletion-preview", ids],
    queryFn: () => mediaApi.previewDelete(ids),
  });

  const cascadeWarnings = data?.items.filter(i => i.cascade_warning) ?? [];

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="bg-surface-raised border border-purple-900/40 rounded-2xl w-full max-w-lg shadow-2xl max-h-[85vh] flex flex-col">
        <div className="flex items-center justify-between px-6 py-4 border-b border-purple-900/20">
          <h2 className="text-lg font-bold text-white">
            {data?.would_pend ? "Confirm Soft-Delete" : "Confirm Delete"}
          </h2>
          <button onClick={onCancel} className="text-slate-400 hover:text-white">
            <X size={18} />
          </button>
        </div>

        <div className="px-6 py-4 overflow-y-auto space-y-4">
          {isLoading ? (
            <p className="text-slate-400 text-sm">Loading preview…</p>
          ) : isError || !data ? (
            <p className="text-red-400 text-sm">Couldn't load the deletion preview. Try again.</p>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3">
                <div className="bg-surface rounded-lg border border-purple-900/20 px-4 py-3">
                  <p className="text-slate-500 text-xs uppercase tracking-wider">Items</p>
                  <p className="text-white text-xl font-bold">{data.total_items}</p>
                </div>
                <div className="bg-surface rounded-lg border border-purple-900/20 px-4 py-3">
                  <p className="text-slate-500 text-xs uppercase tracking-wider">Space Freed</p>
                  <p className="text-white text-xl font-bold">{fmtBytes(data.total_size_bytes)}</p>
                </div>
              </div>

              {data.missing_count > 0 && (
                <p className="text-slate-500 text-xs">
                  {data.missing_count} selected item(s) no longer exist and will be skipped.
                </p>
              )}

              <div className="text-sm text-slate-300">
                {data.would_pend ? (
                  <p>
                    Soft-delete is on ({data.soft_delete_days} day window) — these items will be marked
                    pending and stay restorable from Deletion History until the window expires.
                  </p>
                ) : (
                  <p>Soft-delete is off — these items will be deleted immediately and are not restorable.</p>
                )}
              </div>

              {data.protected_count > 0 && (
                <div className="flex items-start gap-2 bg-yellow-950/30 border border-yellow-900/40 rounded-lg px-3 py-2.5">
                  <ShieldAlert size={16} className="text-yellow-400 flex-shrink-0 mt-0.5" />
                  <p className="text-yellow-200 text-xs">
                    {data.protected_count} of the selected item(s) {data.protected_count === 1 ? "is" : "are"} currently
                    protected (Seerr request, another user's watch, or an actively-seeding torrent). Deleting anyway
                    overrides that protection.
                  </p>
                </div>
              )}

              {cascadeWarnings.length > 0 && (
                <div className="space-y-2">
                  {cascadeWarnings.map(item => (
                    <div key={item.id} className="flex items-start gap-2 bg-red-950/30 border border-red-900/40 rounded-lg px-3 py-2.5">
                      <AlertTriangle size={16} className="text-red-400 flex-shrink-0 mt-0.5" />
                      <div className="text-xs">
                        <p className="text-red-200 font-medium">{item.title}</p>
                        <p className="text-red-300/80">{item.cascade_warning}</p>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>

        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-purple-900/20">
          <button
            onClick={onCancel}
            className="px-4 py-2 rounded-lg bg-surface-overlay hover:bg-white/10 text-slate-300 text-sm transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={confirming || isLoading || !data}
            className="px-4 py-2 rounded-lg bg-red-700 hover:bg-red-600 text-white text-sm transition-colors disabled:opacity-50"
          >
            {confirming ? "Deleting…" : data?.would_pend ? "Confirm Soft-Delete" : "Confirm Delete"}
          </button>
        </div>
      </div>
    </div>
  );
}
