import type { LucideIcon } from "lucide-react";
import { LayoutDashboard, BookOpen, DownloadCloud, Music, SlidersHorizontal, ScrollText } from "lucide-react";

export interface ScreenDef {
  path: string;
  label: string;
}

export interface AreaDef {
  key: string;
  label: string;
  icon: LucideIcon;
  base: string;
  /** Sub-screens shown as a secondary tab row. Areas with exactly one screen render no tab row. */
  screens: ScreenDef[];
}

// Single source of truth for the icon rail, sub-tab rows, breadcrumb, and routes.
export const AREAS: AreaDef[] = [
  {
    key: "overview",
    label: "Overview",
    icon: LayoutDashboard,
    base: "/",
    screens: [{ path: "/", label: "Overview" }],
  },
  {
    key: "library",
    label: "Library",
    icon: BookOpen,
    base: "/library",
    screens: [
      { path: "/library/deletion-suggestions", label: "Deletion Suggestions" },
      { path: "/library/duplicates", label: "Duplicates" },
      { path: "/library/deletion-history", label: "Deletion History" },
    ],
  },
  {
    key: "imports",
    label: "Imports",
    icon: DownloadCloud,
    base: "/imports",
    screens: [
      { path: "/imports", label: "Imports" },
      { path: "/imports/recent-downloads", label: "Recent Downloads" },
      { path: "/imports/llm-accuracy", label: "LLM Accuracy" },
    ],
  },
  {
    key: "music",
    label: "Music",
    icon: Music,
    base: "/music",
    screens: [
      { path: "/music/discovery", label: "Artist Discovery" },
      { path: "/music/related", label: "Related Artists" },
      { path: "/music/playlists", label: "Playlists" },
    ],
  },
  {
    key: "settings",
    label: "Settings",
    icon: SlidersHorizontal,
    base: "/settings",
    screens: [{ path: "/settings", label: "Settings" }],
  },
];

export const LOGS_AREA: AreaDef = {
  key: "logs",
  label: "Logs",
  icon: ScrollText,
  base: "/logs",
  screens: [{ path: "/logs", label: "Logs" }],
};

export function areaForPath(pathname: string): AreaDef | undefined {
  if (pathname === "/") return AREAS[0];
  const all = [...AREAS, LOGS_AREA];
  // Longest base match first so "/imports/match-review" doesn't match a shorter unrelated base.
  return all
    .filter(a => a.base !== "/" && pathname.startsWith(a.base))
    .sort((a, b) => b.base.length - a.base.length)[0];
}

export function screenForPath(area: AreaDef | undefined, pathname: string): ScreenDef | undefined {
  if (!area) return undefined;
  return area.screens.find(s => s.path === pathname) ?? area.screens[0];
}
