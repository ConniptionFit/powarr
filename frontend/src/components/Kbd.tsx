import type { ReactNode } from "react";

// Inline keycap for keyboard-shortcut hint lines (UX-05 Match Review,
// UX-06 Deletion Suggestions — reuse for any future list-page shortcuts).
export default function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="px-1 py-px rounded border border-purple-900/50 bg-surface-raised text-slate-400 font-mono text-[10px]">
      {children}
    </kbd>
  );
}
