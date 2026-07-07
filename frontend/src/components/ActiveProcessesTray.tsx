import { useEffect, useRef, useState } from "react";
import { RefreshCw, RotateCw, Trash2 } from "lucide-react";
import { useTasks } from "../context/TaskContext";
import type { TaskProgress } from "../lib/api";
import AnimatedBot from "./AnimatedBot";

const AUTO_DISMISS_MS = 4000; // "always auto-dismiss" — success or failure alike, per user decision 2026-07-07

// llm_run gets the animated robot (needs an `active` prop, not a bare icon
// render) — everything else gets a plain lucide icon that spins while running.
const KIND_ICON: Record<Exclude<TaskProgress["kind"], "llm_run">, React.ElementType> = {
  scan: RefreshCw,
  plex_sync: RotateCw,
  deletion: Trash2,
};

function TaskIcon({ task }: { task: TaskProgress }) {
  if (task.kind === "llm_run") return <AnimatedBot active={task.status === "running"} size={15} />;
  const Icon = KIND_ICON[task.kind];
  return <Icon size={15} className={task.status === "running" ? "animate-spin" : ""} />;
}

function TaskCard({ task }: { task: TaskProgress }) {
  const pct = task.total && task.total > 0 ? Math.min(100, Math.round(((task.current ?? 0) / task.total) * 100)) : null;
  const borderColor = task.status === "failed" ? "border-red-700/60" : task.status === "done" ? "border-green-700/40" : "border-purple-900/40";

  return (
    <div className={`bg-surface-raised border ${borderColor} rounded-lg shadow-xl px-3 py-2.5 w-72 transition-colors`}>
      <div className="flex items-center gap-2">
        <span className={task.status === "failed" ? "text-red-400" : task.status === "done" ? "text-green-400" : "text-brand-light"}>
          <TaskIcon task={task} />
        </span>
        <span className="text-white text-xs font-medium truncate flex-1">{task.label}</span>
        {task.total !== null && (
          <span className="text-slate-500 text-[11px] flex-shrink-0">{task.current ?? 0}/{task.total}</span>
        )}
      </div>
      {task.message && (
        <p className={`text-[11px] mt-1 truncate ${task.status === "failed" ? "text-red-300" : "text-slate-400"}`}>
          {task.message}
        </p>
      )}
      <div className="h-1 bg-surface rounded-full overflow-hidden mt-2">
        {task.status !== "running" ? (
          <div className={`h-full rounded-full ${task.status === "failed" ? "bg-red-600" : "bg-green-600"}`} style={{ width: "100%" }} />
        ) : pct !== null ? (
          <div className="h-full bg-brand rounded-full transition-all duration-300" style={{ width: `${pct}%` }} />
        ) : (
          <div className="h-full w-1/3 bg-brand rounded-full progress-indeterminate" />
        )}
      </div>
    </div>
  );
}

export default function ActiveProcessesTray() {
  const tasks = useTasks();
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const timers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  useEffect(() => {
    for (const task of tasks) {
      if (task.status !== "running" && !dismissed.has(task.id) && !timers.current.has(task.id)) {
        const t = setTimeout(() => {
          setDismissed(prev => new Set(prev).add(task.id));
          timers.current.delete(task.id);
        }, AUTO_DISMISS_MS);
        timers.current.set(task.id, t);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tasks]);

  useEffect(() => () => { for (const t of timers.current.values()) clearTimeout(t); }, []);

  const visible = tasks.filter(t => !dismissed.has(t.id));
  if (visible.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col-reverse gap-2 pointer-events-none">
      {visible.map(task => (
        <div key={task.id} className="pointer-events-auto">
          <TaskCard task={task} />
        </div>
      ))}
    </div>
  );
}
