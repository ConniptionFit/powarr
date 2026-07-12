import { useState } from "react";
import { BrowserRouter, Routes, Route, Navigate, NavLink, useLocation } from "react-router-dom";
import { Menu, X } from "lucide-react";
import Overview from "./pages/Overview";
import DeletionSuggestions from "./pages/Library/DeletionSuggestions";
import DeletionHistory from "./pages/Library/DeletionHistory";
import MatchReview from "./pages/Imports/MatchReview";
import ArtistDiscovery from "./pages/Music/ArtistDiscovery";
import RelatedArtists from "./pages/Music/RelatedArtists";
import Playlists from "./pages/Music/Playlists";
import SettingsPage from "./pages/Settings";
import LogsPage from "./pages/Logs";
import AuthGate from "./components/AuthGate";
import ActiveProcessesTray from "./components/ActiveProcessesTray";
import IconRail from "./components/IconRail";
import AreaTabs from "./components/AreaTabs";
import Breadcrumb from "./components/Breadcrumb";
import { AREAS, LOGS_AREA } from "./lib/navConfig";
import { TaskProvider } from "./context/TaskContext";

function MobileNav({ onNavigate }: { onNavigate: () => void }) {
  const { pathname } = useLocation();
  return (
    <nav className="flex-1 py-4 px-2 overflow-y-auto space-y-3">
      {[...AREAS, LOGS_AREA].map(area => {
        const Icon = area.icon;
        const areaActive = area.base === "/" ? pathname === "/" : pathname.startsWith(area.base);
        return (
          <div key={area.key}>
            <NavLink
              to={area.screens[0].path}
              onClick={onNavigate}
              className={`flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                areaActive ? "bg-brand/20 text-brand-light" : "text-slate-400 hover:text-white hover:bg-white/5"
              }`}
            >
              <Icon size={17} />
              {area.label}
            </NavLink>
            {area.screens.length > 1 && (
              <div className="ml-8 mt-1 space-y-0.5">
                {area.screens.map(screen => (
                  <NavLink
                    key={screen.path}
                    to={screen.path}
                    onClick={onNavigate}
                    className={({ isActive }) =>
                      `block px-3 py-1.5 rounded-lg text-xs transition-colors ${
                        isActive ? "text-brand-light" : "text-slate-500 hover:text-white"
                      }`
                    }
                  >
                    {screen.label}
                  </NavLink>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </nav>
  );
}

function MobileTopBar({ onOpen }: { onOpen: () => void }) {
  const { pathname } = useLocation();
  const all = [...AREAS, LOGS_AREA];
  const current = all.find(a => (a.base === "/" ? pathname === "/" : pathname.startsWith(a.base)));
  return (
    <div className="md:hidden flex items-center gap-3 px-4 py-3 bg-surface-raised border-b border-purple-900/40 flex-shrink-0">
      <button onClick={onOpen} aria-label="Open menu" className="p-2 -ml-2 rounded-lg text-slate-300 hover:bg-white/5">
        <Menu size={20} />
      </button>
      <span className="text-white font-semibold text-sm">{current?.label ?? "Powarr"}</span>
    </div>
  );
}

export default function App() {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  return (
    <BrowserRouter>
      <AuthGate>
      <TaskProvider>
      <div className="flex h-screen overflow-hidden">
        {/* Desktop icon rail */}
        <aside className="hidden md:flex w-[92px] flex-shrink-0 bg-surface-raised border-r border-purple-900/40 flex-col">
          <IconRail />
        </aside>

        {/* Mobile slide-in nav */}
        {mobileNavOpen && (
          <div className="md:hidden fixed inset-0 z-40 flex">
            <div className="absolute inset-0 bg-black/60" onClick={() => setMobileNavOpen(false)} />
            <aside className="relative w-64 max-w-[80vw] bg-surface-raised border-r border-purple-900/40 flex flex-col">
              <button
                onClick={() => setMobileNavOpen(false)}
                aria-label="Close menu"
                className="absolute top-4 right-3 p-2 rounded-lg text-slate-400 hover:text-white hover:bg-white/5"
              >
                <X size={18} />
              </button>
              <div className="flex items-center gap-2 px-5 py-5 border-b border-purple-900/40">
                <span className="text-xl font-bold text-white tracking-wide">Powarr</span>
              </div>
              <MobileNav onNavigate={() => setMobileNavOpen(false)} />
            </aside>
          </div>
        )}

        {/* Main content */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <MobileTopBar onOpen={() => setMobileNavOpen(true)} />
          <Breadcrumb />
          <AreaTabs />
          <main className="flex-1 overflow-y-auto">
            <Routes>
              <Route path="/" element={<Overview />} />

              <Route path="/library" element={<Navigate to="/library/deletion-suggestions" replace />} />
              <Route path="/library/deletion-suggestions" element={<div className="p-4 sm:p-8"><DeletionSuggestions /></div>} />
              <Route path="/library/deletion-history" element={<div className="p-4 sm:p-8"><DeletionHistory /></div>} />

              <Route path="/imports" element={<div className="p-4 sm:p-8"><MatchReview /></div>} />
              {/* Redirect old /imports/queue URL for backwards compatibility */}
              <Route path="/imports/queue" element={<Navigate to="/imports" replace />} />

              <Route path="/music" element={<Navigate to="/music/discovery" replace />} />
              <Route path="/music/discovery" element={<ArtistDiscovery />} />
              <Route path="/music/related" element={<RelatedArtists />} />
              <Route path="/music/playlists" element={<Playlists />} />

              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/settings/:category" element={<SettingsPage />} />

              <Route path="/logs" element={<LogsPage />} />

              {/* Back-compat redirect for the old top-level Integrations page */}
              <Route path="/integrations" element={<Navigate to="/settings/integrations" replace />} />
            </Routes>
          </main>
        </div>
      </div>
      <ActiveProcessesTray />
      </TaskProvider>
      </AuthGate>
    </BrowserRouter>
  );
}
