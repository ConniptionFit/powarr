import { useEffect, useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { Save, AlertTriangle } from "lucide-react";
import { settingsApi, mediaApi, type ScoringWeights, type ImportMatchingSettings,
         type CleanupSettings, type SyncSettings } from "../../lib/api";

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
  { label: "Min Score Threshold", field: "min_score_threshold", description: "Items below this score are hidden from Cleanup by default", unit: "" },
];

function ImportMatchingSection() {
  const { data } = useQuery({ queryKey: ["import-matching"], queryFn: settingsApi.getImportMatching });
  const [cfg, setCfg] = useState<ImportMatchingSettings | null>(null);
  const [saved, setSaved] = useState(false);

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

  const toggleRow = (label: string, description: string, field: keyof ImportMatchingSettings) => (
    <label className="py-4 border-b border-purple-900/20 flex items-center justify-between cursor-pointer">
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

export default function SettingsPage() {
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

  if (isLoading || !weights) return <div className="p-8 text-slate-400">Loading…</div>;

  return (
    <div className="p-8 max-w-2xl">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white">Settings</h1>
          <p className="text-slate-400 text-sm mt-1">Tune how media is scored for deletion</p>
        </div>
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
                value={weights[f.field] as number}
                onChange={e => handleChange(f.field, Number(e.target.value))}
                className="w-24 bg-surface border border-purple-900/40 rounded px-2 py-1 text-sm text-white text-right"
              />
              {f.unit && <span className="text-slate-500 text-xs">{f.unit}</span>}
            </div>
          </div>
        ))}
      </div>

      <ImportMatchingSection />
      <CleanupSection />
      <SyncSection />
    </div>
  );
}
