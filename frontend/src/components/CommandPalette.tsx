import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Search, CornerDownLeft, Zap } from "lucide-react";
import { AREAS, LOGS_AREA } from "../lib/navConfig";
import Kbd from "./Kbd";
import { useToast } from "../context/ToastContext";
import { setTheme } from "../lib/useTheme";
import { OPEN_SHORTCUTS_EVENT } from "./ShortcutHelp";
import { importsApi, integrationsApi, settingsApi } from "../lib/api";

interface Entry {
  path: string;
  label: string;
  area: string;
  /** Present on action entries: runs instead of navigating. */
  run?: (toast: ReturnType<typeof useToast>) => Promise<void>;
}

// One-shot, user-initiated maintenance actions. Each reports its outcome via toast.
const ACTIONS: Entry[] = [
  {
    path: "action:toggle-theme", area: "Action", label: "Toggle light / dark theme",
    run: async () => { setTheme(document.documentElement.classList.contains("light") ? "dark" : "light"); },
  },
  {
    path: "action:shortcuts", area: "Action", label: "Keyboard shortcuts",
    run: async () => { window.dispatchEvent(new Event(OPEN_SHORTCUTS_EVENT)); },
  },
  {
    path: "action:scan-imports", area: "Action", label: "Scan failed imports now",
    run: async t => { await importsApi.scan(); t.success("Import scan started"); },
  },
  {
    path: "action:sync-plex", area: "Action", label: "Sync library from media server",
    run: async t => { const r = await integrationsApi.syncLibrary(); t.success(`Library sync done (${r.synced} items)`); },
  },
  {
    path: "action:sync-seerr", area: "Action", label: "Sync Seerr requests",
    run: async t => { const r = await integrationsApi.syncSeerr(); t.success(`Seerr sync done (${r.protected} protected)`); },
  },
  {
    path: "action:backup", area: "Action", label: "Run backup now",
    run: async t => { const r = await settingsApi.runBackupNow(); (r.ok ? t.success : t.error)(r.message || "Backup finished"); },
  },
];

const ENTRIES: Entry[] = [
  ...[...AREAS, LOGS_AREA].flatMap(a =>
    a.screens.map(s => ({ path: s.path, label: s.label, area: a.label }))
  ),
  ...ACTIONS,
];

// Subsequence match so "dsug" finds "Deletion Suggestions"; contiguous hits rank first.
function score(entry: Entry, q: string): number {
  const hay = `${entry.area} ${entry.label}`.toLowerCase();
  if (!q) return 1;
  const idx = hay.indexOf(q);
  if (idx >= 0) return 1000 - idx;
  let i = 0;
  for (const ch of hay) if (ch === q[i]) i++;
  return i === q.length ? 1 : 0;
}

export default function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const navigate = useNavigate();
  const toast = useToast();
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen(o => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActive(0);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    return ENTRIES
      .map(e => ({ e, s: score(e, q) }))
      .filter(r => r.s > 0)
      .sort((a, b) => b.s - a.s)
      .map(r => r.e);
  }, [query]);

  if (!open) return null;

  const go = (entry: Entry | undefined) => {
    if (!entry) return;
    setOpen(false);
    if (entry.run) {
      entry.run(toast).catch(e => toast.error(e instanceof Error ? e.message : "Action failed"));
    } else {
      navigate(entry.path);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") setOpen(false);
    else if (e.key === "ArrowDown") { e.preventDefault(); setActive(a => Math.min(a + 1, results.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive(a => Math.max(a - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); go(results[active]); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh] px-4" onKeyDown={onKeyDown}>
      <div className="absolute inset-0 bg-black/60" onClick={() => setOpen(false)} />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Go to page or run action"
        className="relative w-full max-w-lg rounded-xl border border-purple-900/50 bg-surface-raised shadow-2xl overflow-hidden"
      >
        <div className="flex items-center gap-3 px-4 py-3 border-b border-purple-900/40">
          <Search size={16} className="text-slate-500" />
          <input
            ref={inputRef}
            value={query}
            onChange={e => { setQuery(e.target.value); setActive(0); }}
            placeholder="Go to page or run action…"
            aria-label="Search pages and actions"
            className="flex-1 bg-transparent text-sm text-white placeholder-slate-500 outline-none"
          />
          <Kbd>Esc</Kbd>
        </div>
        <ul role="listbox" className="max-h-72 overflow-y-auto py-1">
          {results.length === 0 && <li className="px-4 py-6 text-center text-sm text-slate-500">No matching pages</li>}
          {results.map((r, i) => (
            <li
              key={r.path}
              role="option"
              aria-selected={i === active}
              onMouseEnter={() => setActive(i)}
              onClick={() => go(r)}
              className={`flex items-center justify-between px-4 py-2 text-sm cursor-pointer ${
                i === active ? "bg-brand/20 text-white" : "text-slate-300"
              }`}
            >
              <span>
                {r.run && <Zap size={12} className="inline mr-1.5 text-brand-light" />}
                <span className="text-slate-500">{r.area}</span>
                {r.label !== r.area && <span> › {r.label}</span>}
              </span>
              {i === active && <CornerDownLeft size={13} className="text-brand-light" />}
            </li>
          ))}
        </ul>
        <div className="px-4 py-2 border-t border-purple-900/40 text-[11px] text-slate-500 flex gap-3">
          <span><Kbd>↑</Kbd> <Kbd>↓</Kbd> move</span>
          <span><Kbd>↵</Kbd> open</span>
          <span className="ml-auto"><Kbd>Ctrl</Kbd>+<Kbd>K</Kbd> toggle</span>
        </div>
      </div>
    </div>
  );
}
