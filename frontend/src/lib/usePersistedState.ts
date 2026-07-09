import { useEffect, useState } from "react";

/**
 * useState persisted to localStorage (v0.27.0, Approved Queue #11) — the same
 * per-browser persistence the Failed Imports column layout already uses,
 * generalized so tab/filter/sort choices survive a reload. JSON-serialized;
 * a missing or unparsable stored value falls back to the initial value.
 */
export function usePersistedState<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      if (raw !== null) return JSON.parse(raw) as T;
    } catch { /* fall through to the default */ }
    return initial;
  });
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch { /* private mode / quota — persistence is best-effort */ }
  }, [key, value]);
  return [value, setValue] as const;
}
