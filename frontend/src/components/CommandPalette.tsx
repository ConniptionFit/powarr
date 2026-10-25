import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Search, CornerDownLeft } from "lucide-react";
import { AREAS, LOGS_AREA } from "../lib/navConfig";
import Kbd from "./Kbd";

interface Entry { path: string; label: string; area: string; }

const ENTRIES: Entry[] = [...AREAS, LOGS_AREA].flatMap(a =>
  a.screens.map(s => ({ path: s.path, label: s.label, area: a.label }))
);

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
    navigate(entry.path);
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
        aria-label="Go to page"
        className="relative w-full max-w-lg rounded-xl border border-purple-900/50 bg-surface-raised shadow-2xl overflow-hidden"
      >
        <div className="flex items-center gap-3 px-4 py-3 border-b border-purple-900/40">
          <Search size={16} className="text-slate-500" />
          <input
            ref={inputRef}
            value={query}
            onChange={e => { setQuery(e.target.value); setActive(0); }}
            placeholder="Go to page…"
            aria-label="Search pages"
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
