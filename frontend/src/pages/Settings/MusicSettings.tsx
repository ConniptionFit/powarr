import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Compass, ListMusic, Info } from "lucide-react";
import { req } from "../../lib/api";

interface ADSettings {
  enabled: boolean;
  ollama_host: string;
  embed_model: string;
  max_candidates_per_run: number;
  related_artists_limit: number;
  suggest_connection_threshold: number;
  auto_add_connection_threshold: number;
  related_artists_refresh_days: number;
  similarity_threshold: number;
  scrobble_lookback_days: number;
  auto_promote: boolean;
  thumbnail_retention_days: number;
  recent_taste_lane_enabled: boolean;
  root_folder_path: string;
  quality_profile_id: number;
  metadata_profile_id: number;
  schedule_enabled: boolean;
  schedule_interval_hours: number;
  sync_schedule_enabled: boolean;
  sync_interval_hours: number;
}

interface SPSettings {
  enabled: boolean;
  auto_create_playlists: boolean;
  auto_update_playlists: boolean;
  auto_add_tracks_default: boolean;
  min_artists_per_genre: number;
  excluded_genres: string[];
  blacklisted_artists: string[];
  max_tracks_per_playlist: number;
  schedule_enabled: boolean;
  schedule_interval_hours: number;
  llm_playlist_names: boolean;
  sonic_similarity_enabled: boolean;
  genre_aliases: Record<string, string>;
  playlist_templates: Record<string, string[]>;
  prune_stale_tracks_enabled: boolean;
}

interface LidarrProfiles {
  root_folders: { path: string }[];
  quality_profiles: { id: number; name: string }[];
  metadata_profiles: { id: number; name: string }[];
}

const labelCls = "text-xs text-slate-400 block";
const inputCls = "mt-1 w-full bg-surface border border-purple-900/40 rounded px-3 py-1.5 text-sm text-white";

// SP-14 — genre_aliases is a map (alias -> canonical label); edited as plain
// "alias = canonical" lines so it fits the same lightweight text-field
// pattern as excluded_genres, rather than a bespoke key/value row editor.
function aliasesToText(aliases: Record<string, string> | undefined): string {
  return Object.entries(aliases || {}).map(([a, c]) => `${a} = ${c}`).join("\n");
}
function textToAliases(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const [alias, canonical] = line.split("=").map(s => s.trim());
    if (alias && canonical) out[alias] = canonical;
  }
  return out;
}

// SP-12 — playlist_templates is a map (template name -> genre list); same
// "name = value(s)" line pattern as genre_aliases above, comma-separated
// genres on the right. Empty by default — which genres mean "Workout" vs
// "Focus" is a taste judgment left entirely to the user.
function templatesToText(templates: Record<string, string[]> | undefined): string {
  return Object.entries(templates || {}).map(([name, genres]) => `${name} = ${genres.join(", ")}`).join("\n");
}
function textToTemplates(text: string): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const line of text.split("\n")) {
    const [name, genreList] = line.split("=");
    const trimmedName = name?.trim();
    const genres = (genreList || "").split(",").map(g => g.trim()).filter(Boolean);
    if (trimmedName && genres.length > 0) out[trimmedName] = genres;
  }
  return out;
}

function QdrantHint() {
  return (
    <p className="flex items-center gap-1.5 text-xs text-slate-500 bg-surface rounded px-3 py-2 border border-purple-900/20">
      <Info size={12} className="shrink-0" />
      Qdrant connection is shared — configure it once on the Integrations tab.
    </p>
  );
}

function ArtistDiscoverySettingsCard() {
  const qc = useQueryClient();
  const { data: settings } = useQuery({ queryKey: ["ad-settings"], queryFn: () => req<ADSettings>("/artist-discovery/settings") });
  const { data: profiles } = useQuery({ queryKey: ["ad-profiles"], queryFn: () => req<LidarrProfiles>("/artist-discovery/lidarr/profiles") });
  const [draft, setDraft] = useState<Partial<ADSettings> | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const form = draft ?? settings ?? null;

  const saveMut = useMutation({
    mutationFn: () => req<ADSettings>("/artist-discovery/settings", { method: "PUT", body: JSON.stringify(form || {}) }),
    onSuccess: () => { setDraft(null); setMsg("Saved"); qc.invalidateQueries({ queryKey: ["ad-settings"] }); },
    onError: (e: Error) => setMsg(e.message),
  });

  const set = <K extends keyof ADSettings>(k: K, v: ADSettings[K]) =>
    setDraft(prev => ({ ...(prev ?? settings ?? {}), [k]: v }));

  if (!form) return null;

  return (
    <div className="space-y-4">
      <label className="flex items-center gap-2 text-sm text-slate-300"
        title="Master toggle. Off disables ingestion, centroid discovery, graph sync, and both schedules below.">
        <input type="checkbox" checked={!!form.enabled} onChange={e => set("enabled", e.target.checked)} />
        Enabled
      </label>

      <QdrantHint />

      <div className="grid sm:grid-cols-2 gap-3">
        <label className={labelCls}
          title="Standalone Ollama connection used only for artist-embedding calls — never shares a host/config with Local LLM Assist, even if both point at the same server.">
          Ollama host <span className="text-slate-600">(standalone — independent of LLM Assist)</span>
          <input className={inputCls} value={form.ollama_host || ""} onChange={e => set("ollama_host", e.target.value)} placeholder="http://10.1.1.x:11434" />
        </label>
        <label className={labelCls}
          title="Ollama model that turns an artist's name + tags into the taste vector stored in Qdrant (default all-minilm). Changing this after artists are already tracked can mismatch vector dimensions against existing points — best set once, before first use.">
          Embedding model
          <input className={inputCls} value={form.embed_model || ""} onChange={e => set("embed_model", e.target.value)} />
        </label>
      </div>

      <div className="border-t border-purple-900/20 pt-4">
        <label className="flex items-center gap-2 text-sm text-slate-300"
          title="AD-17 — adds a second discovery lane seeded from artists you've actually listened to recently (same Scrobble lookback window below), alongside the existing all-time most-played centroid. Purely additive — finds more candidates, never removes any.">
          <input type="checkbox" checked={form.recent_taste_lane_enabled ?? true}
            onChange={e => set("recent_taste_lane_enabled", e.target.checked)} />
          Recent-taste discovery lane
        </label>
      </div>

      <div className="border-t border-purple-900/20 pt-4 grid sm:grid-cols-3 gap-3">
        <label className={labelCls}
          title="Minimum cosine similarity (0–1) an undiscovered artist must have to your taste centroid (built from your top 15 most-played discovered artists) to surface as a candidate. Higher = stricter, fewer but closer matches.">
          Similarity threshold
          <input type="number" step="0.01" min="0" max="1" className={inputCls}
            value={form.similarity_threshold ?? 0.75} onChange={e => set("similarity_threshold", parseFloat(e.target.value))} />
        </label>
        <label className={labelCls}
          title="Caps how many new centroid-similarity candidates a single discovery cycle can create.">
          Max candidates / run
          <input type="number" className={inputCls}
            value={form.max_candidates_per_run ?? 5} onChange={e => set("max_candidates_per_run", parseInt(e.target.value))} />
        </label>
        <label className={labelCls}
          title="How many of each seed artist's Last.fm similar artists to process per graph-sync pass (Last.fm returns up to 15; this trims further). Higher finds more candidates per seed but does more enrichment work per cycle.">
          Related artists / seed
          <input type="number" className={inputCls}
            value={form.related_artists_limit ?? 3} onChange={e => set("related_artists_limit", parseInt(e.target.value))} />
        </label>
        <label className={labelCls}
          title="Minimum number of recently-listened seed artists a related artist must connect to before it appears in the Suggested Artists queue. Below this, the connection is tracked but nothing is shown yet.">
          Suggest threshold (graph)
          <input type="number" min="1" className={inputCls}
            value={form.suggest_connection_threshold ?? 3}
            onChange={e => set("suggest_connection_threshold", parseInt(e.target.value))} />
          <span className="block text-[10px] text-slate-600 mt-0.5">
            Recent-listen connections to show in Suggested Artists
          </span>
        </label>
        <label className={labelCls}
          title="Minimum recently-listened connection count to skip the review queue and add straight to Lidarr. 0 disables auto-add entirely — everything above the suggest threshold just queues instead.">
          Auto-add threshold (graph)
          <input type="number" min="0" className={inputCls}
            value={form.auto_add_connection_threshold ?? 0}
            onChange={e => set("auto_add_connection_threshold", parseInt(e.target.value))} />
          <span className="block text-[10px] text-slate-600 mt-0.5">
            0 = off. At/above this count → Lidarr, skip suggested queue
          </span>
        </label>
        <label className={labelCls}
          title="Suggest/auto-add connection counts only count seed artists you've actually listened to (via Last.fm recent tracks) within this many days — long-monitored-but-unplayed artists don't count toward either threshold.">
          Scrobble lookback (days)
          <input type="number" className={inputCls}
            value={form.scrobble_lookback_days ?? 30} onChange={e => set("scrobble_lookback_days", parseInt(e.target.value))} />
          <span className="block text-[10px] text-slate-600 mt-0.5">
            Only count connections to artists heard in this window
          </span>
        </label>
        <label className={labelCls}
          title="How many days before a monitored Lidarr artist (a graph 'seed') is queried against Last.fm's related-artist API again. Each graph sync re-scans up to 5 of the most-overdue seeds, oldest first.">
          Seed re-scan (days)
          <input type="number" className={inputCls}
            value={form.related_artists_refresh_days ?? 30} onChange={e => set("related_artists_refresh_days", parseInt(e.target.value))} />
        </label>
        <label className={labelCls}
          title="Clears the stored thumbnail image on accepted artists this many days after acceptance, to avoid holding enrichment art indefinitely. Bio and years-active are kept. 0 = never purge.">
          Thumbnail retention (days)
          <input type="number" min="0" className={inputCls}
            value={form.thumbnail_retention_days ?? 30}
            onChange={e => set("thumbnail_retention_days", parseInt(e.target.value))} />
          <span className="block text-[10px] text-slate-600 mt-0.5">
            Purge art on accepted artists after N days (0 = keep)
          </span>
        </label>
      </div>

      <div className="border-t border-purple-900/20 pt-4 grid sm:grid-cols-3 gap-3">
        <label className={labelCls} title="Used when Accept adds a candidate to Lidarr. Leave as First available to use whatever Lidarr itself returns as its default.">
          Root folder
          <select className={inputCls} value={form.root_folder_path || ""} onChange={e => set("root_folder_path", e.target.value)}>
            <option value="">First available</option>
            {profiles?.root_folders.map(f => <option key={f.path} value={f.path}>{f.path}</option>)}
          </select>
        </label>
        <label className={labelCls} title="Used when Accept adds a candidate to Lidarr. Leave as First available to use whatever Lidarr itself returns as its default.">
          Quality profile
          <select className={inputCls} value={form.quality_profile_id || 0} onChange={e => set("quality_profile_id", parseInt(e.target.value))}>
            <option value={0}>First available</option>
            {profiles?.quality_profiles.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        <label className={labelCls} title="Used when Accept adds a candidate to Lidarr. Leave as First available to use whatever Lidarr itself returns as its default.">
          Metadata profile
          <select className={inputCls} value={form.metadata_profile_id || 0} onChange={e => set("metadata_profile_id", parseInt(e.target.value))}>
            <option value={0}>First available</option>
            {profiles?.metadata_profiles.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
      </div>

      <p className="text-xs text-slate-500 border-t border-purple-900/20 pt-4">
        Graph candidates use Qdrant connection counts to <em>recently listened</em> artists only.
        Below the suggest threshold: ignored. Between suggest and auto-add: Suggested Artists queue.
        At/above auto-add (when set &gt; 0): added to Lidarr and skipped in the queue.
        Centroid (taste-match) candidates always queue regardless.
      </p>

      <div className="border-t border-purple-900/20 pt-4 grid sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <label className="flex items-center gap-2 text-sm text-slate-300"
            title="Automatically runs Last.fm ingestion + centroid discovery + graph sync on this interval — the same work as clicking Run Discovery Now on the Artist Discovery page.">
            <input type="checkbox" checked={!!form.schedule_enabled} onChange={e => set("schedule_enabled", e.target.checked)} />
            Scheduled discovery cycle
          </label>
          {form.schedule_enabled && (
            <label className={labelCls}>
              Interval (hours)
              <input type="number" className={inputCls}
                value={form.schedule_interval_hours ?? 24} onChange={e => set("schedule_interval_hours", parseInt(e.target.value))} />
            </label>
          )}
        </div>
        <div className="space-y-2">
          <label className="flex items-center gap-2 text-sm text-slate-300"
            title="Automatically refreshes Lidarr monitored/fulfillment status and Last.fm play counts on every existing Qdrant point on this interval — independent of the discovery cycle above (which also runs this sync once as its first step). Can also be triggered manually via Sync Now here or the Full Sync button on the Qdrant card in Settings → Integrations.">
            <input type="checkbox" checked={!!form.sync_schedule_enabled} onChange={e => set("sync_schedule_enabled", e.target.checked)} />
            Scheduled differential sync
          </label>
          {form.sync_schedule_enabled && (
            <label className={labelCls}>
              Interval (hours)
              <input type="number" className={inputCls}
                value={form.sync_interval_hours ?? 1} onChange={e => set("sync_interval_hours", parseInt(e.target.value))} />
            </label>
          )}
        </div>
      </div>

      <div className="flex items-center gap-3 pt-2">
        <button onClick={() => saveMut.mutate()} disabled={saveMut.isPending}
          className="px-3 py-1.5 rounded-lg bg-brand hover:bg-brand-dark text-white text-sm disabled:opacity-50">
          Save
        </button>
        {msg && <span className="text-xs text-slate-400">{msg}</span>}
      </div>
    </div>
  );
}

function PlaylistsSettingsCard() {
  const qc = useQueryClient();
  const { data: settings } = useQuery({ queryKey: ["sp-settings"], queryFn: () => req<SPSettings>("/smart-playlists/settings") });
  const [draft, setDraft] = useState<Partial<SPSettings> | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  // Kept as raw text separately from the parsed genre_aliases object so an
  // incomplete "alias = " line mid-typing isn't silently dropped by the
  // round-trip through textToAliases/aliasesToText on every keystroke.
  const [aliasText, setAliasText] = useState<string | null>(null);
  const [templateText, setTemplateText] = useState<string | null>(null);
  const form = draft ?? settings ?? null;

  const saveMut = useMutation({
    mutationFn: () => req<SPSettings>("/smart-playlists/settings", { method: "PUT", body: JSON.stringify(form || {}) }),
    onSuccess: () => { setDraft(null); setAliasText(null); setTemplateText(null); setMsg("Saved"); qc.invalidateQueries({ queryKey: ["sp-settings"] }); },
    onError: (e: Error) => setMsg(e.message),
  });

  const set = <K extends keyof SPSettings>(k: K, v: SPSettings[K]) =>
    setDraft(prev => ({ ...(prev ?? settings ?? {}), [k]: v }));

  if (!form) return null;

  return (
    <div className="space-y-4">
      <label className="flex items-center gap-2 text-sm text-slate-300">
        <input type="checkbox" checked={!!form.enabled} onChange={e => set("enabled", e.target.checked)} />
        Enabled
      </label>

      <QdrantHint />

      <div className="grid sm:grid-cols-2 gap-3">
        <label className={labelCls}>
          Min artists per genre
          <input type="number" className={inputCls}
            value={form.min_artists_per_genre || 3} onChange={e => set("min_artists_per_genre", parseInt(e.target.value))} />
        </label>
        <label className={labelCls}>
          Max tracks per playlist
          <input type="number" className={inputCls}
            value={form.max_tracks_per_playlist || 200} onChange={e => set("max_tracks_per_playlist", parseInt(e.target.value))} />
        </label>
        <label className={`${labelCls} sm:col-span-2`}>
          Excluded genres <span className="text-slate-600">(comma-separated)</span>
          <input className={inputCls} value={(form.excluded_genres || []).join(", ")}
            onChange={e => set("excluded_genres", e.target.value.split(",").map(g => g.trim()).filter(Boolean))} />
        </label>
        <label className={`${labelCls} sm:col-span-2`}>
          Genre aliases <span className="text-slate-600">(one per line, "alias = canonical name")</span>
          <textarea className={`${inputCls} font-mono`} rows={3} placeholder={"Rap = Hip-Hop"}
            value={aliasText ?? aliasesToText(form.genre_aliases)}
            onChange={e => { setAliasText(e.target.value); set("genre_aliases", textToAliases(e.target.value)); }} />
          <span className="block text-slate-600 mt-1">
            Case/punctuation variants ("Hip-Hop" vs "Hip Hop") always merge automatically — use this for
            genuinely different-looking labels you want treated as the same genre.
          </span>
        </label>
        <label className={`${labelCls} sm:col-span-2`}
          title="SP-12 — a named playlist generated from the UNION of several genres instead of one. Which genres mean 'Workout' vs 'Focus' is entirely up to you.">
          Playlist templates <span className="text-slate-600">(one per line, "Name = Genre1, Genre2, ...")</span>
          <textarea className={`${inputCls} font-mono`} rows={3} placeholder={"Workout = Rock, Electronic, Hip-Hop"}
            value={templateText ?? templatesToText(form.playlist_templates)}
            onChange={e => { setTemplateText(e.target.value); set("playlist_templates", textToTemplates(e.target.value)); }} />
          <span className="block text-slate-600 mt-1">
            Each template becomes its own playlist from the combined artist pool of its listed genres.
          </span>
        </label>
      </div>

      <p className="text-xs text-slate-500 border-t border-purple-900/20 pt-4">
        Artist blacklist is managed on the <Link to="/music/playlists" className="text-brand-light underline">Playlists</Link> page
        (blacklist-only model — all artists are included unless listed there).
      </p>

      <label className="flex items-start gap-2 text-sm text-slate-300 border-t border-purple-900/20 pt-4">
        <input type="checkbox" className="mt-0.5" checked={!!form.auto_create_playlists}
          onChange={e => set("auto_create_playlists", e.target.checked)} />
        <span>
          Auto-create new Plex playlists on scheduled runs
          <span className="block text-xs text-slate-500">
            Off by default — new genre playlists stay as drafts until you Approve them on the Playlists page.
          </span>
        </span>
      </label>

      <label className="flex items-start gap-2 text-sm text-slate-300">
        <input type="checkbox" className="mt-0.5" checked={!!form.auto_update_playlists}
          onChange={e => {
            set("auto_update_playlists", e.target.checked);
            set("auto_add_tracks_default", e.target.checked);
          }} />
        <span>
          Auto-update approved playlists
          <span className="block text-xs text-slate-500">
            On by default. After the artist DB refresh, scheduled runs add new eligible tracks
            to playlists already pushed to Plex.
          </span>
        </span>
      </label>

      <label className="flex items-start gap-2 text-sm text-slate-300">
        <input type="checkbox" className="mt-0.5" checked={!!form.llm_playlist_names} onChange={e => set("llm_playlist_names", e.target.checked)} />
        <span>
          LLM-generated playlist names
          <span className="block text-xs text-slate-500">
            Uses Local LLM Assist for Spotify-style names at create time. You can also
            regenerate on demand from the Playlists page. Falls back to "Powarr · genre".
          </span>
        </span>
      </label>

      <label className="flex items-start gap-2 text-sm text-slate-300"
        title="Uses Plex's own Sonic Analysis (the same data behind a track's 'Sonically Similar' Related tab) to prefer tracks close to the playlist's most recently added track when trimming an artist's candidates to Max tracks per playlist. Pure re-ranking on top of genre/artist eligibility — never changes which artists are included. Requires Plex Pass and analysis having actually been run on your Music library; silently falls back to the prior arbitrary pick when analysis data isn't available for a track.">
        <input type="checkbox" className="mt-0.5" checked={!!form.sonic_similarity_enabled}
          onChange={e => set("sonic_similarity_enabled", e.target.checked)} />
        <span>
          Sonic similarity track bias
          <span className="block text-xs text-slate-500">
            Off by default. Requires Plex Sonic Analysis (Plex Pass) to have run on your library.
          </span>
        </span>
      </label>

      <label className="flex items-start gap-2 text-sm text-slate-300"
        title="SP-13 — playlists were add-only before this: a track never left the playlist once added, even if its artist got blacklisted later or the track itself left Plex (deleted, moved libraries). When on, each generation cycle removes tracks matching either condition from both the real Plex playlist and Powarr's own ledger. Off by default since this removes content from a playlist you curated.">
        <input type="checkbox" className="mt-0.5" checked={!!form.prune_stale_tracks_enabled}
          onChange={e => set("prune_stale_tracks_enabled", e.target.checked)} />
        <span>
          Prune stale tracks
          <span className="block text-xs text-slate-500">
            Off by default. Removes tracks whose artist is now blacklisted or that have left Plex.
          </span>
        </span>
      </label>

      <div className="border-t border-purple-900/20 pt-4 space-y-3">
        <label className="flex items-center gap-2 text-sm text-slate-300">
          <input type="checkbox" checked={!!form.schedule_enabled} onChange={e => set("schedule_enabled", e.target.checked)} />
          Scheduled generation
        </label>
        {form.schedule_enabled && (
          <label className={labelCls}>
            Interval (hours)
            <input type="number" className={inputCls}
              value={form.schedule_interval_hours || 24} onChange={e => set("schedule_interval_hours", parseInt(e.target.value))} />
          </label>
        )}
      </div>

      <div className="flex items-center gap-3 pt-2">
        <button onClick={() => saveMut.mutate()} disabled={saveMut.isPending}
          className="px-3 py-1.5 rounded-lg bg-brand hover:bg-brand-dark text-white text-sm disabled:opacity-50">
          Save
        </button>
        {msg && <span className="text-xs text-slate-400">{msg}</span>}
      </div>
    </div>
  );
}

export default function MusicSettings() {
  const [tab, setTab] = useState<"discovery" | "playlists">("discovery");

  return (
    <div className="bg-surface-raised rounded-xl border border-purple-900/30 px-6 mt-6">
      <div className="flex items-center gap-1 pt-5 pb-3 border-b border-purple-900/20">
        <button
          onClick={() => setTab("discovery")}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
            tab === "discovery" ? "bg-brand/20 text-brand-light" : "text-slate-400 hover:text-white"}`}
        >
          <Compass size={14} /> Artist Discovery
        </button>
        <button
          onClick={() => setTab("playlists")}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
            tab === "playlists" ? "bg-brand/20 text-brand-light" : "text-slate-400 hover:text-white"}`}
        >
          <ListMusic size={14} /> Playlists
        </button>
      </div>
      <div className="py-5">
        {tab === "discovery" ? <ArtistDiscoverySettingsCard /> : <PlaylistsSettingsCard />}
      </div>
    </div>
  );
}
