import { useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Save, AlertTriangle, Lock, Bell, Send, Bot, Wand2, Play, Clock, DatabaseBackup, Activity, RotateCcw, Plug, SlidersHorizontal, Music } from "lucide-react";
import { Skeleton } from "../../../components/Skeleton";
import { settingsApi, mediaApi, authApi, importsApi, fmtBytes, fmtDate, type ScoringWeights, type ScoringProfiles,
         type ImportMatchingSettings, type CleanupSettings, type SyncSettings, type NotificationSettings,
         type OllamaSettings, type LlmPolicies, type LlmAppOverride, type LlmLibraryOverride,
         type LlmScheduleSettings, type BackupSettings, type BackupFile, type SettingsExport } from "../../../lib/api";

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

export function LLMAssistSection() {
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

      {/* LLM-07 (v0.67.0) — independent "risky delete" second opinion, own task
          toggle since it's judgment-affecting and off by default (unlike match/
          explain above, which default on). */}
      <div className="py-4 border-b border-purple-900/20 flex items-center justify-between gap-4">
        <div>
          <p className="text-white text-sm font-medium">Second Opinion Task</p>
          <p className="text-slate-500 text-xs mt-0.5">
            Independent "risky delete" check on Cleanup deletion candidates — flags a KEEP verdict
            that conflicts with a high deletion score (e.g. active watch-progress, seeding) for a
            second look. Advisory only; never blocks or auto-resolves a deletion.
            {" "}Model field empty = use the shared model from the Integrations page. Off by default.
          </p>
        </div>
        <div className="flex items-center gap-3 ml-6 flex-shrink-0">
          <input
            type="text"
            placeholder={cfg.model || "shared model"}
            value={cfg.second_opinion_model}
            onChange={e => setCfg(c => c ? { ...c, second_opinion_model: e.target.value } : c)}
            className="w-44 bg-surface border border-purple-900/40 rounded px-2 py-1.5 text-sm text-white placeholder:text-slate-600"
          />
          <label className="flex items-center gap-1.5 cursor-pointer text-xs text-slate-400">
            <input
              type="checkbox"
              checked={cfg.second_opinion_enabled}
              onChange={e => setCfg(c => c ? { ...c, second_opinion_enabled: e.target.checked } : c)}
              className="accent-purple-500"
            />
            enabled
          </label>
        </div>
      </div>

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

const LLM_POLICY_APPS = ["sonarr", "radarr", "lidarr", "readarr"] as const;

// LLM-08 (v0.68.0) — per-source_app match/blend overrides + per-Plex-library
// explain overrides. Mirrors ScoringProfilesSection's overlay-editor shape
// (v0.30.0): pick a key, edit its partial overlay, blank = inherit global.
export function LlmPoliciesSection() {
  const qc = useQueryClient();
  const { data: policies } = useQuery({ queryKey: ["llm-policies"], queryFn: settingsApi.getLlmPolicies });
  const { data: libraries = [] } = useQuery({ queryKey: ["libraries"], queryFn: mediaApi.libraries });
  const [cfg, setCfg] = useState<LlmPolicies | null>(null);
  const [app, setApp] = useState<string>(LLM_POLICY_APPS[0]);
  const [lib, setLib] = useState("");
  const [saved, setSaved] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => { if (policies) setCfg(policies); }, [policies]);
  useEffect(() => { if (!lib && libraries.length > 0) setLib(libraries[0]); }, [libraries, lib]);

  if (!cfg) return null;

  const appOverlay = cfg.by_app[app] || {};
  const libOverlay = (lib && cfg.by_library[lib]) || {};

  const setAppField = (field: keyof LlmAppOverride, value: boolean | string | number | undefined) => {
    setCfg(c => {
      if (!c) return c;
      const next = { ...c.by_app };
      const cur = { ...(next[app] || {}) };
      if (value === undefined || value === "") delete cur[field];
      else (cur as Record<string, unknown>)[field] = value;
      if (Object.keys(cur).length === 0) delete next[app];
      else next[app] = cur;
      return { ...c, by_app: next };
    });
  };

  const setLibField = (field: keyof LlmLibraryOverride, value: boolean | string | undefined) => {
    if (!lib) return;
    setCfg(c => {
      if (!c) return c;
      const next = { ...c.by_library };
      const cur = { ...(next[lib] || {}) };
      if (value === undefined || value === "") delete cur[field];
      else (cur as Record<string, unknown>)[field] = value;
      if (Object.keys(cur).length === 0) delete next[lib];
      else next[lib] = cur;
      return { ...c, by_library: next };
    });
  };

  const save = async () => {
    setMsg(null);
    try {
      await settingsApi.updateLlmPolicies(cfg);
      qc.invalidateQueries({ queryKey: ["llm-policies"] });
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
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">Per-App / Per-Library LLM Policies</h2>
          <p className="text-slate-500 text-xs mt-1">
            Partial overlays on the global LLM config above. Blank/unset fields inherit the global value. Empty by default.
          </p>
        </div>
        <button
          onClick={save}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-brand text-white hover:bg-brand-dark text-sm transition-colors"
        >
          <Save size={13} />
          {saved ? "Saved!" : "Save"}
        </button>
      </div>
      {msg && <p className="text-xs text-red-400 pb-2">{msg}</p>}

      <div className="py-3 border-b border-purple-900/20">
        <div className="flex items-center justify-between gap-3 mb-3">
          <p className="text-white text-sm font-medium">Per-app (match review + blend weight)</p>
          <select
            value={app}
            onChange={e => setApp(e.target.value)}
            className="bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white"
          >
            {LLM_POLICY_APPS.map(a => (
              <option key={a} value={a}>{a}{cfg.by_app[a] ? " *" : ""}</option>
            ))}
          </select>
        </div>
        <div className="flex items-center justify-between py-2">
          <span className="text-slate-400 text-xs">Match enabled</span>
          <select
            value={appOverlay.match_enabled === undefined ? "" : String(appOverlay.match_enabled)}
            onChange={e => setAppField("match_enabled", e.target.value === "" ? undefined : e.target.value === "true")}
            className="bg-surface border border-purple-900/40 rounded px-2 py-1 text-sm text-white"
          >
            <option value="">inherit</option>
            <option value="true">on</option>
            <option value="false">off</option>
          </select>
        </div>
        <div className="flex items-center justify-between py-2">
          <span className="text-slate-400 text-xs">Match model</span>
          <input
            type="text"
            placeholder="inherit"
            value={appOverlay.match_model ?? ""}
            onChange={e => setAppField("match_model", e.target.value || undefined)}
            className="w-44 bg-surface border border-purple-900/40 rounded px-2 py-1 text-sm text-white placeholder:text-slate-600"
          />
        </div>
        <div className="flex items-center justify-between py-2">
          <span className="text-slate-400 text-xs">LLM blend weight (0-1)</span>
          <input
            type="number" min={0} max={1} step={0.1}
            placeholder="inherit (0.3)"
            value={appOverlay.llm_blend_weight ?? ""}
            onChange={e => setAppField("llm_blend_weight", e.target.value === "" ? undefined : Number(e.target.value))}
            className="w-24 bg-surface border border-purple-900/40 rounded px-2 py-1 text-sm text-white text-right placeholder:text-slate-600"
          />
        </div>
      </div>

      {libraries.length === 0 ? (
        <p className="text-slate-500 text-xs py-3">No libraries found — run a Plex sync first for per-library explain overrides.</p>
      ) : (
        <div className="py-3">
          <div className="flex items-center justify-between gap-3 mb-3">
            <p className="text-white text-sm font-medium">Per-library (deletion rationale)</p>
            <select
              value={lib}
              onChange={e => setLib(e.target.value)}
              className="bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white"
            >
              {libraries.map(l => (
                <option key={l} value={l}>{l}{cfg.by_library[l] ? " *" : ""}</option>
              ))}
            </select>
          </div>
          <div className="flex items-center justify-between py-2">
            <span className="text-slate-400 text-xs">Explain enabled</span>
            <select
              value={libOverlay.explain_enabled === undefined ? "" : String(libOverlay.explain_enabled)}
              onChange={e => setLibField("explain_enabled", e.target.value === "" ? undefined : e.target.value === "true")}
              className="bg-surface border border-purple-900/40 rounded px-2 py-1 text-sm text-white"
            >
              <option value="">inherit</option>
              <option value="true">on</option>
              <option value="false">off</option>
            </select>
          </div>
          <div className="flex items-center justify-between py-2">
            <span className="text-slate-400 text-xs">Explain model</span>
            <input
              type="text"
              placeholder="inherit"
              value={libOverlay.explain_model ?? ""}
              onChange={e => setLibField("explain_model", e.target.value || undefined)}
              className="w-44 bg-surface border border-purple-900/40 rounded px-2 py-1 text-sm text-white placeholder:text-slate-600"
            />
          </div>
        </div>
      )}
    </div>
  );
}

export function LlmScheduleSection() {
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

