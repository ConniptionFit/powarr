import { useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Save, AlertTriangle, Lock, Bell, Send, Bot, Wand2, Play, Clock, DatabaseBackup, Activity, RotateCcw, Plug, SlidersHorizontal, Music } from "lucide-react";
import { Skeleton } from "../../../components/Skeleton";
import { settingsApi, mediaApi, authApi, importsApi, fmtBytes, fmtDate, type ScoringWeights, type ScoringProfiles,
         type ImportMatchingSettings, type CleanupSettings, type SyncSettings, type NotificationSettings,
         type OllamaSettings, type LlmPolicies, type LlmAppOverride, type LlmLibraryOverride,
         type LlmScheduleSettings, type BackupSettings, type BackupFile, type SettingsExport } from "../../../lib/api";

export function SecuritySection() {
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

