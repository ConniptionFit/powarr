import { useState } from "react";
import { BrowserRouter, Routes, Route, NavLink, useLocation } from "react-router-dom";
import { Zap, LayoutDashboard, Trash2, Settings, Plug, ScrollText, Menu, X } from "lucide-react";
import Dashboard from "./pages/Dashboard";
import Cleanup from "./pages/Cleanup";
import SettingsPage from "./pages/Settings";
import IntegrationsPage from "./pages/Integrations";
import LogsPage from "./pages/Logs";
import AuthGate from "./components/AuthGate";
import ActiveProcessesTray from "./components/ActiveProcessesTray";
import { TaskProvider } from "./context/TaskContext";

const nav = [
  { to: "/", icon: LayoutDashboard, label: "Dashboard" },
  { to: "/cleanup", icon: Trash2, label: "Cleanup" },
  { to: "/integrations", icon: Plug, label: "Integrations" },
  { to: "/settings", icon: Settings, label: "Settings" },
  { to: "/logs", icon: ScrollText, label: "Logs" },
];

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <>
      <div className="flex items-center gap-2 px-5 py-5 border-b border-purple-900/40">
        <Zap className="text-brand-light" size={22} />
        <span className="text-xl font-bold text-white tracking-wide">Powarr</span>
      </div>
      <nav className="flex-1 py-4 space-y-1 px-2">
        {nav.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            onClick={onNavigate}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                isActive
                  ? "bg-brand/20 text-brand-light"
                  : "text-slate-400 hover:text-white hover:bg-white/5"
              }`
            }
          >
            <Icon size={17} />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="px-5 py-3 text-xs text-slate-600 border-t border-purple-900/40">
        v0.28.1
      </div>
    </>
  );
}

function MobileTopBar({ onOpen }: { onOpen: () => void }) {
  const { pathname } = useLocation();
  const current = nav.find(n => (n.to === "/" ? pathname === "/" : pathname.startsWith(n.to)));
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
        {/* Desktop sidebar */}
        <aside className="hidden md:flex w-56 flex-shrink-0 bg-surface-raised border-r border-purple-900/40 flex-col">
          <Sidebar />
        </aside>

        {/* Mobile slide-in sidebar */}
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
              <Sidebar onNavigate={() => setMobileNavOpen(false)} />
            </aside>
          </div>
        )}

        {/* Main content */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <MobileTopBar onOpen={() => setMobileNavOpen(true)} />
          <main className="flex-1 overflow-y-auto">
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/cleanup" element={<Cleanup />} />
              <Route path="/integrations" element={<IntegrationsPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/logs" element={<LogsPage />} />
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
