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
- **Season-pack detection (v0.5.1, Sonarr)**: releases named `S03` / `Season 3` / `S01-S03` / `Complete` are recognized as packs, corroborated against the sibling queue records and the actual files in the download (manual-import preview vs the season's aired episode list) — full coverage earns full confidence and the rationale suggests an **entire-season import** (a `Pack S03` badge marks the row; accepting imports every mappable file)
- Every match decision carries a deterministic per-variable **Match Notes** rationale (works with no LLM configured) — shown as a column and on the Match % tooltip; note columns word-wrap to the column width you set, with rows expanding to fit and a **Show more** toggle for full LLM rationales
- Weights, title-only cap, and the anime toggle live in Settings → Failed Import Matching
- Import push mirrors the proven `manualimport` flow and is **verified against history** afterward — silent failures surface back into triage
- Batch accept/reject, per-file mapping preview, manual match override, reject-and-remove-download (qBittorrent/Transmission), live SSE updates
- Stale rows auto-close when a download leaves the queue on its own
- **Orphan cleanup (v0.6.0, prompt-first since v0.6.1)**: pending suggestions whose download no longer exists in **any** configured download client — and whose recorded output path isn't on disk — are flagged for confirmation (`Confirm orphan` filter) with per-row **Confirm Orphan / Keep** buttons and batch confirm; confirming marks them `orphaned` (terminal). An **Auto-Purge Confirmed-Missing** toggle (Settings → Failed Import Matching, off by default) skips the prompt and marks them orphaned immediately. Absence requires positive confirmation from every client; unreachable clients — or an output path that can't be checked — skip the decision for that cycle, never inferring "missing" from an error

### Local LLM Assist (optional, off by default)
- Ollama native or OpenAI-compatible (`LM Studio`, `llama.cpp server`) endpoints
- Single structured review call per match: the LLM sees the deterministic scorer's per-variable results and returns `{agrees, confidence_adjustment, rationale}` — blended into confidence (never the sole signal); one-line deletion rationales on demand
- **Built for weak hardware (v0.7.0)**: a **Model Size Profile** (small/medium/large) scales reply-length caps and timeouts to the model; Ollama **keep-alive** keeps the model loaded between sequential batch calls; every prompt-injected value is hard-capped so a pathological release name or queue message can't blow a small context window; one LLM task runs at a time app-wide (batch runs and per-item explain share a single-flight guard)
- **Built for small models (v0.8.0)**: a **Minimal** verbosity tier (bare agree/disagree or KEEP/DELETE verdict — works even when a plain-text reasoning model like `lfm2.5` burns its whole token budget thinking); a **Simple reply format** (one pipe-separated line) for models that can't produce reliable JSON, with each format auto-falling back to parsing the other; a **Classified confidence** style that asks more/less/same instead of a calibrated float (mapped to fixed ±0.15 steps); unclosed `<think>` blocks are now stripped too, so truncated chain-of-thought can never leak into rationales
- **Cleanup rationales, cached + inline (v0.9.0)**: deletion rationales display inline on the Cleanup page (no more `alert()`), are **cached on the item** and served instantly until the prompt template, model config, or the item's score changes (content-hash cache key; rescores clear stale text); the Bot button becomes Explain/Regenerate accordingly; a **Run LLM on Unscored Candidates** batch runner mirrors the imports one (sequential, single-flight, results stream in live), with an optional **batch pacing delay** between calls for weak hardware
- Fails soft everywhere — no LLM, no problem

### Platform
- Password auth with **TOTP 2FA** and **LAN bypass** by CIDR (disabled by default; login is a pop-out modal)
- Dashboard: library totals, failed imports **by service**, auto-resolved (7d), space freed (30d), push failures
- ntfy push notifications, scheduled Plex sync, in-UI log viewer, `/api/v1/system/health` + Docker `HEALTHCHECK`
- DB-backed settings (PostgreSQL, SQLite fallback) — every knob changes live, no restarts

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
| API | Everything at `/api/v1/*`, interactive docs at `/api/docs` |

## Development

- Backend: Python 3.12, FastAPI, SQLAlchemy (`backend/app/`)
- Frontend: React 18 + TypeScript + Tailwind (`frontend/src/`), built into the same image
- Tests: `docker exec powarr python -m unittest discover -s app/tests`
- Schema changes are additive-only via `_migrate()` — no Alembic ceremony
- `docker-compose.yml` is intentionally gitignored (holds the DB password); `docker-compose.example.yml` is the tracked template
