import { useNavigate, useLocation } from "react-router-dom";
import { Zap } from "lucide-react";
import { AREAS, LOGS_AREA, areaForPath, type AreaDef } from "../lib/navConfig";

function RailItem({ area, active, onClick }: { area: AreaDef; active: boolean; onClick: () => void }) {
  const Icon = area.icon;
  return (
    <button
      onClick={onClick}
      title={area.label}
      className={`relative w-full flex flex-col items-center gap-1 py-3 px-1 rounded-lg transition-colors ${
        active ? "text-brand-light" : "text-slate-400 hover:text-white hover:bg-white/5"
      }`}
      style={active ? { background: "rgba(124,58,237,0.16)" } : undefined}
    >
      {active && (
        <span className="absolute left-0 top-1.5 bottom-1.5 w-[3px] rounded-full bg-brand-light" />
      )}
      <Icon size={20} />
      <span className="text-[10px] leading-none">{area.label}</span>
    </button>
  );
}

export default function IconRail({ onNavigate }: { onNavigate?: () => void }) {
  const navigate = useNavigate();
  const location = useLocation();
  const activeArea = areaForPath(location.pathname);

  const go = (area: AreaDef) => {
    navigate(area.screens[0].path);
    onNavigate?.();
  };

  return (
    <div className="flex flex-col h-full items-center w-full">
      <div className="flex items-center justify-center py-5 border-b border-purple-900/40 w-full">
        <Zap className="text-brand-light" size={22} />
      </div>
      <nav className="flex-1 py-3 space-y-1 w-full px-1.5">
        {AREAS.map(area => (
          <RailItem
            key={area.key}
            area={area}
            active={activeArea?.key === area.key}
            onClick={() => go(area)}
          />
        ))}
      </nav>
      <div className="w-full border-t border-purple-900/40 px-1.5 py-2">
        <RailItem
          area={LOGS_AREA}
          active={activeArea?.key === LOGS_AREA.key}
          onClick={() => go(LOGS_AREA)}
        />
      </div>
      <div className="w-full text-center pb-2 text-[9px] text-slate-600">v0.50.0</div>
    </div>
  );
}
