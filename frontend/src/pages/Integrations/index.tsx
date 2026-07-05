import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle, XCircle, Loader2, Save, RefreshCw, Bot } from "lucide-react";
import { integrationsApi, settingsApi, ollamaApi, type IntegrationConfig } from "../../lib/api";

const INTEGRATION_META: Record<string, { label: string; color: string; description: string }> = {
  plex: { label: "Plex", color: "bg-yellow-600", description: "Media server — required for library sync" },
  tautulli: { label: "Tautulli", color: "bg-blue-600", description: "Optional: enriched watch history" },
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
  const [apiKey, setApiKey] = useState(cfg.api_key ?? "");
  const [username, setUsername] = useState(cfg.username ?? "");
  const [password, setPassword] = useState(cfg.password ?? "");
  const [enabled, setEnabled] = useState(cfg.enabled);
  const [removeMonitored, setRemoveMonitored] = useState(cfg.remove_from_monitored_on_delete);
  const [deleteFromList, setDeleteFromList] = useState(cfg.delete_from_arr_list);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string; version: string | null } | null>(null);
  const [testing, setTesting] = useState(false);

  const isQbit = cfg.name === "qbittorrent";

  const saveMut = useMutation({
    mutationFn: () =>
      integrationsApi.update(cfg.name, {
        url, enabled,
        ...(isQbit ? { username, password } : { api_key: apiKey }),
        remove_from_monitored_on_delete: removeMonitored,
        delete_from_arr_list: deleteFromList,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["integrations"] }),
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
                placeholder="••••••••••••••••"
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
              placeholder="••••••••••••••••"
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
    verbosity: "brief", match_prompt: "", explain_prompt: "",
    ...(cfg ?? {}),
    enabled, host, model, api_style: apiStyle,
  });

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

export default function IntegrationsPage() {
  const { data: integrations = [], isLoading } = useQuery({
    queryKey: ["integrations"],
    queryFn: integrationsApi.list,
  });

  const order = ["plex", "tautulli", "radarr", "sonarr", "lidarr", "readarr", "seerr", "qbittorrent", "transmission"];
  const sorted = [...integrations].sort(
    (a, b) => order.indexOf(a.name) - order.indexOf(b.name)
  );

  return (
    <div className="p-8 max-w-2xl">
      <h1 className="text-2xl font-bold text-white mb-1">Integrations</h1>
      <p className="text-slate-400 text-sm mb-8">Connect Powarr to your media stack</p>

      {isLoading ? (
        <p className="text-slate-400">Loading…</p>
      ) : (
        <div className="space-y-4">
          {sorted.map(cfg => <IntegrationCard key={cfg.name} cfg={cfg} />)}
          <OllamaCard />
        </div>
      )}
    </div>
  );
}
