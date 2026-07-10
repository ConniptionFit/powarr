import { useEffect, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Save, AlertTriangle, Lock, Bell, Send, Bot, Wand2, Play, Clock, DatabaseBackup, Activity, RotateCcw, Plug, SlidersHorizontal, Music } from "lucide-react";
import { settingsApi, mediaApi, authApi, importsApi, fmtBytes, fmtDate, type ScoringWeights, type ScoringProfiles,
         type ImportMatchingSettings, type CleanupSettings, type SyncSettings, type NotificationSettings,
         type OllamaSettings, type LlmScheduleSettings, type BackupSettings, type BackupFile } from "../../lib/api";
import IntegrationsPage from "../Integrations";
import MusicSettings from "./MusicSettings";

function WeightRow({ label, field, value, onChange, description }: {
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

function ImportMatchingSection() {
  const { data } = useQuery({ queryKey: ["import-matching"], queryFn: settingsApi.getImportMatching });
  const [cfg, setCfg] = useState<ImportMatchingSettings | null>(null);
  const [saved, setSaved] = useState(false);
  const [newExt, setNewExt] = useState("");

  useEffect(() => { if (data) setCfg(data); }, [data]);

  const mut = useMutation({
    mutationFn: (c: ImportMatchingSettings) => settingsApi.updateImportMatching(c),
    onSuccess: () => { setSaved(true); setTimeout(() => setSaved(false), 2000); },
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
      {numRow("High Confidence Threshold", "Matches at or above this are eligible for auto-resolve", "high_confidence_threshold", { min: 0, max: 1, step: 0.01 })}
      {numRow("Low Confidence Floor", "Matches below this are logged only, never listed", "low_confidence_floor", { min: 0, max: 1, step: 0.01 })}
      {numRow("Grace Period", "Skip queue items younger than this — the *arr app often retries on its own", "grace_period_minutes", { min: 0, max: 1440, step: 5, unit: "min" })}
      {numRow("Verify Timeout", "Pushed imports unconfirmed in history after this are marked failed", "verify_timeout_minutes", { min: 5, max: 1440, step: 5, unit: "min" })}
      {toggleRow("Include Stalled Downloads", "Also flag downloads stalled with no connections, not just import failures", "include_stalled")}
      {toggleRow("Auto-Purge Confirmed-Missing", "Skip the orphan confirmation prompt: downloads confirmed gone from every download client (and not found on disk) are marked orphaned immediately", "orphan_auto_purge")}
      {numRow("Episode Title Weight", "Weight of episode-title similarity in the episode-level score (heaviest factor, non-overriding)", "title_weight", { min: 0, max: 1, step: 0.05 })}
      {numRow("Episode Number Weight", "Weight of season/episode (or anime absolute) number corroboration", "number_weight", { min: 0, max: 1, step: 0.05 })}
      {numRow("Title-Only Cap", "Confidence ceiling when no episode number corroborates a title match — keeps title-only matches below auto-resolve", "title_only_cap", { min: 0, max: 1, step: 0.01 })}
      {toggleRow("Anime Absolute Numbering", "For Sonarr anime series, match by absolute episode number (with season/episode fallback and stale-data guards)", "anime_absolute_numbering")}
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

function CleanupSection() {
  const { data } = useQuery({ queryKey: ["cleanup-settings"], queryFn: settingsApi.getCleanup });
  const { data: libraries = [] } = useQuery({ queryKey: ["libraries"], queryFn: mediaApi.libraries });
  const [cfg, setCfg] = useState<CleanupSettings | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => { if (data) setCfg(data); }, [data]);

  const mut = useMutation({
    mutationFn: (c: CleanupSettings) => settingsApi.updateCleanup(c),
    onSuccess: () => { setSaved(true); setTimeout(() => setSaved(false), 2000); },
  });

  if (!cfg) return null;

  const toggleLibrary = (lib: string) =>
    setCfg(c => c ? {
      ...c,
      excluded_libraries: c.excluded_libraries.includes(lib)
        ? c.excluded_libraries.filter(l => l !== lib)
        : [...c.excluded_libraries, lib],
    } : c);

  return (
    <div className="bg-surface-raised rounded-xl border border-purple-900/30 px-6 mt-6">
      <div className="flex items-center justify-between pt-5 pb-3">
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">Cleanup Behavior</h2>
        <button
          onClick={() => mut.mutate(cfg)}
          disabled={mut.isPending}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-brand text-white hover:bg-brand-dark text-sm transition-colors disabled:opacity-50"
        >
          <Save size={13} />
          {saved ? "Saved!" : "Save"}
        </button>
      </div>

      <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
        <div>
          <p className="text-white text-sm font-medium">Soft-Delete Window</p>
          <p className="text-slate-500 text-xs mt-0.5">
            Days deletions stay restorable before purging (0 = delete immediately). Pending items appear in Cleanup → Deletion History
          </p>
        </div>
        <div className="flex items-center gap-2 ml-6">
          <input
            type="number" min={0} max={90} step={1}
            value={cfg.soft_delete_days}
            onChange={e => setCfg(c => c ? { ...c, soft_delete_days: Number(e.target.value) } : c)}
            className="w-24 bg-surface border border-purple-900/40 rounded px-2 py-1 text-sm text-white text-right"
          />
          <span className="text-slate-500 text-xs">days</span>
        </div>
      </div>

      <label className="py-4 border-b border-purple-900/20 flex items-center justify-between cursor-pointer">
        <div>
          <p className="text-white text-sm font-medium">Protect Requested Media</p>
          <p className="text-slate-500 text-xs mt-0.5">Hide items matching recent Seerr requests from deletion suggestions</p>
        </div>
        <input
          type="checkbox"
          checked={cfg.protect_requested}
          onChange={e => setCfg(c => c ? { ...c, protect_requested: e.target.checked } : c)}
          className="accent-purple-500 ml-6"
        />
      </label>

      <label className="py-4 border-b border-purple-900/20 flex items-center justify-between cursor-pointer">
        <div>
          <p className="text-white text-sm font-medium">Protect Other Users' Watches</p>
          <p className="text-slate-500 text-xs mt-0.5">
            Hide items another Tautulli user watched within N days (refreshed on each Plex sync). Requires Tautulli enabled.
          </p>
        </div>
        <input
          type="checkbox"
          checked={cfg.protect_other_users}
          onChange={e => setCfg(c => c ? { ...c, protect_other_users: e.target.checked } : c)}
          className="accent-purple-500 ml-6"
        />
      </label>
      {cfg.protect_other_users && (
        <>
          <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
            <div>
              <p className="text-white text-sm font-medium">Other-User Watch Window</p>
              <p className="text-slate-500 text-xs mt-0.5">Days of Tautulli history to protect</p>
            </div>
            <div className="flex items-center gap-2 ml-6">
              <input
                type="number" min={1} max={365} step={1}
                value={cfg.other_user_watch_days}
                onChange={e => setCfg(c => c ? { ...c, other_user_watch_days: Number(e.target.value) } : c)}
                className="w-24 bg-surface border border-purple-900/40 rounded px-2 py-1 text-sm text-white text-right"
              />
              <span className="text-slate-500 text-xs">days</span>
            </div>
          </div>
          <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
            <div>
              <p className="text-white text-sm font-medium">Primary Tautulli User</p>
              <p className="text-slate-500 text-xs mt-0.5">
                Friendly name whose watches do <em>not</em> protect (your own). Leave blank to protect every user's watches.
              </p>
            </div>
            <input
              type="text"
              placeholder="e.g. Powers"
              value={cfg.primary_tautulli_user}
              onChange={e => setCfg(c => c ? { ...c, primary_tautulli_user: e.target.value } : c)}
              className="w-40 bg-surface border border-purple-900/40 rounded px-2 py-1 text-sm text-white ml-6"
            />
          </div>
        </>
      )}

      <div className="py-4">
        <p className="text-white text-sm font-medium mb-1">Excluded Libraries</p>
        <p className="text-slate-500 text-xs mb-3">Items in these Plex libraries are never suggested for deletion</p>
        {libraries.length === 0 ? (
          <p className="text-slate-500 text-xs">No libraries found — run a Plex sync first.</p>
        ) : (
          <div className="flex flex-wrap gap-3">
            {libraries.map(lib => (
              <label key={lib} className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
                <input
                  type="checkbox"
                  checked={cfg.excluded_libraries.includes(lib)}
                  onChange={() => toggleLibrary(lib)}
                  className="accent-purple-500"
                />
                {lib}
              </label>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function SyncSection() {
  const { data } = useQuery({ queryKey: ["sync-settings"], queryFn: settingsApi.getSync });
  const [cfg, setCfg] = useState<SyncSettings | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => { if (data) setCfg(data); }, [data]);

  const mut = useMutation({
    mutationFn: (c: SyncSettings) => settingsApi.updateSync(c),
    onSuccess: () => { setSaved(true); setTimeout(() => setSaved(false), 2000); },
  });

  if (!cfg) return null;

  return (
    <div className="bg-surface-raised rounded-xl border border-purple-900/30 px-6 mt-6">
      <div className="flex items-center justify-between pt-5 pb-3">
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">Scheduled Sync</h2>
        <button
          onClick={() => mut.mutate(cfg)}
          disabled={mut.isPending}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-brand text-white hover:bg-brand-dark text-sm transition-colors disabled:opacity-50"
        >
          <Save size={13} />
          {saved ? "Saved!" : "Save"}
        </button>
      </div>

      <div className="py-4 flex items-center justify-between">
        <div>
          <p className="text-white text-sm font-medium">Plex Sync Interval</p>
          <p className="text-slate-500 text-xs mt-0.5">Automatically re-sync the library every N hours (0 = manual sync only)</p>
        </div>
        <div className="flex items-center gap-2 ml-6">
          <input
            type="number" min={0} max={168} step={1}
            value={cfg.plex_sync_interval_hours}
            onChange={e => setCfg(c => c ? { ...c, plex_sync_interval_hours: Number(e.target.value) } : c)}
            className="w-24 bg-surface border border-purple-900/40 rounded px-2 py-1 text-sm text-white text-right"
          />
          <span className="text-slate-500 text-xs">hours</span>
        </div>
      </div>
    </div>
  );
}

function BackupSection() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["backup-settings"], queryFn: settingsApi.getBackup });
  const { data: files } = useQuery({ queryKey: ["backup-files"], queryFn: settingsApi.listBackups });
  const [cfg, setCfg] = useState<BackupSettings | null>(null);
  const [saved, setSaved] = useState(false);
  const [running, setRunning] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => { if (data) setCfg(data); }, [data]);

  const save = async () => {
    if (!cfg) return;
    setMsg(null);
    try {
      await settingsApi.updateBackup(cfg);
      qc.invalidateQueries({ queryKey: ["backup-settings"] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: unknown) { setMsg(e instanceof Error ? e.message : String(e)); }
  };

  const runNow = async () => {
    setRunning(true);
    setMsg(null);
    try {
      const r = await settingsApi.runBackupNow();
      setMsg(r.message);
      qc.invalidateQueries({ queryKey: ["backup-files"] });
    } catch (e: unknown) { setMsg(e instanceof Error ? e.message : String(e)); }
    finally { setRunning(false); }
  };

  if (!cfg) return null;

  return (
    <div className="bg-surface-raised rounded-xl border border-purple-900/30 px-6 mt-6">
      <div className="flex items-center gap-2 pt-5 pb-3">
        <DatabaseBackup size={14} className="text-brand-light" />
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">Automated Backups</h2>
        <button
          onClick={save}
          className="ml-auto flex items-center gap-2 px-3 py-1.5 rounded-lg bg-brand text-white hover:bg-brand-dark text-sm transition-colors"
        >
          <Save size={13} />
          {saved ? "Saved!" : "Save"}
        </button>
      </div>
      {msg && <p className="text-xs text-slate-300 pb-2">{msg}</p>}
      <p className="text-slate-500 text-xs pb-3">
        Scheduled `pg_dump` (or a plain file copy on the SQLite fallback) to <code>/config/backups</code>, on top of the manual flow in Docker &amp; Deployment.
      </p>

      <label className="py-4 border-b border-purple-900/20 flex items-center justify-between cursor-pointer">
        <div>
          <p className="text-white text-sm font-medium">Enable scheduled backups</p>
        </div>
        <input type="checkbox" checked={cfg.enabled} className="accent-purple-500 ml-6"
               onChange={e => setCfg(c => c ? { ...c, enabled: e.target.checked } : c)} />
      </label>

      <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
        <div>
          <p className="text-white text-sm font-medium">Interval</p>
        </div>
        <div className="flex items-center gap-2 ml-6">
          <input type="number" min={1} value={cfg.interval_hours}
                 onChange={e => setCfg(c => c ? { ...c, interval_hours: Number(e.target.value) } : c)}
                 className="w-20 bg-surface border border-purple-900/40 rounded px-2 py-1.5 text-sm text-white" />
          <span className="text-slate-500 text-xs">hours</span>
        </div>
      </div>

      <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
        <div>
          <p className="text-white text-sm font-medium">Retention</p>
          <p className="text-slate-500 text-xs mt-0.5">Keep the most recent N backup files (0 = unlimited)</p>
        </div>
        <input type="number" min={0} value={cfg.retention_count}
               onChange={e => setCfg(c => c ? { ...c, retention_count: Number(e.target.value) } : c)}
               className="w-20 bg-surface border border-purple-900/40 rounded px-2 py-1.5 text-sm text-white ml-6" />
      </div>

      <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
        <div>
          <p className="text-white text-sm font-medium">Run Backup Now</p>
        </div>
        <button
          onClick={runNow}
          disabled={running}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-700 hover:bg-indigo-600 text-white text-sm transition-colors ml-6 disabled:opacity-50"
        >
          <Play size={14} />
          {running ? "Running…" : "Run Now"}
        </button>
      </div>

      <div className="py-4">
        <p className="text-white text-sm font-medium mb-2">Recent Backups</p>
        {!files || files.length === 0 ? (
          <p className="text-slate-500 text-xs">No backups yet.</p>
        ) : (
          <div className="space-y-1">
            {files.slice(0, 10).map(f => (
              <div key={f.name} className="flex items-center justify-between text-xs text-slate-400 py-1 border-b border-purple-900/10 last:border-0">
                <span className="font-mono truncate">{f.name}</span>
                <span className="flex-shrink-0 ml-4">{fmtBytes(f.size)} — {fmtDate(f.modified)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

type PromptTask = "match" | "explain" | "pack";

// Preset prompt suggestions per task. Placeholders are substituted by the backend;
// the JSON reply-format instruction is appended automatically, so templates stay clean.
const PROMPT_PRESETS: Record<PromptTask, { label: string; text: string }[]> = {
  match: [
    {
      label: "Strict matcher (default style)",
      text: "You match download release names to media library entries.\nRelease name: {release}\nCandidate library entry: {candidate}\nContext: {context}",
    },
    {
      label: "Lenient — alternate titles & translations",
      text: "You match download release names to media library entries. Consider alternate titles, translations, romanizations, and common abbreviations as valid matches. Ignore quality/uploader tags.\nRelease name: {release}\nCandidate library entry: {candidate}\nContext: {context}",
    },
    {
      label: "Anime / absolute numbering aware",
      text: "You match download release names to media library entries. For anime, absolute episode numbers and romaji/native/English titles of the same work count as matches. Strip quality and release-group tags before judging.\nRelease name: {release}\nCandidate library entry: {candidate}\nContext: {context}",
    },
    {
      label: "Conservative — punish year/edition mismatch",
      text: "You match download release names to media library entries. Be conservative: lower your confidence sharply when the year, edition, or cut (e.g. Director's Cut, remaster) differs between release and candidate.\nRelease name: {release}\nCandidate library entry: {candidate}\nContext: {context}",
    },
  ],
  pack: [
    {
      label: "Pack file mapper (default style)",
      text: "You map each file in a season/series pack to its episode.\nPack release name: {release}\nSeries: {candidate}\nFiles: {files}\nContext: {context}",
    },
    {
      label: "Anime pack — absolute + translations",
      text: "You map each file in an anime pack to its episode. Prefer absolute numbers when present; accept romaji/native/English titles. Use pack name, folder name, and each filename together. Strip quality/uploader tags.\nPack release name: {release}\nSeries: {candidate}\nFiles: {files}\nContext: {context}",
    },
  ],
  explain: [
    {
      label: "Balanced reviewer (default style)",
      text: "You review media-library deletion candidates. Assess whether this item looks like a good deletion candidate and why.\nItem: {item}",
    },
    {
      label: "Storage-focused",
      text: "You review media-library deletion candidates with a focus on reclaiming disk space. Weigh file size heavily against watch history.\nItem: {item}",
    },
    {
      label: "Sentimental curator",
      text: "You review media-library deletion candidates but favor keeping classics, critically acclaimed titles, and franchise entries even when unwatched.\nItem: {item}",
    },
  ],
};

// Worst-case injected-data token overhead per task, from the CAP_* truncation
// limits in llm_assist.py (chars/4) plus the fixed reply-instruction scaffold.
const INJECTED_TOKENS: Record<PromptTask, number> = {
  match: Math.ceil((300 + 300 + 400 + 600) / 4) + 80,
  pack: Math.ceil((300 + 300 + 800 + 400) / 4) + 100,
  explain: Math.ceil(500 / 4) + 40,
};
const DEFAULT_TEMPLATE_TOKENS: Record<PromptTask, number> = { match: 40, pack: 45, explain: 35 };

// Placeholders each prompt task's scaffold substitutes — kept here so the
// clickable insert buttons and the static help text can't drift apart.
const PROMPT_PLACEHOLDERS: Record<PromptTask, string[]> = {
  match: ["{release}", "{candidate}", "{context}"],
  pack: ["{release}", "{candidate}", "{files}", "{context}"],
  explain: ["{item}"],
};

const PROMPT_TASK_LABEL: Record<PromptTask, string> = {
  match: "Import Matching",
  pack: "Pack Files",
  explain: "Deletion Rationale",
};

function LLMAssistSection() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["ollama-settings"], queryFn: settingsApi.getOllama });
  const { data: ctx } = useQuery({ queryKey: ["ollama-ctx"], queryFn: settingsApi.ollamaContextLength });
  // In-memory call stats + breaker state — cheap endpoint, poll while the page is open.
  const { data: stats } = useQuery({ queryKey: ["llm-stats"], queryFn: settingsApi.llmStats, refetchInterval: 15000 });
  const [cfg, setCfg] = useState<OllamaSettings | null>(null);
  const [task, setTask] = useState<PromptTask>("match");
  const [msg, setMsg] = useState<string | null>(null);
  const [refining, setRefining] = useState(false);
  const [saved, setSaved] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ output: string | null; latency_ms: number; json_valid: boolean | null; message: string } | null>(null);
  const promptRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { if (data) setCfg(data); }, [data]);

  if (!cfg) return null;

  const promptField = (task === "match" ? "match_prompt" : task === "pack" ? "pack_prompt" : "explain_prompt") as
    "match_prompt" | "pack_prompt" | "explain_prompt";
  const promptValue = (cfg[promptField] as string) || "";
  const setPrompt = (text: string) => setCfg(c => (c ? { ...c, [promptField]: text } : c));

  // Inserts at the current cursor position (or the end, if the textarea never
  // had focus) and restores focus + cursor position afterward so repeated
  // clicks compose naturally instead of always appending to the end.
  const insertPlaceholder = (token: string) => {
    const el = promptRef.current;
    if (!el) { setPrompt(promptValue + token); return; }
    const start = el.selectionStart ?? promptValue.length;
    const end = el.selectionEnd ?? promptValue.length;
    setPrompt(promptValue.slice(0, start) + token + promptValue.slice(end));
    requestAnimationFrame(() => {
      el.focus();
      const pos = start + token.length;
      el.setSelectionRange(pos, pos);
    });
  };

  const save = async () => {
    setMsg(null);
    try {
      await settingsApi.updateOllama(cfg);
      qc.invalidateQueries({ queryKey: ["ollama-settings"] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: unknown) { setMsg(e instanceof Error ? e.message : String(e)); }
  };

  const refine = async () => {
    if (!promptValue.trim()) { setMsg("Write a rough draft in the box first, then I'll clean it up."); return; }
    setRefining(true);
    setMsg(null);
    try {
      await settingsApi.updateOllama(cfg); // refine runs against saved connection config
      const r = await settingsApi.refinePrompt(promptValue, task);
      setPrompt(r.refined);
      setMsg("Draft cleaned up — review and Save to apply.");
    } catch (e: unknown) { setMsg(e instanceof Error ? e.message : String(e)); }
    finally { setRefining(false); }
  };

  const runNow = async () => {
    setMsg(null);
    try {
      const r = await importsApi.llmRun();
      setMsg(r.message);
    } catch (e: unknown) { setMsg(e instanceof Error ? e.message : String(e)); }
  };

  const runCandidates = async () => {
    setMsg(null);
    try {
      const r = await mediaApi.llmRun();
      setMsg(r.message);
    } catch (e: unknown) { setMsg(e instanceof Error ? e.message : String(e)); }
  };

  const testWithRealData = async () => {
    setTesting(true);
    setTestResult(null);
    setMsg(null);
    try {
      await settingsApi.updateOllama(cfg!); // dry run uses saved settings — save first
      setTestResult(await settingsApi.ollamaPreview(task, true));
    } catch (e: unknown) { setMsg(e instanceof Error ? e.message : String(e)); }
    finally { setTesting(false); }
  };

  // Live template-size estimate vs the model's detected context window.
  const promptTokens = Math.ceil(promptValue.length / 4) || DEFAULT_TEMPLATE_TOKENS[task];
  const estTokens = promptTokens + INJECTED_TOKENS[task];
  const ctxLimit = ctx?.context_length ?? null;
  const nearCeiling = ctxLimit !== null && estTokens > ctxLimit * 0.8;

  return (
    <div className="bg-surface-raised rounded-xl border border-purple-900/30 px-6 mt-6">
      <div className="flex items-center gap-2 pt-5 pb-3">
        <Bot size={14} className="text-brand-light" />
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">LLM Assist</h2>
        <span className="text-xs text-slate-500">connection is configured on the Integrations page</span>
        <button
          onClick={save}
          className="ml-auto flex items-center gap-2 px-3 py-1.5 rounded-lg bg-brand text-white hover:bg-brand-dark text-sm transition-colors"
        >
          <Save size={13} />
          {saved ? "Saved!" : "Save"}
        </button>
      </div>
      {msg && <p className="text-xs text-slate-300 pb-2">{msg}</p>}

      {/* Call stats + circuit breaker readout (v0.27.0, Approved Queue #7).
          In-memory on the backend — counters reset when the container restarts. */}
      {stats && (
        <div className={`rounded-lg border px-4 py-3 mb-2 ${stats.breaker_open ? "border-red-800/60 bg-red-950/30" : "border-purple-900/30 bg-surface"}`}>
          <div className="flex items-center gap-2 flex-wrap text-xs">
            <Activity size={13} className={stats.breaker_open ? "text-red-400" : "text-brand-light"} />
            <span className="text-slate-300">
              {stats.calls} call{stats.calls === 1 ? "" : "s"} since startup
              {stats.calls > 0 && <> · <span className="text-green-400">{stats.successes} ok</span> · <span className={stats.failures ? "text-red-400" : "text-slate-500"}>{stats.failures} failed</span></>}
              {stats.avg_latency_ms !== null && <> · avg {(stats.avg_latency_ms / 1000).toFixed(1)}s</>}
              {stats.breaker_trips > 0 && <> · breaker tripped {stats.breaker_trips}×</>}
            </span>
            {stats.breaker_open ? (
              <span className="text-red-300 font-medium">
                Circuit breaker OPEN — LLM calls paused for {Math.ceil(stats.breaker_seconds_remaining / 60)} min
              </span>
            ) : stats.consecutive_failures > 0 ? (
              <span className="text-amber-400">{stats.consecutive_failures} consecutive failure{stats.consecutive_failures === 1 ? "" : "s"}</span>
            ) : null}
            {(stats.breaker_open || stats.consecutive_failures > 0) && (
              <button
                onClick={async () => { await settingsApi.llmBreakerReset(); qc.invalidateQueries({ queryKey: ["llm-stats"] }); }}
                className="ml-auto flex items-center gap-1 px-2 py-1 rounded bg-surface-overlay hover:bg-white/10 text-slate-300 transition-colors"
                title="Close the breaker and clear the failure streak — the next call goes through immediately"
              >
                <RotateCcw size={11} /> Reset
              </button>
            )}
          </div>
          {stats.last_error && (
            <p className="text-slate-500 text-[11px] mt-1 truncate" title={stats.last_error}>last error: {stats.last_error}</p>
          )}
        </div>
      )}

      {/* Per-task toggles + model overrides (v0.27.0, Approved Queue #10) */}
      {(["match", "explain"] as const).map(t => (
        <div key={t} className="py-4 border-b border-purple-900/20 flex items-center justify-between gap-4">
          <div>
            <p className="text-white text-sm font-medium">{t === "match" ? "Import Matching Task" : "Deletion Rationale Task"}</p>
            <p className="text-slate-500 text-xs mt-0.5">
              {t === "match"
                ? "LLM review of failed-import matches and season-pack files. Untick to keep the deterministic scorer only."
                : "LLM rationales for Cleanup deletion candidates."}
              {" "}Model field empty = use the shared model from the Integrations page.
            </p>
          </div>
          <div className="flex items-center gap-3 ml-6 flex-shrink-0">
            <input
              type="text"
              placeholder={cfg.model || "shared model"}
              value={t === "match" ? cfg.match_model : cfg.explain_model}
              onChange={e => setCfg(c => c ? { ...c, [t === "match" ? "match_model" : "explain_model"]: e.target.value } : c)}
              className="w-44 bg-surface border border-purple-900/40 rounded px-2 py-1.5 text-sm text-white placeholder:text-slate-600"
            />
            <label className="flex items-center gap-1.5 cursor-pointer text-xs text-slate-400">
              <input
                type="checkbox"
                checked={t === "match" ? cfg.match_enabled : cfg.explain_enabled}
                onChange={e => setCfg(c => c ? { ...c, [t === "match" ? "match_enabled" : "explain_enabled"]: e.target.checked } : c)}
                className="accent-purple-500"
              />
              enabled
            </label>
          </div>
        </div>
      ))}

      <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
        <div>
          <p className="text-white text-sm font-medium">Circuit Breaker</p>
          <p className="text-slate-500 text-xs mt-0.5">Auto-pause all LLM calls after this many consecutive failures, for the cooldown period, instead of re-hitting a downed host every scan. 0 = never pause</p>
        </div>
        <div className="flex items-center gap-2 ml-6 flex-shrink-0">
          <input
            type="number" min={0} max={100}
            value={cfg.breaker_threshold}
            onChange={e => setCfg(c => c ? { ...c, breaker_threshold: Math.max(0, Number(e.target.value) || 0) } : c)}
            className="w-16 bg-surface border border-purple-900/40 rounded px-2 py-1.5 text-sm text-white text-right"
            title="Consecutive failures before pausing"
          />
          <span className="text-slate-500 text-xs">failures →</span>
          <input
            type="number" min={1} max={1440}
            value={cfg.breaker_cooldown_minutes}
            onChange={e => setCfg(c => c ? { ...c, breaker_cooldown_minutes: Math.max(1, Number(e.target.value) || 1) } : c)}
            className="w-16 bg-surface border border-purple-900/40 rounded px-2 py-1.5 text-sm text-white text-right"
            title="Cooldown minutes while paused"
          />
          <span className="text-slate-500 text-xs">min pause</span>
        </div>
      </div>

      <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
        <div>
          <p className="text-white text-sm font-medium">Explanation Verbosity</p>
          <p className="text-slate-500 text-xs mt-0.5">Minimal = bare verdict only; Brief/Verbose = one-line verdict plus Markdown bullet reasons (no chain-of-thought)</p>
        </div>
        <select
          value={cfg.verbosity}
          onChange={e => setCfg(c => c ? { ...c, verbosity: e.target.value } : c)}
          className="bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white ml-6"
        >
          <option value="minimal">Minimal</option>
          <option value="brief">Brief</option>
          <option value="verbose">Verbose</option>
        </select>
      </div>

      <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
        <div>
          <p className="text-white text-sm font-medium">Model Size Profile</p>
          <p className="text-slate-500 text-xs mt-0.5">
            Scales reply length and timeouts to the model. Selecting Small also pre-fills Minimal verbosity and Classified confidence (small models classify better than they calibrate floats)
          </p>
        </div>
        <select
          value={cfg.model_size}
          onChange={e => {
            const size = e.target.value;
            setCfg(c => c ? (size === "small"
              ? { ...c, model_size: size, verbosity: "minimal", confidence_style: "classified" }
              : { ...c, model_size: size }) : c);
          }}
          className="bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white ml-6"
        >
          <option value="small">Small (1-3B)</option>
          <option value="medium">Medium (7-14B)</option>
          <option value="large">Large (30B+)</option>
        </select>
      </div>

      <label className="py-4 border-b border-purple-900/20 flex items-center justify-between cursor-pointer">
        <div>
          <p className="text-white text-sm font-medium">Forbid Thinking / Chain-of-Thought</p>
          <p className="text-slate-500 text-xs mt-0.5">
            Instruct the model to answer immediately with no &lt;think&gt; blocks or step-by-step reasoning (saves tokens). On by default; stripping still runs as a backstop.
          </p>
        </div>
        <input type="checkbox" className="accent-purple-500 ml-6"
               checked={cfg.forbid_thinking !== false}
               onChange={e => setCfg(c => c ? { ...c, forbid_thinking: e.target.checked } : c)} />
      </label>

      <label className="py-4 border-b border-purple-900/20 flex items-center justify-between cursor-pointer">
        <div>
          <p className="text-white text-sm font-medium">Compact Scorer Summary</p>
          <p className="text-slate-500 text-xs mt-0.5">
            Inject a short structured scorer line into match prompts instead of the full prose rationale — smaller context, same signal. On by default.
          </p>
        </div>
        <input type="checkbox" className="accent-purple-500 ml-6"
               checked={cfg.compact_det_summary !== false}
               onChange={e => setCfg(c => c ? { ...c, compact_det_summary: e.target.checked } : c)} />
      </label>

      <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
        <div>
          <p className="text-white text-sm font-medium">Confidence Question</p>
          <p className="text-slate-500 text-xs mt-0.5">Numeric asks the model for a ±0.3 adjustment; Classified asks only more/less/same confident (mapped to fixed ±0.15 steps) — small models classify far better than they calibrate numbers</p>
        </div>
        <select
          value={cfg.confidence_style}
          onChange={e => setCfg(c => c ? { ...c, confidence_style: e.target.value } : c)}
          className="bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white ml-6"
        >
          <option value="numeric">Numeric adjustment</option>
          <option value="classified">Classified (more/less/same)</option>
        </select>
      </div>

      <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
        <div>
          <p className="text-white text-sm font-medium">Keep Model Loaded (minutes)</p>
          <p className="text-slate-500 text-xs mt-0.5">Ollama only: keep the model in memory between sequential calls so batch runs don't reload it every time. 0 = Ollama's default unload behavior</p>
        </div>
        <input
          type="number"
          min={0}
          max={120}
          value={cfg.keep_alive_minutes}
          onChange={e => setCfg(c => c ? { ...c, keep_alive_minutes: Math.max(0, Number(e.target.value) || 0) } : c)}
          className="bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white ml-6 w-24"
        />
      </div>

      <div className="py-4 border-b border-purple-900/20">
        <p className="text-white text-sm font-medium mb-1">Inference Tuning</p>
        <p className="text-slate-500 text-xs mb-3">
          Optional overrides on top of the Model Size Profile. Leave max tokens / timeout at 0 to keep the profile defaults.
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div>
            <label className="text-xs text-slate-400 mb-1 block">Temperature</label>
            <input type="number" min={0} max={2} step={0.05}
                   value={cfg.temperature ?? 0}
                   onChange={e => setCfg(c => c ? { ...c, temperature: Number(e.target.value) } : c)}
                   className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white" />
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1 block">Max Tokens (0 = profile)</label>
            <input type="number" min={0} max={4096} step={16}
                   value={cfg.max_tokens ?? 0}
                   onChange={e => setCfg(c => c ? { ...c, max_tokens: Math.max(0, Number(e.target.value) || 0) } : c)}
                   className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white" />
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1 block">Timeout Seconds (0 = profile)</label>
            <input type="number" min={0} max={300} step={5}
                   value={cfg.timeout_seconds ?? 0}
                   onChange={e => setCfg(c => c ? { ...c, timeout_seconds: Math.max(0, Number(e.target.value) || 0) } : c)}
                   className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white" />
          </div>
        </div>
      </div>

      <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
        <div>
          <p className="text-white text-sm font-medium">Batch Pacing Delay (ms)</p>
          <p className="text-slate-500 text-xs mt-0.5">Optional pause between sequential calls during a batch run, so weak hardware isn't pinned at 100% CPU for the whole run. 0 = no pause</p>
        </div>
        <input
          type="number"
          min={0}
          max={60000}
          step={250}
          value={cfg.batch_delay_ms}
          onChange={e => setCfg(c => c ? { ...c, batch_delay_ms: Math.max(0, Number(e.target.value) || 0) } : c)}
          className="bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white ml-6 w-24"
        />
      </div>

      <div className="py-4 border-b border-purple-900/20">
        <div className="flex items-center justify-between mb-2">
          <div>
            <p className="text-white text-sm font-medium">Prompt Templates</p>
            <p className="text-slate-500 text-xs mt-0.5">
              Pick a suggestion, write your own, or draft roughly and let the LLM clean it up. Empty = built-in default.
            </p>
          </div>
          <div className="flex items-center rounded-lg overflow-hidden border border-purple-900/40">
            {(["match", "pack", "explain"] as PromptTask[]).map(t => (
              <button key={t} onClick={() => setTask(t)}
                      className={`px-3 py-1.5 text-sm transition-colors ${task === t ? "bg-brand text-white" : "bg-surface-raised text-slate-400 hover:text-white"}`}>
                {PROMPT_TASK_LABEL[t]}
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-2 mb-2">
          <select
            value=""
            onChange={e => { if (e.target.value !== "") setPrompt(PROMPT_PRESETS[task][Number(e.target.value)].text); }}
            className="bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white"
          >
            <option value="">Load a suggestion…</option>
            {PROMPT_PRESETS[task].map((p, i) => <option key={p.label} value={i}>{p.label}</option>)}
          </select>
          <button
            onClick={refine}
            disabled={refining}
            title="Send your rough draft to the LLM and have it rewritten as a clean template"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-indigo-700 hover:bg-indigo-600 text-white text-sm transition-colors disabled:opacity-50"
          >
            <Wand2 size={13} className={refining ? "animate-pulse" : ""} />
            {refining ? "Cleaning up…" : "Clean Up My Draft"}
          </button>
          <button
            onClick={() => setPrompt("")}
            className="px-3 py-1.5 rounded bg-surface-overlay hover:bg-white/10 text-slate-300 text-sm transition-colors"
          >
            Use Default
          </button>
        </div>

        <div className="flex items-center gap-1.5 mb-2">
          <span className="text-[11px] text-slate-500 uppercase tracking-wider">Insert:</span>
          {PROMPT_PLACEHOLDERS[task].map(token => (
            <button
              key={token}
              onClick={() => insertPlaceholder(token)}
              title={`Insert ${token} at the cursor`}
              className="px-2 py-0.5 rounded bg-surface-overlay hover:bg-brand/20 hover:text-brand-light text-slate-300 text-xs font-mono transition-colors"
            >
              {token}
            </button>
          ))}
        </div>

        <textarea
          ref={promptRef}
          rows={5}
          value={promptValue}
          onChange={e => setPrompt(e.target.value)}
          placeholder={`(using built-in default — type here or load a suggestion to customize the ${PROMPT_TASK_LABEL[task].toLowerCase()} prompt)`}
          className="w-full bg-surface border border-purple-900/40 rounded px-3 py-2 text-xs font-mono text-white placeholder:text-slate-600"
        />
        <div className="flex items-center justify-between mt-1">
          <p className={`text-[11px] ${nearCeiling ? "text-amber-400" : "text-slate-600"}`}>
            ≈ {estTokens} tokens with worst-case injected data
            {ctxLimit !== null && ` · model context window: ${ctxLimit.toLocaleString()}`}
            {nearCeiling && " — template likely too large for this model's context, trim it"}
          </p>
          <button
            onClick={testWithRealData}
            disabled={testing}
            title="Save, then dry-run the current prompt/model settings against one real item from your data — nothing is stored"
            className="px-3 py-1 rounded bg-surface-overlay hover:bg-white/10 text-slate-300 text-xs transition-colors disabled:opacity-50"
          >
            {testing ? "Testing…" : "Test with Real Data"}
          </button>
        </div>
        {testResult && (
          <div className="mt-2 bg-surface rounded border border-purple-900/40 px-3 py-2 text-xs">
            <p className="text-slate-300">
              {testResult.message} <span className="text-slate-500">({(testResult.latency_ms / 1000).toFixed(1)}s{testResult.json_valid !== null ? `, verdict ${testResult.json_valid ? "parsed ✓" : "did NOT parse ✗"}` : ""})</span>
            </p>
            {testResult.output && <p className="text-slate-500 font-mono mt-1 break-words whitespace-pre-wrap max-h-40 overflow-y-auto">{testResult.output}</p>}
          </div>
        )}
      </div>

      <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
        <div>
          <p className="text-white text-sm font-medium">Run LLM on Unscored Imports</p>
          <p className="text-slate-500 text-xs mt-0.5">
            Score open failed imports that never got an LLM signal (up to 50 per run, sequential). Results stream into the list live.
          </p>
        </div>
        <button
          onClick={runNow}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-700 hover:bg-indigo-600 text-white text-sm transition-colors ml-6"
        >
          <Play size={14} />
          Run Now
        </button>
      </div>

      <div className="py-4 flex items-center justify-between">
        <div>
          <p className="text-white text-sm font-medium">Run LLM on Unscored Candidates</p>
          <p className="text-slate-500 text-xs mt-0.5">
            Generate deletion rationales for Cleanup candidates that don't have a current cached one (up to 50 per run, sequential). Results appear inline on the Cleanup page.
          </p>
        </div>
        <button
          onClick={runCandidates}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-700 hover:bg-indigo-600 text-white text-sm transition-colors ml-6"
        >
          <Play size={14} />
          Run Now
        </button>
      </div>
    </div>
  );
}

function LlmScheduleSection() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["llm-schedule-settings"], queryFn: settingsApi.getLlmSchedule });
  const [cfg, setCfg] = useState<LlmScheduleSettings | null>(null);
  const [saved, setSaved] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => { if (data) setCfg(data); }, [data]);

  if (!cfg) return null;

  const save = async () => {
    setMsg(null);
    try {
      await settingsApi.updateLlmSchedule(cfg);
      qc.invalidateQueries({ queryKey: ["llm-schedule-settings"] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: unknown) { setMsg(e instanceof Error ? e.message : String(e)); }
  };

  return (
    <div className="bg-surface-raised rounded-xl border border-purple-900/30 px-6 mt-6">
      <div className="flex items-center gap-2 pt-5 pb-3">
        <Clock size={14} className="text-brand-light" />
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">Scheduled LLM Backlog Scanning</h2>
        <button
          onClick={save}
          className="ml-auto flex items-center gap-2 px-3 py-1.5 rounded-lg bg-brand text-white hover:bg-brand-dark text-sm transition-colors"
        >
          <Save size={13} />
          {saved ? "Saved!" : "Save"}
        </button>
      </div>
      {msg && <p className="text-xs text-slate-300 pb-2">{msg}</p>}
      <p className="text-slate-500 text-xs pb-3">
        Automatically runs the same backlog scans as the "Run Now" buttons above, on a schedule, so you don't have to trigger them by hand. Off by default; respects the existing single-flight guard and batch pacing delay.
      </p>

      <label className="py-4 border-b border-purple-900/20 flex items-center justify-between cursor-pointer">
        <div>
          <p className="text-white text-sm font-medium">Enable scheduled scanning</p>
          <p className="text-slate-500 text-xs mt-0.5">Runs every maintenance cycle (~5 min) when due</p>
        </div>
        <input type="checkbox" checked={cfg.enabled} className="accent-purple-500 ml-6"
               onChange={e => setCfg(c => c ? { ...c, enabled: e.target.checked } : c)} />
      </label>

      <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
        <div>
          <p className="text-white text-sm font-medium">Mode</p>
          <p className="text-slate-500 text-xs mt-0.5">Quiet hours = only run within a daily window; Trickle = run every cycle, any time</p>
        </div>
        <select
          value={cfg.mode}
          onChange={e => setCfg(c => c ? { ...c, mode: e.target.value } : c)}
          className="bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white ml-6"
        >
          <option value="quiet_hours">Quiet hours window</option>
          <option value="trickle">Always-on trickle</option>
        </select>
      </div>

      {cfg.mode === "quiet_hours" && (
        <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
          <div>
            <p className="text-white text-sm font-medium">Quiet hours window (UTC)</p>
            <p className="text-slate-500 text-xs mt-0.5">Hour of day, 0-23 — wraps past midnight if end is earlier than start</p>
          </div>
          <div className="flex items-center gap-2 ml-6">
            <input type="number" min={0} max={23} value={cfg.quiet_hours_start}
                   onChange={e => setCfg(c => c ? { ...c, quiet_hours_start: Number(e.target.value) } : c)}
                   className="w-16 bg-surface border border-purple-900/40 rounded px-2 py-1.5 text-sm text-white" />
            <span className="text-slate-500 text-xs">to</span>
            <input type="number" min={0} max={23} value={cfg.quiet_hours_end}
                   onChange={e => setCfg(c => c ? { ...c, quiet_hours_end: Number(e.target.value) } : c)}
                   className="w-16 bg-surface border border-purple-900/40 rounded px-2 py-1.5 text-sm text-white" />
          </div>
        </div>
      )}

      <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
        <div>
          <p className="text-white text-sm font-medium">Max items per pass</p>
          <p className="text-slate-500 text-xs mt-0.5">Combined cap across imports + candidates each time it runs</p>
        </div>
        <input type="number" min={1} value={cfg.max_items_per_pass}
               onChange={e => setCfg(c => c ? { ...c, max_items_per_pass: Number(e.target.value) } : c)}
               className="w-20 bg-surface border border-purple-900/40 rounded px-2 py-1.5 text-sm text-white ml-6" />
      </div>

      <label className="py-4 border-b border-purple-900/20 flex items-center justify-between cursor-pointer">
        <div>
          <p className="text-white text-sm font-medium">Scan Failed Imports backlog</p>
        </div>
        <input type="checkbox" checked={cfg.scan_imports} className="accent-purple-500 ml-6"
               onChange={e => setCfg(c => c ? { ...c, scan_imports: e.target.checked } : c)} />
      </label>

      <label className="py-4 flex items-center justify-between cursor-pointer">
        <div>
          <p className="text-white text-sm font-medium">Scan Cleanup deletion-rationale backlog</p>
        </div>
        <input type="checkbox" checked={cfg.scan_media} className="accent-purple-500 ml-6"
               onChange={e => setCfg(c => c ? { ...c, scan_media: e.target.checked } : c)} />
      </label>
    </div>
  );
}

function SecuritySection() {
  const qc = useQueryClient();
  const { data: status } = useQuery({ queryKey: ["auth-status"], queryFn: authApi.status });
  const [msg, setMsg] = useState<string | null>(null);

  // enable form
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  // change password form
  const [curPw, setCurPw] = useState("");
  const [newPw, setNewPw] = useState("");
  // totp
  const [totpSetup, setTotpSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [totpCode, setTotpCode] = useState("");
  // lan
  const [lanBypass, setLanBypass] = useState(true);
  const [cidrs, setCidrs] = useState("");
  // sso (Authentik forward-auth)
  const [ssoEnabled, setSsoEnabled] = useState(false);
  const [ssoAllowLan, setSsoAllowLan] = useState(false);
  const [ssoProxies, setSsoProxies] = useState("");
  const [ssoHeader, setSsoHeader] = useState("X-Authentik-Username");
  // disable
  const [disablePw, setDisablePw] = useState("");

  useEffect(() => {
    if (status) {
      setLanBypass(status.lan_bypass);
      setCidrs(status.lan_cidrs.join("\n"));
      setSsoEnabled(status.sso_enabled);
      setSsoAllowLan(status.sso_allow_lan_without_sso);
      setSsoProxies((status.sso_trusted_proxies ?? []).join("\n"));
      if (status.sso_username_header) setSsoHeader(status.sso_username_header);
    }
  }, [status]);

  const refresh = () => qc.invalidateQueries({ queryKey: ["auth-status"] });
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setMsg(null);
    try { await fn(); setMsg(ok); refresh(); }
    catch (e: unknown) { setMsg(e instanceof Error ? e.message : String(e)); }
  };

  if (!status) return null;

  const inputCls = "w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white placeholder:text-slate-600";
  const btnCls = "px-3 py-1.5 rounded bg-brand hover:bg-brand-dark text-white text-sm transition-colors disabled:opacity-40";
  const subtleBtnCls = "px-3 py-1.5 rounded bg-surface-overlay hover:bg-white/10 text-slate-300 text-sm transition-colors disabled:opacity-40";

  return (
    <div className="bg-surface-raised rounded-xl border border-purple-900/30 px-6 mt-6">
      <div className="flex items-center gap-2 pt-5 pb-3">
        <Lock size={14} className="text-brand-light" />
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">Security</h2>
        {msg && <span className="text-xs text-slate-300 ml-auto">{msg}</span>}
      </div>

      {!status.enabled ? (
        <div className="py-4 space-y-3">
          <p className="text-slate-400 text-sm">
            Authentication is <span className="text-yellow-300">disabled</span>. Set credentials to enable password login
            (LAN traffic keeps working via the bypass below).
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <input type="text" placeholder="Username" value={username} onChange={e => setUsername(e.target.value)} className={inputCls} />
            <input type="password" placeholder="Password (min 8 chars)" value={password} onChange={e => setPassword(e.target.value)} className={inputCls} />
            <input type="password" placeholder="Confirm password" value={confirm} onChange={e => setConfirm(e.target.value)} className={inputCls} />
          </div>
          <button
            disabled={!username || password.length < 8 || password !== confirm}
            onClick={() => run(() => authApi.setup(username, password), "Authentication enabled")}
            className={btnCls}
          >
            Enable Authentication
          </button>
          {password && confirm && password !== confirm && <p className="text-red-400 text-xs">Passwords don't match</p>}
        </div>
      ) : (
        <>
          <div className="py-3 border-b border-purple-900/20 flex items-center gap-3">
            <p className="text-sm text-slate-300">
              Authentication <span className="text-green-400">enabled</span>
              {status.username && <> for <span className="text-white font-medium">{status.username}</span></>}
              {status.bypassed && <span className="text-slate-500"> — you're on the LAN bypass</span>}
            </p>
            <div className="ml-auto flex items-center gap-2">
              <input type="password" placeholder="Password" value={disablePw} onChange={e => setDisablePw(e.target.value)}
                     className="w-36 bg-surface border border-purple-900/40 rounded px-2 py-1 text-xs text-white placeholder:text-slate-600" />
              <button onClick={() => run(() => authApi.disable(disablePw), "Authentication disabled")} className={subtleBtnCls}>
                Disable
              </button>
            </div>
          </div>

          <div className="py-3 border-b border-purple-900/20">
            <p className="text-white text-sm font-medium mb-2">Change Password</p>
            <div className="flex gap-2">
              <input type="password" placeholder="Current" value={curPw} onChange={e => setCurPw(e.target.value)} className={inputCls} />
              <input type="password" placeholder="New (min 8 chars)" value={newPw} onChange={e => setNewPw(e.target.value)} className={inputCls} />
              <button
                disabled={!curPw || newPw.length < 8}
                onClick={() => run(async () => { await authApi.changePassword(curPw, newPw); setCurPw(""); setNewPw(""); }, "Password changed")}
                className={btnCls}
              >
                Change
              </button>
            </div>
          </div>

          <div className="py-3 border-b border-purple-900/20">
            <p className="text-white text-sm font-medium mb-1">Two-Factor Authentication (TOTP)</p>
            {status.totp_enabled ? (
              <div className="flex items-center gap-2">
                <p className="text-green-400 text-xs">Enabled — codes required at login</p>
                <input type="password" placeholder="Password" value={disablePw} onChange={e => setDisablePw(e.target.value)}
                       className="w-36 ml-auto bg-surface border border-purple-900/40 rounded px-2 py-1 text-xs text-white placeholder:text-slate-600" />
                <button onClick={() => run(() => authApi.totpDisable(disablePw), "TOTP disabled")} className={subtleBtnCls}>
                  Disable TOTP
                </button>
              </div>
            ) : totpSetup ? (
              <div className="space-y-2">
                <p className="text-slate-400 text-xs">
                  Add this secret to Google Authenticator (or any TOTP app), then confirm with a code:
                </p>
                <p className="font-mono text-sm text-brand-light break-all bg-surface rounded px-3 py-2">{totpSetup.secret}</p>
                <p className="font-mono text-xs text-slate-500 break-all">{totpSetup.otpauth_uri}</p>
                <div className="flex gap-2">
                  <input type="text" inputMode="numeric" placeholder="123456" value={totpCode}
                         onChange={e => setTotpCode(e.target.value)}
                         className="w-32 bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white placeholder:text-slate-600" />
                  <button
                    disabled={totpCode.trim().length < 6}
                    onClick={() => run(async () => { await authApi.totpEnable(totpCode); setTotpSetup(null); setTotpCode(""); }, "TOTP enabled")}
                    className={btnCls}
                  >
                    Confirm & Enable
                  </button>
                  <button onClick={() => setTotpSetup(null)} className={subtleBtnCls}>Cancel</button>
                </div>
              </div>
            ) : (
              <button onClick={async () => {
                try { setTotpSetup(await authApi.totpSetup()); setMsg(null); }
                catch (e: unknown) { setMsg(e instanceof Error ? e.message : String(e)); }
              }} className={subtleBtnCls}>
                Set Up TOTP
              </button>
            )}
          </div>
        </>
      )}

      <div className="py-4">
        <label className="flex items-center justify-between cursor-pointer mb-2">
          <div>
            <p className="text-white text-sm font-medium">LAN Bypass</p>
            <p className="text-slate-500 text-xs mt-0.5">Requests from the CIDRs below skip authentication entirely</p>
          </div>
          <input type="checkbox" checked={lanBypass} onChange={e => setLanBypass(e.target.checked)} className="accent-purple-500 ml-6" />
        </label>
        <textarea
          rows={4}
          value={cidrs}
          onChange={e => setCidrs(e.target.value)}
          className="w-full bg-surface border border-purple-900/40 rounded px-3 py-2 text-xs font-mono text-white"
        />
        <button
          onClick={() => run(() => authApi.updateConfig(lanBypass, cidrs.split("\n").map(s => s.trim()).filter(Boolean)), "LAN settings saved")}
          className={`${btnCls} mt-2`}
        >
          Save LAN Settings
        </button>
      </div>

      <div className="py-4 border-t border-purple-900/20">
        <label className="flex items-center justify-between cursor-pointer">
          <div>
            <p className="text-white text-sm font-medium">Single Sign-On (Authentik)</p>
            <p className="text-slate-500 text-xs mt-0.5">
              Trust an identity asserted by your reverse proxy (Authentik forward-auth) — honored
              only from the trusted proxy IPs below, so a direct client can't forge it.
            </p>
          </div>
          <input type="checkbox" checked={ssoEnabled} onChange={e => setSsoEnabled(e.target.checked)} className="accent-purple-500 ml-6" />
        </label>

        {ssoEnabled && (
          <div className="mt-3 space-y-3">
            <div>
              <p className="text-slate-400 text-xs mb-1">Trusted proxy IPs / CIDRs (one per line) — your reverse proxy's address on the shared network</p>
              <textarea rows={2} value={ssoProxies} onChange={e => setSsoProxies(e.target.value)}
                        placeholder="192.168.112.2"
                        className="w-full bg-surface border border-purple-900/40 rounded px-3 py-2 text-xs font-mono text-white placeholder:text-slate-600" />
            </div>
            <div>
              <p className="text-slate-400 text-xs mb-1">Identity header</p>
              <input type="text" value={ssoHeader} onChange={e => setSsoHeader(e.target.value)} className={inputCls} />
            </div>
            <label className="flex items-center justify-between cursor-pointer">
              <div>
                <p className="text-white text-sm font-medium">Allow LAN access without SSO</p>
                <p className="text-slate-500 text-xs mt-0.5">
                  Off: direct/LAN requests must log in (localhost stays reachable as break-glass).
                  On: the LAN CIDRs above bypass SSO for direct requests.
                </p>
              </div>
              <input type="checkbox" checked={ssoAllowLan} onChange={e => setSsoAllowLan(e.target.checked)} className="accent-purple-500 ml-6" />
            </label>
          </div>
        )}

        <button
          onClick={() => run(() => authApi.updateSso({
            sso_enabled: ssoEnabled,
            sso_allow_lan_without_sso: ssoAllowLan,
            sso_trusted_proxies: ssoProxies.split("\n").map(s => s.trim()).filter(Boolean),
            sso_username_header: ssoHeader.trim() || "X-Authentik-Username",
          }), "SSO settings saved")}
          className={`${btnCls} mt-3`}
        >
          Save SSO Settings
        </button>
      </div>
    </div>
  );
}

function NotificationsSection() {
  const { data } = useQuery({ queryKey: ["notification-settings"], queryFn: settingsApi.getNotifications });
  const [cfg, setCfg] = useState<NotificationSettings | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => { if (data) setCfg(data); }, [data]);

  if (!cfg) return null;

  return (
    <div className="bg-surface-raised rounded-xl border border-purple-900/30 px-6 mt-6">
      <div className="flex items-center gap-2 pt-5 pb-3">
        <Bell size={14} className="text-brand-light" />
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">Notifications (ntfy)</h2>
        {msg && <span className="text-xs text-slate-300 ml-auto">{msg}</span>}
      </div>

      <div className="py-4 space-y-3">
        <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
          <input type="checkbox" checked={cfg.enabled}
                 onChange={e => setCfg(c => c ? { ...c, enabled: e.target.checked } : c)}
                 className="accent-purple-500" />
          Push a summary when a scan finds new suggestions, auto-resolves, or push failures
        </label>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-slate-400 mb-1 block">ntfy Server URL</label>
            <input type="text" placeholder="http://10.1.1.2:8091" value={cfg.ntfy_url}
                   onChange={e => setCfg(c => c ? { ...c, ntfy_url: e.target.value } : c)}
                   className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white placeholder:text-slate-600" />
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1 block">Topic</label>
            <input type="text" value={cfg.topic}
                   onChange={e => setCfg(c => c ? { ...c, topic: e.target.value } : c)}
                   className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white" />
          </div>
        </div>
        <div className="pt-2 border-t border-purple-900/20">
          <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
            <input type="checkbox" checked={cfg.actionable_new_suggestions}
                   onChange={e => setCfg(c => c ? { ...c, actionable_new_suggestions: e.target.checked } : c)}
                   className="accent-purple-500" />
            Send an Accept/Reject actionable notification per new suggestion
          </label>
          <p className="text-slate-500 text-xs mt-1 mb-2">
            Adds click-to-act buttons via signed one-time links (7-day expiry). Needs a URL the ntfy client can reach — falls back to the aggregate summary above if blank, or if a scan produces more than the max below.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-slate-400 mb-1 block">Public Base URL</label>
              <input type="text" placeholder="https://powarr.pwrs.dev" value={cfg.public_base_url}
                     onChange={e => setCfg(c => c ? { ...c, public_base_url: e.target.value } : c)}
                     className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white placeholder:text-slate-600" />
            </div>
            <div>
              <label className="text-xs text-slate-400 mb-1 block">Max Actionable per Scan</label>
              <input type="number" min={1} value={cfg.actionable_max_per_scan}
                     onChange={e => setCfg(c => c ? { ...c, actionable_max_per_scan: Number(e.target.value) } : c)}
                     className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white" />
            </div>
          </div>
        </div>
        <div className="pt-2 border-t border-purple-900/20">
          <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
            <input type="checkbox" checked={cfg.digest_enabled}
                   onChange={e => setCfg(c => c ? { ...c, digest_enabled: e.target.checked } : c)}
                   className="accent-purple-500" />
            Weekly digest summary
          </label>
          <p className="text-slate-500 text-xs mt-1 mb-2">
            One ntfy push per week with open imports, 7-day resolve counts, deletion candidates, and space freed.
          </p>
          {cfg.digest_enabled && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-slate-400 mb-1 block">Weekday (UTC)</label>
                <select value={cfg.digest_weekday}
                        onChange={e => setCfg(c => c ? { ...c, digest_weekday: Number(e.target.value) } : c)}
                        className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white">
                  {["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].map((d, i) => (
                    <option key={d} value={i}>{d}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs text-slate-400 mb-1 block">Hour (UTC, 0–23)</label>
                <input type="number" min={0} max={23} value={cfg.digest_hour_utc}
                       onChange={e => setCfg(c => c ? { ...c, digest_hour_utc: Number(e.target.value) } : c)}
                       className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white" />
              </div>
            </div>
          )}
        </div>
        <div className="flex gap-2">
          <button
            onClick={async () => {
              setMsg(null);
              try { await settingsApi.updateNotifications(cfg); setMsg("Saved"); }
              catch (e: unknown) { setMsg(e instanceof Error ? e.message : String(e)); }
            }}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-brand hover:bg-brand-dark text-white text-sm transition-colors"
          >
            <Save size={13} /> Save
          </button>
          <button
            onClick={async () => {
              setMsg(null);
              try {
                await settingsApi.updateNotifications(cfg); // test uses saved config
                const r = await settingsApi.testNotification();
                setMsg(r.message);
              } catch (e: unknown) { setMsg(e instanceof Error ? e.message : String(e)); }
            }}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-surface-overlay hover:bg-white/10 text-slate-300 text-sm transition-colors"
          >
            <Send size={13} /> Send Test
          </button>
        </div>
      </div>
    </div>
  );
}

function ScoringProfilesSection() {
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

function ScoringWeightsSection() {
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

  if (isLoading || !weights) return <div className="text-slate-400">Loading…</div>;

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

const CATEGORIES: { key: string; label: string; icon: typeof Save; description: string }[] = [
  { key: "integrations", label: "Integrations", icon: Plug, description: "Plex, Tautulli, *arr apps, Seerr, download clients, Qdrant, Ollama connection" },
  { key: "matching-scoring", label: "Matching & Scoring", icon: SlidersHorizontal, description: "Failed import matching thresholds, scoring weights, per-library profiles" },
  { key: "automation", label: "Automation", icon: Clock, description: "Cleanup behavior, scheduled Plex sync, automated backups" },
  { key: "llm-assist", label: "LLM Assist", icon: Bot, description: "Local LLM behavior, prompts, verbosity, scheduled backlog scanning" },
  { key: "notifications", label: "Notifications", icon: Bell, description: "ntfy alerts, actionable notifications, weekly digest" },
  { key: "security", label: "Security", icon: Lock, description: "Auth, TOTP, LAN bypass, SSO / forward-auth" },
  { key: "music", label: "Music", icon: Music, description: "Artist Discovery and Playlists configuration" },
];

export default function SettingsPage() {
  const { category } = useParams<{ category?: string }>();
  const navigate = useNavigate();
  const cat = CATEGORIES.find(c => c.key === category);

  if (!category) {
    return (
      <div className="p-4 sm:p-8">
        <h1 className="text-2xl font-bold text-white mb-1">Settings</h1>
        <p className="text-slate-400 text-sm mb-6">Choose a category to configure</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 max-w-3xl">
          {CATEGORIES.map(c => {
            const Icon = c.icon;
            return (
              <button
                key={c.key}
                onClick={() => navigate(`/settings/${c.key}`)}
                className="text-left bg-surface-raised rounded-xl border border-purple-900/30 hover:border-purple-500/60 p-5 transition-colors"
              >
                <Icon size={20} className="text-brand-light mb-2" />
                <p className="text-white font-semibold">{c.label}</p>
                <p className="text-slate-400 text-sm mt-1">{c.description}</p>
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  return (
    <div className="p-4 sm:p-8 max-w-2xl">
      <button onClick={() => navigate("/settings")} className="text-slate-400 hover:text-white text-sm mb-4">
        ‹ All Settings
      </button>
      <h1 className="text-2xl font-bold text-white mb-1">{cat?.label ?? "Settings"}</h1>
      <p className="text-slate-400 text-sm mb-6">{cat?.description}</p>

      {category === "integrations" && <IntegrationsPage embedded />}
      {category === "matching-scoring" && (
        <>
          <ScoringWeightsSection />
          <ScoringProfilesSection />
          <ImportMatchingSection />
        </>
      )}
      {category === "automation" && (
        <>
          <CleanupSection />
          <SyncSection />
          <BackupSection />
        </>
      )}
      {category === "llm-assist" && (
        <>
          <LLMAssistSection />
          <LlmScheduleSection />
        </>
      )}
      {category === "notifications" && <NotificationsSection />}
      {category === "security" && <SecuritySection />}
      {category === "music" && <MusicSettings />}
    </div>
  );
}
