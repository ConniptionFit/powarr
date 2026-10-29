import { useQuery } from "@tanstack/react-query";
import { req } from "../../lib/api";
import type { ADSettings } from "./MusicSettings";

export type AutoAddMode = "off" | "ask" | "auto";

interface Preset { name: string; description: string; values: Partial<ADSettings> }

const PRESET_LABEL: Record<string, string> = { conservative: "Careful", balanced: "Balanced", aggressive: "Eager" };

/** v0.102.0: one control over the direct-lane switch, the preset and the auto-add switch. */
export function modeOf(s: Partial<ADSettings> | undefined | null): AutoAddMode {
  if (!s) return "ask";
  if (s.direct_lane_enabled === false || s.aggressiveness === "off") return "off";
  return s.auto_add_enabled ? "auto" : "ask";
}

export function patchForMode(mode: AutoAddMode, s: Partial<ADSettings>): Partial<ADSettings> {
  if (mode === "off") return { direct_lane_enabled: false, aggressiveness: "off", auto_add_enabled: false };
  const aggressiveness = !s.aggressiveness || s.aggressiveness === "off" ? "balanced" : s.aggressiveness;
  return { direct_lane_enabled: true, aggressiveness, auto_add_enabled: mode === "auto" };
}

const MODES: [AutoAddMode, string, string][] = [
  ["off", "Off", "Discovery from your listening is paused."],
  ["ask", "Ask me", "New artists wait in Review for you to add or skip."],
  ["auto", "Automatic", "Strong matches go straight to Lidarr, within your limits."],
];

export function ModeSegments({ value, onChange, disabled }: { value: AutoAddMode; onChange: (m: AutoAddMode) => void; disabled?: boolean }) {
  return (
    <div role="radiogroup" aria-label="Auto-add" className="inline-flex rounded-xl border border-purple-900/40 overflow-hidden">
      {MODES.map(([id, label, hint]) => (
        <button key={id} type="button" role="radio" aria-checked={value === id} disabled={disabled} title={hint}
          onClick={() => onChange(id)}
          className={`px-3.5 py-1.5 text-sm transition-colors disabled:opacity-50 ${
            value === id ? "bg-brand text-white font-medium" : "bg-surface text-slate-300 hover:text-white"}`}>
          {label}
        </button>
      ))}
    </div>
  );
}

/** Mode + preset + weekly limit, for the Setup panel. */
export function AutoAddModeControl({ form, patch }: { form: ADSettings; patch: (p: Partial<ADSettings>) => void }) {
  const { data: presets = [] } = useQuery({ queryKey: ["ad-presets"], queryFn: () => req<Preset[]>("/artist-discovery/presets") });
  const mode = modeOf(form);
  const hint = MODES.find((m) => m[0] === mode)?.[2];
  return (
    <div className="space-y-3">
      <ModeSegments value={mode} onChange={(m) => patch(patchForMode(m, form))} />
      <p className="text-xs text-slate-500">{hint}</p>
      {mode !== "off" && (
        <div className="grid sm:grid-cols-2 gap-3 items-end">
          <div>
            <span className="text-xs text-slate-400 block mb-1">How eagerly it suggests</span>
            <div className="inline-flex rounded-xl border border-purple-900/40 overflow-hidden">
              {presets.filter((p) => PRESET_LABEL[p.name]).map((p) => (
                <button key={p.name} type="button" title={p.description}
                  onClick={() => patch({ aggressiveness: p.name, direct_lane_enabled: true, ...p.values })}
                  className={`px-3 py-1.5 text-sm ${form.aggressiveness === p.name ? "bg-brand text-white font-medium" : "bg-surface text-slate-300 hover:text-white"}`}>
                  {PRESET_LABEL[p.name]}
                </button>
              ))}
            </div>
            {form.aggressiveness === "custom" && <span className="text-xs text-amber-300 ml-2">Custom numbers in Advanced</span>}
          </div>
          {mode === "auto" && (
            <label className="text-xs text-slate-400 block" title="Most artists added in a rolling week. 0 = no weekly cap.">
              Weekly limit
              <input type="number" min={0} className="mt-1 w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white"
                value={form.max_adds_per_week ?? 0}
                onChange={(e) => patch({ max_adds_per_week: Math.max(0, parseInt(e.target.value) || 0) })} />
            </label>
          )}
        </div>
      )}
    </div>
  );
}
