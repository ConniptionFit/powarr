import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Save, AlertTriangle, Lock, Bell, Send, Bot, Wand2, Play } from "lucide-react";
import { settingsApi, mediaApi, authApi, importsApi, type ScoringWeights, type ImportMatchingSettings,
         type CleanupSettings, type SyncSettings, type NotificationSettings, type OllamaSettings } from "../../lib/api";

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

// Preset prompt suggestions per task. Placeholders are substituted by the backend;
// the JSON reply-format instruction is appended automatically, so templates stay clean.
const PROMPT_PRESETS: Record<"match" | "explain", { label: string; text: string }[]> = {
  match: [
    {
      label: "Strict matcher (default style)",
      text: "You match download release names to media library entries.\nRelease name: {release}\nCandidate library entry: {candidate}\nContext: {context}",
    },
    {
      label: "Lenient — alternate titles & translations",
      text: "You match download release names to media library entries. Consider alternate titles, translations, romanizations, and common abbreviations as valid matches.\nRelease name: {release}\nCandidate library entry: {candidate}\nContext: {context}",
    },
    {
      label: "Conservative — punish year/edition mismatch",
      text: "You match download release names to media library entries. Be conservative: lower your confidence sharply when the year, edition, or cut (e.g. Director's Cut, remaster) differs between release and candidate.\nRelease name: {release}\nCandidate library entry: {candidate}\nContext: {context}",
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

function LLMAssistSection() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["ollama-settings"], queryFn: settingsApi.getOllama });
  const [cfg, setCfg] = useState<OllamaSettings | null>(null);
  const [task, setTask] = useState<"match" | "explain">("match");
  const [msg, setMsg] = useState<string | null>(null);
  const [refining, setRefining] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => { if (data) setCfg(data); }, [data]);

  if (!cfg) return null;

  const promptField = task === "match" ? "match_prompt" : "explain_prompt";
  const promptValue = cfg[promptField];
  const setPrompt = (text: string) => setCfg(c => (c ? { ...c, [promptField]: text } : c));

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

      <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
        <div>
          <p className="text-white text-sm font-medium">Explanation Verbosity</p>
          <p className="text-slate-500 text-xs mt-0.5">Brief = one-liners; Verbose = detailed multi-sentence explanations in notes, tooltips, and rationales</p>
        </div>
        <select
          value={cfg.verbosity}
          onChange={e => setCfg(c => c ? { ...c, verbosity: e.target.value } : c)}
          className="bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white ml-6"
        >
          <option value="brief">Brief</option>
          <option value="verbose">Verbose</option>
        </select>
      </div>

      <div className="py-4 border-b border-purple-900/20">
        <div className="flex items-center justify-between mb-2">
          <div>
            <p className="text-white text-sm font-medium">Prompt Templates</p>
            <p className="text-slate-500 text-xs mt-0.5">
              Pick a suggestion, write your own, or draft roughly and let the LLM clean it up.
              Placeholders: {task === "match" ? "{release} {candidate} {context}" : "{item}"} · empty = built-in default
            </p>
          </div>
          <div className="flex items-center rounded-lg overflow-hidden border border-purple-900/40">
            <button onClick={() => setTask("match")}
                    className={`px-3 py-1.5 text-sm transition-colors ${task === "match" ? "bg-brand text-white" : "bg-surface-raised text-slate-400 hover:text-white"}`}>
              Import Matching
            </button>
            <button onClick={() => setTask("explain")}
                    className={`px-3 py-1.5 text-sm transition-colors ${task === "explain" ? "bg-brand text-white" : "bg-surface-raised text-slate-400 hover:text-white"}`}>
              Deletion Rationale
            </button>
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

        <textarea
          rows={5}
          value={promptValue}
          onChange={e => setPrompt(e.target.value)}
          placeholder={`(using built-in default — type here or load a suggestion to customize the ${task === "match" ? "matching" : "rationale"} prompt)`}
          className="w-full bg-surface border border-purple-900/40 rounded px-3 py-2 text-xs font-mono text-white placeholder:text-slate-600"
        />
      </div>

      <div className="py-4 flex items-center justify-between">
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
  // disable
  const [disablePw, setDisablePw] = useState("");

  useEffect(() => {
    if (status) {
      setLanBypass(status.lan_bypass);
      setCidrs(status.lan_cidrs.join("\n"));
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
          <div className="grid grid-cols-3 gap-3">
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
        <div className="grid grid-cols-2 gap-3">
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
      <LLMAssistSection />
      <CleanupSection />
      <SyncSection />
      <NotificationsSection />
      <SecuritySection />
    </div>
  );
}
