import { Link, useLocation } from "react-router-dom";
import { areaForPath, screenForPath } from "../lib/navConfig";

export default function Breadcrumb() {
  const location = useLocation();
  const area = areaForPath(location.pathname);
  if (!area) return null;
  const screen = screenForPath(area, location.pathname);
  // Show the sub-screen segment only when it's a distinct screen from the area
  // itself (single-screen areas like Overview/Settings/Logs render just the area).
  const showScreen = !!screen && screen.label !== area.label;

  const crumbCls =
    "text-[11px] font-semibold uppercase tracking-wider text-slate-500 transition-colors";

  return (
    <nav aria-label="Breadcrumb" className="px-4 sm:px-8 pt-4 sm:pt-6 flex items-center gap-1.5">
      {/* Area links back to its first screen; when we're already on a bare area
          screen this is a no-op click, but it stays a real affordance for sub-screens. */}
      <Link to={area.screens[0].path} className={`${crumbCls} hover:text-brand-light`}>
        {area.label}
      </Link>
      {showScreen && (
        <>
          <span className={`${crumbCls} text-slate-600`}>›</span>
          <span className={`${crumbCls} text-slate-400`}>{screen!.label}</span>
        </>
      )}
    </nav>
  );
}
