import { useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Save, AlertTriangle, Lock, Bell, Send, Bot, Wand2, Play, Clock, DatabaseBackup, Activity, RotateCcw, Plug, SlidersHorizontal, Music } from "lucide-react";
import { Skeleton } from "../../../components/Skeleton";
import { settingsApi, mediaApi, authApi, importsApi, fmtBytes, fmtDate, type ScoringWeights, type ScoringProfiles,
         type ImportMatchingSettings, type CleanupSettings, type SyncSettings, type NotificationSettings,
         type OllamaSettings, type LlmPolicies, type LlmAppOverride, type LlmLibraryOverride,
         type LlmScheduleSettings, type BackupSettings, type BackupFile, type SettingsExport } from "../../../lib/api";

export function CleanupSection() {
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

      <label className="py-4 border-b border-purple-900/20 flex items-center justify-between cursor-pointer">
        <div>
          <p className="text-white text-sm font-medium">Protect Other Users' Watches</p>
          <p className="text-slate-500 text-xs mt-0.5">
            Hide items another Tautulli user watched within N days (refreshed on each Plex sync). Requires Tautulli enabled.
          </p>
        </div>
        <input
          type="checkbox"
          checked={cfg.protect_other_users}
          onChange={e => setCfg(c => c ? { ...c, protect_other_users: e.target.checked } : c)}
          className="accent-purple-500 ml-6"
        />
      </label>
      {cfg.protect_other_users && (
        <>
          <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
            <div>
              <p className="text-white text-sm font-medium">Other-User Watch Window</p>
              <p className="text-slate-500 text-xs mt-0.5">Days of Tautulli history to protect</p>
            </div>
            <div className="flex items-center gap-2 ml-6">
              <input
                type="number" min={1} max={365} step={1}
                value={cfg.other_user_watch_days}
                onChange={e => setCfg(c => c ? { ...c, other_user_watch_days: Number(e.target.value) } : c)}
                className="w-24 bg-surface border border-purple-900/40 rounded px-2 py-1 text-sm text-white text-right"
              />
              <span className="text-slate-500 text-xs">days</span>
            </div>
          </div>
          <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
            <div>
              <p className="text-white text-sm font-medium">Primary Tautulli User</p>
              <p className="text-slate-500 text-xs mt-0.5">
                Friendly name whose watches do <em>not</em> protect (your own). Leave blank to protect every user's watches.
              </p>
            </div>
            <input
              type="text"
              placeholder="e.g. Powers"
              value={cfg.primary_tautulli_user}
              onChange={e => setCfg(c => c ? { ...c, primary_tautulli_user: e.target.value } : c)}
              className="w-40 bg-surface border border-purple-900/40 rounded px-2 py-1 text-sm text-white ml-6"
            />
          </div>
        </>
      )}

      <label className="py-4 border-b border-purple-900/20 flex items-center justify-between cursor-pointer">
        <div>
          <p className="text-white text-sm font-medium">Protect Actively-Seeding Torrents</p>
          <p className="text-slate-500 text-xs mt-0.5">
            Hide items whose file lives inside a torrent still seeding in qBittorrent/Transmission (refreshed on each Plex sync). Requires a download client enabled.
          </p>
        </div>
        <input
          type="checkbox"
          checked={cfg.protect_seeding_torrents}
          onChange={e => setCfg(c => c ? { ...c, protect_seeding_torrents: e.target.checked } : c)}
          className="accent-purple-500 ml-6"
        />
      </label>

      <label className="py-4 border-b border-purple-900/20 flex items-center justify-between cursor-pointer">
        <div>
          <p className="text-white text-sm font-medium">Protect In-Progress Watches</p>
          <p className="text-slate-500 text-xs mt-0.5">
            Hide items you've started but not finished, per Tautulli watch history (refreshed on each Plex sync). Requires Tautulli enabled.
          </p>
        </div>
        <input
          type="checkbox"
          checked={cfg.protect_in_progress}
          onChange={e => setCfg(c => c ? { ...c, protect_in_progress: e.target.checked } : c)}
          className="accent-purple-500 ml-6"
        />
      </label>
      {cfg.protect_in_progress && (
        <>
          <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
            <div>
              <p className="text-white text-sm font-medium">In-Progress Range</p>
              <p className="text-slate-500 text-xs mt-0.5">
                Watch completion band that counts as "in progress" — below the minimum is barely started, at or above the maximum is essentially finished
              </p>
            </div>
            <div className="flex items-center gap-2 ml-6">
              <input
                type="number" min={0} max={100} step={1}
                value={cfg.in_progress_min_percent}
                onChange={e => setCfg(c => c ? { ...c, in_progress_min_percent: Number(e.target.value) } : c)}
                className="w-20 bg-surface border border-purple-900/40 rounded px-2 py-1 text-sm text-white text-right"
              />
              <span className="text-slate-500 text-xs">to</span>
              <input
                type="number" min={0} max={100} step={1}
                value={cfg.in_progress_max_percent}
                onChange={e => setCfg(c => c ? { ...c, in_progress_max_percent: Number(e.target.value) } : c)}
                className="w-20 bg-surface border border-purple-900/40 rounded px-2 py-1 text-sm text-white text-right"
              />
              <span className="text-slate-500 text-xs">%</span>
            </div>
          </div>
          <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
            <div>
              <p className="text-white text-sm font-medium">In-Progress Lookback Window</p>
              <p className="text-slate-500 text-xs mt-0.5">Days of Tautulli history to check for in-progress watches</p>
            </div>
            <div className="flex items-center gap-2 ml-6">
              <input
                type="number" min={1} max={365} step={1}
                value={cfg.in_progress_lookback_days}
                onChange={e => setCfg(c => c ? { ...c, in_progress_lookback_days: Number(e.target.value) } : c)}
                className="w-24 bg-surface border border-purple-900/40 rounded px-2 py-1 text-sm text-white text-right"
              />
              <span className="text-slate-500 text-xs">days</span>
            </div>
          </div>
        </>
      )}

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

export function SyncSection() {
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

export function BackupSection() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["backup-settings"], queryFn: settingsApi.getBackup });
  const { data: files } = useQuery({ queryKey: ["backup-files"], queryFn: settingsApi.listBackups });
  const [cfg, setCfg] = useState<BackupSettings | null>(null);
  const [saved, setSaved] = useState(false);
  const [running, setRunning] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => { if (data) setCfg(data); }, [data]);

  const save = async () => {
    if (!cfg) return;
    setMsg(null);
    try {
      await settingsApi.updateBackup(cfg);
      qc.invalidateQueries({ queryKey: ["backup-settings"] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: unknown) { setMsg(e instanceof Error ? e.message : String(e)); }
  };

  const runNow = async () => {
    setRunning(true);
    setMsg(null);
    try {
      const r = await settingsApi.runBackupNow();
      setMsg(r.message);
      qc.invalidateQueries({ queryKey: ["backup-files"] });
    } catch (e: unknown) { setMsg(e instanceof Error ? e.message : String(e)); }
    finally { setRunning(false); }
  };

  if (!cfg) return null;

  return (
    <div className="bg-surface-raised rounded-xl border border-purple-900/30 px-6 mt-6">
      <div className="flex items-center gap-2 pt-5 pb-3">
        <DatabaseBackup size={14} className="text-brand-light" />
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">Automated Backups</h2>
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
        Scheduled `pg_dump` (or a plain file copy on the SQLite fallback) to <code>/config/backups</code>, on top of the manual flow in Docker &amp; Deployment.
      </p>

      <label className="py-4 border-b border-purple-900/20 flex items-center justify-between cursor-pointer">
        <div>
          <p className="text-white text-sm font-medium">Enable scheduled backups</p>
        </div>
        <input type="checkbox" checked={cfg.enabled} className="accent-purple-500 ml-6"
               onChange={e => setCfg(c => c ? { ...c, enabled: e.target.checked } : c)} />
      </label>

      <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
        <div>
          <p className="text-white text-sm font-medium">Interval</p>
        </div>
        <div className="flex items-center gap-2 ml-6">
          <input type="number" min={1} value={cfg.interval_hours}
                 onChange={e => setCfg(c => c ? { ...c, interval_hours: Number(e.target.value) } : c)}
                 className="w-20 bg-surface border border-purple-900/40 rounded px-2 py-1.5 text-sm text-white" />
          <span className="text-slate-500 text-xs">hours</span>
        </div>
      </div>

      <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
        <div>
          <p className="text-white text-sm font-medium">Retention</p>
          <p className="text-slate-500 text-xs mt-0.5">Keep the most recent N backup files (0 = unlimited)</p>
        </div>
        <input type="number" min={0} value={cfg.retention_count}
               onChange={e => setCfg(c => c ? { ...c, retention_count: Number(e.target.value) } : c)}
               className="w-20 bg-surface border border-purple-900/40 rounded px-2 py-1.5 text-sm text-white ml-6" />
      </div>

      <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
        <div>
          <p className="text-white text-sm font-medium">Run Backup Now</p>
        </div>
        <button
          onClick={runNow}
          disabled={running}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-700 hover:bg-indigo-600 text-white text-sm transition-colors ml-6 disabled:opacity-50"
        >
          <Play size={14} />
          {running ? "Running…" : "Run Now"}
        </button>
      </div>

      <div className="py-4">
        <p className="text-white text-sm font-medium mb-2">Recent Backups</p>
        {!files || files.length === 0 ? (
          <p className="text-slate-500 text-xs">No backups yet.</p>
        ) : (
          <div className="space-y-1">
            {files.slice(0, 10).map(f => (
              <div key={f.name} className="flex items-center justify-between text-xs text-slate-400 py-1 border-b border-purple-900/10 last:border-0">
                <span className="font-mono truncate">{f.name}</span>
                <span className="flex-shrink-0 ml-4">{fmtBytes(f.size)} — {fmtDate(f.modified)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// OPS-02 — config-as-code settings export/import, deliberately separate from
// BackupSection above: a JSON settings snapshot (sans secrets) is a much
// smaller, safer, diffable artifact than a full DB dump, for disaster
// recovery or standing up a second instance.
export function ConfigExportSection() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["backup-settings"], queryFn: settingsApi.getBackup });
  const { data: files } = useQuery({ queryKey: ["settings-export-files"], queryFn: settingsApi.listSettingsExports });
  const [cfg, setCfg] = useState<BackupSettings | null>(null);
  const [saved, setSaved] = useState(false);
  const [running, setRunning] = useState(false);
  const [importing, setImporting] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { if (data) setCfg(data); }, [data]);

  const save = async () => {
    if (!cfg) return;
    setMsg(null);
    try {
      await settingsApi.updateBackup(cfg);
      qc.invalidateQueries({ queryKey: ["backup-settings"] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: unknown) { setMsg(e instanceof Error ? e.message : String(e)); }
  };

  const runNow = async () => {
    setRunning(true);
    setMsg(null);
    try {
      const r = await settingsApi.runSettingsExportNow();
      setMsg(r.message);
      qc.invalidateQueries({ queryKey: ["settings-export-files"] });
    } catch (e: unknown) { setMsg(e instanceof Error ? e.message : String(e)); }
    finally { setRunning(false); }
  };

  const handleImportFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setImporting(true);
    setMsg(null);
    try {
      const text = await file.text();
      const payload = JSON.parse(text) as SettingsExport;
      const r = await settingsApi.importSettings(payload);
      setMsg(`Imported ${r.app_settings_imported} setting(s), updated ${r.integrations_updated} integration(s). Reload the page to see the new values.`);
    } catch (err: unknown) {
      setMsg(err instanceof Error ? `Import failed: ${err.message}` : String(err));
    } finally {
      setImporting(false);
    }
  };

  if (!cfg) return null;

  return (
    <div className="bg-surface-raised rounded-xl border border-purple-900/30 px-6 mt-6">
      <div className="flex items-center gap-2 pt-5 pb-3">
        <DatabaseBackup size={14} className="text-brand-light" />
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">Config-as-Code (Settings Export)</h2>
      </div>
      {msg && <p className="text-xs text-slate-300 pb-2">{msg}</p>}
      <p className="text-slate-500 text-xs pb-3">
        A JSON snapshot of every setting plus integration URLs — <strong>never credentials</strong> (api keys/passwords are
        never exported; re-enter them after an import). For disaster recovery or standing up a second instance.
      </p>

      <div className="py-4 border-b border-purple-900/20 flex items-center justify-between gap-3">
        <div>
          <p className="text-white text-sm font-medium">Export / Import</p>
          <p className="text-slate-500 text-xs mt-0.5">Download the current settings, or restore from a previously exported file</p>
        </div>
        <div className="flex items-center gap-2 ml-6 flex-shrink-0">
          <button
            onClick={settingsApi.exportSettings}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-surface-overlay hover:bg-white/10 text-slate-300 text-sm transition-colors"
          >
            <DatabaseBackup size={13} /> Export
          </button>
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={importing}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-surface-overlay hover:bg-white/10 text-slate-300 text-sm transition-colors disabled:opacity-50"
          >
            {importing ? "Importing…" : "Import"}
          </button>
          <input ref={fileInputRef} type="file" accept="application/json" className="hidden" onChange={handleImportFile} />
        </div>
      </div>

      <label className="py-4 border-b border-purple-900/20 flex items-center justify-between cursor-pointer">
        <div>
          <p className="text-white text-sm font-medium">Enable scheduled settings export</p>
          <p className="text-slate-500 text-xs mt-0.5">Alongside the DB backup above, same interval</p>
        </div>
        <input type="checkbox" checked={cfg.export_settings_enabled} className="accent-purple-500 ml-6"
               onChange={e => setCfg(c => c ? { ...c, export_settings_enabled: e.target.checked } : c)} />
      </label>

      <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
        <div>
          <p className="text-white text-sm font-medium">Retention</p>
          <p className="text-slate-500 text-xs mt-0.5">Keep the most recent N export files (0 = unlimited)</p>
        </div>
        <input type="number" min={0} value={cfg.export_settings_retention_count}
               onChange={e => setCfg(c => c ? { ...c, export_settings_retention_count: Number(e.target.value) } : c)}
               className="w-20 bg-surface border border-purple-900/40 rounded px-2 py-1.5 text-sm text-white ml-6" />
      </div>

      <div className="py-4 border-b border-purple-900/20 flex items-center justify-between">
        <div>
          <p className="text-white text-sm font-medium">Run Settings Export Now</p>
        </div>
        <div className="flex items-center gap-3 ml-6">
          <button
            onClick={save}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-brand text-white hover:bg-brand-dark text-sm transition-colors"
          >
            <Save size={13} />
            {saved ? "Saved!" : "Save"}
          </button>
          <button
            onClick={runNow}
            disabled={running}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-700 hover:bg-indigo-600 text-white text-sm transition-colors disabled:opacity-50"
          >
            <Play size={14} />
            {running ? "Running…" : "Run Now"}
          </button>
        </div>
      </div>

      <div className="py-4">
        <p className="text-white text-sm font-medium mb-2">Recent Exports</p>
        {!files || files.length === 0 ? (
          <p className="text-slate-500 text-xs">No settings exports yet.</p>
        ) : (
          <div className="space-y-1">
            {files.slice(0, 10).map(f => (
              <div key={f.name} className="flex items-center justify-between text-xs text-slate-400 py-1 border-b border-purple-900/10 last:border-0">
                <span className="font-mono truncate">{f.name}</span>
                <span className="flex-shrink-0 ml-4">{fmtBytes(f.size)} — {fmtDate(f.modified)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

