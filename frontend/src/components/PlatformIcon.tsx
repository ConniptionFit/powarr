import { Clapperboard, Tv, Music, Book, type LucideIcon } from "lucide-react";

/** *arr platform → Lucide icon + accent classes (v0.28.0). `chip` is text-color
    only — every call site already supplies its own inactive border
    (border-purple-900/40) alongside it; a chip-owned border class used to
    fight that one for specificity, and since Tailwind's generated stylesheet
    order (not class-string order) decides the winner, it happened to make
    Sonarr's teal border win everywhere while the other three stayed subtle. */
export const PLATFORM_META: Record<string, { label: string; Icon: LucideIcon; badge: string; chip: string }> = {
  radarr: { label: "Radarr", Icon: Clapperboard, badge: "bg-amber-600", chip: "text-amber-300" },
  sonarr: { label: "Sonarr", Icon: Tv, badge: "bg-teal-600", chip: "text-teal-300" },
  lidarr: { label: "Lidarr", Icon: Music, badge: "bg-pink-600", chip: "text-pink-300" },
  readarr: { label: "Readarr", Icon: Book, badge: "bg-orange-700", chip: "text-orange-300" },
};

export const PLATFORM_ORDER = ["radarr", "sonarr", "lidarr", "readarr"] as const;
export type PlatformName = (typeof PLATFORM_ORDER)[number];

export function PlatformIcon({
  app,
  size = 13,
  className = "",
  showLabel = false,
}: {
  app: string;
  size?: number;
  className?: string;
  showLabel?: boolean;
}) {
  const meta = PLATFORM_META[app];
  if (!meta) {
    return showLabel ? <span className={className}>{app}</span> : null;
  }
  const { Icon, label } = meta;
  if (showLabel) {
    return (
      <span className={`inline-flex items-center gap-1 ${className}`}>
        <Icon size={size} />
        {label}
      </span>
    );
  }
  return <Icon size={size} className={className} aria-label={label} />;
}

/** Colored source badge used in Failed Imports table cells. */
export function PlatformBadge({ app }: { app: string }) {
  const meta = PLATFORM_META[app];
  const cls = meta?.badge ?? "bg-slate-600";
  const label = meta?.label ?? app;
  const Icon = meta?.Icon;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-bold text-white ${cls}`} title={label}>
      {Icon && <Icon size={12} />}
      {label}
    </span>
  );
}
