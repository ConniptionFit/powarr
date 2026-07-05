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
- Import push mirrors the proven `manualimport` flow and is **verified against history** afterward — silent failures surface back into triage
- Batch accept/reject, per-file mapping preview, manual match override, reject-and-remove-download (qBittorrent/Transmission), live SSE updates
- Stale rows auto-close when a download leaves the queue on its own

### Local LLM Assist (optional, off by default)
- Ollama native or OpenAI-compatible (`LM Studio`, `llama.cpp server`) endpoints
- Blended into match confidence (never the sole signal); one-line deletion rationales on demand
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
| Download clients | API key field takes `username:password` |
| Auto-resolve | Off by default — writes to live *arr apps; enable in Settings → Failed Import Matching once you trust the match quality |
| Auth | Off by default; enable in Settings → Security. LAN CIDRs bypass login; TOTP works with any authenticator app |
| API | Everything at `/api/v1/*`, interactive docs at `/api/docs` |

## Development

- Backend: Python 3.12, FastAPI, SQLAlchemy (`backend/app/`)
- Frontend: React 18 + TypeScript + Tailwind (`frontend/src/`), built into the same image
- Tests: `docker exec powarr python -m unittest discover -s app/tests`
- Schema changes are additive-only via `_migrate()` — no Alembic ceremony
- `docker-compose.yml` is intentionally gitignored (holds the DB password); `docker-compose.example.yml` is the tracked template
