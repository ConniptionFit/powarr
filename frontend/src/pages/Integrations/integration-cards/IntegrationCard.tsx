import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle, XCircle, Loader2, Save, RefreshCw } from "lucide-react";
import { integrationsApi, type IntegrationConfig } from "../../../lib/api";


export const INTEGRATION_META: Record<string, { label: string; color: string; description: string }> = {
  plex: { label: "Plex", color: "bg-yellow-600", description: "Media server — required for library sync" },
  jellyfin: { label: "Jellyfin", color: "bg-violet-700", description: "Alternative media server (read-only library sync). Use Plex or Jellyfin as your source, not both" },
  tautulli: { label: "Tautulli", color: "bg-blue-600", description: "Optional: watch history + multi-user deletion protection" },
  sonarr: { label: "Sonarr", color: "bg-teal-600", description: "TV show management" },
  radarr: { label: "Radarr", color: "bg-amber-600", description: "Movie management" },
  lidarr: { label: "Lidarr", color: "bg-pink-600", description: "Music management" },
  readarr: { label: "Readarr", color: "bg-orange-700", description: "Book management — failed-import matching" },
  seerr: { label: "Seerr", color: "bg-purple-700", description: "Request management — protects requested media from deletion" },
  qbittorrent: { label: "qBittorrent", color: "bg-sky-700", description: "Download client — WebUI username & password" },
  transmission: { label: "Transmission", color: "bg-red-800", description: "Download client — API key field takes username:password" },
  bazarr: { label: "Bazarr", color: "bg-indigo-700", description: "Subtitles — INT-01: warns about known subtitles before you delete a linked movie/series (read-only, never deletes on Bazarr's side)" },
};

export function IntegrationCard({ cfg }: { cfg: IntegrationConfig }) {
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
  const isJellyfin = cfg.name === "jellyfin";

  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState<string | null>(null);

  const handleSync = async () => {
    setSyncing(true);
    setSyncResult(null);
    try {
      const result = isJellyfin ? await integrationsApi.syncJellyfin() : await integrationsApi.syncPlex();
      const warning = "warning" in result && result.warning ? ` (${result.warning})` : "";
      setSyncResult(`Synced ${result.synced} items${warning}`);
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
        {(isPlex || isJellyfin) && (
          <button
            onClick={handleSync}
            disabled={syncing || !enabled}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-brand hover:bg-brand-dark text-white text-sm transition-colors disabled:opacity-40"
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
