import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle, XCircle, Loader2, Save } from "lucide-react";
import { integrationsApi, type IntegrationConfig } from "../../../lib/api";
import { IntegrationCard } from "./IntegrationCard";
import { LastFmCard } from "./LastFmCard";

// AD-18 — Spotify Web API via Client Credentials (app-level token, no user
// OAuth login). Client ID lives in username, Client Secret in api_key — same
// combo precedent as LastFmCard, since this doesn't fit the generic
// URL+single-key IntegrationCard either.
export function SpotifyCard({ cfg }: { cfg: IntegrationConfig }) {
  const qc = useQueryClient();
  const [clientId, setClientId] = useState(cfg.username ?? "");
  const [clientSecret, setClientSecret] = useState("");
  const [enabled, setEnabled] = useState(cfg.enabled);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string; version: string | null } | null>(null);
  const [testing, setTesting] = useState(false);

  const saveMut = useMutation({
    mutationFn: () =>
      integrationsApi.update("spotify", {
        username: clientId, enabled,
        ...(clientSecret ? { api_key: clientSecret } : {}),
      }),
    onSuccess: () => {
      setClientSecret("");
      qc.invalidateQueries({ queryKey: ["integrations"] });
    },
  });

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      setTestResult(await integrationsApi.test("spotify"));
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
        <div className="px-2 py-0.5 rounded text-xs font-bold text-white bg-green-700">Spotify</div>
        <span className="text-slate-500 text-xs">
          Optional 30s track preview source. Spotify has scaled back preview availability for newer API apps; a missing preview isn't an error
        </span>
      </div>

      <div className="flex gap-3">
        <div className="flex-1">
          <label className="text-xs text-slate-400 mb-1 block">Client ID</label>
          <input type="text" value={clientId} onChange={e => setClientId(e.target.value)}
            className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white" />
        </div>
        <div className="flex-1">
          <label className="text-xs text-slate-400 mb-1 block">Client Secret</label>
          <input type="password" placeholder={cfg.api_key_set ? "•••• saved — leave blank to keep" : "Spotify Developer Dashboard secret"}
            value={clientSecret} onChange={e => setClientSecret(e.target.value)}
            className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white placeholder:text-slate-600" />
        </div>
      </div>

      <label className="flex items-center gap-2 text-sm text-slate-300 mt-3">
        <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
        Search Spotify for artist previews (off by default — leave unchecked if you don't use Spotify)
      </label>

      <div className="flex items-center flex-wrap gap-2 mt-4">
        <button onClick={handleTest} disabled={testing || !clientId}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-surface-overlay hover:bg-white/10 text-slate-300 text-sm transition-colors disabled:opacity-40">
          {testing ? <Loader2 size={13} className="animate-spin" /> : null}
          Test Connection
        </button>
        <button onClick={() => saveMut.mutate()} disabled={saveMut.isPending}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-brand hover:bg-brand-dark text-white text-sm transition-colors disabled:opacity-40">
          <Save size={13} /> Save
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
