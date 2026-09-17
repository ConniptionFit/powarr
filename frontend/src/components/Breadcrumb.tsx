import { Link, useLocation } from "react-router-dom";
import { areaForPath } from "../lib/navConfig";

// The leaf-segment version of this (Area › Screen) was dropped: every
// multi-screen area also renders AreaTabs directly below with the active
// screen already highlighted there, and every single-screen area's screen
// label is identical to its area label — so the leaf never carried
// information AreaTabs (or the area name alone) didn't already show.
export default function Breadcrumb() {
  const location = useLocation();
  const area = areaForPath(location.pathname);
  if (!area) return null;

  return (
    <nav aria-label="Breadcrumb" className="px-4 sm:px-8 pt-4 sm:pt-6">
      {/* Links back to the area's first screen; a no-op click when already
          there, but a real affordance from any of its sub-screens. */}
      <Link
        to={area.screens[0].path}
        className="text-[11px] font-semibold uppercase tracking-wider text-slate-500 hover:text-brand-light transition-colors"
      >
        {area.label}
      </Link>
    </nav>
  );
}
