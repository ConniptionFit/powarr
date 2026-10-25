import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle, XCircle, Loader2, Save, RefreshCw, Bot } from "lucide-react";
import { settingsApi, ollamaApi } from "../../../lib/api";


export const OTHER = "__other__";

// Known-good small models with pre-tuned profiles (mirrors PROMPT_PRESETS'
// pattern). Selecting one fills the model and applies the profile in one click —
// the model still has to be pulled on the Ollama host (warned if absent).
export const MODEL_PRESETS: Array<{ label: string; model: string;
  profile: { model_size: string; verbosity: string; confidence_style: string } }> = [
  { label: "qwen2.5:3b — solid small all-rounder", model: "qwen2.5:3b",
    profile: { model_size: "small", verbosity: "minimal", confidence_style: "classified" } },
  { label: "llama3.2:3b — good instruction-following", model: "llama3.2:3b",
    profile: { model_size: "small", verbosity: "minimal", confidence_style: "classified" } },
  { label: "llama3.2:1b — tiniest workable", model: "llama3.2:1b",
    profile: { model_size: "small", verbosity: "minimal", confidence_style: "classified" } },
  { label: "phi3.5 — small but chatty", model: "phi3.5",
    profile: { model_size: "small", verbosity: "minimal", confidence_style: "classified" } },
  { label: "qwen2.5:7b — reliable JSON at 7B", model: "qwen2.5:7b",
    profile: { model_size: "medium", verbosity: "brief", confidence_style: "numeric" } },
  { label: "mistral:7b — reliable JSON at 7B", model: "mistral",
    profile: { model_size: "medium", verbosity: "brief", confidence_style: "numeric" } },
];

export function OllamaCard() {
  const qc = useQueryClient();
  const { data: cfg } = useQuery({ queryKey: ["ollama-settings"], queryFn: settingsApi.getOllama });

  const [enabled, setEnabled] = useState(false);
  const [host, setHost] = useState("");
  const [model, setModel] = useState("");
  const [apiStyle, setApiStyle] = useState("ollama");
  const [useOther, setUseOther] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const [presetProfile, setPresetProfile] = useState<Record<string, string> | null>(null);
  const [presetWarning, setPresetWarning] = useState<string | null>(null);
  const [benching, setBenching] = useState(false);
  const [benchResult, setBenchResult] = useState<string | null>(null);

  useEffect(() => {
    if (cfg) { setEnabled(cfg.enabled); setHost(cfg.host); setModel(cfg.model); setApiStyle(cfg.api_style || "ollama"); }
  }, [cfg]);

  const { data: modelsResp, refetch: refetchModels, isFetching: loadingModels } = useQuery({
    queryKey: ["ollama-models"],
    queryFn: ollamaApi.models,
    enabled: false, // fetched on demand — needs a saved host first
  });
  const models = modelsResp?.models ?? [];
  const dropdownValue = useOther || (model !== "" && models.length > 0 && !models.includes(model)) ? OTHER : model;

  // Spread the loaded config so behavior settings (verbosity, prompt templates —
  // managed in Settings → LLM Assist) survive connection-side saves.
  const buildPayload = () => ({
    verbosity: "brief", model_size: "medium", keep_alive_minutes: 10,
    reply_format: "markdown", confidence_style: "numeric", batch_delay_ms: 0,
    match_prompt: "", explain_prompt: "", pack_prompt: "",
    match_enabled: true, explain_enabled: true, match_model: "", explain_model: "",
    second_opinion_enabled: false, second_opinion_model: "",
    breaker_threshold: 5, breaker_cooldown_minutes: 10,
    temperature: 0, max_tokens: 0, timeout_seconds: 0,
    forbid_thinking: true, compact_det_summary: true,
    ...(cfg ?? {}),
    ...(presetProfile ?? {}), // a chosen model preset overrides the profile fields
    enabled, host, model, api_style: apiStyle,
  });

  const applyPreset = (idx: number) => {
    const p = MODEL_PRESETS[idx];
    setUseOther(false);
    setModel(p.model);
    setPresetProfile(p.profile);
    setPresetWarning(models.length > 0 && !models.some(m => m === p.model || m.startsWith(p.model + ":"))
      ? `${p.model} isn't on the Ollama host yet — pull it first (ollama pull ${p.model})`
      : null);
  };

  const benchmark = async () => {
    setBenching(true);
    setBenchResult(null);
    try {
      await settingsApi.updateOllama(buildPayload()); // dry run uses saved config
      const r = await settingsApi.ollamaPreview("match", false);
      setBenchResult(`${(r.latency_ms / 1000).toFixed(1)}s — ${r.json_valid
        ? "verdict parsed ✓ (model handles structured matching)"
        : "verdict did NOT parse ✗ — model may be too small; try Minimal verbosity or Classified confidence"}`);
    } catch (e: unknown) {
      setBenchResult(e instanceof Error ? e.message : String(e));
    } finally { setBenching(false); }
  };

  const saveMut = useMutation({
    mutationFn: () => settingsApi.updateOllama(buildPayload()),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ollama-settings"] });
      if (host) refetchModels();
    },
  });

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      await settingsApi.updateOllama(buildPayload()); // test runs against saved config
      const r = await ollamaApi.test();
      setTestResult(r);
    } catch (e: unknown) {
      setTestResult({ ok: false, message: e instanceof Error ? e.message : String(e) });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="bg-surface-raised rounded-xl border border-purple-900/30 p-5">
      <div className="flex items-center gap-3 mb-4">
        <span className={`w-2.5 h-2.5 rounded-full ${enabled ? "bg-green-400" : "bg-slate-600"}`} />
        <div className="px-2 py-0.5 rounded text-xs font-bold text-white bg-indigo-600 flex items-center gap-1">
          <Bot size={12} /> Ollama
        </div>
        <span className="text-slate-500 text-xs">Optional: local-LLM assist for import-match confidence — everything works without it</span>
        <label className="ml-auto flex items-center gap-2 text-sm text-slate-400 cursor-pointer">
          <input
            type="checkbox"
            checked={enabled}
            onChange={e => setEnabled(e.target.checked)}
            className="accent-purple-500"
          />
          Enabled
        </label>
      </div>

      <div className="space-y-3">
        <div className="flex gap-3">
          <div className="flex-1">
            <label className="text-xs text-slate-400 mb-1 block">Host</label>
            <input
              type="text"
              placeholder="10.1.1.x:11434"
              value={host}
              onChange={e => setHost(e.target.value)}
              className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white placeholder:text-slate-600"
            />
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1 block">API Style</label>
            <select
              value={apiStyle}
              onChange={e => setApiStyle(e.target.value)}
              className="bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white"
            >
              <option value="ollama">Ollama native</option>
              <option value="openai">OpenAI-compatible</option>
            </select>
          </div>
        </div>
        <div>
          <label className="text-xs text-slate-400 mb-1 block">Model</label>
          <div className="flex gap-2">
            <select
              value={dropdownValue}
              onChange={e => {
                if (e.target.value === OTHER) { setUseOther(true); setModel(""); }
                else { setUseOther(false); setModel(e.target.value); }
              }}
              className="flex-1 bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white"
            >
              <option value="">— select a model —</option>
              {models.map(m => <option key={m} value={m}>{m}</option>)}
              {!useOther && model && models.length === 0 && <option value={model}>{model}</option>}
              <option value={OTHER}>Other…</option>
            </select>
            <button
              onClick={() => refetchModels()}
              disabled={loadingModels || !host}
              title="Load model list from the saved Ollama host"
              className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-surface-overlay hover:bg-white/10 text-slate-300 text-sm transition-colors disabled:opacity-40"
            >
              <RefreshCw size={13} className={loadingModels ? "animate-spin" : ""} />
              Load Models
            </button>
          </div>
          {modelsResp && !modelsResp.ok && (
            <p className="text-xs text-red-400 mt-1">{modelsResp.message}</p>
          )}
          {dropdownValue === OTHER && (
            <input
              type="text"
              placeholder="model name, e.g. llama3.2:3b"
              value={model}
              onChange={e => setModel(e.target.value)}
              className="w-full mt-2 bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white placeholder:text-slate-600"
            />
          )}
        </div>
        <div>
          <label className="text-xs text-slate-400 mb-1 block">Known-good small-model presets</label>
          <select
            value=""
            onChange={e => { if (e.target.value !== "") applyPreset(Number(e.target.value)); }}
            className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white"
          >
            <option value="">Pick a preset to fill model + tuned profile (size, verbosity, confidence style)…</option>
            {MODEL_PRESETS.map((p, i) => <option key={p.model} value={i}>{p.label}</option>)}
          </select>
          {presetProfile && !presetWarning && (
            <p className="text-xs text-slate-500 mt-1">Preset profile will apply on Save: {Object.entries(presetProfile).map(([k, v]) => `${k}=${v}`).join(", ")}</p>
          )}
          {presetWarning && <p className="text-xs text-amber-400 mt-1">{presetWarning}</p>}
        </div>
      </div>

      <div className="flex items-center flex-wrap gap-2 mt-4">
        <button
          onClick={handleTest}
          disabled={testing || !host}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-surface-overlay hover:bg-white/10 text-slate-300 text-sm transition-colors disabled:opacity-40"
        >
          {testing ? <Loader2 size={13} className="animate-spin" /> : null}
          Test
        </button>
        <button
          onClick={benchmark}
          disabled={benching || !host || !model}
          title="Save, then send a tiny fixed match prompt to measure latency and check the reply parses — nothing is stored"
          className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-surface-overlay hover:bg-white/10 text-slate-300 text-sm transition-colors disabled:opacity-40"
        >
          {benching ? <Loader2 size={13} className="animate-spin" /> : null}
          Benchmark Model
        </button>
        <button
          onClick={() => saveMut.mutate()}
          disabled={saveMut.isPending}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-brand hover:bg-brand-dark text-white text-sm transition-colors disabled:opacity-40"
        >
          <Save size={13} />
          Save
        </button>
        {testResult && (
          <div className={`flex items-center gap-1.5 text-sm ml-1 ${testResult.ok ? "text-green-400" : "text-red-400"}`}>
            {testResult.ok ? <CheckCircle size={14} /> : <XCircle size={14} />}
            {testResult.message}
          </div>
        )}
        {benchResult && <span className="text-sm text-slate-300 ml-1">{benchResult}</span>}
      </div>
    </div>
  );
}
