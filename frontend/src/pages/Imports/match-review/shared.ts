import { Download } from "lucide-react";
import { type FailedImport } from "../../../lib/api";


/** True when the click landed on (or inside) an interactive control — row-click
 *  select must ignore these so buttons/checkboxes/links keep their own behavior. */
export function isInteractiveTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  return !!target.closest("button, a, input, select, textarea, label, [role='button']");
}

/** Flatten Markdown bullets/bold into plain prose for title tooltips. */
export const stripMd = (text?: string | null) =>
  (text ?? "").replace(/\n- /g, "; ").replace(/^- /, "").replace(/\*\*/g, "");

export const STATUS_META: Record<string, { label: string; cls: string }> = {
  suggested: { label: "Suggested", cls: "bg-yellow-900/60 text-yellow-300" },
  auto_resolved: { label: "Auto-resolved", cls: "bg-green-900/60 text-green-300" },
  accepted: { label: "Accepted", cls: "bg-green-900/60 text-green-300" },
  rejected: { label: "Rejected", cls: "bg-red-900/60 text-red-300" },
  closed_external: { label: "Self-resolved", cls: "bg-surface-overlay text-slate-300" },
  resolve_failed: { label: "Push failed", cls: "bg-red-900/60 text-red-300" },
  orphan_pending: { label: "Confirm orphan", cls: "bg-orange-900/60 text-orange-300" },
  orphaned: { label: "Orphaned", cls: "bg-surface-overlay text-slate-300" },
};

// --- Column system: visibility + widths persisted per-browser (localStorage) ---

export type ColKey = "source" | "release" | "matched" | "match_pct" | "match_notes" | "llm_pct" | "llm_notes" | "status" | "detected";

export interface ColDef {
  key: ColKey;
  label: string;
  width: number; // default px
  sortField?: keyof FailedImport;
  tooltip?: string;
}

export const COLUMNS: ColDef[] = [
  { key: "source", label: "Source", width: 90, sortField: "source_app", tooltip: "Download source (Radarr, Sonarr, Lidarr, Readarr)" },
  { key: "release", label: "Downloaded Release", width: 340, sortField: "raw_title", tooltip: "The item that was downloaded and needs to be imported" },
  { key: "matched", label: "Library Match", width: 240, sortField: "matched_title", tooltip: "Powarr's suggested match in your library" },
  { key: "match_pct", label: "Match", width: 90, sortField: "heuristic_confidence", tooltip: "Confidence of the suggested library match" },
  { key: "match_notes", label: "Match Notes", width: 260, sortField: "match_rationale", tooltip: "Reasoning behind the suggested match" },
  { key: "llm_pct", label: "LLM", width: 90, sortField: "llm_confidence", tooltip: "Local LLM analysis of the match" },
  { key: "llm_notes", label: "LLM Notes", width: 260, sortField: "llm_rationale", tooltip: "LLM reasoning for its confidence level" },
  { key: "status", label: "Status", width: 120, sortField: "status", tooltip: "Current status of this import" },
  { key: "detected", label: "Detected", width: 110, sortField: "created_at", tooltip: "When this import was first detected" },
];

export const LS_VISIBLE = "powarr.failedImports.visibleCols";
export const LS_WIDTHS = "powarr.failedImports.colWidths";
export const LS_MATCH_NOTES_SEEN = "powarr.failedImports.matchNotesIntroduced"; // v0.5.0 one-time column surfacing

export function loadVisible(): Set<ColKey> {
  try {
    const raw = localStorage.getItem(LS_VISIBLE);
    if (raw) {
      const set = new Set(JSON.parse(raw) as ColKey[]);
      // Layouts saved before v0.5.0 predate the Match Notes column — show it once;
      // hiding it again afterwards sticks.
      if (!localStorage.getItem(LS_MATCH_NOTES_SEEN)) {
        set.add("match_notes");
        localStorage.setItem(LS_MATCH_NOTES_SEEN, "1");
      }
      return set;
    }
  } catch { /* fall through */ }
  return new Set(COLUMNS.map(c => c.key));
}

export function loadWidths(): Record<string, number> {
  try {
    const raw = localStorage.getItem(LS_WIDTHS);
    if (raw) return JSON.parse(raw);
  } catch { /* fall through */ }
  return {};
}
