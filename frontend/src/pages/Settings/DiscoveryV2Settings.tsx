import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, RefreshCw } from "lucide-react";
import { api } from "../Music/artist-discovery/shared";
import type { ADSettings } from "./MusicSettings";

const labelCls = "text-xs text-slate-400 block";
const inputCls = "mt-1 w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white";
const hintCls = "block text-[10px] text-slate-600 mt-0.5";
const SOURCES: [string, string][] = [["lastfm", "Last.fm"], ["plex", "Plex"], ["listenbrainz", "ListenBrainz"]];
const PRIORITY_LABEL: Record<string, string> = { plex: "Plex", tautulli: "Tautulli", lastfm: "Last.fm", listenbrainz: "ListenBrainz" };
const BUCKETS: [string, string][] = [["close", "Close"], ["branching", "Branching out"], ["wildcard", "Wildcard"]];

type Props = {
  form: ADSettings;
  set: <K extends keyof ADSettings>(k: K, v: ADSettings[K]) => void;
};

const list = (v: string) => v.split(",").map((x) => x.trim()).filter(Boolean);

function Section({ title, hint, children, defaultOpen = true }: { title: string; hint?: string; children: React.ReactNode; defaultOpen?: boolean }) {
  return (
    <details open={defaultOpen} className="border-t border-purple-900/20 pt-4 group">
      <summary className="cursor-pointer list-none">
        <h3 className="text-sm font-semibold text-white inline">{title}</h3>
        {hint && <p className="text-xs text-slate-500 mt-0.5">{hint}</p>}
      </summary>
      <div className="mt-3 space-y-4">{children}</div>
    </details>
  );
}

function Slider({ label, hint, value, min, max, step, onChange }: {
  label: string; hint?: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void;
}) {
  return (
    <label className={labelCls} title={hint}>
      <span className="flex justify-between"><span>{label}</span><span className="text-slate-300">{value}</span></span>
      <input type="range" min={min} max={max} step={step} value={value} className="w-full accent-purple-500 mt-1"
        onChange={(e) => onChange(parseFloat(e.target.value))} />
      {hint && <span className={hintCls}>{hint}</span>}
    </label>
  );
}

function ListeningSources({ form, set }: Props) {
  const qc = useQueryClient();
  const [days, setDays] = useState(7);
  const { data: status } = useQuery({ queryKey: ["ad-listening-status"], queryFn: api.listeningStatus });
  const { data: preview } = useQuery({ queryKey: ["ad-listening-preview", days], queryFn: () => api.listeningPreview(days) });
  const sync = useMutation({
    mutationFn: api.listeningSync,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["ad-listening-status"] }); qc.invalidateQueries({ queryKey: ["ad-listening-preview"] }); },
  });
  const sources = form.listening_sources ?? ["lastfm", "plex", "listenbrainz"];
  const priority = form.listening_source_priority ?? ["plex", "tautulli", "lastfm", "listenbrainz"];
  const counted = form.listening_users ?? [];
  const toggleSource = (s: string) => set("listening_sources", sources.includes(s) ? sources.filter((x) => x !== s) : [...sources, s]);
  const toggleUser = (u: string) => set("listening_users", counted.includes(u) ? counted.filter((x) => x !== u) : [...counted, u]);
  const move = (i: number, d: number) => {
    const next = [...priority];
    const j = i + d;
    if (j < 0 || j >= next.length) return;
    [next[i], next[j]] = [next[j], next[i]];
    set("listening_source_priority", next);
  };
  return (
    <Section title="Listening sources" hint="Where Powarr learns what you play. The same play reported by two sources counts once.">
      <label className="flex items-center gap-2 text-sm text-slate-300">
        <input type="checkbox" checked={form.listening_enabled ?? true} onChange={(e) => set("listening_enabled", e.target.checked)} />
        Build a listening log from these sources (off = Last.fm top artists only, as before)
      </label>
      <div className="grid sm:grid-cols-3 gap-3">
        {SOURCES.map(([id, label]) => {
          const st = status?.sources.find((s) => s.source === id);
          return (
            <label key={id} className="rounded-lg border border-purple-900/30 bg-surface p-3 text-sm text-slate-300 space-y-1">
              <span className="flex items-center gap-2">
                <input type="checkbox" checked={sources.includes(id)} onChange={() => toggleSource(id)} />
                {label}
              </span>
              <span className="block text-[11px] text-slate-500">
                {st ? (st.configured ? `${st.events.toLocaleString()} plays logged` : "Not configured in Integrations") : "…"}
                {st?.last_sync && st.configured ? ` · synced ${new Date(st.last_sync).toLocaleString()}` : ""}
              </span>
            </label>
          );
        })}
      </div>
      <div className="grid sm:grid-cols-3 gap-3">
        <label className={labelCls} title="Plex reads its own play history. Tautulli is used if Plex is not connected or you choose it here.">
          Plex history comes from
          <select className={inputCls} value={form.plex_history_via ?? "plex"} onChange={(e) => set("plex_history_via", e.target.value)}>
            <option value="plex">Plex directly (default)</option>
            <option value="tautulli">Tautulli</option>
          </select>
        </label>
        <label className={labelCls} title="Two reports of the same track from different sources within this many seconds (or half the track length, if longer) are one play.">
          Same-play window (seconds)
          <input type="number" min={10} max={600} className={inputCls} value={form.listening_dedupe_tolerance_s ?? 180}
            onChange={(e) => set("listening_dedupe_tolerance_s", parseInt(e.target.value) || 180)} />
        </label>
        <label className={labelCls} title="Plays shorter than this are ignored (Last.fm's own rule is 30 seconds). Only applies when the source reports how long was played.">
          Minimum play length (seconds)
          <input type="number" min={0} max={240} className={inputCls} value={form.listening_min_play_s ?? 30}
            onChange={(e) => set("listening_min_play_s", parseInt(e.target.value) || 0)} />
        </label>
        <label className={labelCls} title="How far back the first sync reaches.">
          First sync reaches back (days)
          <input type="number" min={7} max={365} className={inputCls} value={form.listening_backfill_days ?? 90}
            onChange={(e) => set("listening_backfill_days", parseInt(e.target.value) || 90)} />
        </label>
        <label className={labelCls} title="Older plays count less. 0 = every play counts the same.">
          Taste fades after (days)
          <input type="number" min={0} max={365} className={inputCls} value={form.seed_half_life_days ?? 0}
            onChange={(e) => set("seed_half_life_days", parseInt(e.target.value) || 0)} />
          <span className={hintCls}>Half weight after this many days. 0 = no fade</span>
        </label>
      </div>

      <div>
        <p className={labelCls}>Accounts that count (Plex / Tautulli)</p>
        <div className="flex flex-wrap gap-1.5 mt-1.5">
          <span className={`text-xs px-2.5 py-1 rounded-full border ${counted.length === 0 ? "bg-brand text-white border-brand" : "border-purple-900/40 text-slate-400"}`}>
            {counted.length === 0 ? "All accounts (default)" : "Only the selected"}
          </span>
          {(status?.users ?? []).map((u) => (
            <button key={u} type="button" onClick={() => toggleUser(u)}
              className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                counted.includes(u) ? "bg-purple-900/60 text-white border-purple-600" : "border-purple-900/40 text-slate-400 hover:border-purple-700"}`}>
              {u}
            </button>
          ))}
          {(status?.users ?? []).length === 0 && <span className="text-xs text-slate-600">Accounts appear after the first Plex or Tautulli sync.</span>}
        </div>
        <p className={hintCls}>Select none to count everyone. Select some to count only them.</p>
      </div>

      <div>
        <p className={labelCls}>When two sources saw the same play, the first one in this list wins its time</p>
        <ol className="mt-1.5 space-y-1">
          {priority.map((p, i) => (
            <li key={p} className="flex items-center gap-2 text-sm text-slate-300">
              <span className="text-slate-600 w-4">{i + 1}</span>
              <span className="flex-1">{PRIORITY_LABEL[p] ?? p}</span>
              <button type="button" onClick={() => move(i, -1)} disabled={i === 0} className="p-1 text-slate-400 hover:text-white disabled:opacity-30"><ArrowUp size={13} /></button>
              <button type="button" onClick={() => move(i, 1)} disabled={i === priority.length - 1} className="p-1 text-slate-400 hover:text-white disabled:opacity-30"><ArrowDown size={13} /></button>
            </li>
          ))}
        </ol>
      </div>

      <div className="rounded-lg border border-purple-900/30 bg-surface p-3 space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm text-white font-medium">Duplicate check</p>
          <div className="flex items-center gap-2">
            <select value={days} onChange={(e) => setDays(parseInt(e.target.value))}
              className="bg-surface-raised border border-purple-900/40 rounded px-2 py-1 text-xs text-slate-300">
              <option value={7}>Last 7 days</option><option value={30}>Last 30 days</option>
            </select>
            <button type="button" onClick={() => sync.mutate()} disabled={sync.isPending}
              className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded bg-brand text-white hover:bg-brand-light disabled:opacity-50">
              <RefreshCw size={12} className={sync.isPending ? "animate-spin" : ""} /> Sync now
            </button>
          </div>
        </div>
        {sync.data && <p className="text-xs text-slate-400">{sync.data.message}</p>}
        {preview && (
          <>
            <p className="text-xs text-slate-300">
              {preview.raw_reports.toLocaleString()} reports became {preview.real_plays.toLocaleString()} real plays.
              {" "}{preview.seen_by_multiple_sources.toLocaleString()} were seen by more than one source.
            </p>
            {preview.examples.length > 0 && (
              <ul className="text-[11px] text-slate-500 space-y-0.5">
                {preview.examples.map((e, i) => (
                  <li key={i}>{e.artist} – {e.track}: {e.sources.join(" + ")} ({e.gap_seconds}s apart)</li>
                ))}
              </ul>
            )}
          </>
        )}
        <p className="text-[11px] text-slate-600">Save settings first, then Sync now, to apply a changed window or source list.</p>
      </div>
    </Section>
  );
}

export function DiscoveryV2Settings({ form, set }: Props) {
  const buckets = form.auto_add_buckets ?? ["close", "branching"];
  const toggleBucket = (b: string) => set("auto_add_buckets", buckets.includes(b) ? buckets.filter((x) => x !== b) : [...buckets, b]);
  const num = (k: keyof ADSettings, fb: number, float = false) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = float ? parseFloat(e.target.value) : parseInt(e.target.value);
    set(k as any, (Number.isNaN(v) ? fb : v) as any);
  };
  const sentence = [
    form.auto_add_enabled ? "Automatically add to Lidarr and search" : "Auto-add is off, so nothing below happens on its own. When it is on: add to Lidarr and search",
    `when the score is at least ${form.auto_add_score ?? 1.2}`,
    `and at least ${form.auto_add_min_connections ?? 2} of your artists link to it`,
    `from at least ${form.auto_add_min_sources ?? 1} source(s)`,
    buckets.length ? `and it is a ${buckets.join(" or ")} pick` : "and it is any kind of pick",
    (form.auto_add_genres ?? []).length ? `with a tag like ${(form.auto_add_genres ?? []).join(" / ")}` : "",
    (form.auto_add_excluded_genres ?? []).length ? `but never tagged ${(form.auto_add_excluded_genres ?? []).join(" / ")}` : "",
    form.auto_add_require_mbid ?? true ? "and it has a MusicBrainz ID" : "",
  ].filter(Boolean).join(", ").replace(", and", " and") +
    `. Limit: ${form.max_adds_per_run ?? 3} per run, ${form.max_adds_per_day || "no"} per day, ${form.max_adds_per_week || "no"} per week.`;

  return (
    <div className="space-y-4">
      <ListeningSources form={form} set={set} />

      <Section title="Auto-add rules" hint="What an artist must clear before Powarr adds it to Lidarr and starts a search on its own.">
        <p className="text-xs text-slate-300 bg-surface rounded-lg border border-purple-900/30 p-3 leading-relaxed">{sentence}</p>
        <div>
          <p className={labelCls}>Allowed picks</p>
          <div className="flex flex-wrap gap-3 mt-1.5">
            {BUCKETS.map(([id, label]) => (
              <label key={id} className="flex items-center gap-1.5 text-sm text-slate-300">
                <input type="checkbox" checked={buckets.includes(id)} onChange={() => toggleBucket(id)} /> {label}
              </label>
            ))}
          </div>
          <p className={hintCls}>Close = fits your usual tags. Wildcard = a stretch. None ticked = any.</p>
        </div>
        <div className="grid sm:grid-cols-3 gap-3">
          <label className={labelCls} title="Distinct places that suggested it: Last.fm, Deezer, ListenBrainz.">
            Sources that must agree
            <input type="number" min={1} max={3} className={inputCls} value={form.auto_add_min_sources ?? 1} onChange={num("auto_add_min_sources", 1)} />
          </label>
          <label className={labelCls} title="0 = no weekly cap.">
            Max adds per week
            <input type="number" min={0} className={inputCls} value={form.max_adds_per_week ?? 0} onChange={num("max_adds_per_week", 0)} />
          </label>
          <label className={labelCls} title="Auto-added artists get this tag in Lidarr so they are easy to find. Blank = no tag.">
            Lidarr tag
            <input type="text" className={inputCls} value={form.lidarr_tag ?? "powarr-discovered"} onChange={(e) => set("lidarr_tag", e.target.value)} />
          </label>
          <label className={labelCls} title="Only auto-add artists with at least one of these tags. Blank = any.">
            Only these genres
            <input type="text" className={inputCls} placeholder="rock, electronic" value={(form.auto_add_genres ?? []).join(", ")}
              onChange={(e) => set("auto_add_genres", list(e.target.value))} />
          </label>
          <label className={labelCls} title="Never auto-add artists with any of these tags (they can still be queued).">
            Never these genres
            <input type="text" className={inputCls} placeholder="christmas, kids" value={(form.auto_add_excluded_genres ?? []).join(", ")}
              onChange={(e) => set("auto_add_excluded_genres", list(e.target.value))} />
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-300 sm:mt-5">
            <input type="checkbox" checked={form.auto_add_require_mbid ?? true} onChange={(e) => set("auto_add_require_mbid", e.target.checked)} />
            Require a MusicBrainz ID
          </label>
        </div>
        <p className="text-xs text-slate-500">Score, seeds, per-run and per-day caps, monitor mode and search on add are in Discovery aggressiveness above.</p>
      </Section>

      <Section title="Keep out of my taste profile" hint="Optional. Stops these from shaping suggestions, e.g. kids' music or sleep sounds on a shared server." defaultOpen={false}>
        <div className="grid sm:grid-cols-2 gap-3">
          <label className={labelCls}>
            Artists to ignore as seeds
            <input type="text" className={inputCls} placeholder="Cocomelon, Rain Sounds" value={(form.seed_exclude_artists ?? []).join(", ")}
              onChange={(e) => set("seed_exclude_artists", list(e.target.value))} />
          </label>
          <label className={labelCls} title="A seed is dropped when its Last.fm tags contain any of these.">
            Tags to ignore as seeds
            <input type="text" className={inputCls} placeholder="childrens, sleep, christmas" value={(form.seed_exclude_tags ?? []).join(", ")}
              onChange={(e) => set("seed_exclude_tags", list(e.target.value))} />
          </label>
        </div>
      </Section>

      <Section title="Matching and ranking" hint="How the final score is blended. Every score on a card can be opened to see these in action. Defaults suit most libraries." defaultOpen={false}>
        <label className="flex items-center gap-2 text-sm text-slate-300">
          <input type="checkbox" checked={form.advanced_ranking ?? true} onChange={(e) => set("advanced_ranking", e.target.checked)} />
          Blend tags, popularity, freshness and feedback into the score (off = Last.fm similarity only)
        </label>
        <div className="grid sm:grid-cols-2 gap-x-6 gap-y-3">
          <Slider label="Similarity to what you play" value={form.weight_similarity ?? 1} min={0} max={2} step={0.1} onChange={(v) => set("weight_similarity", v)} />
          <Slider label="Several sources agree" hint="Bonus per extra source" value={form.weight_agreement ?? 0.25} min={0} max={1} step={0.05} onChange={(v) => set("weight_agreement", v)} />
          <Slider label="Fits your usual tags" value={form.weight_tag_fit ?? 0.5} min={0} max={1.5} step={0.05} onChange={(v) => set("weight_tag_fit", v)} />
          <Slider label="Prefer smaller artists" value={form.weight_obscurity ?? 0.2} min={0} max={1} step={0.05} onChange={(v) => set("weight_obscurity", v)} />
          <Slider label="Recent release bonus" value={form.weight_freshness ?? 0.2} min={0} max={1} step={0.05} onChange={(v) => set("weight_freshness", v)} />
          <Slider label="Learn from my feedback" hint="More like this / Not for me" value={form.weight_feedback ?? 0.4} min={0} max={1} step={0.05} onChange={(v) => set("weight_feedback", v)} />
          <Slider label="Variety" hint="Higher spreads results across different seeds and sounds" value={form.diversity_strength ?? 0.5} min={0} max={1} step={0.05} onChange={(v) => set("diversity_strength", v)} />
          <Slider label="Familiar to adventurous (default)" value={form.default_adventure ?? 0.3} min={0} max={1} step={0.05} onChange={(v) => set("default_adventure", v)} />
          <Slider label="Most wildcards in a mix" hint="Ceiling for the slider" value={form.wildcard_max_share ?? 0.25} min={0} max={0.6} step={0.05} onChange={(v) => set("wildcard_max_share", v)} />
        </div>
        <div className="grid sm:grid-cols-3 gap-3">
          <label className={labelCls} title="Skip artists with fewer Last.fm listeners than this. 0 = no floor.">
            Min listeners
            <input type="number" min={0} className={inputCls} value={form.min_listeners ?? 0} onChange={num("min_listeners", 0)} />
          </label>
          <label className={labelCls} title="Skip artists with more listeners than this (you probably know them). 0 = no ceiling.">
            Max listeners
            <input type="number" min={0} className={inputCls} value={form.max_listeners ?? 0} onChange={num("max_listeners", 0)} />
          </label>
          <label className={labelCls} title="Releases newer than this get the freshness bonus.">
            Fresh release window (days)
            <input type="number" min={1} className={inputCls} value={form.freshness_days ?? 120} onChange={num("freshness_days", 120)} />
          </label>
          <label className={labelCls} title="Tag fit at or above this is a Close pick.">
            Close pick fit
            <input type="number" step={0.05} min={0} max={1} className={inputCls} value={form.close_fit ?? 0.5} onChange={num("close_fit", 0.5, true)} />
          </label>
          <label className={labelCls} title="Tag fit below this is a Wildcard.">
            Wildcard below fit
            <input type="number" step={0.05} min={0} max={1} className={inputCls} value={form.wildcard_fit ?? 0.2} onChange={num("wildcard_fit", 0.2, true)} />
          </label>
          <label className={labelCls} title="Most results led by the same seed artist before others move ahead.">
            Max per seed
            <input type="number" min={0} className={inputCls} value={form.max_per_seed ?? 4} onChange={num("max_per_seed", 4)} />
          </label>
          <label className={labelCls} title="How many top candidates get the slower tag, listener and release lookups.">
            Candidates to research
            <input type="number" min={10} max={300} className={inputCls} value={form.evidence_top_n ?? 60} onChange={num("evidence_top_n", 60)} />
          </label>
          <label className={labelCls} title="How long looked-up tags, listener counts and releases are reused.">
            Reuse lookups (days)
            <input type="number" min={1} max={90} className={inputCls} value={form.evidence_ttl_days ?? 14} onChange={num("evidence_ttl_days", 14)} />
          </label>
          <label className={labelCls} title="A suggestion counts as played if you listen within this many days.">
            Scorecard window (days)
            <input type="number" min={1} max={180} className={inputCls} value={form.scorecard_window_days ?? 30} onChange={num("scorecard_window_days", 30)} />
          </label>
        </div>
        <div className="flex flex-wrap gap-x-6 gap-y-2">
          <label className="flex items-center gap-2 text-sm text-slate-300"><input type="checkbox" checked={form.use_deezer_related ?? true} onChange={(e) => set("use_deezer_related", e.target.checked)} /> Deezer related artists</label>
          <label className="flex items-center gap-2 text-sm text-slate-300"><input type="checkbox" checked={form.use_listenbrainz_similar ?? true} onChange={(e) => set("use_listenbrainz_similar", e.target.checked)} /> ListenBrainz similar artists</label>
          <label className="flex items-center gap-2 text-sm text-slate-300"><input type="checkbox" checked={form.use_musicbrainz_releases ?? true} onChange={(e) => set("use_musicbrainz_releases", e.target.checked)} /> MusicBrainz new releases</label>
          <label className="flex items-center gap-2 text-sm text-slate-300" title="Needs LLM Assist configured in Integrations. Written from tags and seeds only, never from the model's memory.">
            <input type="checkbox" checked={form.blurbs_enabled ?? true} onChange={(e) => set("blurbs_enabled", e.target.checked)} /> One-line blurbs from the local LLM
          </label>
        </div>
      </Section>
    </div>
  );
}
