import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Activity, Copy, DownloadCloud, Image, Link2, Shield, Trash2 } from "lucide-react";
import { mediaApi, fmtBytes, type LibraryHealth, type ReclaimPoint } from "../../lib/api";
import { SkeletonGrid } from "../../components/Skeleton";
import ScrollFadeX from "../../components/ScrollFadeX";

// LIB-06 — library health dashboard. Read-only KPI tiles over signals Powarr
// already tracks locally (synced tables only — the endpoint makes no live
// Plex/*arr calls). Deliberately no composite 0-100 "health score": score
// formulas are a confirmation-gated surface in Powarr, and each tile links
// to the page where the number can actually be acted on instead.
// LIB-11 — reclaimed space over time. Powarr has always logged every deletion
// with its size and an indexed timestamp, but surfaced only two aggregates
// (30-day and all-time), so there was no way to see whether cleanup keeps up
// with growth. Drawn with plain divs rather than pulling in a charting
// dependency for one sparkline — the app ships four runtime deps and this
// doesn't warrant a fifth.
function ReclaimTrendCard() {
  const { data } = useQuery({
    queryKey: ["reclaim-trend", 90],
    queryFn: () => mediaApi.reclaimTrend(90),
  });
  if (!data) return null;

  const peak = Math.max(...data.points.map(p => p.bytes), 0);
  const active = data.points.filter(p => p.deleted > 0).length;

  return (
    <div className="bg-surface-raised rounded-xl border border-purple-900/30 p-5 mb-6">
      <div className="flex items-baseline justify-between gap-3 flex-wrap mb-3">
        <div>
          <p className="text-slate-400 text-xs uppercase tracking-wider">Space reclaimed — last 90 days</p>
          <p className="text-white text-lg font-semibold mt-0.5">
            {fmtBytes(data.total_bytes)}{" "}
            <span className="text-slate-500 text-sm font-normal">
              across {data.total_deleted.toLocaleString()} deletion{data.total_deleted === 1 ? "" : "s"}
            </span>
          </p>
        </div>
        {active > 0 && (
          <p className="text-slate-500 text-xs">
            {active} active day{active === 1 ? "" : "s"} · peak {fmtBytes(peak)}
          </p>
        )}
      </div>

      {data.total_deleted === 0 ? (
        <p className="text-slate-500 text-xs">
          Nothing has been deleted through Powarr in this window, so there's no trend to plot yet.
          Deletions made here are logged automatically and will appear.
        </p>
      ) : (
        <>
          <div className="flex items-end gap-px h-16" role="img"
               aria-label={`Daily reclaimed space over the last ${data.days} days, totalling ${fmtBytes(data.total_bytes)}`}>
            {data.points.map(p => <TrendBar key={p.date} point={p} peak={peak} />)}
          </div>
          <div className="flex justify-between text-[10px] text-slate-600 mt-1.5">
            <span>{data.points[0]?.date}</span>
            <span>{data.points[data.points.length - 1]?.date}</span>
          </div>
        </>
      )}
    </div>
  );
}

function TrendBar({ point, peak }: { point: ReclaimPoint; peak: number }) {
  // Zero-days keep a 1px floor so the axis reads as continuous rather than
  // looking like missing data.
  const pct = peak > 0 ? (point.bytes / peak) * 100 : 0;
  return (
    <div
      className="flex-1 min-w-0 bg-brand-light/70 hover:bg-brand-light rounded-sm transition-colors"
      style={{ height: `${Math.max(pct, point.bytes > 0 ? 4 : 1.5)}%` }}
      title={`${point.date}: ${point.deleted} deleted, ${fmtBytes(point.bytes)}`}
    />
  );
}

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

      <ReclaimTrendCard />

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
        <Tile
          icon={Link2}
          color="bg-sky-700"
          title="*arr link coverage"
          body={<ArrCoverage data={data} />}
        />
        <Tile
          icon={Copy}
          color="bg-amber-700"
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
          icon={DownloadCloud}
          color="bg-purple-700"
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
          icon={Image}
          color="bg-indigo-600"
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
          icon={Shield}
          color="bg-emerald-700"
          title="Protected from deletion"
          body={<Protections data={data} />}
        />
        <Tile
          icon={Trash2}
          color="bg-red-700"
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
          <ScrollFadeX className="overflow-x-auto">
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
          </ScrollFadeX>
        )}
      </div>
    </div>
  );
}

// Icon-badge + label layout matches Overview's StatCard so the two KPI grids
// read as one family; unlike StatCard, body is a free-form slot since these
// tiles render progress bars and multi-row lists, not just a number.
function Tile({ icon: Icon, color, title, to, body }: {
  icon: React.ElementType;
  color: string;
  title: string;
  to?: string;
  body: React.ReactNode;
}) {
  const inner = (
    <div className="bg-surface-raised rounded-xl border border-purple-900/30 p-5 h-full flex items-start gap-4 hover:border-purple-700/50 transition-colors">
      <div className={`p-3 rounded-lg ${color} flex-shrink-0`}>
        <Icon size={20} className="text-white" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-slate-400 text-xs uppercase tracking-wider mb-1">{title}</p>
        {body}
      </div>
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
