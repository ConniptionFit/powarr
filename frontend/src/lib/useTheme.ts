import { useCallback, useEffect, useState } from "react";

export type Theme = "dark" | "light";
const KEY = "powarr.theme";

function initial(): Theme {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === "dark" || saved === "light") return saved;
  } catch { /* storage unavailable: fall through to the OS preference */ }
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function apply(theme: Theme) {
  document.documentElement.classList.toggle("light", theme === "light");
}

// Apply before first paint so a light-theme user never sees a dark flash.
export function initTheme() { apply(initial()); }

const listeners = new Set<(t: Theme) => void>();

export function setTheme(theme: Theme) {
  apply(theme);
  try { localStorage.setItem(KEY, theme); } catch { /* best-effort */ }
  listeners.forEach(l => l(theme));
}

export function useTheme() {
  const [theme, set] = useState<Theme>(() =>
    document.documentElement.classList.contains("light") ? "light" : "dark");
  useEffect(() => {
    listeners.add(set);
    return () => { listeners.delete(set); };
  }, []);
  const toggle = useCallback(() => setTheme(theme === "light" ? "dark" : "light"), [theme]);
  return { theme, toggle };
}
