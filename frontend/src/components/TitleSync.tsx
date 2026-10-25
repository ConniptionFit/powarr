import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import { AREAS, LOGS_AREA } from "../lib/navConfig";

// Keeps the browser tab title in step with the current screen ("Duplicates · Powarr"),
// so multiple tabs and the history list are distinguishable.
export default function TitleSync() {
  const { pathname } = useLocation();
  useEffect(() => {
    const areas = [...AREAS, LOGS_AREA];
    const screens = areas.flatMap(a => a.screens.map(s => ({ ...s, area: a })));
    const exact = screens.find(s => s.path === pathname);
    const area = areas.filter(a => a.base !== "/").find(a => pathname.startsWith(a.base));
    const label = exact?.label ?? area?.label;
    document.title = label ? `${label} · Powarr` : "Powarr";
  }, [pathname]);
  return null;
}
