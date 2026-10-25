import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { CheckCircle2, Circle, X } from "lucide-react";
import { integrationsApi, type IntegrationConfig } from "../lib/api";
import { usePersistedState } from "../lib/usePersistedState";

const connected = (list: IntegrationConfig[], ...names: string[]) =>
  list.some(i => names.includes(i.name) && i.enabled && !!i.url);

// First-run guide on the Overview: shows which core integrations are connected and links to
// Settings for the rest. Hidden once the required steps are done or the user dismisses it.
export default function SetupChecklist() {
  const [dismissed, setDismissed] = usePersistedState("powarr.setupChecklist.dismissed", false);
  const { data: integrations } = useQuery({ queryKey: ["integrations"], queryFn: integrationsApi.list });
  if (dismissed || !integrations) return null;

  const steps = [
    { label: "Connect Plex or Jellyfin", hint: "Required for library sync", done: connected(integrations, "plex", "jellyfin") },
    { label: "Connect Sonarr or Radarr", hint: "Lets Powarr act on shows and movies", done: connected(integrations, "sonarr", "radarr") },
    { label: "Connect a download client", hint: "qBittorrent or Transmission, for import matching", done: connected(integrations, "qbittorrent", "transmission") },
    { label: "Connect Tautulli or Seerr", hint: "Protects watched and requested media from deletion", done: connected(integrations, "tautulli", "seerr") },
  ];
  if (steps.every(s => s.done)) return null;

  const doneCount = steps.filter(s => s.done).length;
  return (
    <section aria-label="Setup checklist" className="mb-6 rounded-xl border border-purple-900/30 bg-surface-raised p-5">
      <div className="flex items-start justify-between gap-3 mb-3">
        <div>
          <h2 className="text-sm font-semibold text-white">Finish setting up Powarr</h2>
          <p className="text-xs text-slate-500 mt-0.5">{doneCount} of {steps.length} done</p>
        </div>
        <button onClick={() => setDismissed(true)} aria-label="Dismiss setup checklist" className="text-slate-500 hover:text-white">
          <X size={16} />
        </button>
      </div>
      <ul className="space-y-2">
        {steps.map(s => (
          <li key={s.label} className="flex items-start gap-2 text-sm">
            {s.done
              ? <CheckCircle2 size={16} className="mt-0.5 text-green-400 flex-shrink-0" />
              : <Circle size={16} className="mt-0.5 text-slate-500 flex-shrink-0" />}
            <div>
              <span className={s.done ? "text-slate-500 line-through" : "text-slate-200"}>{s.label}</span>
              <span className="text-slate-500"> · {s.hint}</span>
            </div>
          </li>
        ))}
      </ul>
      <Link to="/settings/integrations" className="inline-block mt-4 text-sm text-brand-light hover:underline">
        Open integration settings →
      </Link>
    </section>
  );
}
