import { useEffect, useState } from "react";
import { X } from "lucide-react";
import Modal from "./Modal";
import Kbd from "./Kbd";

const GROUPS: { title: string; rows: [string[], string][] }[] = [
  {
    title: "Everywhere",
    rows: [
      [["Ctrl", "K"], "Command palette (go to page or run action)"],
      [["?"], "Show this cheat sheet"],
      [["Esc"], "Close dialog or palette"],
    ],
  },
  {
    title: "Match Review",
    rows: [
      [["j", "k"], "Next / previous item"],
      [["x"], "Select item"],
      [["e"], "Expand item"],
      [["y"], "Accept match"],
      [["n"], "Reject"],
    ],
  },
  {
    title: "Deletion Suggestions",
    rows: [
      [["j", "k"], "Next / previous row"],
      [["x"], "Select row"],
      [["d"], "Preview deletion"],
      [["i"], "Ignore / unignore"],
    ],
  },
];

function isTyping(t: EventTarget | null) {
  const el = t as HTMLElement | null;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
}

export const OPEN_SHORTCUTS_EVENT = "powarr:shortcuts";

export default function ShortcutHelp() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "?" && !e.ctrlKey && !e.metaKey && !isTyping(e.target)) {
        e.preventDefault();
        setOpen(o => !o);
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener(OPEN_SHORTCUTS_EVENT, onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener(OPEN_SHORTCUTS_EVENT, onOpen);
    };
  }, []);

  if (!open) return null;
  return (
    <Modal onClose={() => setOpen(false)} label="Keyboard shortcuts" className="w-full max-w-md max-h-[85vh] flex flex-col">
      <div className="flex items-center justify-between px-5 py-3 border-b border-purple-900/40">
        <h2 className="text-sm font-semibold text-white">Keyboard shortcuts</h2>
        <button onClick={() => setOpen(false)} aria-label="Close" className="text-slate-500 hover:text-white">
          <X size={16} />
        </button>
      </div>
      <div className="overflow-y-auto px-5 py-4 space-y-5">
        {GROUPS.map(g => (
          <section key={g.title}>
            <h3 className="text-[11px] uppercase tracking-wide text-slate-500 mb-2">{g.title}</h3>
            <ul className="space-y-1.5">
              {g.rows.map(([keys, desc]) => (
                <li key={desc} className="flex items-center justify-between gap-4 text-sm text-slate-300">
                  <span>{desc}</span>
                  <span className="flex gap-1 flex-shrink-0">{keys.map(k => <Kbd key={k}>{k}</Kbd>)}</span>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </Modal>
  );
}
