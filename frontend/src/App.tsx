import { BrowserRouter, Routes, Route, NavLink } from "react-router-dom";
import { Zap, LayoutDashboard, Trash2, Settings, Plug, ScrollText } from "lucide-react";
import Dashboard from "./pages/Dashboard";
import Cleanup from "./pages/Cleanup";
import SettingsPage from "./pages/Settings";
import IntegrationsPage from "./pages/Integrations";
import LogsPage from "./pages/Logs";
import AuthGate from "./components/AuthGate";

const nav = [
  { to: "/", icon: LayoutDashboard, label: "Dashboard" },
  { to: "/cleanup", icon: Trash2, label: "Cleanup" },
  { to: "/integrations", icon: Plug, label: "Integrations" },
  { to: "/settings", icon: Settings, label: "Settings" },
  { to: "/logs", icon: ScrollText, label: "Logs" },
];

export default function App() {
  return (
    <BrowserRouter>
      <AuthGate>
      <div className="flex h-screen overflow-hidden">
        {/* Sidebar */}
        <aside className="w-56 flex-shrink-0 bg-surface-raised border-r border-purple-900/40 flex flex-col">
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
            v0.17.0
          </div>
        </aside>

        {/* Main content */}
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
      </AuthGate>
    </BrowserRouter>
  );
}
