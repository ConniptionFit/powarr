import { useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Save, AlertTriangle, Lock, Bell, Send, Bot, Wand2, Play, Clock, DatabaseBackup, Activity, RotateCcw, Plug, SlidersHorizontal, Music } from "lucide-react";
import { Skeleton } from "../../../components/Skeleton";
import { settingsApi, mediaApi, authApi, importsApi, fmtBytes, fmtDate, type ScoringWeights, type ScoringProfiles,
         type ImportMatchingSettings, type CleanupSettings, type SyncSettings, type NotificationSettings,
         type OllamaSettings, type LlmPolicies, type LlmAppOverride, type LlmLibraryOverride,
         type LlmScheduleSettings, type BackupSettings, type BackupFile, type SettingsExport } from "../../../lib/api";

export function NotificationsSection() {
  const { data } = useQuery({ queryKey: ["notification-settings"], queryFn: settingsApi.getNotifications });
  const [cfg, setCfg] = useState<NotificationSettings | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => { if (data) setCfg(data); }, [data]);

  if (!cfg) return null;

  return (
    <div className="bg-surface-raised rounded-xl border border-purple-900/30 px-6 mt-6">
      <div className="flex items-center gap-2 pt-5 pb-3">
        <Bell size={14} className="text-brand-light" />
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">Notifications (ntfy + Discord)</h2>
        {msg && <span className="text-xs text-slate-300 ml-auto">{msg}</span>}
      </div>

      <div className="py-4 space-y-3">
        <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
          <input type="checkbox" checked={cfg.enabled}
                 onChange={e => setCfg(c => c ? { ...c, enabled: e.target.checked } : c)}
                 className="accent-purple-500" />
          Push a summary when a scan finds new suggestions, auto-resolves, or push failures
        </label>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
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
        <div className="pt-2 border-t border-purple-900/20">
          <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
            <input type="checkbox" checked={cfg.actionable_new_suggestions}
                   onChange={e => setCfg(c => c ? { ...c, actionable_new_suggestions: e.target.checked } : c)}
                   className="accent-purple-500" />
            Send an Accept/Reject actionable notification per new suggestion
          </label>
          <p className="text-slate-500 text-xs mt-1 mb-2">
            Adds click-to-act buttons via signed one-time links (7-day expiry). Needs a URL the ntfy client can reach — falls back to the aggregate summary above if blank, or if a scan produces more than the max below.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-slate-400 mb-1 block">Public Base URL</label>
              <input type="text" placeholder="https://powarr.pwrs.dev" value={cfg.public_base_url}
                     onChange={e => setCfg(c => c ? { ...c, public_base_url: e.target.value } : c)}
                     className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white placeholder:text-slate-600" />
            </div>
            <div>
              <label className="text-xs text-slate-400 mb-1 block">Max Actionable per Scan</label>
              <input type="number" min={1} value={cfg.actionable_max_per_scan}
                     onChange={e => setCfg(c => c ? { ...c, actionable_max_per_scan: Number(e.target.value) } : c)}
                     className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white" />
            </div>
          </div>
        </div>
        <div className="pt-2 border-t border-purple-900/20">
          <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
            <input type="checkbox" checked={cfg.digest_enabled}
                   onChange={e => setCfg(c => c ? { ...c, digest_enabled: e.target.checked } : c)}
                   className="accent-purple-500" />
            Weekly digest summary
          </label>
          <p className="text-slate-500 text-xs mt-1 mb-2">
            One ntfy push per week summarizing recent activity — pick which sections to include below.
          </p>
          {cfg.digest_enabled && (
            <>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
                <div>
                  <label className="text-xs text-slate-400 mb-1 block">Weekday (UTC)</label>
                  <select value={cfg.digest_weekday}
                          onChange={e => setCfg(c => c ? { ...c, digest_weekday: Number(e.target.value) } : c)}
                          className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white">
                    {["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].map((d, i) => (
                      <option key={d} value={i}>{d}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-slate-400 mb-1 block">Hour (UTC, 0–23)</label>
                  <input type="number" min={0} max={23} value={cfg.digest_hour_utc}
                         onChange={e => setCfg(c => c ? { ...c, digest_hour_utc: Number(e.target.value) } : c)}
                         className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white" />
                </div>
              </div>
              <div>
                <label className="text-xs text-slate-400 mb-1.5 block">Include in digest</label>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                  {([
                    ["digest_include_imports", "Imports"],
                    ["digest_include_artists", "Added artists"],
                    ["digest_include_playlists", "Created playlists"],
                    ["digest_include_cleanup", "Files cleaned up"],
                  ] as const).map(([key, label]) => (
                    <label key={key} className="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer">
                      <input type="checkbox" checked={cfg[key]}
                             onChange={e => setCfg(c => c ? { ...c, [key]: e.target.checked } : c)}
                             className="accent-purple-500" />
                      {label}
                    </label>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
        <div className="pt-2 border-t border-purple-900/20">
          <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
            <input type="checkbox" checked={cfg.discord_enabled}
                   onChange={e => setCfg(c => c ? { ...c, discord_enabled: e.target.checked } : c)}
                   className="accent-purple-500" />
            Discord webhook (NOTIF-01) — same events, alongside ntfy
          </label>
          <p className="text-slate-500 text-xs mt-1 mb-2">
            Sends the same scan summary / actionable-suggestion / weekly-digest notifications to a Discord channel via an Incoming Webhook. Accept/Reject show as clickable links in the embed rather than native buttons (Discord webhooks have no interactive-component support).
          </p>
          <div>
            <label className="text-xs text-slate-400 mb-1 block">Webhook URL</label>
            <input type="password" placeholder={cfg.discord_webhook_url_set ? "•••••••••••••••• (leave blank to keep)" : "https://discord.com/api/webhooks/..."}
                   value={cfg.discord_webhook_url}
                   onChange={e => setCfg(c => c ? { ...c, discord_webhook_url: e.target.value } : c)}
                   className="w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white placeholder:text-slate-600" />
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

