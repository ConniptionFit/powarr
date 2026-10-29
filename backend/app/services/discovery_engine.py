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
from app.models import discovery_v2  # noqa: F401  (tables for create_all)
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
    sources: set = field(default_factory=set)  # lastfm | deezer | listenbrainz
    # v0.101.0 ranking output (filled by the evidence step)
    final: float = 0.0
    tags: list = field(default_factory=list)
    listeners: int | None = None
    release: dict | None = None
    fit: float | None = None
    bucket: str | None = None
    breakdown: dict = field(default_factory=dict)
    ranked: bool = False

    def extra(self, rk, profile: dict) -> dict:
        seeds = sorted(self.seeds, key=lambda n: -self.seeds[n])
        return {"bucket": self.bucket, "tags": self.tags[:6], "listeners": self.listeners,
                "sources": sorted(self.sources), "breakdown": self.breakdown, "release": self.release,
                "reason_line": rk.build_reason(seeds, rk.shared_tags(self.tags, profile),
                                               self.bucket or "branching", sorted(self.sources), self.release)}


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
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"artist_name": self.name, "musicbrainz_id": self.mbid, "action": self.action,
                "reason": self.reason, "score": None if self.score is None else round(self.score, 3),
                "connections": self.connections, "seeds": self.seeds, "candidate_id": self.candidate_id,
                **self.extra}


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


def adds_in_last_days(db, days: int) -> int:
    since = datetime.utcnow() - timedelta(days=days)
    return (db.query(ArtistDiscoveryDecision)
            .filter(ArtistDiscoveryDecision.action == "added",
                    ArtistDiscoveryDecision.created_at >= since).count())


LOG_MIN_EVENTS = 25  # below this the listen log is too thin to seed from; Last.fm top artists are used


async def _maybe_sync_listening(db, cfg: ArtistDiscoverySettings) -> None:
    """Refresh the listen log before a real run if it has not synced recently."""
    from app.services import listening
    try:
        cur = listening._load_cursor(db)
        last = max(cur.values()) if cur else 0
        if datetime.utcnow().timestamp() - last > 900:
            await listening.sync_listening(db, cfg)
    except Exception as e:  # never let the log block discovery
        logger.warning(f"Direct lane: listening sync skipped: {e}")


async def _load_seeds(db, lastfm, cfg: ArtistDiscoverySettings, period: str, ad) -> tuple[list[dict], str, str | None]:
    """(seeds_all, source label, error). Prefers the de-duplicated listen log when it has
    enough data; otherwise Last.fm top artists, exactly as before."""
    from app.services import listening
    days = listening._PERIOD_DAYS.get(period, 90)
    if cfg.listening_enabled and listening.log_size(db, days) >= LOG_MIN_EVENTS:
        rows = listening.seed_plays(db, period, cfg.seed_half_life_days)
        seeds = [{"name": r["name"], "mbid": r["mbid"], "plays": r["plays"], "wplays": r["weighted"]} for r in rows]
        return seeds, "listening log", None
    try:
        top = await lastfm.get_top_artists(period=period, limit=max(cfg.max_seeds * 3, 50))
    except Exception as e:
        return [], "Last.fm", str(e)
    seeds = []
    for a in top:
        name = (a.get("name") or "").strip()
        if name:
            n = _as_int(a.get("playcount"))
            seeds.append({"name": name, "mbid": (a.get("mbid") or "").strip() or None, "plays": n, "wplays": float(n)})
    return seeds, "Last.fm", None


def _add_link(cands: dict, key: str, name: str, mbid: str | None, seed_name: str,
              contribution: float, match: float, source: str) -> None:
    c = cands.setdefault(key, _Candidate(name=name))
    if mbid and not c.mbid:
        c.mbid = mbid
    c.sources.add(source)
    # A seed counts once per candidate, at its best link.
    if contribution > c.seeds.get(seed_name, 0.0):
        c.score += contribution - c.seeds.get(seed_name, 0.0)
        c.seeds[seed_name] = contribution
    c.best_match = max(c.best_match, match)


async def run_direct_discovery(db, cfg: ArtistDiscoverySettings, *, dry_run: bool = False,
                               run: ArtistDiscoveryRun | None = None) -> dict[str, Any]:
    """Run the direct lane once. With dry_run=True nothing is written (no candidate
    rows, no Lidarr adds, no decision log) and the decisions are returned instead.
    (Cached evidence from the lookups is still stored; it is not user-visible state.)"""
    from app.services import artist_discovery as ad
    from app.services import discovery_ranker as rk
    from app.services import listening
    from app.services.youtube_filter import is_non_music_channel

    cfg = effective_settings(cfg)
    pre = preflight(db, cfg)
    result: dict[str, Any] = {
        "ok": True, "dry_run": dry_run, "preset": cfg.aggressiveness,
        "blockers": pre["blockers"], "warnings": list(pre["warnings"]),
        "seeds": 0, "seed_source": "", "candidates_scored": 0, "queued": 0, "added": 0, "failed": 0,
        "skipped": 0, "skip_counts": {}, "decisions": [], "message": "",
    }
    if pre["blockers"]:
        result["ok"] = False
        result["message"] = "Direct lane did not run: " + pre["blockers"][0]
        return result

    lastfm = ad._lastfm_client(db)
    lidarr_row_ok = ad._lidarr_client(db) is not None
    run_id = run.id if run is not None else None
    advanced = bool(cfg.advanced_ranking)
    sem = asyncio.Semaphore(4)

    # --- seeds --------------------------------------------------------------------
    if not dry_run and cfg.listening_enabled:
        await _maybe_sync_listening(db, cfg)
    period = cfg.seed_period if cfg.seed_period in SEED_PERIODS else "3month"
    seeds_all, seed_source, err = await _load_seeds(db, lastfm, cfg, period, ad)
    if err:
        result["ok"] = False
        result["message"] = f"Last.fm top-artists fetch failed: {err}"
        return result
    result["seed_source"] = seed_source
    # Everything you listen to in this period is "known" even if it isn't picked as a seed.
    known_norm = {ad._norm_artist(s["name"]) for s in seeds_all}
    excluded_seed_names = {ad._norm_artist(n) for n in cfg.seed_exclude_artists if n.strip()}
    seeds = []
    for s in seeds_all:
        if s["plays"] < cfg.min_seed_plays:
            continue
        if ad._norm_artist(s["name"]) in excluded_seed_names:
            continue
        if cfg.filter_youtube_channels:
            is_nm, _ = is_non_music_channel(s["name"], mbid=s["mbid"], blocked_channels=cfg.blocked_channels)
            if is_nm:
                continue
        seeds.append(s)
        if len(seeds) >= cfg.max_seeds * (2 if cfg.seed_exclude_tags else 1):
            break
    seed_tags: dict[str, list[str]] = {}
    if seeds and (advanced or cfg.seed_exclude_tags):
        seed_tags = await rk.gather_seed_tags(db, lastfm, seeds, ad._norm_artist, cfg.evidence_ttl_days, sem)
    if cfg.seed_exclude_tags:
        bad = {rk.clean_tag(t) for t in cfg.seed_exclude_tags if t.strip()}
        kept = []
        for s in seeds:
            hit = [t for t in seed_tags.get(s["name"], []) if rk.clean_tag(t) in bad]
            if hit:
                result["skip_counts"]["seed excluded by tag"] = result["skip_counts"].get("seed excluded by tag", 0) + 1
                continue
            kept.append(s)
        seeds = kept[:cfg.max_seeds]
    seeds = seeds[:cfg.max_seeds]
    result["seeds"] = len(seeds)
    if not seeds:
        result["message"] = (f"No seeds: {seed_source} returned {len(seeds_all)} artist(s) for '{period}', "
                             f"none usable with at least {cfg.min_seed_plays} plays. Try a longer seed period, "
                             f"a lower minimum plays, or check the seed exclusions.")
        return result
    max_plays = max(s["wplays"] for s in seeds)

    # --- score candidates -----------------------------------------------------------
    fetched = await asyncio.gather(*[_fetch_similar(lastfm, sem, s, cfg.similar_per_seed) for s in seeds])
    errors = [e for _, _, e in fetched if e]
    if errors and len(errors) == len(seeds):
        result["ok"] = False
        result["message"] = f"Last.fm similar-artist lookups all failed ({errors[0]})"
        return result
    if errors:
        result["warnings"].append(f"{len(errors)} of {len(seeds)} seed lookups failed ({errors[0]}).")

    cands: dict[str, _Candidate] = {}
    seed_weights = {s["name"]: _seed_weight(s["wplays"], max_plays) for s in seeds}
    for seed, similar, _err in fetched:
        weight = seed_weights[seed["name"]]
        for r in similar:
            rname = (r.get("name") or "").strip()
            match = _as_float(r.get("match"))
            if not rname or match < cfg.min_match:
                continue
            _add_link(cands, ad._norm_artist(rname), rname, (r.get("mbid") or "").strip() or None,
                      seed["name"], weight * match, match, "lastfm")

    # Independent second opinions: Deezer's related-artists graph and ListenBrainz's
    # similar artists, for the strongest seeds. Cached; each is optional and fails soft.
    if advanced:
        top_seeds = sorted(seeds, key=lambda x: -seed_weights[x["name"]])[:12]
        if cfg.use_deezer_related:
            lists = await asyncio.gather(*[rk.deezer_related(db, ad._norm_artist(s["name"]), s["name"],
                                                             cfg.evidence_ttl_days, asyncio.Semaphore(2))
                                           for s in top_seeds], return_exceptions=True)
            for seed, rows in zip(top_seeds, lists):
                if isinstance(rows, Exception):
                    continue
                for i, row in enumerate(rows):
                    m = 0.5 + 0.5 * (1 - i / max(len(rows), 1))
                    _add_link(cands, ad._norm_artist(row["name"]), row["name"], None, seed["name"],
                              seed_weights[seed["name"]] * m * 0.8, m, "deezer")
        lb = listening._integration(db, "listenbrainz") if cfg.use_listenbrainz_similar else None
        if lb is not None:
            lists = await asyncio.gather(*[rk.listenbrainz_similar(db, lb, ad._norm_artist(s["name"]), s["name"],
                                                                   s.get("mbid"), cfg.listenbrainz_algorithm,
                                                                   cfg.evidence_ttl_days, sem)
                                           for s in top_seeds if s.get("mbid")], return_exceptions=True)
            for seed, rows in zip([s for s in top_seeds if s.get("mbid")], lists):
                if isinstance(rows, Exception) or not rows:
                    continue
                top = max((r["score"] for r in rows), default=0.0) or 1.0
                for row in rows:
                    m = max(0.0, min(1.0, row["score"] / top)) * 0.9
                    if m >= cfg.min_match:
                        _add_link(cands, ad._norm_artist(row["name"]), row["name"], row.get("mbid"),
                                  seed["name"], seed_weights[seed["name"]] * m * 0.8, m, "listenbrainz")
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
    feedback = rk.load_feedback(db) if advanced else rk.FeedbackModel()

    counts = result["skip_counts"]

    def bump(reason: str) -> None:
        counts[reason] = counts.get(reason, 0) + 1

    decisions: list[_Decision] = []
    survivors: list[tuple[str, _Candidate]] = []
    for key, c in sorted(cands.items(), key=lambda kv: (-kv[1].score, kv[0])):
        if key in known_norm or key in lidarr_by_name or (c.mbid and c.mbid in lidarr_by_mbid) or key in plex_names:
            bump("already in your library")
        elif key in feedback.known:
            bump("marked as already known")
        else:
            survivors.append((key, c))

    # --- evidence + final score -------------------------------------------------------
    profile = rk.tag_profile(seed_tags, seed_weights) if advanced else {}
    if advanced:
        window = [kv for kv in survivors if kv[1].score >= cfg.suggest_score * 0.4][:max(cfg.evidence_top_n, 10)]
        infos = await asyncio.gather(*[rk.gather_candidate_info(db, lastfm, k, c.name, c.mbid, cfg.evidence_ttl_days, sem)
                                       for k, c in window])
        for (k, c), info in zip(window, infos):
            c.tags = list(info.get("tags") or [])
            c.listeners = info.get("listeners")
        if cfg.use_musicbrainz_releases:
            for k, c in window[:20]:
                c.release = await rk.gather_release(db, k, c.name, c.mbid, cfg.evidence_ttl_days, network=not dry_run)
        for k, c in window:
            fit = rk.tag_fit(c.tags, profile)
            fb = feedback.adjust(c.tags, sorted(c.seeds, key=lambda n: -c.seeds[n]))
            fresh = rk.freshness((c.release or {}).get("date"), cfg.freshness_days)
            c.final, c.breakdown = rk.score_candidate(
                c.score, len(c.sources), fit, rk.obscurity(c.listeners, cfg.max_listeners), fresh, fb, cfg)
            c.fit = fit
            c.bucket = rk.assign_bucket(fit, cfg)
            c.ranked = True
    for k, c in survivors:
        if not c.ranked:
            c.final = c.score
            c.bucket = c.bucket or "branching"

    # popularity guardrails (only when the listener count is known)
    kept: list[tuple[str, _Candidate]] = []
    for k, c in survivors:
        if c.listeners is not None and cfg.min_listeners and c.listeners < cfg.min_listeners:
            bump("below the minimum listeners")
            continue
        if c.listeners is not None and cfg.max_listeners and c.listeners > cfg.max_listeners:
            bump("above the maximum listeners")
            continue
        kept.append((k, c))
    items = [{"score": c.final, "seeds": sorted(c.seeds, key=lambda n: -c.seeds[n]), "tags": c.tags, "ref": (k, c)}
             for k, c in kept]
    ordered = [it["ref"] for it in rk.diversify(items, cfg.diversity_strength if advanced else 0.0,
                                               cfg.max_per_seed if advanced else 0)]

    near_miss_budget = 30
    queued = added = failed = 0
    adds_today = adds_in_last_day(db)  # a preview honours the caps real adds already used
    adds_week = adds_in_last_days(db, 7)
    lidarr = ad._lidarr_client(db) if lidarr_row_ok else None

    for key, c in ordered:
        seed_names = sorted(c.seeds, key=lambda n: -c.seeds[n])
        n_conn = len(seed_names)
        score = c.final
        extra = c.extra(rk, profile)
        if score < cfg.suggest_score:
            bump("below the suggest score")
            if near_miss_budget > 0 and score >= cfg.suggest_score * 0.5:
                near_miss_budget -= 1
                decisions.append(_Decision(
                    c.name, "skipped", f"Score {score:.2f} is below the suggest score {cfg.suggest_score:.2f}",
                    c.mbid, score, n_conn, seed_names, extra=extra))
            continue
        existing = ad._find_candidate(db, c.mbid, c.name)
        if existing is not None and existing.status != "pending":
            verdict = "previously rejected" if existing.status == "rejected" else "already added"
            bump(verdict)
            decisions.append(_Decision(c.name, "skipped", f"Candidate was {verdict}", c.mbid, score,
                                       n_conn, seed_names, existing.id, extra=extra))
            continue

        # Resolve a missing MBID through Lidarr's lookup when it is needed (strict mode
        # or auto-add) — Last.fm often returns similar artists with no MBID.
        mbid = c.mbid or (existing.musicbrainz_id if existing else None)
        wants_auto = (cfg.auto_add_enabled and lidarr is not None and score >= cfg.auto_add_score
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
                                       None, score, n_conn, seed_names, extra=extra))
            continue
        if cfg.filter_youtube_channels:
            is_nm, why = is_non_music_channel(c.name, mbid=mbid, blocked_channels=cfg.blocked_channels)
            if is_nm:
                bump("non-music channel")
                decisions.append(_Decision(c.name, "skipped", f"Looks like a non-music channel ({why})",
                                           mbid, score, n_conn, seed_names, extra=extra))
                continue
        needs_tags = bool(cfg.excluded_genres or cfg.auto_add_genres or cfg.auto_add_excluded_genres)
        if needs_tags and not c.tags:
            try:
                c.tags = [t for t in await lastfm.get_top_tags(c.name, mbid)]
            except Exception:
                c.tags = []
            extra = c.extra(rk, profile)
        lowtags = [t.lower() for t in c.tags]
        if cfg.excluded_genres:
            hit_tags = [t for t in lowtags if any(x.strip().lower() in t for x in cfg.excluded_genres if x.strip())]
            if hit_tags:
                bump("excluded genre")
                decisions.append(_Decision(c.name, "skipped", f"Tagged '{hit_tags[0]}', an excluded genre",
                                           mbid, score, n_conn, seed_names, extra=extra))
                continue

        # --- decide: add, or queue -------------------------------------------------
        auto_block = None
        if not cfg.auto_add_enabled:
            auto_block = "auto-add is off"
        elif lidarr is None:
            auto_block = "Lidarr is not enabled"
        elif score < cfg.auto_add_score:
            auto_block = f"score {score:.2f} is below the auto-add score {cfg.auto_add_score:.2f}"
        elif n_conn < max(cfg.auto_add_min_connections, 1):
            auto_block = f"{n_conn} seed(s) point to it, auto-add needs {cfg.auto_add_min_connections}"
        elif cfg.auto_add_buckets and c.bucket not in cfg.auto_add_buckets:
            auto_block = f"it is a '{c.bucket}' pick and auto-add is limited to {', '.join(cfg.auto_add_buckets)}"
        elif len(c.sources) < max(cfg.auto_add_min_sources, 1):
            auto_block = f"only {len(c.sources)} source(s) suggest it, auto-add needs {cfg.auto_add_min_sources}"
        elif cfg.auto_add_require_mbid and not mbid:
            auto_block = "it has no MusicBrainz ID"
        elif cfg.auto_add_genres and not any(g.strip().lower() in t for t in lowtags for g in cfg.auto_add_genres if g.strip()):
            auto_block = "none of its tags are in the auto-add genre list"
        elif cfg.auto_add_excluded_genres and any(g.strip().lower() in t for t in lowtags for g in cfg.auto_add_excluded_genres if g.strip()):
            auto_block = "it carries a tag on the auto-add exclusion list"
        elif added >= cfg.max_adds_per_run:
            auto_block = f"this run's add cap ({cfg.max_adds_per_run}) is reached"
        elif cfg.max_adds_per_day > 0 and adds_today + added >= cfg.max_adds_per_day:
            auto_block = f"the daily add cap ({cfg.max_adds_per_day}) is reached"
        elif cfg.max_adds_per_week > 0 and adds_week + added >= cfg.max_adds_per_week:
            auto_block = f"the weekly add cap ({cfg.max_adds_per_week}) is reached"

        if auto_block is None:
            if dry_run:
                added += 1
                decisions.append(_Decision(
                    c.name, "would_add", f"Score {score:.2f} from {n_conn} seed(s) clears the auto-add bar",
                    mbid, score, n_conn, seed_names, extra=extra))
                continue
            row = existing or await _create_row(db, c, mbid, seed_names, ad, rk, profile)
            ar = await ad.add_to_lidarr(db, row.id, auto=True)
            if ar.get("ok"):
                added += 1
                decisions.append(_Decision(
                    c.name, "added", f"Score {score:.2f} from {n_conn} seed(s) cleared the auto-add bar",
                    mbid, score, n_conn, seed_names, row.id, extra=extra))
            else:
                failed += 1
                decisions.append(_Decision(
                    c.name, "failed", f"Lidarr add failed, left in the queue: {ar.get('message')}",
                    mbid, score, n_conn, seed_names, row.id, extra=extra))
            continue

        if existing is not None:  # already queued: refresh its ranking, nothing else to do
            bump("already in the queue")
            if not dry_run:
                _apply_rank(existing, c, rk, profile)
            continue
        if queued >= cfg.max_queue_per_run:
            bump("queue cap reached")
            continue
        if dry_run:
            queued += 1
            decisions.append(_Decision(
                c.name, "would_queue", f"Score {score:.2f} from {n_conn} seed(s); not auto-added because {auto_block}",
                mbid, score, n_conn, seed_names, extra=extra))
            continue
        row = await _create_row(db, c, mbid, seed_names, ad, rk, profile)
        queued += 1
        decisions.append(_Decision(
            c.name, "queued", f"Score {score:.2f} from {n_conn} seed(s); not auto-added because {auto_block}",
            mbid, score, n_conn, seed_names, row.id, extra=extra))

    # --- persist ----------------------------------------------------------------------
    if not dry_run:
        for d in decisions:
            db.add(ArtistDiscoveryDecision(
                run_id=run_id, artist_name=d.name, musicbrainz_id=d.mbid, action=d.action, reason=d.reason,
                score=d.score, connections=d.connections, seeds=json.dumps(d.seeds[:8]),
                candidate_id=d.candidate_id))
        db.commit()
        if cfg.blurbs_enabled:
            try:
                from app.services import discovery_blurbs
                await discovery_blurbs.fill_blurbs(db, cfg.blurbs_max_per_run)
            except Exception as e:
                logger.info(f"Direct lane: blurbs skipped: {e}")

    skipped = sum(counts.values())
    result.update(queued=queued, added=added, failed=failed, skipped=skipped,
                  decisions=[d.as_dict() for d in decisions])
    verb = "would add" if dry_run else "added"
    qverb = "would queue" if dry_run else "queued"
    msg = (f"{len(seeds)} seed(s) from the {seed_source}, {len(cands)} candidate(s) scored: {verb} {added}, {qverb} {queued}"
           + (f", {failed} Lidarr add(s) failed" if failed else ""))
    if counts:
        top_reasons = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
        msg += "; skipped " + ", ".join(f"{n} ({r})" for r, n in top_reasons)
    if not added and not queued and not failed:
        msg += ". Nothing cleared the bar: lower the suggest score or minimum match, or pick a wider preset."
    result["message"] = msg
    return result


def _apply_rank(row: DiscoveredArtist, c: "_Candidate", rk, profile: dict) -> None:
    """Copy the v2 ranking output onto a candidate row (new or refreshed)."""
    seeds = sorted(c.seeds, key=lambda n: -c.seeds[n])
    shared = rk.shared_tags(c.tags, profile)
    row.rank_score = round(c.final, 4)
    row.bucket = c.bucket
    row.breakdown = json.dumps({**c.breakdown, "_sources": sorted(c.sources)})
    row.reason = rk.build_reason(seeds, shared, c.bucket or "branching", sorted(c.sources), c.release)
    row.listeners = c.listeners
    row.tags = json.dumps(c.tags[:8])
    row.latest_release = json.dumps(c.release) if c.release else None


async def _create_row(db, c: _Candidate, mbid: str | None, seed_names: list[str], ad, rk=None,
                      profile: dict | None = None) -> DiscoveredArtist:
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
    if rk is not None:
        _apply_rank(row, c, rk, profile or {})
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
