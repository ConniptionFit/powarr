"""Artist Discovery — direct lane (v2 engine).

The original pipeline (artist_discovery.py) needs Qdrant, an embeddings host and a
pile of accumulated "connection" counts before anything reaches Lidarr, and it can
fail without saying why. This lane is the simple, explainable path:

    Last.fm top artists for a period  ->  weighted seeds
    each seed's Last.fm similar artists  ->  scored, summed per candidate
    drop what you already own / rejected / blocked
    rank the rest  ->  queue for review, or add to Lidarr (within caps)

Every candidate it judges leaves a row in artist_discovery_decisions with a plain
reason, and a dry run returns the same decisions without touching anything.
No Qdrant, no Ollama.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.models.artist_discovery import (
    ArtistDiscoveryDecision, ArtistDiscoveryRun, DiscoveredArtist,
)
from app.schemas.settings import ArtistDiscoverySettings

logger = logging.getLogger("powarr")

SEED_PERIODS = ("7day", "1month", "3month", "6month", "12month", "overall")
LIDARR_MONITOR_OPTIONS = ("all", "future", "missing", "existing", "first", "latest", "none")

# The knobs a preset controls. "custom" uses whatever is stored on the settings.
PRESETS: dict[str, dict[str, Any]] = {
    "conservative": {
        "seed_period": "1month", "min_seed_plays": 5, "max_seeds": 25, "similar_per_seed": 10,
        "min_match": 0.5, "suggest_score": 0.9, "auto_add_score": 1.8, "auto_add_min_connections": 3,
        "max_adds_per_run": 1, "max_adds_per_day": 2, "max_queue_per_run": 10,
    },
    "balanced": {
        "seed_period": "3month", "min_seed_plays": 3, "max_seeds": 40, "similar_per_seed": 15,
        "min_match": 0.4, "suggest_score": 0.6, "auto_add_score": 1.2, "auto_add_min_connections": 2,
        "max_adds_per_run": 3, "max_adds_per_day": 5, "max_queue_per_run": 20,
    },
    "aggressive": {
        "seed_period": "3month", "min_seed_plays": 2, "max_seeds": 80, "similar_per_seed": 25,
        "min_match": 0.3, "suggest_score": 0.35, "auto_add_score": 0.8, "auto_add_min_connections": 1,
        "max_adds_per_run": 8, "max_adds_per_day": 15, "max_queue_per_run": 40,
    },
}
PRESET_DESCRIPTIONS = {
    "off": "Direct lane disabled. Only the legacy Qdrant lanes run.",
    "conservative": "Recent favourites only, strong matches, one add per run. Few surprises.",
    "balanced": "Last 3 months of listening, solid matches, a few adds per run. A good default.",
    "aggressive": "Wider seeds, looser matches, one strong link is enough. Expect misses.",
    "custom": "Use the individual settings below.",
}


def effective_settings(cfg: ArtistDiscoverySettings) -> ArtistDiscoverySettings:
    """cfg with the preset's knob values applied. "custom" (or an unknown name)
    leaves the stored knobs alone."""
    preset = PRESETS.get(cfg.aggressiveness)
    return cfg.model_copy(update=preset) if preset else cfg


def presets_payload() -> list[dict[str, Any]]:
    out = [{"name": "off", "description": PRESET_DESCRIPTIONS["off"], "values": {}}]
    for name, values in PRESETS.items():
        out.append({"name": name, "description": PRESET_DESCRIPTIONS[name], "values": values})
    out.append({"name": "custom", "description": PRESET_DESCRIPTIONS["custom"], "values": {}})
    return out


@dataclass
class _Candidate:
    name: str
    mbid: str | None = None
    score: float = 0.0
    best_match: float = 0.0
    seeds: dict[str, float] = field(default_factory=dict)  # seed name -> contribution


@dataclass
class _Decision:
    name: str
    action: str  # added | queued | skipped | failed  (would_* in dry runs)
    reason: str
    mbid: str | None = None
    score: float | None = None
    connections: int | None = None
    seeds: list[str] = field(default_factory=list)
    candidate_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"artist_name": self.name, "musicbrainz_id": self.mbid, "action": self.action,
                "reason": self.reason, "score": None if self.score is None else round(self.score, 3),
                "connections": self.connections, "seeds": self.seeds, "candidate_id": self.candidate_id}


def _seed_weight(plays: int, max_plays: int) -> float:
    if max_plays <= 0:
        return 0.0
    return math.log1p(max(plays, 0)) / math.log1p(max_plays)


def _as_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _as_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def adds_in_last_day(db) -> int:
    since = datetime.utcnow() - timedelta(hours=24)
    return (db.query(ArtistDiscoveryDecision)
            .filter(ArtistDiscoveryDecision.action == "added",
                    ArtistDiscoveryDecision.created_at >= since).count())


async def _fetch_similar(lastfm, sem: asyncio.Semaphore, seed: dict, limit: int):
    async with sem:
        try:
            return seed, await lastfm.get_similar_artists(seed["name"], seed.get("mbid"), limit=limit), None
        except Exception as e:  # one bad seed must not sink the run
            return seed, [], str(e)


def preflight(db, cfg: ArtistDiscoverySettings) -> dict[str, Any]:
    """Cheap config check shared by the run and the status endpoint. `blockers`
    stop the direct lane; `warnings` limit what it can do."""
    from app.services import artist_discovery as ad
    blockers: list[str] = []
    warnings: list[str] = []
    if not cfg.enabled:
        blockers.append("Artist Discovery is switched off (Settings → Music → Artist Discovery → Enable).")
    if not cfg.direct_lane_enabled or cfg.aggressiveness == "off":
        blockers.append("The direct lane is off (aggressiveness is 'off' or the lane is disabled).")
    if ad._lastfm_client(db) is None:
        blockers.append("Last.fm is not configured: enable it with an API key and username in Integrations.")
    if ad._lidarr_client(db) is None:
        warnings.append("Lidarr is not enabled, so candidates can be queued but never added, "
                        "and artists you already own can't be excluded.")
    elif not cfg.auto_add_enabled:
        warnings.append("Auto-add is off: candidates go to the review queue only.")
    return {"blockers": blockers, "warnings": warnings}


async def run_direct_discovery(db, cfg: ArtistDiscoverySettings, *, dry_run: bool = False,
                               run: ArtistDiscoveryRun | None = None) -> dict[str, Any]:
    """Run the direct lane once. With dry_run=True nothing is written (no candidate
    rows, no Lidarr adds, no decision log) and the decisions are returned instead."""
    from app.services import artist_discovery as ad
    from app.services.youtube_filter import is_non_music_channel

    cfg = effective_settings(cfg)
    pre = preflight(db, cfg)
    result: dict[str, Any] = {
        "ok": True, "dry_run": dry_run, "preset": cfg.aggressiveness,
        "blockers": pre["blockers"], "warnings": list(pre["warnings"]),
        "seeds": 0, "candidates_scored": 0, "queued": 0, "added": 0, "failed": 0, "skipped": 0,
        "skip_counts": {}, "decisions": [], "message": "",
    }
    if pre["blockers"]:
        result["ok"] = False
        result["message"] = "Direct lane did not run: " + pre["blockers"][0]
        return result

    lastfm = ad._lastfm_client(db)
    lidarr_row_ok = ad._lidarr_client(db) is not None
    run_id = run.id if run is not None else None

    # --- seeds --------------------------------------------------------------------
    period = cfg.seed_period if cfg.seed_period in SEED_PERIODS else "3month"
    try:
        top = await lastfm.get_top_artists(period=period, limit=max(cfg.max_seeds * 3, 50))
    except Exception as e:
        result["ok"] = False
        result["message"] = f"Last.fm top-artists fetch failed: {e}"
        return result
    seeds_all = []
    for a in top:
        name = (a.get("name") or "").strip()
        if name:
            seeds_all.append({"name": name, "mbid": (a.get("mbid") or "").strip() or None,
                              "plays": _as_int(a.get("playcount"))})
    # Everything you listen to in this period is "known" even if it isn't picked as a seed.
    known_norm = {ad._norm_artist(s["name"]) for s in seeds_all}
    seeds = []
    for s in seeds_all:
        if s["plays"] < cfg.min_seed_plays:
            continue
        if cfg.filter_youtube_channels:
            is_nm, _ = is_non_music_channel(s["name"], mbid=s["mbid"], blocked_channels=cfg.blocked_channels)
            if is_nm:
                continue
        seeds.append(s)
        if len(seeds) >= cfg.max_seeds:
            break
    result["seeds"] = len(seeds)
    if not seeds:
        result["message"] = (f"No seeds: Last.fm returned {len(seeds_all)} artist(s) for '{period}', "
                             f"none with at least {cfg.min_seed_plays} plays. Try a longer seed period "
                             f"or a lower minimum plays.")
        return result
    max_plays = max(s["plays"] for s in seeds)

    # --- score candidates -----------------------------------------------------------
    sem = asyncio.Semaphore(4)
    fetched = await asyncio.gather(*[_fetch_similar(lastfm, sem, s, cfg.similar_per_seed) for s in seeds])
    errors = [e for _, _, e in fetched if e]
    if errors and len(errors) == len(seeds):
        result["ok"] = False
        result["message"] = f"Last.fm similar-artist lookups all failed ({errors[0]})"
        return result
    if errors:
        result["warnings"].append(f"{len(errors)} of {len(seeds)} seed lookups failed ({errors[0]}).")

    cands: dict[str, _Candidate] = {}
    for seed, similar, _err in fetched:
        weight = _seed_weight(seed["plays"], max_plays)
        for r in similar:
            rname = (r.get("name") or "").strip()
            match = _as_float(r.get("match"))
            if not rname or match < cfg.min_match:
                continue
            key = ad._norm_artist(rname)
            c = cands.setdefault(key, _Candidate(name=rname))
            rmbid = (r.get("mbid") or "").strip() or None
            if rmbid and not c.mbid:
                c.mbid = rmbid
            contribution = weight * match
            # A seed counts once per candidate, at its best link.
            if contribution > c.seeds.get(seed["name"], 0.0):
                c.score += contribution - c.seeds.get(seed["name"], 0.0)
                c.seeds[seed["name"]] = contribution
            c.best_match = max(c.best_match, match)
    result["candidates_scored"] = len(cands)

    # --- ownership ------------------------------------------------------------------
    lidarr_by_mbid: dict = {}
    lidarr_by_name: dict = {}
    if lidarr_row_ok:
        try:
            idx = await ad._lidarr_artist_index(db)
            if idx:
                lidarr_by_mbid, lidarr_by_name = idx
        except Exception as e:
            result["warnings"].append(f"Could not read the Lidarr library ({e}); ownership check skipped.")
    plex_names = ad._plex_artist_names(db)

    counts = result["skip_counts"]

    def bump(reason: str) -> None:
        counts[reason] = counts.get(reason, 0) + 1

    decisions: list[_Decision] = []
    near_miss_budget = 30
    ranked = sorted(cands.items(), key=lambda kv: (-kv[1].score, kv[0]))
    queued = added = failed = 0
    adds_today = 0 if dry_run else adds_in_last_day(db)
    lidarr = ad._lidarr_client(db) if lidarr_row_ok else None
    would = "would_" if dry_run else ""

    for key, c in ranked:
        seed_names = sorted(c.seeds, key=lambda n: -c.seeds[n])
        n_conn = len(seed_names)
        if key in known_norm or key in lidarr_by_name or (c.mbid and c.mbid in lidarr_by_mbid) or key in plex_names:
            bump("already in your library")
            continue
        if c.score < cfg.suggest_score:
            bump("below the suggest score")
            if near_miss_budget > 0 and c.score >= cfg.suggest_score * 0.5:
                near_miss_budget -= 1
                decisions.append(_Decision(
                    c.name, "skipped", f"Score {c.score:.2f} is below the suggest score {cfg.suggest_score:.2f}",
                    c.mbid, c.score, n_conn, seed_names))
            continue
        existing = ad._find_candidate(db, c.mbid, c.name)
        if existing is not None and existing.status != "pending":
            verdict = "previously rejected" if existing.status == "rejected" else "already added"
            bump(verdict)
            decisions.append(_Decision(c.name, "skipped", f"Candidate was {verdict}", c.mbid, c.score,
                                       n_conn, seed_names, existing.id))
            continue

        # Resolve a missing MBID through Lidarr's lookup when it is needed (strict mode
        # or auto-add) — Last.fm often returns similar artists with no MBID.
        mbid = c.mbid or (existing.musicbrainz_id if existing else None)
        wants_auto = (cfg.auto_add_enabled and lidarr is not None and c.score >= cfg.auto_add_score
                      and n_conn >= max(cfg.auto_add_min_connections, 1))
        if not mbid and lidarr is not None and (cfg.require_musicbrainz_id or wants_auto):
            try:
                found = await lidarr.lookup_artist(c.name)
                hit = next((r for r in found if ad._norm_artist(r.get("artistName") or "") == key), None)
                mbid = (hit or {}).get("foreignArtistId") or None
            except Exception:
                mbid = None
            if mbid and (mbid in lidarr_by_mbid):
                bump("already in your library")
                continue
        if cfg.require_musicbrainz_id and not mbid:
            bump("no MusicBrainz ID")
            decisions.append(_Decision(c.name, "skipped", "No MusicBrainz ID and 'require MusicBrainz ID' is on",
                                       None, c.score, n_conn, seed_names))
            continue
        if cfg.filter_youtube_channels:
            is_nm, why = is_non_music_channel(c.name, mbid=mbid, blocked_channels=cfg.blocked_channels)
            if is_nm:
                bump("non-music channel")
                decisions.append(_Decision(c.name, "skipped", f"Looks like a non-music channel ({why})",
                                           mbid, c.score, n_conn, seed_names))
                continue
        if cfg.excluded_genres:
            try:
                tags = [t.lower() for t in await lastfm.get_top_tags(c.name, mbid)]
            except Exception:
                tags = []
            hit_tags = [t for t in tags if any(x.strip().lower() in t for x in cfg.excluded_genres if x.strip())]
            if hit_tags:
                bump("excluded genre")
                decisions.append(_Decision(c.name, "skipped", f"Tagged '{hit_tags[0]}', an excluded genre",
                                           mbid, c.score, n_conn, seed_names))
                continue

        # --- decide: add, or queue -------------------------------------------------
        auto_block = None
        if not cfg.auto_add_enabled:
            auto_block = "auto-add is off"
        elif lidarr is None:
            auto_block = "Lidarr is not enabled"
        elif c.score < cfg.auto_add_score:
            auto_block = f"score {c.score:.2f} is below the auto-add score {cfg.auto_add_score:.2f}"
        elif n_conn < max(cfg.auto_add_min_connections, 1):
            auto_block = f"{n_conn} seed(s) point to it, auto-add needs {cfg.auto_add_min_connections}"
        elif added >= cfg.max_adds_per_run:
            auto_block = f"this run's add cap ({cfg.max_adds_per_run}) is reached"
        elif cfg.max_adds_per_day > 0 and adds_today + added >= cfg.max_adds_per_day:
            auto_block = f"the daily add cap ({cfg.max_adds_per_day}) is reached"

        if auto_block is None:
            if dry_run:
                added += 1
                decisions.append(_Decision(
                    c.name, "would_add", f"Score {c.score:.2f} from {n_conn} seed(s) clears the auto-add bar",
                    mbid, c.score, n_conn, seed_names))
                continue
            row = existing or await _create_row(db, c, mbid, seed_names, ad)
            ar = await ad.add_to_lidarr(db, row.id)
            if ar.get("ok"):
                added += 1
                decisions.append(_Decision(
                    c.name, "added", f"Score {c.score:.2f} from {n_conn} seed(s) cleared the auto-add bar",
                    mbid, c.score, n_conn, seed_names, row.id))
            else:
                failed += 1
                decisions.append(_Decision(
                    c.name, "failed", f"Lidarr add failed, left in the queue: {ar.get('message')}",
                    mbid, c.score, n_conn, seed_names, row.id))
            continue

        if existing is not None:  # already queued, nothing new to do
            bump("already in the queue")
            continue
        if queued >= cfg.max_queue_per_run:
            bump("queue cap reached")
            continue
        if dry_run:
            queued += 1
            decisions.append(_Decision(
                c.name, "would_queue", f"Score {c.score:.2f} from {n_conn} seed(s); not auto-added because {auto_block}",
                mbid, c.score, n_conn, seed_names))
            continue
        row = await _create_row(db, c, mbid, seed_names, ad)
        queued += 1
        decisions.append(_Decision(
            c.name, "queued", f"Score {c.score:.2f} from {n_conn} seed(s); not auto-added because {auto_block}",
            mbid, c.score, n_conn, seed_names, row.id))

    # --- persist ----------------------------------------------------------------------
    if not dry_run:
        for d in decisions:
            db.add(ArtistDiscoveryDecision(
                run_id=run_id, artist_name=d.name, musicbrainz_id=d.mbid, action=d.action, reason=d.reason,
                score=d.score, connections=d.connections, seeds=json.dumps(d.seeds[:8]),
                candidate_id=d.candidate_id))
        db.commit()

    skipped = sum(counts.values())
    result.update(queued=queued, added=added, failed=failed, skipped=skipped,
                  decisions=[d.as_dict() for d in decisions])
    verb = "would add" if dry_run else "added"
    qverb = "would queue" if dry_run else "queued"
    msg = (f"{len(seeds)} seed(s), {len(cands)} candidate(s) scored: {verb} {added}, {qverb} {queued}"
           + (f", {failed} Lidarr add(s) failed" if failed else ""))
    if counts:
        top_reasons = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
        msg += "; skipped " + ", ".join(f"{n} ({r})" for r, n in top_reasons)
    if not added and not queued and not failed:
        msg += ". Nothing cleared the bar: lower the suggest score or minimum match, or pick a wider preset."
    result["message"] = msg
    return result


async def _create_row(db, c: _Candidate, mbid: str | None, seed_names: list[str], ad) -> DiscoveredArtist:
    enrichment = await ad._enrich_candidate(db, mbid, c.name)
    if not mbid and enrichment.get("musicbrainz_id"):
        mbid = enrichment["musicbrainz_id"]
    row = DiscoveredArtist(
        musicbrainz_id=mbid, artist_name=c.name,
        genres=json.dumps(ad.clean_tags(enrichment.get("genres"))),
        mood_tags=json.dumps([]), source="direct",
        similarity_score=round(c.best_match, 4),
        associated_seed_mbids=json.dumps(seed_names),
        seed_artist_name=seed_names[0] if seed_names else None,
        seed_artist_names=json.dumps(seed_names),
        status="pending",
        image_url=enrichment.get("image_url"), bio=enrichment.get("bio"),
        years_active=enrichment.get("years_active"),
    )
    db.add(row)
    db.flush()
    return row


async def run_direct_tracked(db, cfg: ArtistDiscoverySettings, *, trigger: str = "manual") -> dict[str, Any]:
    """Stand-alone real run with its own ArtistDiscoveryRun row (the full cycle
    makes its own and passes it to run_direct_discovery instead)."""
    run = ArtistDiscoveryRun(run_type="direct", started_at=datetime.utcnow(), trigger=trigger,
                             preset=cfg.aggressiveness)
    db.add(run)
    db.flush()
    try:
        res = await run_direct_discovery(db, cfg, run=run)
    except Exception as e:
        logger.error(f"Direct discovery failed: {e}", exc_info=True)
        res = {"ok": False, "message": f"Error: {e}", "queued": 0, "added": 0, "skipped": 0}
    run.candidates_found = res.get("queued", 0) + res.get("added", 0)
    run.candidates_added = res.get("added", 0)
    run.candidates_skipped = res.get("skipped", 0)
    run.message = res.get("message")
    run.finished_at = datetime.utcnow()
    db.commit()
    res["run_id"] = run.id
    return res


def prune_decisions(db, max_age_days: int = 90, max_rows: int = 5000) -> int:
    cutoff = datetime.utcnow() - timedelta(days=max_age_days)
    n = db.query(ArtistDiscoveryDecision).filter(ArtistDiscoveryDecision.created_at < cutoff).delete()
    total = db.query(ArtistDiscoveryDecision).count()
    if total > max_rows:
        ids = [r[0] for r in db.query(ArtistDiscoveryDecision.id)
               .order_by(ArtistDiscoveryDecision.id.desc()).offset(max_rows).all()]
        if ids:
            n += db.query(ArtistDiscoveryDecision).filter(
                ArtistDiscoveryDecision.id.in_(ids)).delete(synchronize_session=False)
    if n:
        db.commit()
    return n


async def get_status(db, deep: bool = False) -> dict[str, Any]:
    """Plain-language health check for "why isn't discovery adding anything?".
    Config-only unless deep=True, which also pings Last.fm and Lidarr."""
    from app.models.app_setting import AppSetting
    from app.models.integration import Integration
    from app.services import artist_discovery as ad

    stored = ad.load_settings(db)
    cfg = effective_settings(stored)
    checks: list[dict[str, str]] = []

    def add(key: str, status: str, label: str, detail: str) -> None:
        checks.append({"key": key, "status": status, "label": label, "detail": detail})

    add("enabled", "ok" if cfg.enabled else "error", "Artist Discovery",
        "Enabled." if cfg.enabled else "Switched off. Nothing runs, scheduled or manual, until you enable it.")
    lane_on = cfg.direct_lane_enabled and cfg.aggressiveness != "off"
    add("direct_lane", "ok" if lane_on else "warn", "Direct lane",
        f"On, preset '{cfg.aggressiveness}'." if lane_on else "Off. Only the legacy Qdrant lanes can find artists.")

    lf = db.query(Integration).filter_by(name="lastfm").first()
    if not lf or not lf.enabled:
        add("lastfm", "error", "Last.fm", "Not enabled. This is the listening-history source; discovery cannot run without it.")
    elif not lf.api_key or not lf.username:
        add("lastfm", "error", "Last.fm", "Enabled but the API key or username is missing.")
    elif deep:
        res = await ad._lastfm_client(db).test_connection()
        add("lastfm", "ok" if res.get("ok") else "error", "Last.fm", res.get("message") or "")
    else:
        add("lastfm", "ok", "Last.fm", f"Configured for user '{lf.username}'.")

    lr = db.query(Integration).filter_by(name="lidarr", enabled=True).first()
    if not lr:
        add("lidarr", "error", "Lidarr", "Not enabled. Candidates can be queued but nothing can be added.")
    elif deep:
        try:
            folders = await ad._lidarr_client(db).get_root_folders()
            qps = await ad._lidarr_client(db).get_quality_profiles()
            mps = await ad._lidarr_client(db).get_metadata_profiles()
            problems = []
            if not (cfg.root_folder_path or folders):
                problems.append("no root folder")
            if not (cfg.quality_profile_id or qps):
                problems.append("no quality profile")
            if not (cfg.metadata_profile_id or mps):
                problems.append("no metadata profile")
            add("lidarr", "error" if problems else "ok", "Lidarr",
                ("Reachable but " + ", ".join(problems) + ". Adds will fail.") if problems else "Reachable, root folder and profiles resolve.")
        except Exception as e:
            add("lidarr", "error", "Lidarr", f"Not reachable: {e}")
    else:
        add("lidarr", "ok", "Lidarr", "Enabled.")

    if cfg.auto_add_enabled:
        used = adds_in_last_day(db)
        cap = cfg.max_adds_per_day
        add("auto_add", "ok", "Auto-add",
            f"On. {used} add(s) in the last 24h" + (f" of {cap} allowed." if cap > 0 else " (no daily cap)."))
    else:
        add("auto_add", "warn", "Auto-add",
            "Off. Discovery only fills the review queue, nothing is added to Lidarr on its own.")

    legacy_auto = ad._effective_auto_add_threshold(cfg) > 0 or (cfg.auto_add_all_time_threshold or 0) > 0
    if not legacy_auto:
        add("legacy_auto_add", "ok", "Legacy connection auto-add", "Off (thresholds are 0). The direct lane handles auto-adding.")

    qdrant = ad._qdrant(db)
    add("qdrant", "ok" if qdrant else "warn", "Qdrant (legacy lanes)",
        "Configured." if qdrant else "Not configured. Only the direct lane runs; the taste-centroid and connection lanes are skipped.")

    if cfg.schedule_enabled:
        row = db.query(AppSetting).filter_by(key="last_artist_discovery_run").first()
        nxt = None
        if row and row.value:
            try:
                nxt = (datetime.fromisoformat(row.value) + timedelta(hours=cfg.schedule_interval_hours)).isoformat()
            except ValueError:
                nxt = None
        add("schedule", "ok", "Schedule", f"Every {cfg.schedule_interval_hours}h" + (f", next due {nxt}Z." if nxt else ", first run is due now."))
    else:
        add("schedule", "warn", "Schedule", "Off. Discovery only runs when you click Run Discovery Now.")

    last = db.query(ArtistDiscoveryRun).order_by(ArtistDiscoveryRun.started_at.desc()).first()
    if last:
        add("last_run", "ok" if last.finished_at else "warn", "Last run",
            f"{last.started_at.isoformat()}Z: {last.message or 'no message'}")
    else:
        add("last_run", "warn", "Last run", "No run recorded yet.")

    worst = "error" if any(c["status"] == "error" for c in checks) else (
        "warn" if any(c["status"] == "warn" for c in checks) else "ok")
    return {"overall": worst, "checks": checks, "preset": cfg.aggressiveness,
            "effective": {k: getattr(cfg, k) for k in (
                "seed_period", "min_seed_plays", "max_seeds", "similar_per_seed", "min_match", "suggest_score",
                "auto_add_score", "auto_add_min_connections", "max_adds_per_run", "max_adds_per_day",
                "max_queue_per_run", "lidarr_monitor", "search_on_add", "auto_add_enabled")}}


def list_decisions(db, *, run_id: int | None = None, action: str | None = None,
                   limit: int = 100) -> list[dict[str, Any]]:
    q = db.query(ArtistDiscoveryDecision)
    if run_id is not None:
        q = q.filter(ArtistDiscoveryDecision.run_id == run_id)
    if action:
        q = q.filter(ArtistDiscoveryDecision.action == action)
    rows = q.order_by(ArtistDiscoveryDecision.id.desc()).limit(limit).all()
    return [{"id": r.id, "run_id": r.run_id, "created_at": r.created_at, "artist_name": r.artist_name,
             "musicbrainz_id": r.musicbrainz_id, "action": r.action, "reason": r.reason, "score": r.score,
             "connections": r.connections, "seeds": json.loads(r.seeds) if r.seeds else [],
             "candidate_id": r.candidate_id} for r in rows]
