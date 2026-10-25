import { type MediaItem } from "../../../lib/api";
import { type PlatformName } from "../../../components/PlatformIcon";


export function ScoreBadge({ score }: { score: number }) {
  const color =
    score >= 75 ? "bg-red-900/60 text-red-300" :
    score >= 50 ? "bg-yellow-900/60 text-yellow-300" :
    "bg-green-900/60 text-green-300";
  return <span className={`inline-block px-2 py-0.5 rounded text-xs font-bold ${color}`}>{score.toFixed(0)}</span>;
}

/** True when the click landed on (or inside) an interactive control. */
export function isInteractiveTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  return !!target.closest("button, a, input, select, textarea, label, [role='button']");
}

export type SortKey = "score" | "file_size" | "watch_count" | "last_watched_at";

export interface ShowGroup {
  parent_title: string;
  episodes: MediaItem[];
  total_size: number;
  total_watch_count: number;
  last_watched_at: string | null;
  avg_score: number;
  sonarr_id: number | null;
  radarr_id: number | null;
  lidarr_id: number | null;
  ids: number[];
}

export function groupByShow(items: MediaItem[]): ShowGroup[] {
  const map = new Map<string, ShowGroup>();
  for (const ep of items) {
    const key = ep.parent_title || ep.title;
    if (!map.has(key)) {
      map.set(key, {
        parent_title: key,
        episodes: [],
        total_size: 0,
        total_watch_count: 0,
        last_watched_at: null,
        avg_score: 0,
        sonarr_id: ep.sonarr_id,
        radarr_id: ep.radarr_id,
        lidarr_id: ep.lidarr_id,
        ids: [],
      });
    }
    const g = map.get(key)!;
    g.episodes.push(ep);
    g.total_size += ep.file_size;
    g.total_watch_count += ep.watch_count;
    g.ids.push(ep.id);
    if (ep.sonarr_id) g.sonarr_id = ep.sonarr_id;
    if (ep.radarr_id) g.radarr_id = ep.radarr_id;
    if (ep.lidarr_id) g.lidarr_id = ep.lidarr_id;
    if (ep.last_watched_at) {
      if (!g.last_watched_at || ep.last_watched_at > g.last_watched_at) {
        g.last_watched_at = ep.last_watched_at;
      }
    }
  }
  for (const g of map.values()) {
    g.avg_score = g.episodes.reduce((s, e) => s + e.score, 0) / g.episodes.length;
  }
  return Array.from(map.values());
}

export function matchesPlatform(item: { sonarr_id: number | null; radarr_id: number | null; lidarr_id: number | null }, platform: PlatformName): boolean {
  if (platform === "sonarr") return item.sonarr_id != null;
  if (platform === "radarr") return item.radarr_id != null;
  if (platform === "lidarr") return item.lidarr_id != null;
  return false; // readarr has no MediaItem FK — chip omitted on this page
}
