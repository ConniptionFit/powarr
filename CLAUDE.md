# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Powarr: self-hosted FastAPI + React app for the Plex + *arr stack. It scores library items as deletion candidates, triages stuck/failed *arr imports (deterministic matcher plus optional local LLM second opinion), and runs Last.fm/Qdrant-based Artist Discovery and Smart Playlists. `README.md` is a long, versioned feature changelog (current app version is set in `backend/app/main.py`); consult it for the intent behind a feature before changing behavior.

## Commands

There is no Makefile, linter config, or CI. The app ships as one Docker image (`Dockerfile`: node build of `frontend/`, then Python image serving the built bundle from `static/`).

```bash
# Full stack
docker compose up --build -d            # copy docker-compose.example.yml first; docker-compose.yml is gitignored

# Backend (run from backend/; Python 3.12, deps in backend/requirements.txt)
pip install -r requirements.txt
POWARR_DATA_DIR=/tmp/powarr uvicorn app.main:app --port 7979 --reload
POWARR_DATA_DIR=/tmp/powarr python -m unittest discover -s app/tests          # all tests
POWARR_DATA_DIR=/tmp/powarr python -m unittest app.tests.test_tasks           # one module
POWARR_DATA_DIR=/tmp/powarr python -m unittest app.tests.test_tasks.TestBumpTotal.test_find_running   # one test

# Frontend (run from frontend/)
npm install
npm run dev      # Vite on :5173, proxies /api to localhost:7979
npm run build    # tsc && vite build (tsc is the only type check)
```

Tests are plain `unittest` (not pytest), under `backend/app/tests/`. Imports resolve as `app.*`, so run from `backend/`. Always set `POWARR_DATA_DIR` to a writable path; the default `/config` is the container volume. With `POWARR_DB_URL` unset the DB is SQLite at `$POWARR_DATA_DIR/powarr.db`.

## Architecture

**Backend (`backend/app/`)** layers: `api/v1/*` routers (all mounted under `/api/v1`) call `services/*` (business logic), which use `integrations/*` (one class per external service) and SQLAlchemy `models/*`. `schemas/*` hold Pydantic models, mostly the settings schemas.

- **Startup (`main.py`)**: `init_db()`, then seed/migrate helpers, then two long-lived asyncio tasks: `import_matcher.poller_loop` (failed-import detection/scoring, every scan interval) and `scheduler.maintenance_loop` (checks due-ness every 5 min: Plex sync, soft-delete purge, backups, digest, LLM backlog scans, artist discovery, playlists). New periodic work goes into the maintenance loop, not a new task.
- **Auth** is middleware in `main.py` gating all `/api/*` except `_AUTH_EXEMPT` paths; session/TOTP/LAN-CIDR-bypass logic is in `services/auth.py`.
- **Settings are DB-backed JSON, not env vars.** Each group is one `AppSetting` row (`key` -> JSON text) validated by a Pydantic class in `schemas/settings.py`; read with the `_get_setting(db, key, Schema)` pattern (see `services/scheduler.py`). Changes apply live with no restart. Integration credentials live in the `integrations` table; secrets are write-only through the API and optionally Fernet-encrypted (`services/secret_box.py`, `POWARR_FERNET_KEY`).
- **Schema changes are additive-only**: new tables come from `Base.metadata.create_all`; new columns on existing tables must be added to the `pending_by_table` dict in `database._migrate()`. There is no Alembic usage despite the dependency. A new model module must also be imported in `init_db()`.
- **Integrations**: subclass `integrations/base.BaseIntegration` (`test_connection`, `_headers`, per-integration circuit breaker via `services/circuit_breaker.py`). Use `shared_async_client()` (pooled per event loop) for chatty callers. A new service must be registered in `INTEGRATION_NAMES` in `api/v1/integrations.py` and seeded in `main._seed_integrations`.
- **Import matching** (`services/import_matcher.py`, ~2.5k lines, the core): deterministic per-variable confidence scoring, optional LLM review (`services/llm_assist.py`, `media_llm.py`, `llm_match_log.py`), then auto-import gated by the dual algorithm/LLM threshold settings, or manual triage. It writes to live Sonarr/Radarr/Lidarr/Readarr, so changes to the accept/push path need care (README documents past incidents, e.g. never pass `seriesId`/`movieId` or `folder`+`downloadId` together to `manualimport`).
- **Cleanup scoring**: `services/scorer.py` computes the weighted deletion score; `deleter.py` propagates deletes to the owning *arr app; soft-delete window and audit log (`deletion_log`) sit on top.
- **LLM assist** fails soft everywhere and runs one task at a time app-wide (single-flight guard) with a circuit breaker; the rest of the app must work with no LLM configured.
- **Live UI updates and the Active Processes tray**: long operations register with `services/tasks.py` (`create_task`/`bump_total`/`finish_task`, in-memory only) and progress is published over the SSE bus `import_matcher.publish()`, served at `/imports/events` (the name is historical; it carries all event kinds). Fire-and-forget coroutines must go through `tasks.spawn_background` to keep a strong reference.
- **Artist Discovery / Smart Playlists** (`services/artist_discovery.py`, `playlist_generator.py`, `embeddings.py`): Last.fm and Ollama embeddings feed a shared Qdrant collection (`music_affinity_space`), with candidates landing in a review queue and approved adds going to Lidarr. Ollama embeddings are optional; the graph lane works without vectors.

**Frontend (`frontend/src/`)**: React 18 + TypeScript + Tailwind + TanStack Query + react-router. All HTTP goes through `lib/api.ts` (base `/api/v1`). Navigation structure lives in `lib/navConfig.tsx` (icon rail areas with tabs); pages are under `pages/<Area>/`. Per-user view state (filters, sort, column layout) persists in localStorage via `lib/usePersistedState.ts`. Task/tray state comes from `context/TaskContext.tsx`.

## Conventions worth knowing

- Backend code comments tag changes with the release that introduced them (`v0.xx.x`) and audit IDs (e.g. `AD-24`, `SEC-05`). Follow that style and bump the `version=` in `main.py` plus a README entry for user-visible changes.
- Tests are named by feature or audit ID (e.g. `test_ad24_optional_embeddings.py`).
