import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Activity, Copy, DownloadCloud, Image, Link2, Shield, Trash2 } from "lucide-react";
import { mediaApi, fmtBytes, type LibraryHealth } from "../../lib/api";
import { SkeletonGrid } from "../../components/Skeleton";

// LIB-06 — library health dashboard. Read-only KPI tiles over signals Powarr
// already tracks locally (synced tables only — the endpoint makes no live
// Plex/*arr calls). Deliberately no composite 0-100 "health score": score
// formulas are a confirmation-gated surface in Powarr, and each tile links
// to the page where the number can actually be acted on instead.
export default function Health() {
  const { data, isLoading } = useQuery({
    queryKey: ["library-health"],
    queryFn: mediaApi.health,
  });

  if (isLoading || !data) return <SkeletonGrid cols={3} rows={2} />;

  return (
    <div className="p-4 sm:p-8">
      <p className="text-slate-400 text-sm mb-5">
        A snapshot of library upkeep — computed from Powarr's synced data, refreshed by the regular Plex sync.
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
        <Tile
          icon={<Link2 size={16} />}
          title="*arr link coverage"
          body={<ArrCoverage data={data} />}
        />
        <Tile
          icon={<Copy size={16} />}
          title="Duplicates"
          to="/library/duplicates"
          body={
            data.duplicate_groups === 0 ? (
              <Good>No duplicate titles found</Good>
            ) : (
              <>
                <Big>{data.duplicate_groups}</Big>
                <Sub>
                  duplicate group{data.duplicate_groups === 1 ? "" : "s"}
                  {data.duplicate_reclaimable_bytes > 0 &&
                    ` · up to ${fmtBytes(data.duplicate_reclaimable_bytes)} reclaimable`}
                </Sub>
              </>
            )
          }
        />
        <Tile
          icon={<DownloadCloud size={16} />}
          title="Import backlog"
          to="/imports"
          body={
            data.open_imports_total === 0 && data.malformed_flags_open === 0 ? (
              <Good>Nothing waiting on review</Good>
            ) : (
              <>
                <Big>{data.open_imports_total}</Big>
                <Sub>
                  open import{data.open_imports_total === 1 ? "" : "s"}
                  {" — "}
                  {Object.entries(data.open_imports_by_status)
                    .filter(([, n]) => n > 0)
                    .map(([s, n]) => `${n} ${s.replace(/_/g, " ")}`)
                    .join(", ") || "none"}
                  {data.malformed_flags_open > 0 &&
                    ` · ${data.malformed_flags_open} malformed-import flag${data.malformed_flags_open === 1 ? "" : "s"}`}
                </Sub>
              </>
            )
          }
        />
        <Tile
          icon={<Image size={16} />}
          title="Artist thumbnails"
          body={
            data.artist_thumbnails_total === 0 ? (
              <Sub>No artist thumbnail cache yet — populated by the daily refresh once Lidarr or Plex music artists sync.</Sub>
            ) : (
              <>
                <Big>
                  {Math.round((data.artist_thumbnails_with_image / data.artist_thumbnails_total) * 100)}%
                </Big>
                <Sub>
                  {data.artist_thumbnails_with_image} of {data.artist_thumbnails_total} library artists have a
                  cached photo (the rest are confirmed misses, retried weekly)
                </Sub>
              </>
            )
          }
        />
        <Tile
          icon={<Shield size={16} />}
          title="Protected from deletion"
          body={<Protections data={data} />}
        />
        <Tile
          icon={<Trash2 size={16} />}
          title="Deletion state"
          to="/library/deletion-suggestions"
          body={
            <>
              <Big>{data.pending_soft_deletes}</Big>
              <Sub>
                pending soft-delete{data.pending_soft_deletes === 1 ? "" : "s"} · {data.ignored_items} item
                {data.ignored_items === 1 ? "" : "s"} ignored
              </Sub>
            </>
          }
        />
      </div>

      <div className="bg-surface-raised border border-purple-900/40 rounded-lg p-4">
        <div className="flex items-center gap-2 text-slate-300 text-sm font-medium mb-3">
          <Activity size={16} className="text-brand-light" /> Library footprint
        </div>
        {data.by_type.length === 0 ? (
          <p className="text-slate-500 text-sm">Nothing synced yet — run a Plex sync first.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-slate-500 text-xs uppercase tracking-wider">
                  <th className="py-1.5 pr-4">Type</th>
                  <th className="py-1.5 pr-4">Items</th>
                  <th className="py-1.5">Size</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-purple-900/20">
                {data.by_type.map(t => (
                  <tr key={t.media_type}>
                    <td className="py-1.5 pr-4 text-white capitalize">{t.media_type}</td>
                    <td className="py-1.5 pr-4 text-slate-300 tabular-nums">{t.count.toLocaleString()}</td>
                    <td className="py-1.5 text-slate-300 tabular-nums">
                      {t.total_size_bytes > 0 ? fmtBytes(t.total_size_bytes) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function Tile({ icon, title, to, body }: { icon: React.ReactNode; title: string; to?: string; body: React.ReactNode }) {
  const inner = (
    <div className="bg-surface-raised border border-purple-900/40 rounded-lg p-4 h-full hover:border-purple-700/50 transition-colors">
      <div className="flex items-center gap-2 text-slate-300 text-sm font-medium mb-2">
        <span className="text-brand-light">{icon}</span> {title}
      </div>
      {body}
    </div>
  );
  return to ? <Link to={to} className="block">{inner}</Link> : inner;
}

function Big({ children }: { children: React.ReactNode }) {
  return <div className="text-2xl text-white font-semibold tabular-nums">{children}</div>;
}

function Sub({ children }: { children: React.ReactNode }) {
  return <div className="text-slate-400 text-xs mt-1">{children}</div>;
}

function Good({ children }: { children: React.ReactNode }) {
  return <div className="text-green-400 text-sm">{children}</div>;
}

function ArrCoverage({ data }: { data: LibraryHealth }) {
  if (data.arr_link_coverage.length === 0) {
    return <Sub>No linkable items synced yet (movies, episodes, tracks).</Sub>;
  }
  const APP_LABEL: Record<string, string> = { radarr_id: "Radarr", sonarr_id: "Sonarr", lidarr_id: "Lidarr" };
  return (
    <div className="space-y-1.5">
      {data.arr_link_coverage.map(c => {
        const pct = c.total > 0 ? Math.round((c.linked / c.total) * 100) : 0;
        return (
          <div key={c.media_type}>
            <div className="flex justify-between text-xs text-slate-400 mb-0.5">
              <span className="capitalize">{c.media_type}s → {APP_LABEL[c.arr_field] ?? c.arr_field}</span>
              <span className="tabular-nums">{c.linked.toLocaleString()}/{c.total.toLocaleString()} ({pct}%)</span>
            </div>
            <div className="h-1.5 rounded bg-surface overflow-hidden">
              <div className={`h-full rounded ${pct >= 90 ? "bg-green-500/70" : pct >= 50 ? "bg-yellow-500/70" : "bg-red-500/70"}`}
                   style={{ width: `${pct}%` }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Protections({ data }: { data: LibraryHealth }) {
  const LABELS: Record<string, string> = {
    seerr_requested: "Actively requested (Seerr)",
    recently_watched: "Recently watched (Tautulli)",
    seeding: "Actively seeding torrent",
    in_progress: "Watch in progress",
  };
  const rows = Object.entries(data.protections);
  const total = rows.reduce((s, [, n]) => s + n, 0);
  if (total === 0) return <Good>No items currently shielded by a protect flag</Good>;
  return (
    <div className="space-y-1">
      {rows.filter(([, n]) => n > 0).map(([k, n]) => (
        <div key={k} className="flex justify-between text-xs text-slate-400">
          <span>{LABELS[k] ?? k}</span>
          <span className="text-slate-300 tabular-nums">{n.toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}
