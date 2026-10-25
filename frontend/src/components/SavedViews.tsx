import { useEffect, useRef, useState } from "react";
import { Bookmark, Check, Trash2 } from "lucide-react";
import { usePersistedState } from "../lib/usePersistedState";
import { useToast } from "../context/ToastContext";

interface View<T> { name: string; values: T; }

/**
 * Named filter presets for a list page. `current` is a snapshot of the page's filter state;
 * `apply` pushes a saved snapshot back through the page's setters. Stored per browser.
 */
export default function SavedViews<T extends Record<string, unknown>>({
  scope, current, apply,
}: { scope: string; current: T; apply: (values: T) => void }) {
  const [views, setViews] = usePersistedState<View<T>[]>(`powarr.views.${scope}`, []);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const toast = useToast();

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);

  const save = () => {
    const n = name.trim();
    if (!n) return;
    setViews(vs => [...vs.filter(v => v.name !== n), { name: n, values: current }]);
    setName("");
    toast.success(`Saved view "${n}"`);
  };
  const matches = (v: View<T>) => JSON.stringify(v.values) === JSON.stringify(current);

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-purple-900/40 bg-surface-raised text-sm text-slate-300 hover:text-white"
      >
        <Bookmark size={14} /> Views{views.length > 0 && <span className="text-slate-500">({views.length})</span>}
      </button>
      {open && (
        <div className="absolute z-30 mt-1 w-64 rounded-lg border border-purple-900/50 bg-surface-raised shadow-xl p-2 space-y-2">
          {views.length === 0 && <p className="px-1 text-xs text-slate-500">No saved views yet. Set your filters, name them below, and save.</p>}
          <ul className="space-y-0.5">
            {views.map(v => (
              <li key={v.name} className="flex items-center gap-1">
                <button
                  onClick={() => { apply(v.values); setOpen(false); }}
                  className="flex-1 flex items-center gap-1.5 text-left px-2 py-1 rounded text-sm text-slate-300 hover:bg-white/5 truncate"
                >
                  {matches(v) ? <Check size={12} className="text-green-400" /> : <span className="w-3" />}
                  <span className="truncate">{v.name}</span>
                </button>
                <button
                  onClick={() => setViews(vs => vs.filter(x => x.name !== v.name))}
                  aria-label={`Delete view ${v.name}`}
                  className="p-1 text-slate-500 hover:text-red-400"
                >
                  <Trash2 size={13} />
                </button>
              </li>
            ))}
          </ul>
          <form onSubmit={e => { e.preventDefault(); save(); }} className="flex gap-1 border-t border-purple-900/40 pt-2">
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="Save current as…"
              aria-label="View name"
              className="flex-1 min-w-0 px-2 py-1 bg-transparent border border-purple-900/40 rounded text-sm text-white placeholder:text-slate-500"
            />
            <button type="submit" disabled={!name.trim()} className="px-2 py-1 rounded bg-brand text-white text-sm disabled:opacity-40">Save</button>
          </form>
        </div>
      )}
    </div>
  );
}
