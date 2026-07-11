import { NavLink, useLocation } from "react-router-dom";
import { areaForPath } from "../lib/navConfig";

export default function AreaTabs() {
  const location = useLocation();
  const area = areaForPath(location.pathname);
  if (!area || area.screens.length < 2) return null;

  return (
    <div className="flex items-center gap-1 border-b border-purple-900/40 px-4 sm:px-8">
      {area.screens.map(screen => (
        <NavLink
          key={screen.path}
          to={screen.path}
          className={({ isActive }) =>
            `px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors ${
              isActive
                ? "border-brand text-brand-light"
                : "border-transparent text-slate-400 hover:text-white"
            }`
          }
        >
          {screen.label}
        </NavLink>
      ))}
    </div>
  );
}
