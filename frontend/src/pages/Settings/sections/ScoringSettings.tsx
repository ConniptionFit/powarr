import { useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Save, AlertTriangle, Lock, Bell, Send, Bot, Wand2, Play, Clock, DatabaseBackup, Activity, RotateCcw, Plug, SlidersHorizontal, Music } from "lucide-react";
import { Skeleton } from "../../../components/Skeleton";
import { settingsApi, mediaApi, authApi, importsApi, fmtBytes, fmtDate, type ScoringWeights, type ScoringProfiles,
         type ImportMatchingSettings, type CleanupSettings, type SyncSettings, type NotificationSettings,
         type OllamaSettings, type LlmPolicies, type LlmAppOverride, type LlmLibraryOverride,
         type LlmScheduleSettings, type BackupSettings, type BackupFile, type SettingsExport } from "../../../lib/api";

export function WeightRow({ label, field, value, onChange, description }: {
  label: string;
  field: string;
  value: number;
  onChange: (field: string, val: number) => void;
  description: string;
}) {
  return (
    <div className="py-4 border-b border-purple-900/20 last:border-0">
      <div className="flex items-center justify-between mb-1">
        <div>
          <p className="text-white text-sm font-medium">{label}</p>
          <p className="text-slate-500 text-xs mt-0.5">{description}</p>
        </div>
        <div className="flex items-center gap-3 ml-6">
          <input
            type="range"
            min={0}
            max={10}
            step={0.5}
            value={value}
            onChange={e => onChange(field, Number(e.target.value))}
            className="w-32 accent-purple-500"
          />
          <input
            type="number"
            value={value}
            step={0.5}
            min={0}
            max={20}
            onChange={e => onChange(field, Number(e.target.value))}
            className="w-16 bg-surface-raised border border-purple-900/40 rounded px-2 py-1 text-sm text-white text-right"
          />
        </div>
      </div>
    </div>
  );
}

const FIELDS: { label: string; field: keyof ScoringWeights; description: string }[] = [
  { label: "Watch History Weight", field: "watch_history_weight", description: "How much watch recency and count affects the score" },
  { label: "File Size Weight", field: "file_size_weight", description: "Larger files score higher as deletion candidates" },
  { label: "File Age Weight", field: "file_age_weight", description: "Older files (by date added to Plex) score higher" },
  { label: "Release Date Weight", field: "release_date_weight", description: "Older releases score higher" },
  { label: "Never-Watched Boost", field: "never_watched_boost", description: "Multiplier applied to unwatched items' watch score" },
];

const REFS: { label: string; field: keyof ScoringWeights; description: string; unit: string }[] = [
  { label: "Max Size Reference (GB)", field: "max_size_gb_reference", description: "File size considered '100%' for scoring", unit: "GB" },
  { label: "Max Age Reference (days)", field: "max_age_days_reference", description: "Days old considered '100%' for scoring", unit: "days" },
  { label: "Max Release Age Reference (years)", field: "max_release_age_years_reference", description: "Years since release considered '100%' for scoring", unit: "years" },
  { label: "Watch Half-Life (days)", field: "watch_half_life_days", description: "Days after a watch until the watch-factor reaches ~63% of max (smooth decay; series-level watches count too)", unit: "days" },
  { label: "Min Score Threshold", field: "min_score_threshold", description: "Items below this score are hidden from Cleanup by default", unit: "" },
];

const PROFILE_OVERLAY_FIELDS: { label: string; field: keyof ScoringWeights; description: string }[] = [
  ...FIELDS,
  { label: "Watch Half-Life (days)", field: "watch_half_life_days", description: "Override decay half-life for this library" },
  { label: "Min Score Threshold", field: "min_score_threshold", description: "Override Cleanup visibility floor for this library" },
];

export function ImportMatchingSection() {
  const { data } = useQuery({ queryKey: ["import-matching"], queryFn: settingsApi.getImportMatching });
  const [cfg, setCfg] = useState<ImportMatchingSettings | null>(null);
  const [saved, setSaved] = useState(false);
  const [newExt, setNewExt] = useState("");
  const [junkRuleError, setJunkRuleError] = useState<string | null>(null);

  useEffect(() => { if (data) setCfg(data); }, [data]);

  const mut = useMutation({
    mutationFn: (c: ImportMatchingSettings) => settingsApi.updateImportMatching(c),
    onSuccess: () => { setSaved(true); setJunkRuleError(null); setTimeout(() => setSaved(false), 2000); },
    onError: (e: unknown) => setJunkRuleError(e instanceof Error ? e.message : String(e)),
  });

  if (!cfg) return null;

  const set = <K extends keyof ImportMatchingSettings>(k: K, v: ImportMatchingSettings[K]) =>
    setCfg(c => (c ? { ...c, [k]: v } : c));

  const numRow = (label: string, description: string, field: keyof ImportMatchingSettings,
                  opts: { min: number; max: number; step: number; unit?: string }) => (
    <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
      <div>
        <p className="text-white text-sm font-medium">{label}</p>
        <p className="text-slate-500 text-xs mt-0.5">{description}</p>
      </div>
      <div className="flex items-center gap-2 ml-6">
        <input
          type="number"
          min={opts.min} max={opts.max} step={opts.step}
          value={cfg[field] as number}
          onChange={e => set(field, Number(e.target.value) as never)}
          className="w-24 bg-surface border border-purple-900/40 rounded px-2 py-1 text-sm text-white text-right"
        />
        {opts.unit && <span className="text-slate-500 text-xs">{opts.unit}</span>}
      </div>
    </div>
  );

  // Slider variant for 0-1 mix weights (Approved Queue #9) — same shape as
  // numRow, but a range input with a live readout beats typing decimals for
  // "how much should the LLM count" style settings.
  const sliderRow = (label: string, description: string, field: keyof ImportMatchingSettings,
                     opts: { min: number; max: number; step: number }) => (
    <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
      <div>
        <p className="text-white text-sm font-medium">{label}</p>
        <p className="text-slate-500 text-xs mt-0.5">{description}</p>
      </div>
      <div className="flex items-center gap-3 ml-6">
        <input
          type="range"
          min={opts.min} max={opts.max} step={opts.step}
          value={cfg[field] as number}
          onChange={e => set(field, Number(e.target.value) as never)}
          className="w-40 accent-purple-500"
        />
        <span className="text-white text-sm font-mono w-10 text-right">{(cfg[field] as number).toFixed(2)}</span>
      </div>
    </div>
  );

  const toggleRow = (label: string, description: string, field: keyof ImportMatchingSettings, nested = false) => (
    <label className={`py-4 border-b border-purple-900/20 flex items-center justify-between cursor-pointer ${nested ? "pl-6" : ""}`}>
      <div>
        <p className="text-white text-sm font-medium">{label}</p>
        <p className="text-slate-500 text-xs mt-0.5">{description}</p>
      </div>
      <input
        type="checkbox"
        checked={cfg[field] as boolean}
        onChange={e => set(field, e.target.checked as never)}
        className="accent-purple-500 ml-6"
      />
    </label>
  );

  const addExtension = () => {
    const ext = newExt.trim().toLowerCase();
    if (!ext) return;
    const normalized = ext.startsWith(".") ? ext : `.${ext}`;
    if (!cfg.suspicious_extensions.includes(normalized)) {
      set("suspicious_extensions", [...cfg.suspicious_extensions, normalized]);
    }
    setNewExt("");
  };

  const removeExtension = (ext: string) =>
    set("suspicious_extensions", cfg.suspicious_extensions.filter(e => e !== ext));

  const addJunkRule = () =>
    set("junk_strip_rules", [...cfg.junk_strip_rules, { name: "", pattern: "", enabled: true }]);
  const updateJunkRule = (i: number, rule: ImportMatchingSettings["junk_strip_rules"][number]) =>
    set("junk_strip_rules", cfg.junk_strip_rules.map((r, j) => (j === i ? rule : r)));
  const removeJunkRule = (i: number) =>
    set("junk_strip_rules", cfg.junk_strip_rules.filter((_, j) => j !== i));

  return (
    <div className="bg-surface-raised rounded-xl border border-purple-900/30 px-6 mt-6">
      <div className="flex items-center justify-between pt-5 pb-3">
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">Failed Import Matching</h2>
        <button
          onClick={() => mut.mutate(cfg)}
          disabled={mut.isPending}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-brand text-white hover:bg-brand-dark text-sm transition-colors disabled:opacity-50"
        >
          <Save size={13} />
          {saved ? "Saved!" : "Save"}
        </button>
      </div>

      {toggleRow("Detection Enabled", "Poll enabled *arr queues for stuck downloads (read-only)", "enabled")}
      {numRow("Poll Interval", "Seconds between background scans (minimum 60)", "poll_interval_seconds", { min: 60, max: 86400, step: 30, unit: "sec" })}
      {numRow("Algorithm Auto-Import Threshold", "Deterministic (algorithm) confidence needed for the algorithm leg of the auto-import gate (0–1, e.g. 0.90 = 90%)", "high_confidence_threshold", { min: 0, max: 1, step: 0.01 })}
      {numRow("LLM Auto-Import Threshold", "LLM confidence needed for the LLM leg of the auto-import gate (0–1, e.g. 0.80 = 80%)", "llm_auto_threshold", { min: 0, max: 1, step: 0.01 })}

      <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
        <div>
          <p className="text-white text-sm font-medium">Auto-Import Requires</p>
          <p className="text-slate-500 text-xs mt-0.5">
            Which signal(s) must clear their threshold above before an import is pushed automatically.
            Either (default): one passing signal is enough — e.g. LLM 95% with algorithm 50% still imports.
            Both: the same example fails. Rows the LLM never scored fail the LLM leg.
          </p>
        </div>
        <select
          value={cfg.auto_import_mode}
          onChange={e => set("auto_import_mode", e.target.value as ImportMatchingSettings["auto_import_mode"])}
          className="ml-6 bg-surface border border-purple-900/40 rounded px-2 py-1 text-sm text-white"
        >
          <option value="either">Either (LLM or Algorithm)</option>
          <option value="both">Both (LLM and Algorithm)</option>
          <option value="llm">LLM only</option>
          <option value="algorithm">Algorithm only</option>
        </select>
      </div>
      {numRow("Low Confidence Floor", "Matches below this are logged only, never listed", "low_confidence_floor", { min: 0, max: 1, step: 0.01 })}
      {numRow("Grace Period", "Skip queue items younger than this — the *arr app often retries on its own", "grace_period_minutes", { min: 0, max: 1440, step: 5, unit: "min" })}
      {numRow("Verify Timeout", "Pushed imports unconfirmed in history after this are marked failed", "verify_timeout_minutes", { min: 5, max: 1440, step: 5, unit: "min" })}
      {toggleRow("Include Stalled Downloads", "Also flag downloads stalled with no connections, not just import failures", "include_stalled")}
      {toggleRow("Auto-Purge Confirmed-Missing", "Skip the orphan confirmation prompt: downloads confirmed gone from every download client (and not found on disk) are marked orphaned immediately", "orphan_auto_purge")}
      {numRow("Episode Title Weight", "Weight of episode-title similarity in the episode-level score (heaviest factor, non-overriding)", "title_weight", { min: 0, max: 1, step: 0.05 })}
      {numRow("Episode Number Weight", "Weight of season/episode (or anime absolute) number corroboration", "number_weight", { min: 0, max: 1, step: 0.05 })}
      {numRow("Title-Only Cap", "Confidence ceiling when no episode number corroborates a title match — keeps title-only matches below auto-resolve", "title_only_cap", { min: 0, max: 1, step: 0.01 })}
      {toggleRow("Anime Absolute Numbering", "For Sonarr anime series, match by absolute episode number (with season/episode fallback and stale-data guards)", "anime_absolute_numbering")}
      {toggleRow("Anime Absolute Pack Coverage", "FI-08 — for an anime batch pack with no season marker (e.g. \"001-100\"), score coverage against that absolute-episode range instead of the whole aired show, so a genuinely complete pack doesn't read stuck at a tiny fraction. Off by default: this can raise pack confidence enough to newly qualify for auto-resolve on affected releases — only turn on if you understand that impact.", "anime_absolute_pack_coverage")}
      {toggleRow("Nightly Malformed-Import Audit", "FI-10 — off by default. Re-checks Sonarr pack grabs that already left the queue for incomplete on-disk coverage (e.g. a double-segment pack that only imported half), and sends an ntfy notification when found. Never rewrites the library — flags surface in Imports → Recent Downloads for manual review/re-import.", "malformed_audit_enabled")}
      {numRow("Malformed-Audit Interval", "How often the nightly audit runs", "malformed_audit_interval_hours", { min: 1, max: 168, step: 1, unit: "hr" })}
      {numRow("Malformed-Audit Lookback", "How many days back to re-check pack grabs", "malformed_audit_lookback_days", { min: 1, max: 30, step: 1, unit: "days" })}
      {numRow("Malformed-Audit Threshold", "Coverage ratio below which a pack gets flagged", "malformed_audit_threshold", { min: 0, max: 1, step: 0.05 })}
      {sliderRow("LLM Blend Weight", "The LLM's share of the final confidence blend: final = (1−w)·deterministic + w·LLM. 0 = ignore the LLM entirely; 0.3 = long-standing default", "llm_blend_weight", { min: 0, max: 1, step: 0.05 })}
      {toggleRow("Auto-Reject Equal-or-Better Quality", "Skip triage when the *arr app already has equal or better quality for every file in the download (Sonarr/Radarr \"not an upgrade\"; Lidarr \"not an upgrade\" / \"album already imported\"). Off by default; the Downgrade badge and filter always show these regardless of this setting.", "quality_downgrade_auto_reject")}

      <div className="py-4 border-b border-purple-900/20">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-white text-sm font-medium flex items-center gap-1.5">
              Suspicious File Extensions
              <AlertTriangle size={13} className="text-red-400" />
            </p>
            <p className="text-slate-500 text-xs mt-0.5">
              Any file in a download matching one of these extensions gets the row flagged — across all *arr apps, any single match is enough.
              Archive formats (zip/rar/7z/...) are deliberately not included by default since most legitimate downloads arrive compressed.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5 mt-3">
          {cfg.suspicious_extensions.map(ext => (
            <span key={ext} className="flex items-center gap-1 px-2 py-1 rounded bg-red-900/30 border border-red-900/40 text-red-300 text-xs">
              {ext}
              <button onClick={() => removeExtension(ext)} className="text-red-400 hover:text-white ml-0.5" title={`Remove ${ext}`}>
                ×
              </button>
            </span>
          ))}
        </div>
        <div className="flex items-center gap-2 mt-3">
          <input
            type="text"
            value={newExt}
            onChange={e => setNewExt(e.target.value)}
            onKeyDown={e => e.key === "Enter" && (e.preventDefault(), addExtension())}
            placeholder=".ext"
            className="w-32 bg-surface border border-purple-900/40 rounded px-2 py-1 text-sm text-white"
          />
          <button
            onClick={addExtension}
            className="px-3 py-1 rounded bg-surface-overlay hover:bg-white/10 text-slate-300 text-xs transition-colors"
          >
            Add
          </button>
        </div>
      </div>

      {toggleRow("Auto-Reject Suspicious File Types", "Skip triage entirely for downloads containing a file matching the list above. Off by default; the Suspicious badge and filter always show these regardless of this setting.", "suspicious_extension_auto_reject")}
      {toggleRow("Also Delete From Disk", "When auto-rejecting a suspicious download, also delete it via the download client (deletes every file in the download, not just the flagged one — no per-file delete is available). Only takes effect when Auto-Reject Suspicious File Types is also on.", "suspicious_extension_delete_from_disk", true)}

      <div className="py-4 border-b border-purple-900/20">
        <p className="text-white text-sm font-medium">Junk Strip Rules</p>
        <p className="text-slate-500 text-xs mt-0.5">
          LLM-09 — user-authored regex rules applied to the raw release title before the built-in cleanup and
          any heuristic/LLM matching sees it. Applied in order, top to bottom. Empty by default. Each rule's
          matches are replaced with a space; validated (must compile) on save.
        </p>
        {junkRuleError && <p className="text-xs text-red-400 mt-2">{junkRuleError}</p>}
        <div className="mt-3 space-y-2">
          {cfg.junk_strip_rules.map((rule, i) => (
            <div key={i} className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={rule.enabled !== false}
                onChange={e => updateJunkRule(i, { ...rule, enabled: e.target.checked })}
                className="accent-purple-500 flex-shrink-0"
                title="Enabled"
              />
              <input
                type="text"
                value={rule.name || ""}
                onChange={e => updateJunkRule(i, { ...rule, name: e.target.value })}
                placeholder="name"
                className="w-32 bg-surface border border-purple-900/40 rounded px-2 py-1 text-xs text-white placeholder:text-slate-600"
              />
              <input
                type="text"
                value={rule.pattern || ""}
                onChange={e => updateJunkRule(i, { ...rule, pattern: e.target.value })}
                placeholder="regex pattern"
                className="flex-1 bg-surface border border-purple-900/40 rounded px-2 py-1 text-xs text-white font-mono placeholder:text-slate-600"
              />
              <button
                onClick={() => removeJunkRule(i)}
                className="text-red-400 hover:text-white px-1.5 flex-shrink-0"
                title="Remove rule"
              >
                ×
              </button>
            </div>
          ))}
        </div>
        <button
          onClick={addJunkRule}
          className="mt-3 px-3 py-1 rounded bg-surface-overlay hover:bg-white/10 text-slate-300 text-xs transition-colors"
        >
          Add Rule
        </button>
      </div>

      <label className="py-4 border-b border-purple-900/20 flex items-center justify-between cursor-pointer">
        <div>
          <p className="text-white text-sm font-medium flex items-center gap-1.5">
            Auto-Resolve
            <AlertTriangle size={13} className="text-yellow-400" />
          </p>
          <p className="text-slate-500 text-xs mt-0.5">
            Automatically push imports back to Sonarr/Radarr/Lidarr for high-confidence matches — writes to your live *arr apps
          </p>
        </div>
        <input
          type="checkbox"
          checked={cfg.auto_resolve_enabled}
          onChange={e => set("auto_resolve_enabled", e.target.checked)}
          className="accent-purple-500 ml-6"
        />
      </label>

      <div className="py-4 flex items-center gap-6">
        <p className="text-white text-sm font-medium">Services</p>
        {(["sonarr", "radarr", "lidarr", "readarr"] as const).map(s => (
          <label key={s} className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer capitalize">
            <input
              type="checkbox"
              checked={cfg[`${s}_enabled`]}
              onChange={e => set(`${s}_enabled`, e.target.checked)}
              className="accent-purple-500"
            />
            {s}
          </label>
        ))}
      </div>
    </div>
  );
}

export function ScoringProfilesSection() {
  const qc = useQueryClient();
  const { data: profiles } = useQuery({ queryKey: ["scoring-profiles"], queryFn: settingsApi.getScoringProfiles });
  const { data: libraries = [] } = useQuery({ queryKey: ["libraries"], queryFn: mediaApi.libraries });
  const [cfg, setCfg] = useState<ScoringProfiles | null>(null);
  const [lib, setLib] = useState("");
  const [saved, setSaved] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    if (profiles) setCfg(profiles);
  }, [profiles]);

  useEffect(() => {
    if (!lib && libraries.length > 0) setLib(libraries[0]);
  }, [libraries, lib]);

  if (!cfg) return null;

  const overlay = (lib && cfg.by_library[lib]) || {};

  const setOverlayField = (field: keyof ScoringWeights, raw: string) => {
    if (!lib) return;
    setCfg(c => {
      if (!c) return c;
      const next = { ...c.by_library };
      const cur = { ...(next[lib] || {}) };
      if (raw.trim() === "") {
        delete cur[field];
      } else {
        cur[field] = Number(raw);
      }
      if (Object.keys(cur).length === 0) delete next[lib];
      else next[lib] = cur;
      return { by_library: next };
    });
  };

  const clearLibrary = () => {
    if (!lib) return;
    setCfg(c => {
      if (!c) return c;
      const next = { ...c.by_library };
      delete next[lib];
      return { by_library: next };
    });
  };

  const save = async () => {
    setMsg(null);
    try {
      await settingsApi.updateScoringProfiles(cfg);
      qc.invalidateQueries({ queryKey: ["scoring-profiles"] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="bg-surface-raised rounded-xl border border-purple-900/30 px-6 mt-6">
      <div className="flex items-center justify-between pt-5 pb-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">Per-Library Scoring Profiles</h2>
          <p className="text-slate-500 text-xs mt-1">
            Cleanup only — partial overlays on the global weights above. Empty fields inherit the default. Saving rescores the library.
          </p>
        </div>
        <button
          onClick={save}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-brand text-white hover:bg-brand-dark text-sm transition-colors"
        >
          <Save size={13} />
          {saved ? "Saved!" : "Save & Rescore"}
        </button>
      </div>
      {msg && <p className="text-xs text-red-400 pb-2">{msg}</p>}

      {libraries.length === 0 ? (
        <p className="text-slate-500 text-xs pb-5">No libraries found — run a Plex sync first.</p>
      ) : (
        <>
          <div className="py-3 border-b border-purple-900/20 flex items-center justify-between gap-3">
            <div>
              <p className="text-white text-sm font-medium">Library</p>
              <p className="text-slate-500 text-xs mt-0.5">
                {Object.keys(cfg.by_library).length
                  ? `${Object.keys(cfg.by_library).length} library overlay(s) configured`
                  : "No overlays yet — all libraries use global weights"}
              </p>
            </div>
            <div className="flex items-center gap-2 ml-6">
              <select
                value={lib}
                onChange={e => setLib(e.target.value)}
                className="bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white"
              >
                {libraries.map(l => (
                  <option key={l} value={l}>{l}{cfg.by_library[l] ? " *" : ""}</option>
                ))}
              </select>
              <button
                onClick={clearLibrary}
                disabled={!lib || !cfg.by_library[lib]}
                className="px-3 py-1.5 rounded bg-surface-overlay hover:bg-white/10 text-slate-300 text-sm transition-colors disabled:opacity-40"
              >
                Clear
              </button>
            </div>
          </div>
          {PROFILE_OVERLAY_FIELDS.map(f => (
            <div key={f.field} className="py-3 border-b border-purple-900/20 last:border-0 flex items-center justify-between">
              <div>
                <p className="text-white text-sm font-medium">{f.label}</p>
                <p className="text-slate-500 text-xs mt-0.5">{f.description}</p>
              </div>
              <input
                type="number"
                step={0.5}
                placeholder="inherit"
                value={overlay[f.field] ?? ""}
                onChange={e => setOverlayField(f.field, e.target.value)}
                className="w-24 bg-surface border border-purple-900/40 rounded px-2 py-1 text-sm text-white text-right ml-6 placeholder:text-slate-600"
              />
            </div>
          ))}
        </>
      )}
    </div>
  );
}

export function ScoringWeightsSection() {
  const { data, isLoading } = useQuery({ queryKey: ["scoring"], queryFn: settingsApi.getScoring });
  const [weights, setWeights] = useState<ScoringWeights | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => { if (data) setWeights(data); }, [data]);

  const mut = useMutation({
    mutationFn: settingsApi.updateScoring,
    onSuccess: () => { setSaved(true); setTimeout(() => setSaved(false), 2000); },
  });

  const handleChange = (field: string, val: number) => {
    setWeights(w => w ? { ...w, [field]: val } : w);
  };

  if (isLoading || !weights) return <div className="pt-4"><Skeleton className="h-10 w-full" count={5} /></div>;

  return (
    <>
      <div className="flex items-center justify-end mb-2">
        <button
          onClick={() => mut.mutate(weights)}
          disabled={mut.isPending}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-brand text-white hover:bg-brand-dark text-sm transition-colors disabled:opacity-50"
        >
          <Save size={15} />
          {saved ? "Saved!" : "Save & Rescore"}
        </button>
      </div>

      <div className="bg-surface-raised rounded-xl border border-purple-900/30 px-6 mb-6">
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider pt-5 pb-3">Scoring Weights</h2>
        {FIELDS.map(f => (
          <WeightRow key={f.field} {...f} value={weights[f.field] as number} onChange={handleChange} />
        ))}
      </div>

      <div className="bg-surface-raised rounded-xl border border-purple-900/30 px-6">
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider pt-5 pb-3">Reference Values</h2>
        {REFS.map(f => (
          <div key={f.field} className="py-4 border-b border-purple-900/20 last:border-0 flex items-center justify-between">
            <div>
              <p className="text-white text-sm font-medium">{f.label}</p>
              <p className="text-slate-500 text-xs mt-0.5">{f.description}</p>
            </div>
            <div className="flex items-center gap-2 ml-6">
              <input
                type="number"
                value={(weights[f.field] as number) ?? ""}
                onChange={e => handleChange(f.field, Number(e.target.value))}
                className="w-24 bg-surface border border-purple-900/40 rounded px-2 py-1 text-sm text-white text-right"
              />
              {f.unit && <span className="text-slate-500 text-xs">{f.unit}</span>}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

