# Powarr

⚡ Self-hosted media cleanup and failed-import triage for the Plex + *arr stack.

Powarr connects to your Plex library and scores every item as a **deletion candidate** based on configurable weighted factors — then goes further: it watches your **Sonarr / Radarr / Lidarr / Readarr queues for stuck imports**, confidence-matches them against grab history and your library, and lets you resolve them in one click (or automatically). An optional **local LLM** (Ollama or any OpenAI-compatible server) supplies a second opinion on tricky matches.

Successor to the original Node.js Powarr completed-downloads monitor, rebuilt on FastAPI + React.

---

## Features

### Cleanup (deletion scoring)
- Scores every movie, episode, and track by watch history, file size, file age, and release age — all weights tunable live
- By-Show / By-Episode views, library exclusion rules, ignore list
- **Seerr request protection** — recently requested media is never suggested
- Optional **soft-delete window** with restore, full deletion audit log + space-freed stats
- Deletion propagates to the owning *arr app (unmonitor or delete, per service)

### Failed Import Matching
- Background poller detects stuck queue items (`importPending`/`importFailed`/`importBlocked`, warnings, optionally stalled)
- Confidence scoring: *arr's own mapping > grab-history downloadId match > fuzzy title match; auto-resolve above a threshold (off by default), triage UI between the floor and threshold
- **Multi-variable episode matching (v0.5.0, Sonarr)**: episode title (heaviest factor, configurable weight), season/episode numbers, and **anime absolute episode numbers** (`seriesType: anime`) with stale-data guards and S/E fallback; title-only matches are capped below auto-resolve
- **Title-similarity punctuation fix + paired-episode corroboration (v0.16.0)**: series-title similarity now strips commas/colons/apostrophes before comparing (a library title's punctuation, absent from the release filename, used to defeat the containment-bonus scoring path and understate otherwise-exact matches); episode-number mismatches are now corroborated against Sonarr's own manual-import per-file resolution before being scored as a mismatch — catches paired/segment-numbered releases (an uploader packing 2+ canonical episodes into one file under its own numbering) that a naive filename regex alone can't explain
- **Season-pack detection (v0.5.1, Sonarr)**: releases named `S03` / `Season 3` / `S01-S03` / `Complete` are recognized as packs, corroborated against the sibling queue records and the actual files in the download (manual-import preview vs the season's aired episode list) — full coverage earns full confidence and the rationale suggests an **entire-season import** (a `Pack S03` badge marks the row; accepting imports every mappable file)
- Every match decision carries a deterministic per-variable **Match Notes** rationale (works with no LLM configured) — shown as a column and on the Match % tooltip; note columns word-wrap to the column width you set, with rows expanding to fit and a **Show more** toggle for full LLM rationales
- Weights, title-only cap, and the anime toggle live in Settings → Failed Import Matching
- Import push mirrors the proven `manualimport` flow and is **verified against history** afterward — silent failures surface back into triage
- Batch accept/reject, per-file mapping preview, manual match override, reject-and-remove-download (qBittorrent/Transmission), live SSE updates
- **Editable per-file episode mapping (v0.17.0, Sonarr)**: click any file's **Mapped To** cell in the expanded file-details table to open a type-to-filter episode picker (scoped to the matched series, defaults to the currently-mapped episode) — corrections persist (`PUT /imports/{id}/file-mapping`), show green **Overridden**, and are threaded into the actual `ManualImport` command at accept time, not just displayed. Replaces the v0.15.0 Pack Episodes/Select Episode columns, which only worked after an LLM pack review and sat blank otherwise.
- **Quality-downgrade detection (v0.17.0, Sonarr)**: downloads where every file rejects as "not an upgrade" over an existing library file (will never import as-is) get a **Downgrade** badge and a dedicated filter chip, computed once per newly-detected row. Optional **Auto-Reject Quality Downgrades** toggle (Settings → Failed Import Matching, off by default) skips triage for them entirely — and removes the entry from the *arr app's own queue too (v0.18.0), not just Powarr's triage table.
- **Suspicious file-type detection (v0.19.0, all apps)**: any file in a download matching a configurable extension list (pre-populated with executable/script types — `.exe`, `.scr`, `.bat`, `.js`, etc.; archive formats excluded by default since most legitimate downloads arrive compressed) gets a **Suspicious** badge and filter chip — one match is enough, unlike quality-downgrade. The extension list is user-editable in Settings → Failed Import Matching. Optional **Auto-Reject Suspicious File Types** toggle (off by default) skips triage entirely, with a further optional **Also Delete From Disk** toggle that removes the whole download via the download client's own delete API (no per-file delete exists across qBittorrent/Transmission, so this removes every file in the download, not just the flagged one).
- **Pack/single filter + queued LLM runs (v0.21.0)**: the triage table has **Packs only** / **Singles only** filter chips alongside the existing status/Downgrade/Suspicious filters. An on-demand LLM run requested while one is already active no longer fails with "already in progress" — it queues and starts automatically once the current run finishes, shown with a `ListEnd` icon and queue position.
- Stale rows auto-close when a download leaves the queue on its own
- **Orphan cleanup (v0.6.0, prompt-first since v0.6.1)**: pending suggestions whose download no longer exists in **any** configured download client — and whose recorded output path isn't on disk — are flagged for confirmation (`Confirm orphan` filter) with per-row **Confirm Orphan / Keep** buttons and batch confirm; confirming marks them `orphaned` (terminal). An **Auto-Purge Confirmed-Missing** toggle (Settings → Failed Import Matching, off by default) skips the prompt and marks them orphaned immediately — and removes the entry from the *arr app's own queue too (v0.18.0), clearing the stuck row from Sonarr/Radarr/etc.'s own Activity view, not just Powarr's. Absence requires positive confirmation from every client; unreachable clients — or an output path that can't be checked — skip the decision for that cycle, never inferring "missing" from an error

### Local LLM Assist (optional, off by default)
- Ollama native or OpenAI-compatible (`LM Studio`, `llama.cpp server`) endpoints
- Single structured review call per match: the LLM sees the deterministic scorer's per-variable results and returns `{agrees, confidence_adjustment, rationale}` — blended into confidence (never the sole signal); one-line deletion rationales on demand. The **blend weight is adjustable** since v0.12.0 (Settings → Failed Import Matching, default 0.3 LLM share; 0 = deterministic only)
- **Built for weak hardware (v0.7.0)**: a **Model Size Profile** (small/medium/large) scales reply-length caps and timeouts to the model; Ollama **keep-alive** keeps the model loaded between sequential batch calls; every prompt-injected value is hard-capped so a pathological release name or queue message can't blow a small context window; one LLM task runs at a time app-wide (batch runs and per-item explain share a single-flight guard); requesting an import-matching run while one is active **queues it** instead of failing (v0.21.0) — it starts automatically once the current run finishes
- **Built for small models (v0.8.0)**: a **Minimal** verbosity tier (bare agree/disagree or KEEP/DELETE verdict — works even when a plain-text reasoning model like `lfm2.5` burns its whole token budget thinking); a **Simple reply format** (one pipe-separated line) for models that can't produce reliable JSON, with each format auto-falling back to parsing the other; a **Classified confidence** style that asks more/less/same instead of a calibrated float (mapped to fixed ±0.15 steps); unclosed `<think>` blocks are now stripped too, so truncated chain-of-thought can never leak into rationales
- **Cleanup rationales, cached + inline (v0.9.0)**: deletion rationales display inline on the Cleanup page (no more `alert()`), are **cached on the item** and served instantly until the prompt template, model config, or the item's score changes (content-hash cache key; rescores clear stale text); the Bot button becomes Explain/Regenerate accordingly; a **Run LLM on Unscored Candidates** batch runner mirrors the imports one (sequential, single-flight, results stream in live), with an optional **batch pacing delay** between calls for weak hardware
- **Trust-but-verify tooling (v0.10.0)**: the prompt editor shows a **live token estimate** (with worst-case injected data) against the model's **auto-detected context window** (Ollama `/api/show`), warning before a template outgrows the model; **Test with Real Data** dry-runs the saved prompt/model against one real item and reports output + latency; **Benchmark Model** sends a fixed match prompt and flags models too small for structured replies; a curated **known-good small-model preset list** fills the model plus a tuned profile in one click
- **Streaming rationales (v0.11.0)**: Explain/Regenerate on the Cleanup page streams tokens live as the model writes (verbose generations can take 45-60s — now you watch them arrive), with `<think>` blocks suppressed mid-stream (even tags split across chunks) and automatic fallback to the plain request when streaming isn't available; the finished text lands in the same cache
- **Enhanced pack matching (v0.13.0)**: LLM context now includes triggered-series metadata (what *arr grabbed the file for) as the primary signal — the most reliable source of truth when a season pack should match a specific series. Per-file episode review for season packs: one-click LLM analysis of all files in a download, returning per-file episode suggestions with reasoning — UI similar to Sonarr's manual-import interface, showing season, episode, confidence level, and rationale for each file
- **Concise pack match reasoning (v0.21.0)**: per-file pack review now classifies each match into a short closed vocabulary — Exact Match, Title Match, Number Match, Absolute Number Match, Multi-Episode File, Sequence Match, Low Confidence — shown as a colored badge in a new **Match Reasoning** column in the expanded file-details table (green = strongest evidence, red = needs a look), instead of free-text prose. A weaker local model on a large pack sometimes collapses its reply to a single file instead of the full array — that response is now salvaged as a one-item result rather than discarded entirely, though full per-file coverage on big packs still depends on the model's capability.
- **Year-mismatch hallucination guard (v0.19.0)**: weak local models occasionally reported a self-contradictory "year mismatch" between two identical years (e.g. "2025 vs 2025") — the match-review context now explicitly instructs the model not to report a mismatch between equal years, since a candidate title occasionally carries a disambiguating "(YYYY)" suffix (e.g. "Paradise (2025)") and the comparison is sometimes genuinely possible
- **Judging-guidance fixes + Markdown replies + structured agree/disagree (v0.20.0)**: the match-review prompt now explicitly tells the model to ignore file-quality/format/codec/uploader identifiers (resolution, codec, release-group tags — these describe the file, not the show) and to consider translations/alternate titles before penalizing low string similarity on foreign-language or anime releases. On-demand LLM runs (the Bot icon, "Run LLM on Selected", "Run LLM on Unscored Imports") now always request the most in-depth reasoning available, regardless of the configured default verbosity. `llm_rationale` is plain prose again — the `[agrees]`/`[disagrees]` text prefix is gone, replaced by a proper `llm_agrees` boolean shown as a thumbs-up/down icon next to the LLM confidence badge. New **Markdown** reply format (alongside JSON/Simple) asks the model to bold key terms and use bullet lists in its reasoning, rendered with a small built-in Markdown-subset renderer (no new dependency) — pick it from the Reply Format dropdown in Settings → LLM Assist.
- **Clickable prompt placeholders (v0.20.0)**: the prompt-template editor in Settings → LLM Assist now shows each task's placeholders (`{release}` `{candidate}` `{context}` for matching; `{item}` for deletion rationale) as buttons — click one to insert it at the current cursor position in the textarea, instead of having to type it by hand
- **Scheduled backlog scanning (v0.26.0)**: on top of the manual "Run Now" buttons, Settings → LLM Assist → Scheduled LLM Backlog Scanning can run the same failed-import/deletion-rationale backlog scans automatically — either only within a daily **quiet-hours window** (UTC) or as an **always-on trickle** every maintenance cycle (~5 min), capped at a configurable max items per pass. Off by default; reuses the existing single-flight guard and batch pacing delay, so it never competes with an on-demand run
- **Circuit breaker + call stats (v0.27.0)**: Settings → LLM Assist shows a live readout of every LLM call since startup (count, ok/failed, average latency, last error), and after N consecutive failures (default 5) the assist **auto-pauses** for a cooldown (default 10 min) instead of re-hitting a downed or overloaded host on every scan cycle — calls fail soft instantly while paused, and a Reset button closes the breaker early. In-memory only; counters reset with the container
- **Per-task toggles + models (v0.27.0)**: import matching (match review + pack files) and deletion rationales can be enabled and assigned a model **independently** in Settings → LLM Assist — e.g. a fast small model for match verdicts and a larger one for rationales. Blank model fields keep using the shared model from the Integrations page, so existing configs behave unchanged
- **Explain Visible batch button (v0.27.0)**: the Deletion Suggestions page can generate rationales for everything currently listed in one background run (already-cached items are skipped; progress shows in the Active Processes tray). The blend weight is now a **slider**
- Fails soft everywhere — no LLM, no problem

### Platform
- Password auth with **TOTP 2FA** and **LAN bypass** by CIDR (disabled by default; login is a pop-out modal)
- Dashboard: library totals, failed imports **by service**, auto-resolved (7d), space freed (30d), push failures, and (v0.18.0) live **Next Import Scan** / **Next Plex Sync** countdowns (`GET /system/schedule`) — replaces the old static "Getting Started" panel
- ntfy push notifications, scheduled Plex sync, in-UI log viewer, `/api/v1/system/health` + Docker `HEALTHCHECK`
- DB-backed settings (PostgreSQL, SQLite fallback) — every knob changes live, no restarts
- **Animated LLM-busy indicator (v0.21.0, superseded by the `BotState` icon set in v0.25.0)**: every "querying the LLM" button (per-item score, batch runs, pack review, deletion Explain) shows an animated version of the robot icon while a request is in flight, instead of a plain spin/pulse
- **Active Processes tray (v0.22.0)**: a bottom-right corner tray shows live progress for every tracked background operation — Plex/Seerr sync, *arr scans, LLM batch runs (import-matching and deletion-rationale), deletions — with a real progress bar when the total is known and an indeterminate shimmer otherwise, auto-dismissing a few seconds after each finishes. Replaces the old blocking `alert()` on Plex sync completion. Verified against a 137k-item Plex library sync and a multi-thousand-row scan in production.
- **Ten-state `BotState` icon set (v0.25.0)**: the plain CSS "thinking" bot from v0.21.0 is replaced by a ten-variant animated SVG component (`available`/`thinking`/`responding`/`idea`/`complete`/`error`/`idle`/etc.), wired to the actual LLM/task lifecycle — batch scoring and pack review show `thinking`, the deletion-rationale stream shows `responding`, and the Active Processes tray's LLM task card reflects `thinking`/`complete`/`error` per its live status, instead of one generic busy spinner
- **Mobile-friendly layout (v0.25.0)**: a slide-in hamburger nav replaces the fixed sidebar below the `md` breakpoint, page padding and grids collapse to a single column on narrow viewports, and the Deletion Suggestions / Deletion History tables scroll horizontally instead of clipping
- **Persisted view state (v0.27.0)**: the Cleanup tab, Failed Imports status/sort/Downgrade/Suspicious/pack filters, and Deletion Suggestions min-score/type/mode/sort now survive a reload (browser localStorage, like the column layout has since v0.4.0)
- **Triage UX batch (v0.28.0)**: click any blank space on a row to toggle its checkbox (Failed Imports + Deletion Suggestions); **Process N Items** batch-imports threshold-eligible suggestions when auto-resolve is enabled; large Accept batches run in the background with Active Processes tray progress (no more proxy 504s); search + conditional Radarr/Sonarr/Lidarr/Readarr filter chips on both triage tables; Lucide platform icons (Clapperboard/Tv/Music/Book) on source badges and chips
- **Gone downloads orphan on accept (v0.28.1)**: if *arr reports no importable files left for a download, Accept / Process N / auto-resolve mark the row **orphaned** with a warning instead of a push failure; each scan also clears stuck "no files" triage rows and orphans unverified accepts whose download is confirmed gone. Import-batch tray icon is a spinner (not a checkmark)
- **Servarr NullReference 500 fallback (v0.28.2)**: when Sonarr/Lidarr crash on `GET /manualimport?downloadId=…` for downloads whose files are already missing (HTTP 500 *"Object reference not set…"*), Powarr retries with `folder=` alone (never `folder`+`downloadId`, never `seriesId`), treats empty/crash as gone files, and orphans the row instead of surfacing a raw 500 in triage
- **Automated DB backups (v0.26.0)**: Settings → Automated Backups schedules `pg_dump` (or a plain file copy on the SQLite fallback) to `/config/backups` on an interval, with a configurable retention count and a manual "Run Now" button — on top of the existing manual `docker exec postgres pg_dump ...` flow in [Docker & Deployment]
- **ntfy click-to-act links (v0.26.0)**: opt-in Accept/Reject buttons on the ntfy notification for each new failed-import suggestion, via signed one-time links (7-day expiry, single-action-scoped, safe to replay — an already-resolved row is a no-op). Needs a reachable **Public Base URL** configured in Settings → Notifications; a scan with more new suggestions than the configured max falls back to the existing aggregate summary notification only

---

## Quick Start

```yaml
# docker-compose.yml (see docker-compose.example.yml)
services:
  powarr:
    build: .
    container_name: powarr
    ports:
      - "7979:7979"
    volumes:
      - ./data:/config
    restart: unless-stopped
    environment:
      - POWARR_DATA_DIR=/config
      # Optional — omit to use the SQLite fallback in /config
      - POWARR_DB_URL=postgresql://powarr:CHANGE_ME@postgres:5432/powarr
```

```bash
docker compose up --build -d
```

Then open `http://<host>:7979`:

1. **Integrations** → configure Plex (required) and any of Tautulli, Sonarr, Radarr, Lidarr, Readarr, Seerr, qBittorrent, Transmission, Ollama
2. Run **Sync Library**
3. **Cleanup** → review deletion suggestions and failed imports
4. **Settings** → tune scoring weights, matching thresholds, notifications, security

## Configuration Notes

| Topic | Detail |
|---|---|
| Reverse-proxied *arr apps | Include the base path in the URL (`http://host:8989/sonarr`); redirects are followed either way |
| Download clients | qBittorrent (v0.6.0): dedicated **Username/Password** fields (WebUI credentials; SID session cookie handled automatically, works on qBittorrent 4.x and 5.x). Transmission: API key field takes `username:password` |
| Auto-resolve | Off by default — writes to live *arr apps; enable in Settings → Failed Import Matching once you trust the match quality |
| Auth | Off by default; enable in Settings → Security. LAN CIDRs bypass login; TOTP works with any authenticator app |
| Integration secrets | API keys and download-client passwords are **write-only** (v0.23.0): the API never returns a stored secret, so the Integrations form shows a "saved — leave blank to keep" placeholder. Leave a secret field blank to keep the current value; type a new one only to change it |
| API | Everything at `/api/v1/*`, interactive docs at `/api/docs` |
| Automated backups | The image now installs `postgresql-client-16` (v0.26.0, matched to this deployment's Postgres 16 — pulled from the PGDG apt repo since Debian bookworm's own repo ships v15) so scheduled `pg_dump` can run inside the container |

## Development

- Backend: Python 3.12, FastAPI, SQLAlchemy (`backend/app/`)
- Frontend: React 18 + TypeScript + Tailwind (`frontend/src/`), built into the same image
- Tests: `docker exec powarr python -m unittest discover -s app/tests`
- Schema changes are additive-only via `_migrate()` — no Alembic ceremony
- `docker-compose.yml` is intentionally gitignored (holds the DB password); `docker-compose.example.yml` is the tracked template
