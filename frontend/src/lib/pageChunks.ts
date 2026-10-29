// Dynamic-import loaders for every routed page. App lazy-loads from here and the
// icon rail calls prefetchPath() on hover/focus so the chunk is usually cached by click time.
export const PAGE_LOADERS = {
  Overview: () => import("../pages/Overview"),
  DeletionSuggestions: () => import("../pages/Library/DeletionSuggestions"),
  Duplicates: () => import("../pages/Library/Duplicates"),
  Health: () => import("../pages/Library/Health"),
  Rules: () => import("../pages/Library/Rules"),
  Upgrades: () => import("../pages/Library/Upgrades"),
  DeletionHistory: () => import("../pages/Library/DeletionHistory"),
  MatchReview: () => import("../pages/Imports/MatchReview"),
  RecentDownloads: () => import("../pages/Imports/RecentDownloads"),
  LlmAccuracy: () => import("../pages/Imports/LlmAccuracy"),
  ArtistDiscovery: () => import("../pages/Music/ArtistDiscovery"),
  Playlists: () => import("../pages/Music/Playlists"),
  SettingsPage: () => import("../pages/Settings"),
  LogsPage: () => import("../pages/Logs"),
  Status: () => import("../pages/Logs/Status"),
} as const;

// First screen of each area, keyed by URL prefix (longest match wins).
const BY_PATH: [string, keyof typeof PAGE_LOADERS][] = [
  ["/library/deletion-suggestions", "DeletionSuggestions"],
  ["/library/duplicates", "Duplicates"],
  ["/library/health", "Health"],
  ["/library/rules", "Rules"],
  ["/library/upgrades", "Upgrades"],
  ["/library/deletion-history", "DeletionHistory"],
  ["/library", "DeletionSuggestions"],
  ["/imports/recent-downloads", "RecentDownloads"],
  ["/imports/llm-accuracy", "LlmAccuracy"],
  ["/imports", "MatchReview"],
  ["/music/playlists", "Playlists"],
  ["/music", "ArtistDiscovery"],
  ["/settings", "SettingsPage"],
  ["/logs/status", "Status"],
  ["/logs", "LogsPage"],
];

const warmed = new Set<string>();

export function prefetchPath(path: string) {
  const hit = path === "/" ? (["/", "Overview"] as const) : BY_PATH.find(([p]) => path.startsWith(p));
  if (!hit || warmed.has(hit[1])) return;
  warmed.add(hit[1]);
  PAGE_LOADERS[hit[1] as keyof typeof PAGE_LOADERS]().catch(() => warmed.delete(hit[1]));
}
