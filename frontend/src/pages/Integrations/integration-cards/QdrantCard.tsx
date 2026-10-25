import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle, XCircle, Loader2, Save, RefreshCw, Sparkles } from "lucide-react";
import { req } from "../../../lib/api";


export function QdrantCard() {
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
