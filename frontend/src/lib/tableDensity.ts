/** Shared Cleanup table density (v0.29.0, Approved Queue #17). */
export type TableDensity = "comfortable" | "compact";

export const DENSITY_CLASSES: Record<TableDensity, { cell: string; head: string }> = {
  comfortable: { cell: "px-4 py-3", head: "px-4 py-3" },
  compact: { cell: "px-3 py-1.5 text-xs", head: "px-3 py-1.5 text-xs" },
};

export const DENSITY_STORAGE_KEY = "powarr.tableDensity";
