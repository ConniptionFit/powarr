import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle, XCircle, Loader2, Save, RefreshCw, Bot, Sparkles } from "lucide-react";
import { integrationsApi, settingsApi, ollamaApi, type IntegrationConfig, req } from "../../lib/api";

const INTEGRATION_META: Record<string, { label: string; color: string; description: string }> = {
  plex: { label: "Plex", color: "bg-yellow-600", description: "Media server — required for library sync" },
  tautulli: { label: "Tautulli", color: "bg-blue-600", description: "Optional: watch history + multi-user deletion protection" },
  sonarr: { label: "Sonarr", color: "bg-teal-600", description: "TV show management" },
  radarr: { label: "Radarr", color: "bg-amber-600", description: "Movie management" },
  lidarr: { label: "Lidarr", color: "bg-pink-600", description: "Music management" },
  readarr: { label: "Readarr", color: "bg-orange-700", description: "Book management — failed-import matching" },
  seerr: { label: "Seerr", color: "bg-purple-700", description: "Request management — protects requested media from deletion" },
  qbittorrent: { label: "qBittorrent", color: "bg-sky-700", description: "Download client — WebUI username & password" },
  transmission: { label: "Transmission", color: "bg-red-800", description: "Download client — API key field takes username:password" },
};

function IntegrationCard({ cfg }: { cfg: IntegrationConfig }) {
  const qc = useQueryClient();
  const meta = INTEGRATION_META[cfg.name] ?? { label: cfg.name, color: "bg-slate-600", description: "" };

  const [url, setUrl] = useState(cfg.url ?? "");
  // Secret fields start blank — the stored key/password is never sent to the
  // browser (masked server-side). Blank on save = leave the stored secret as-is.
  const [apiKey, setApiKey] = useState("");
  const [username, setUsername] = useState(cfg.username ?? "");
  const [password, setPassword] = useState("");
  const [enabled, setEnabled] = useState(cfg.enabled);
  const [removeMonitored, setRemoveMonitored] = useState(cfg.remove_from_monitored_on_delete);
  const [deleteFromList, setDeleteFromList] = useState(cfg.delete_from_arr_list);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string; version: string | null } | null>(null);
  const [testing, setTesting] = useState(false);

  const isQbit = cfg.name === "qbittorrent";

  const saveMut = useMutation({
    // Only send a secret when the user actually typed one — an empty field means
    // "keep the stored secret", so a URL-only edit never touches the key/password.
    mutationFn: () =>
      integrationsApi.update(cfg.name, {
        url, enabled,
        ...(isQbit
          ? { username, ...(password ? { password } : {}) }
          : (apiKey ? { api_key: apiKey } : {})),
        remove_from_monitored_on_delete: removeMonitored,
        delete_from_arr_list: deleteFromList,
      }),
    onSuccess: () => {
      setApiKey("");
      setPassword("");
      qc.invalidateQueries({ queryKey: ["integrations"] });
    },
  });

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const r = await integrationsApi.test(cfg.name);
      setTestResult(r);
    } catch (e: unknown) {
      setTestResult({ ok: false, message: e instanceof Error ? e.message : String(e), version: null });
    } finally {
      setTesting(false);
    }
  };

  const isArr = ["sonarr", "radarr", "lidarr"].includes(cfg.name);
  const isPlex = cfg.name === "plex";

  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState<string | null>(null);

  const handleSync = async () => {
    setSyncing(true);
    setSyncResult(null);
    try {
      const result = await integrationsApi.syncPlex();
      setSyncResult(`Synced ${result.synced} items`);
      qc.invalidateQueries({ queryKey: ["media"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
    } catch (e: unknown) {
      setSyncResult(`Failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className="bg-surface-raised rounded-xl border border-purple-900/30 p-5">
      <div className="flex items-center gap-3 mb-4">
        <span className={`w-2.5 h-2.5 rounded-full ${enabled ? "bg-green-400" : "bg-slate-600"}`} />
        <div className={`px-2 py-0.5 rounded text-xs font-bold text-white ${meta.color}`}>{meta.label}</div>
        <span className="text-slate-500 text-xs">{meta.description}</span>
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
        <div>
          <label className="text-xs text-slate-400 mb-1 block">URL</label>
          <input
            type="text"
            placeholder={`http://${cfg.name}:port`}
            value={url}
            onChange={e => setUrl(e.target.value)}
            className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white placeholder:text-slate-600"
          />
        </div>
        {isQbit ? (
          <div className="flex gap-3">
            <div className="flex-1">
              <label className="text-xs text-slate-400 mb-1 block">Username</label>
              <input
                type="text"
                placeholder="WebUI username"
                value={username}
                onChange={e => setUsername(e.target.value)}
                className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white placeholder:text-slate-600"
              />
            </div>
            <div className="flex-1">
              <label className="text-xs text-slate-400 mb-1 block">Password</label>
              <input
                type="password"
                placeholder={cfg.password_set ? "•••• saved — leave blank to keep" : "WebUI password"}
                value={password}
                onChange={e => setPassword(e.target.value)}
                className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white placeholder:text-slate-600"
              />
            </div>
          </div>
        ) : (
          <div>
            <label className="text-xs text-slate-400 mb-1 block">API Key</label>
            <input
              type="password"
              placeholder={cfg.api_key_set ? "•••• saved — leave blank to keep" : "API key"}
              value={apiKey}
              onChange={e => setApiKey(e.target.value)}
              className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white placeholder:text-slate-600"
            />
          </div>
        )}

        {isArr && (
          <div className="pt-2 space-y-2 border-t border-purple-900/20">
            <p className="text-xs text-slate-500 uppercase tracking-wider">On Delete Behavior</p>
            <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
              <input type="checkbox" checked={removeMonitored} onChange={e => setRemoveMonitored(e.target.checked)} className="accent-purple-500" />
              Remove from monitored
            </label>
            <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
              <input type="checkbox" checked={deleteFromList} onChange={e => setDeleteFromList(e.target.checked)} className="accent-purple-500" />
              Delete from {meta.label} entirely
            </label>
          </div>
        )}
      </div>

      <div className="flex items-center flex-wrap gap-2 mt-4">
        <button
          onClick={handleTest}
          disabled={testing || !url || (isQbit ? !username : !apiKey)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-surface-overlay hover:bg-white/10 text-slate-300 text-sm transition-colors disabled:opacity-40"
        >
          {testing ? <Loader2 size={13} className="animate-spin" /> : null}
          Test
        </button>
        <button
          onClick={() => saveMut.mutate()}
          disabled={saveMut.isPending}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-brand hover:bg-brand-dark text-white text-sm transition-colors disabled:opacity-40"
        >
          <Save size={13} />
          Save
        </button>
        {isPlex && (
          <button
            onClick={handleSync}
            disabled={syncing || !enabled}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-yellow-700 hover:bg-yellow-600 text-white text-sm transition-colors disabled:opacity-40"
          >
            <RefreshCw size={13} className={syncing ? "animate-spin" : ""} />
            {syncing ? "Syncing…" : "Sync Library"}
          </button>
        )}
        {testResult && (
          <div className={`flex items-center gap-1.5 text-sm ml-1 ${testResult.ok ? "text-green-400" : "text-red-400"}`}>
            {testResult.ok ? <CheckCircle size={14} /> : <XCircle size={14} />}
            {testResult.ok ? `Connected${testResult.version ? ` (v${testResult.version})` : ""}` : testResult.message}
          </div>
        )}
        {syncResult && (
          <span className={`text-sm ml-1 ${syncResult.startsWith("Failed") ? "text-red-400" : "text-green-400"}`}>
            {syncResult}
          </span>
        )}
      </div>
    </div>
  );
}

const OTHER = "__other__";

// Known-good small models with pre-tuned profiles (mirrors PROMPT_PRESETS'
// pattern). Selecting one fills the model and applies the profile in one click —
// the model still has to be pulled on the Ollama host (warned if absent).
const MODEL_PRESETS: Array<{ label: string; model: string;
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

function OllamaCard() {
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

function LastFmCard({ cfg }: { cfg: IntegrationConfig }) {
  const qc = useQueryClient();
  const [username, setUsername] = useState(cfg.username ?? "");
  const [apiKey, setApiKey] = useState("");
  const [enabled, setEnabled] = useState(cfg.enabled);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string; version: string | null } | null>(null);
  const [testing, setTesting] = useState(false);

  const saveMut = useMutation({
    mutationFn: () =>
      integrationsApi.update("lastfm", {
        username, enabled,
        ...(apiKey ? { api_key: apiKey } : {}),
      }),
    onSuccess: () => {
      setApiKey("");
      qc.invalidateQueries({ queryKey: ["integrations"] });
    },
  });

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      setTestResult(await integrationsApi.test("lastfm"));
    } catch (e: unknown) {
      setTestResult({ ok: false, message: e instanceof Error ? e.message : String(e), version: null });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="bg-surface-raised rounded-xl border border-purple-900/30 p-5">
      <div className="flex items-center gap-3 mb-4">
        <span className={`w-2.5 h-2.5 rounded-full ${enabled ? "bg-green-400" : "bg-slate-600"}`} />
        <div className="px-2 py-0.5 rounded text-xs font-bold text-white bg-red-700">Last.fm</div>
        <span className="text-slate-500 text-xs">Scrobble history + related-artist graph for Artist Discovery</span>
      </div>

      <div className="flex gap-3">
        <div className="flex-1">
          <label className="text-xs text-slate-400 mb-1 block">Username</label>
          <input
            type="text"
            value={username}
            onChange={e => setUsername(e.target.value)}
            className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white"
          />
        </div>
        <div className="flex-1">
          <label className="text-xs text-slate-400 mb-1 block">API Key</label>
          <input
            type="password"
            placeholder={cfg.api_key_set ? "•••• saved — leave blank to keep" : "Last.fm API key"}
            value={apiKey}
            onChange={e => setApiKey(e.target.value)}
            className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white placeholder:text-slate-600"
          />
        </div>
      </div>

      <label className="flex items-center gap-2 text-sm text-slate-300 mt-3">
        <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
        Enabled
      </label>

      <div className="flex items-center flex-wrap gap-2 mt-4">
        <button
          onClick={handleTest}
          disabled={testing || !username}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-surface-overlay hover:bg-white/10 text-slate-300 text-sm transition-colors disabled:opacity-40"
        >
          {testing ? <Loader2 size={13} className="animate-spin" /> : null}
          Test Connection
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
      </div>
    </div>
  );
}

function QdrantCard() {
  const qc = useQueryClient();
  const { data: cfg } = useQuery({ queryKey: ["qdrant-settings"], queryFn: () => req<any>("/integrations/qdrant/settings") });
  const [qdrantUrl, setQdrantUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [collection, setCollection] = useState("music_affinity_space");
  const [apiKeySet, setApiKeySet] = useState(false);
  const [testResult, setTestResult] = useState<any>(null);
  const [testing, setTesting] = useState(false);
  const [syncResult, setSyncResult] = useState<any>(null);

  useEffect(() => {
    if (cfg) {
      setQdrantUrl(cfg.url || "");
      setCollection(cfg.collection || "music_affinity_space");
      setApiKeySet(cfg.api_key_set || false);
    }
  }, [cfg]);

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const r = await req<any>("/integrations/qdrant/test", { method: "POST" });
      setTestResult(r);
    } catch (e: unknown) {
      setTestResult({ ok: false, message: e instanceof Error ? e.message : String(e) });
    } finally {
      setTesting(false);
    }
  };

  const saveMut = useMutation({
    mutationFn: () => req("/integrations/qdrant/settings", {
      method: "PUT",
      body: JSON.stringify({ url: qdrantUrl, collection, api_key: apiKey || undefined }),
    }),
    onSuccess: () => {
      setApiKey("");
      qc.invalidateQueries({ queryKey: ["qdrant-settings"] });
    },
  });

  const fullSyncMut = useMutation({
    mutationFn: () => req<any>("/integrations/qdrant/full-sync", { method: "POST" }),
    onSuccess: r => setSyncResult(r),
    onError: (e: unknown) => setSyncResult({ ok: false, message: e instanceof Error ? e.message : String(e) }),
  });

  return (
    <div className="bg-surface-raised rounded-xl border border-purple-900/30 p-5">
      <div className="flex items-center gap-3 mb-4">
        <span className={`w-2.5 h-2.5 rounded-full ${qdrantUrl ? "bg-green-400" : "bg-slate-600"}`} />
        <div className="px-2 py-0.5 rounded text-xs font-bold text-white bg-violet-600 flex items-center gap-1">
          <Sparkles size={12} /> Qdrant
        </div>
        <span className="text-slate-500 text-xs">Shared vector DB connection for Artist Discovery and Smart Playlists</span>
      </div>

      <div className="space-y-3">
        <div>
          <label className="text-xs text-slate-400 mb-1 block">Qdrant URL</label>
          <input
            type="text"
            placeholder="http://10.1.1.x:6333"
            value={qdrantUrl}
            onChange={e => setQdrantUrl(e.target.value)}
            className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white placeholder:text-slate-600"
          />
        </div>
        <div className="flex gap-3">
          <div className="flex-1">
            <label className="text-xs text-slate-400 mb-1 block">Collection</label>
            <input
              type="text"
              value={collection}
              onChange={e => setCollection(e.target.value)}
              className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white"
            />
          </div>
          <div className="flex-1">
            <label className="text-xs text-slate-400 mb-1 block">API Key (optional)</label>
            <input
              type="password"
              placeholder={apiKeySet ? "•••• saved — leave blank to keep" : "API key (if needed)"}
              value={apiKey}
              onChange={e => setApiKey(e.target.value)}
              className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white placeholder:text-slate-600"
            />
          </div>
        </div>
      </div>

      {testResult && testResult.collection_info && (
        <div className="mt-4 pt-4 border-t border-purple-900/20 space-y-2">
          <p className="text-xs text-slate-500 uppercase tracking-wider">Collection Info</p>
          <div className="bg-surface rounded px-3 py-2 text-xs text-slate-300 space-y-1">
            <div>Points: {testResult.collection_info.points_count}</div>
            {testResult.sample_artist && <div>Sample artist: {testResult.sample_artist}</div>}
            {testResult.sample_payload_keys && testResult.sample_payload_keys.length > 0 && (
              <div>Metadata fields: {testResult.sample_payload_keys.join(", ")}</div>
            )}
          </div>
        </div>
      )}

      <div className="flex items-center flex-wrap gap-2 mt-4">
        <button
          onClick={handleTest}
          disabled={testing || !qdrantUrl}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-surface-overlay hover:bg-white/10 text-slate-300 text-sm transition-colors disabled:opacity-40"
        >
          {testing ? <Loader2 size={13} className="animate-spin" /> : null}
          Test Connection
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
      </div>

      <div className="mt-4 pt-4 border-t border-purple-900/20">
        <div className="flex items-center flex-wrap gap-2">
          <button
            onClick={() => { setSyncResult(null); fullSyncMut.mutate(); }}
            disabled={fullSyncMut.isPending || !qdrantUrl}
            title="Manually resync every point in the collection against current Lidarr/Last.fm state. Not scheduled — only runs when clicked."
            className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-surface-overlay hover:bg-white/10 text-slate-300 text-sm transition-colors disabled:opacity-40"
          >
            {fullSyncMut.isPending ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
            Full Sync
          </button>
          <span className="text-slate-500 text-xs">Manual only — resyncs every point against Lidarr/Last.fm</span>
          {syncResult && (
            <div className={`flex items-center gap-1.5 text-sm ml-1 ${syncResult.ok ? "text-green-400" : "text-red-400"}`}>
              {syncResult.ok ? <CheckCircle size={14} /> : <XCircle size={14} />}
              {syncResult.message}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function IntegrationsPage({ embedded = false }: { embedded?: boolean }) {
  const { data: integrations = [], isLoading } = useQuery({
    queryKey: ["integrations"],
    queryFn: integrationsApi.list,
  });

  const order = ["plex", "tautulli", "radarr", "sonarr", "lidarr", "readarr", "seerr", "qbittorrent", "transmission"];
  const sorted = [...integrations]
    .filter(cfg => cfg.name !== "lastfm")
    .sort((a, b) => order.indexOf(a.name) - order.indexOf(b.name));
  const lastfm = integrations.find(cfg => cfg.name === "lastfm");

  const body = isLoading ? (
    <p className="text-slate-400">Loading…</p>
  ) : (
    <div className="space-y-4">
      {sorted.map(cfg => <IntegrationCard key={cfg.name} cfg={cfg} />)}
      <QdrantCard />
      {lastfm && <LastFmCard cfg={lastfm} />}
      <OllamaCard />
    </div>
  );

  if (embedded) return <div className="max-w-2xl">{body}</div>;

  return (
    <div className="p-4 sm:p-8 max-w-2xl">
      <h1 className="text-2xl font-bold text-white mb-1">Integrations</h1>
      <p className="text-slate-400 text-sm mb-8">Connect Powarr to your media stack</p>
      {body}
    </div>
  );
}
