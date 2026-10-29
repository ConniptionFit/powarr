import { useQuery } from "@tanstack/react-query";
import { req } from "../../lib/api";
import type { ADSettings } from "./MusicSettings";

interface Preset {
  name: string;
  description: string;
  values: Partial<ADSettings>;
}

const labelCls = "text-xs text-slate-400 block";
const inputCls = "mt-1 w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white";
const hintCls = "block text-[10px] text-slate-600 mt-0.5";

const PERIODS: [string, string][] = [
  ["7day", "Last 7 days"], ["1month", "Last month"], ["3month", "Last 3 months"],
  ["6month", "Last 6 months"], ["12month", "Last year"], ["overall", "All time"],
];
const MONITOR: [string, string][] = [
  ["all", "All albums"], ["future", "Future albums only"], ["missing", "Missing albums"],
  ["existing", "Existing albums"], ["first", "First album"], ["latest", "Latest album"], ["none", "None (add artist only)"],
];

/** Aggressiveness presets + the knobs behind them. Choosing a preset copies its values
 * in; editing any knob switches to "custom" so what you see is what runs. */
export function DirectLaneSettings({ form, set, patch, hideAutoToggle = false }: {
  hideAutoToggle?: boolean;
  form: ADSettings;
  set: <K extends keyof ADSettings>(k: K, v: ADSettings[K]) => void;
  patch: (p: Partial<ADSettings>) => void;
}) {
  const { data: presets = [] } = useQuery({ queryKey: ["ad-presets"], queryFn: () => req<Preset[]>("/artist-discovery/presets") });
  const current = form.aggressiveness ?? "balanced";
  const active = presets.find((p) => p.name === current);
  const shown = { ...form, ...(active?.values ?? {}) } as ADSettings;
  const isOff = current === "off" || form.direct_lane_enabled === false;

  const choose = (p: Preset) => patch({ aggressiveness: p.name, direct_lane_enabled: p.name !== "off", ...p.values });
  // Editing a knob freezes the preset's current values into the form and flips to custom.
  const edit = <K extends keyof ADSettings>(k: K, v: ADSettings[K]) =>
    patch({ ...(active?.values ?? {}), aggressiveness: "custom", [k]: v } as Partial<ADSettings>);
  const num = (k: keyof ADSettings, fallback: number, float = false) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = float ? parseFloat(e.target.value) : parseInt(e.target.value);
    edit(k as any, (Number.isNaN(v) ? fallback : v) as any);
  };

  return (
    <div className="border-t border-purple-900/20 pt-4 space-y-4">
      <div>
        <h3 className="text-sm font-semibold text-white">Discovery aggressiveness</h3>
        <p className="text-xs text-slate-500 mt-0.5">
          Scores artists that Last.fm lists as similar to what you actually play, then queues or adds the best ones.
          Needs only Last.fm and Lidarr (no Qdrant or Ollama).
        </p>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
        {presets.map((p) => (
          <button key={p.name} type="button" onClick={() => choose(p)} title={p.description}
            className={`px-3 py-2 rounded-lg border text-sm capitalize transition-colors ${
              current === p.name
                ? "bg-brand text-white border-brand font-semibold"
                : "bg-surface text-slate-300 border-purple-900/40 hover:border-purple-700"}`}>
            {p.name}
          </button>
        ))}
      </div>
      {active && <p className="text-xs text-slate-400">{active.description}</p>}

      <div className={isOff ? "opacity-40 pointer-events-none space-y-4" : "space-y-4"}>
        {!hideAutoToggle && (
        <label className="flex items-start gap-2 text-sm text-slate-300"
          title="Off: every candidate goes to the review queue and nothing is added on its own. On: candidates that clear the auto-add score and connection count are added to Lidarr, within the caps below.">
          <input type="checkbox" className="mt-1" checked={!!form.auto_add_enabled}
            onChange={(e) => set("auto_add_enabled", e.target.checked)} />
          <span>
            Add artists to Lidarr automatically
            <span className="block text-xs text-slate-500">
              {form.auto_add_enabled
                ? "On. Strong matches skip the queue, limited by the per-run and daily caps."
                : "Off. Everything goes to the review queue for you to accept."}
            </span>
          </span>
        </label>
        )}

        <div className="grid sm:grid-cols-3 gap-3">
          <label className={labelCls} title="Which stretch of your Last.fm history picks the seed artists.">
            Seed period
            <select className={inputCls} value={shown.seed_period ?? "3month"} onChange={(e) => edit("seed_period", e.target.value)}>
              {PERIODS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
            <span className={hintCls}>Shorter = tracks what you play now</span>
          </label>
          <label className={labelCls} title="A seed needs at least this many plays in the period.">
            Min plays per seed
            <input type="number" min="1" className={inputCls} value={shown.min_seed_plays ?? 3} onChange={num("min_seed_plays", 3)} />
          </label>
          <label className={labelCls} title="Most-played artists used as seeds. More seeds finds more, and costs more Last.fm calls.">
            Max seeds
            <input type="number" min="1" max="200" className={inputCls} value={shown.max_seeds ?? 40} onChange={num("max_seeds", 40)} />
          </label>
          <label className={labelCls} title="How many similar artists to read per seed.">
            Similar artists per seed
            <input type="number" min="1" max="50" className={inputCls} value={shown.similar_per_seed ?? 15} onChange={num("similar_per_seed", 15)} />
          </label>
          <label className={labelCls} title="Ignore a Last.fm similarity link weaker than this (0 to 1).">
            Min similarity
            <input type="number" step="0.05" min="0" max="1" className={inputCls} value={shown.min_match ?? 0.4} onChange={num("min_match", 0.4, true)} />
            <span className={hintCls}>Lower = looser matches</span>
          </label>
          <label className={labelCls} title="Score = sum over seeds of (seed weight x similarity). A top seed with a 90% link scores about 0.9.">
            Queue score
            <input type="number" step="0.05" min="0" className={inputCls} value={shown.suggest_score ?? 0.6} onChange={num("suggest_score", 0.6, true)} />
            <span className={hintCls}>Needed to enter the review queue</span>
          </label>
          <label className={labelCls} title="Score needed to add straight to Lidarr (when automatic adding is on).">
            Auto-add score
            <input type="number" step="0.05" min="0" className={inputCls} value={shown.auto_add_score ?? 1.2} onChange={num("auto_add_score", 1.2, true)} />
          </label>
          <label className={labelCls} title="How many different seed artists must point at a candidate before it can be auto-added. 1 lets a single strong link do it.">
            Auto-add min seeds
            <input type="number" min="1" className={inputCls} value={shown.auto_add_min_connections ?? 2} onChange={num("auto_add_min_connections", 2)} />
          </label>
          <label className={labelCls} title="Most artists a single run may add to Lidarr.">
            Max adds per run
            <input type="number" min="0" className={inputCls} value={shown.max_adds_per_run ?? 3} onChange={num("max_adds_per_run", 3)} />
          </label>
          <label className={labelCls} title="Rolling 24 hour cap across all runs. 0 = no daily cap.">
            Max adds per day
            <input type="number" min="0" className={inputCls} value={shown.max_adds_per_day ?? 5} onChange={num("max_adds_per_day", 5)} />
            <span className={hintCls}>0 = no daily cap</span>
          </label>
          <label className={labelCls} title="Most new candidates a single run may put in the review queue.">
            Max queued per run
            <input type="number" min="0" className={inputCls} value={shown.max_queue_per_run ?? 20} onChange={num("max_queue_per_run", 20)} />
          </label>
        </div>

        <div className="grid sm:grid-cols-3 gap-3">
          <label className={labelCls} title="Lidarr's 'monitor' option for newly added artists.">
            Lidarr monitor
            <select className={inputCls} value={form.lidarr_monitor ?? "all"} onChange={(e) => set("lidarr_monitor", e.target.value)}>
              {MONITOR.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-300 sm:mt-5"
            title="Tell Lidarr to search for missing albums as soon as the artist is added. Applies to every add (auto and manual).">
            <input type="checkbox" checked={form.search_on_add ?? true} onChange={(e) => set("search_on_add", e.target.checked)} />
            Search on add
          </label>
          <label className={labelCls} title="Skip candidates whose Last.fm tags contain any of these words. Comma separated, e.g. christmas, kids, karaoke.">
            Excluded genres
            <input type="text" className={inputCls} placeholder="christmas, karaoke"
              value={(form.excluded_genres ?? []).join(", ")}
              onChange={(e) => set("excluded_genres", e.target.value.split(",").map((x) => x.trim()).filter(Boolean))} />
          </label>
        </div>
      </div>

      <p className="text-xs text-slate-500">
        Presets set the numbers above for you; change any number and the preset becomes Custom. Use Preview (dry run) on the
        Artist Discovery page to see what these settings would do before saving or running them. The schedule below decides
        how often it runs on its own.
      </p>
    </div>
  );
}
