import { createContext, useContext, useEffect, useRef, useState } from "react";
import { tasksApi, type TaskProgress } from "../lib/api";
import { subscribeEvents } from "../lib/eventBus";

// One shared subscription for the whole app (mounted once in App.tsx) so every
// page can read "what's currently running" without opening its own EventSource
// or prop-drilling. All consumers share one connection via lib/eventBus. Task
// events ride the same /imports/events bus as everything else (see tasks.py) —
// filtered here to "type === task_update" only.
const TaskContext = createContext<Map<string, TaskProgress>>(new Map());

export function useTasks(): TaskProgress[] {
  return Array.from(useContext(TaskContext).values());
}

export function TaskProvider({ children }: { children: React.ReactNode }) {
  const [tasks, setTasks] = useState<Map<string, TaskProgress>>(new Map());
  const hydrated = useRef(false);

  useEffect(() => {
    if (!hydrated.current) {
      hydrated.current = true;
      tasksApi.list().then(initial => {
        if (initial.length) setTasks(prev => {
          const next = new Map(prev);
          for (const t of initial) next.set(t.id, t);
          return next;
        });
      }).catch(() => { /* fail soft — tray just starts empty */ });
    }

    return subscribeEvents(data => {
      if (data?.type === "task_update") {
        const task: TaskProgress = data.task;
        setTasks(prev => new Map(prev).set(task.id, task));
      }
    });
  }, []);

  return <TaskContext.Provider value={tasks}>{children}</TaskContext.Provider>;
}
