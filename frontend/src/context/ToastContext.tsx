import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { CheckCircle2, AlertCircle, Info, X } from "lucide-react";

type Kind = "success" | "error" | "info";
interface ToastAction { label: string; onClick: () => void; }
interface Toast { id: number; kind: Kind; message: string; action?: ToastAction; }

interface ToastApi {
  success: (message: string) => void;
  error: (message: string) => void;
  info: (message: string) => void;
  /** Success toast with an Undo-style button; lingers longer so it can be used. */
  undoable: (message: string, onUndo: () => void) => void;
}

const ToastContext = createContext<ToastApi>({ success() {}, error() {}, info() {}, undoable() {} });
export const useToast = () => useContext(ToastContext);

const ICONS = { success: CheckCircle2, error: AlertCircle, info: Info } as const;
const COLORS = {
  success: "border-green-500/40 text-green-300",
  error: "border-red-500/40 text-red-300",
  info: "border-purple-500/40 text-brand-light",
} as const;

// Errors linger longer so they can be read; all dismiss on click.
const TTL: Record<Kind, number> = { success: 3500, info: 3500, error: 8000 };

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => setToasts(t => t.filter(x => x.id !== id)), []);

  const push = useCallback((kind: Kind, message: string, action?: ToastAction) => {
    const id = nextId.current++;
    setToasts(t => [...t.slice(-3), { id, kind, message, action }]);
    setTimeout(() => dismiss(id), action ? 8000 : TTL[kind]);
  }, [dismiss]);

  const api = useMemo<ToastApi>(() => ({
    success: m => push("success", m),
    error: m => push("error", m),
    info: m => push("info", m),
    undoable: (m, onUndo) => push("success", m, { label: "Undo", onClick: onUndo }),
  }), [push]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        role="status"
        aria-live="polite"
        className="fixed top-4 right-4 z-[60] flex flex-col gap-2 w-[min(22rem,calc(100vw-2rem))] pointer-events-none"
      >
        {toasts.map(t => {
          const Icon = ICONS[t.kind];
          return (
            <div
              key={t.id}
              className={`tray-card-enter pointer-events-auto flex items-start gap-2 rounded-lg border bg-surface-raised px-3 py-2.5 text-sm shadow-xl ${COLORS[t.kind]}`}
            >
              <Icon size={16} className="mt-0.5 flex-shrink-0" />
              <span className="flex-1 text-slate-200 break-words">{t.message}</span>
              {t.action && (
                <button
                  onClick={() => { t.action!.onClick(); dismiss(t.id); }}
                  className="rounded px-2 py-0.5 text-xs font-semibold text-brand-light hover:bg-white/10"
                >
                  {t.action.label}
                </button>
              )}
              <button onClick={() => dismiss(t.id)} aria-label="Dismiss" className="text-slate-500 hover:text-white">
                <X size={14} />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}
