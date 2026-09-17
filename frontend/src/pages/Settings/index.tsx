import { useEffect, useRef, useState } from "react";
import { useParams, useNavigate, Navigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Save, AlertTriangle, Lock, Bell, Send, Bot, Wand2, Play, Clock, DatabaseBackup, Activity, RotateCcw, Plug, SlidersHorizontal, Music } from "lucide-react";
import { Skeleton } from "../../components/Skeleton";
import { settingsApi, mediaApi, authApi, importsApi, fmtBytes, fmtDate, type ScoringWeights, type ScoringProfiles,
         type ImportMatchingSettings, type CleanupSettings, type SyncSettings, type NotificationSettings,
         type OllamaSettings, type LlmPolicies, type LlmAppOverride, type LlmLibraryOverride,
         type LlmScheduleSettings, type BackupSettings, type BackupFile, type SettingsExport } from "../../lib/api";
import IntegrationsPage from "../Integrations";
import MusicSettings from "./MusicSettings";
import { ImportMatchingSection, ScoringWeightsSection, ScoringProfilesSection } from "./sections/ScoringSettings";
import { CleanupSection, SyncSection, BackupSection, ConfigExportSection } from "./sections/AutomationSettings";
import { LLMAssistSection, LlmPoliciesSection, LlmScheduleSection } from "./sections/LlmSettings";
import { SecuritySection } from "./sections/SecuritySettings";
import { NotificationsSection } from "./sections/NotificationsSettings";

const CATEGORIES: { key: string; label: string; icon: typeof Save; description: string }[] = [
  { key: "integrations", label: "Integrations", icon: Plug, description: "Plex, Tautulli, *arr apps, Seerr, download clients, Qdrant, Ollama connection" },
  { key: "matching-scoring", label: "Matching & Scoring", icon: SlidersHorizontal, description: "Failed import matching thresholds, scoring weights, per-library profiles" },
  { key: "automation", label: "Automation", icon: Clock, description: "Cleanup behavior, scheduled Plex sync, automated backups" },
  { key: "llm-assist", label: "LLM Assist", icon: Bot, description: "Local LLM behavior, prompts, verbosity, scheduled backlog scanning" },
  { key: "notifications", label: "Notifications", icon: Bell, description: "ntfy alerts, actionable notifications, weekly digest" },
  { key: "security", label: "Security", icon: Lock, description: "Auth, TOTP, LAN bypass, SSO / forward-auth" },
  { key: "music", label: "Music", icon: Music, description: "Artist Discovery and Playlists configuration" },
];

export default function SettingsPage() {
  const { category } = useParams<{ category?: string }>();
  const navigate = useNavigate();
  const cat = CATEGORIES.find(c => c.key === category);

  if (!category) {
    return (
      <div className="p-4 sm:p-8">
        <h1 className="text-2xl font-bold text-white mb-1">Settings</h1>
        <p className="text-slate-400 text-sm mb-6">Choose a category to configure</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 max-w-3xl">
          {CATEGORIES.map(c => {
            const Icon = c.icon;
            return (
              <button
                key={c.key}
                onClick={() => navigate(`/settings/${c.key}`)}
                className="text-left bg-surface-raised rounded-xl border border-purple-900/30 hover:border-purple-500/60 p-5 transition-colors"
              >
                <Icon size={20} className="text-brand-light mb-2" />
                <p className="text-white font-semibold">{c.label}</p>
                <p className="text-slate-400 text-sm mt-1">{c.description}</p>
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  // Unknown /settings/:category (typo, stale bookmark) — fall back to the grid
  // rather than rendering a heading with no section below it.
  if (!cat) return <Navigate to="/settings" replace />;

  return (
    <div className="p-4 sm:p-8 max-w-2xl">
      {/* Direct category switcher — jump straight between categories instead of
          bouncing back out to the grid. Scrolls horizontally on narrow screens. */}
      <div className="flex items-center gap-1.5 overflow-x-auto pb-2 mb-4 -mx-1 px-1">
        {CATEGORIES.map(c => {
          const Icon = c.icon;
          const active = c.key === category;
          return (
            <button
              key={c.key}
              onClick={() => navigate(`/settings/${c.key}`)}
              className={`flex items-center gap-1.5 whitespace-nowrap px-3 py-1.5 rounded-lg text-sm transition-colors ${
                active
                  ? "bg-brand text-white"
                  : "text-slate-400 hover:text-white hover:bg-white/5 border border-purple-900/40"
              }`}
            >
              <Icon size={14} />
              {c.label}
            </button>
          );
        })}
      </div>
      <h1 className="text-2xl font-bold text-white mb-1">{cat.label}</h1>
      <p className="text-slate-400 text-sm mb-6">{cat.description}</p>

      {category === "integrations" && <IntegrationsPage embedded />}
      {category === "matching-scoring" && (
        <>
          <ScoringWeightsSection />
          <ScoringProfilesSection />
          <ImportMatchingSection />
        </>
      )}
      {category === "automation" && (
        <>
          <CleanupSection />
          <SyncSection />
          <BackupSection />
          <ConfigExportSection />
        </>
      )}
      {category === "llm-assist" && (
        <>
          <LLMAssistSection />
          <LlmPoliciesSection />
          <LlmScheduleSection />
        </>
      )}
      {category === "notifications" && <NotificationsSection />}
      {category === "security" && <SecuritySection />}
      {category === "music" && <MusicSettings />}
    </div>
  );
}
