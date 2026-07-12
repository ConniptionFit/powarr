import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Copy, Trash2 } from "lucide-react";
import { mediaApi, fmtBytes, fmtDate, type DuplicateGroup, type EpisodeDeleteMode } from "../../lib/api";
import { SkeletonTable } from "../../components/Skeleton";
import DeletionPreviewModal from "../../components/DeletionPreviewModal";

// LIB-03 — duplicate & upgrade hunter. Distinct from Deletion Suggestions:
// this groups MediaItem rows that look like the same logical title living as
// separate Plex library entries (a stale grab left after an upgrade, a
// re-add, a duplicate import), rather than scoring individual items for
// staleness. The default "keep" per group is the largest file (Powarr's only
// quality proxy without an extra *arr lookup) — the user can pick a
// different one to keep before reviewing/deleting the rest, and actual
// deletion reuses the same preview-delete / batch-delete flow as everywhere
// else so protection flags and *arr cascade warnings still apply.
export default function Duplicates() {
  const qc = useQueryClient();
  const { data: groups = [], isLoading } = useQuery({
    queryKey: ["media-duplicates"],
    queryFn: mediaApi.duplicates,
  });

  const [keepId, setKeepId] = useState<Record<string, number>>({});
  const [previewIds, setPreviewIds] = useState<number[] | null>(null);

  // Seed the "keep" choice from the suggested (largest-file) item whenever a
  // group first appears — doesn't clobber a choice the user already made.
  useEffect(() => {
    setKeepId(prev => {
      const next = { ...prev };
      let changed = false;
      for (const g of groups) {
        const key = groupKey(g);
        if (!(key in next)) { next[key] = g.suggested_keep_id; changed = true; }
      }
      return changed ? next : prev;
    });
  }, [groups]);

  const deleteBatchMut = useMutation({
    mutationFn: ({ ids, deleteMode }: { ids: number[]; deleteMode?: EpisodeDeleteMode }) =>
      mediaApi.deleteBatch(ids, deleteMode),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["media-duplicates"] });
      qc.invalidateQueries({ queryKey: ["media"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      setPreviewIds(null);
    },
  });

  if (isLoading) return <SkeletonTable rows={5} />;

  const totalReclaimable = groups.reduce((sum, g) => sum + g.reclaimable_bytes, 0);

  return (
    <div>
      <div className="flex items-center justify-between mb-5 gap-3 flex-wrap">
        <p className="text-slate-400 text-sm">
          Same title, multiple copies living separately in Plex — pick what to keep, review, then delete the rest.
        </p>
        {groups.length > 0 && (
          <p className="text-slate-400 text-sm">
            {groups.length} group{groups.length === 1 ? "" : "s"} · up to{" "}
            <span className="text-white font-medium">{fmtBytes(totalReclaimable)}</span> reclaimable
          </p>
        )}
      </div>

      {groups.length === 0 ? (
        <div className="text-center py-16 text-slate-500">
          <Copy size={32} className="mx-auto mb-3 opacity-40" />
          No duplicates found — every movie, show, artist, and album title is a single library entry.
        </div>
      ) : (
        <div className="space-y-4">
          {groups.map(g => {
            const key = groupKey(g);
            const keep = keepId[key] ?? g.suggested_keep_id;
            const toDelete = g.items.filter(i => i.id !== keep).map(i => i.id);
            return (
              <div key={key} className="bg-surface-raised border border-purple-900/40 rounded-lg p-4">
                <div className="flex items-center justify-between mb-3 gap-3 flex-wrap">
                  <div>
                    <span className="text-white font-medium">{g.title}</span>
                    {g.year && <span className="text-slate-500 ml-2">({g.year})</span>}
                    <span className="text-slate-500 ml-2 text-xs uppercase">{g.media_type}</span>
                  </div>
                  <div className="flex items-center gap-3">
                    {g.has_size_signal ? (
                      <span className="text-slate-400 text-sm">
                        Reclaim <span className="text-white font-medium">{fmtBytes(g.reclaimable_bytes)}</span>
                      </span>
                    ) : (
                      <span className="text-yellow-500/80 text-xs">
                        No file-size signal — files live at the episode/track level, pick manually
                      </span>
                    )}
                    <button
                      onClick={() => setPreviewIds(toDelete)}
                      disabled={toDelete.length === 0}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-red-900/30 border border-red-900/50 text-red-300 hover:bg-red-900/50 text-sm transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      <Trash2 size={14} />
                      Delete {toDelete.length} duplicate{toDelete.length === 1 ? "" : "s"}
                    </button>
                  </div>
                </div>
                <div className="space-y-1.5">
                  {g.items.map(item => (
                    <label
                      key={item.id}
                      className="flex items-center gap-3 px-3 py-2 rounded-md bg-surface hover:bg-white/5 transition-colors cursor-pointer text-sm"
                    >
                      <input
                        type="radio"
                        name={`keep-${key}`}
                        checked={item.id === keep}
                        onChange={() => setKeepId(prev => ({ ...prev, [key]: item.id }))}
                        className="accent-brand"
                      />
                      <span className={
                        item.id === keep
                          ? (g.has_size_signal ? "text-green-400 font-medium" : "text-slate-300 font-medium")
                          : "text-slate-300"
                      }>
                        {item.id === keep ? "Keep" : "Delete"}
                      </span>
                      <span className="text-slate-400 flex-1 truncate" title={item.file_path ?? undefined}>
                        {item.file_path ?? "(no file path)"}
                      </span>
                      <span className="text-slate-500">{fmtBytes(item.file_size)}</span>
                      {item.library_section && (
                        <span className="text-slate-600 text-xs">{item.library_section}</span>
                      )}
                      <span className="text-slate-600 text-xs">{fmtDate(item.added_at)}</span>
                    </label>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {previewIds && (
        <DeletionPreviewModal
          ids={previewIds}
          onCancel={() => setPreviewIds(null)}
          onConfirm={deleteMode => deleteBatchMut.mutate({ ids: previewIds, deleteMode })}
          confirming={deleteBatchMut.isPending}
        />
      )}
    </div>
  );
}

function groupKey(g: DuplicateGroup): string {
  return `${g.media_type}:${g.title}:${g.year ?? ""}`;
}
