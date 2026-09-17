import { useEffect, useRef, useState } from "react";
import { Compass, Loader2, RefreshCw, RotateCw, Trash2, Users } from "lucide-react";
import { useTasks } from "../context/TaskContext";
import type { TaskProgress } from "../lib/api";
import BotState from "./BotState";

const AUTO_DISMISS_MS = 4000; // "always auto-dismiss" — success or failure alike, per user decision 2026-07-07

// llm_run gets the animated BotState (color/motion carries the status itself)
// — everything else gets a plain Lucide icon that spins while running.
const KIND_ICON: Record<Exclude<TaskProgress["kind"], "llm_run">, React.ElementType> = {
  scan: RefreshCw,
  plex_sync: RotateCw,
  deletion: Trash2,
  import_batch: Loader2, // spinning loader — checkmark read as "done", not in-progress
  artist_discovery: Compass, // same icon as the Music → Artist Discovery page header
  related_search: Users, // same icon as the Music → Related Artists page header
};

function TaskIcon({ task }: { task: TaskProgress }) {
  if (task.kind === "llm_run") {
    const variant = task.status === "failed" ? "error" : task.status === "done" ? "complete" : "thinking";
    return <BotState variant={variant} size={18} />;
  }
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
          <span className="text-slate-500 text-[11px] flex-shrink-0 tabular-nums">{task.current ?? 0}/{task.total}</span>
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
  // Ids that should play the slide-up enter animation (cleared after animation ends).
  const [entering, setEntering] = useState<Set<string>>(new Set());
  const timers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  const known = useRef<Set<string>>(new Set());

  useEffect(() => {
    const fresh: string[] = [];
    for (const task of tasks) {
      if (!known.current.has(task.id)) {
        known.current.add(task.id);
        fresh.push(task.id);
      }
      if (task.status !== "running" && !dismissed.has(task.id) && !timers.current.has(task.id)) {
        const t = setTimeout(() => {
          setDismissed(prev => new Set(prev).add(task.id));
          timers.current.delete(task.id);
          known.current.delete(task.id);
        }, AUTO_DISMISS_MS);
        timers.current.set(task.id, t);
      }
      // A still-running card that was somehow marked dismissed (e.g. total bump
      // after a premature finish) should reappear.
      if (task.status === "running" && timers.current.has(task.id)) {
        clearTimeout(timers.current.get(task.id)!);
        timers.current.delete(task.id);
      }
      if (task.status === "running" && dismissed.has(task.id)) {
        setDismissed(prev => {
          if (!prev.has(task.id)) return prev;
          const next = new Set(prev);
          next.delete(task.id);
          return next;
        });
      }
    }
    if (fresh.length) {
      setEntering(prev => {
        const next = new Set(prev);
        for (const id of fresh) next.add(id);
        return next;
      });
      // Drop the enter class after the CSS animation finishes so later
      // current/total updates don't re-trigger it.
      const clear = setTimeout(() => {
        setEntering(prev => {
          const next = new Set(prev);
          for (const id of fresh) next.delete(id);
          return next;
        });
      }, 250);
      return () => clearTimeout(clear);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tasks]);

  useEffect(() => () => { for (const t of timers.current.values()) clearTimeout(t); }, []);

  // Newest at the bottom (closest to the corner); older cards slide up as new
  // ones appear. flex-col-reverse puts the first DOM child at the bottom.
  const visible = [...tasks]
    .filter(t => !dismissed.has(t.id))
    .sort((a, b) => a.started_at - b.started_at);

  if (visible.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col-reverse gap-2 pointer-events-none">
      {visible.map(task => (
        <div
          key={task.id}
          className={`pointer-events-auto ${entering.has(task.id) ? "tray-card-enter" : ""}`}
        >
          <TaskCard task={task} />
        </div>
      ))}
    </div>
  );
}
