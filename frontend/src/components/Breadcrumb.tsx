import { useLocation } from "react-router-dom";
import { areaForPath, screenForPath } from "../lib/navConfig";

export default function Breadcrumb() {
  const location = useLocation();
  const area = areaForPath(location.pathname);
  if (!area) return null;
  const screen = screenForPath(area, location.pathname);
  const hasTabs = area.screens.length > 1;
  const suffix = !hasTabs && screen && screen.label !== area.label ? ` · ${screen.label}` : "";

  return (
    <div className="px-4 sm:px-8 pt-4 sm:pt-6">
      <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
        {area.label}
        {suffix}
      </span>
    </div>
  );
}
