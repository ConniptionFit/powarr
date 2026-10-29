"""Discovery ranking (v0.101.0): the slow "gather evidence" half and the fast "score
and rank" half of the direct lane, kept apart so admin settings can re-rank cached
evidence without refetching anything.

Pure functions (tag fit, obscurity, freshness, scoring, buckets, diversity) take plain
values and are unit tested. The gather helpers cache what they fetch in
artist_discovery_evidence and fail soft: a missing signal contributes nothing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import datetime, timedelta
from typing import Any

from app.models.discovery_v2 import DiscoveryEvidence, DiscoveryFeedback
from app.schemas.settings import ArtistDiscoverySettings

logger = logging.getLogger("powarr")

BUCKETS = ("close", "branching", "wildcard")
BUCKET_LABELS = {"close": "Close to your taste", "branching": "Branching out", "wildcard": "Wildcard"}


# --- evidence cache ---------------------------------------------------------------------

def cache_get(db, key: str, source: str, signal: str, ttl_days: int) -> Any | None:
    """Cached detail (decoded JSON) or None when absent or older than the TTL."""
    row = (db.query(DiscoveryEvidence)
           .filter_by(artist_key=key, source=source, signal=signal)
           .order_by(DiscoveryEvidence.fetched_at.desc()).first())
    if not row or row.fetched_at < datetime.utcnow() - timedelta(days=max(ttl_days, 1)):
        return None
    try:
        return json.loads(row.detail) if row.detail else {}
    except ValueError:
        return None


def cache_put(db, key: str, name: str, source: str, signal: str, detail: Any, value: float | None = None) -> None:
    db.query(DiscoveryEvidence).filter_by(artist_key=key, source=source, signal=signal).delete()
    db.add(DiscoveryEvidence(artist_key=key, artist_name=name, source=source, signal=signal,
                             value=value, detail=json.dumps(detail)))
    db.commit()


def prune_evidence(db, max_age_days: int = 60) -> int:
    n = db.query(DiscoveryEvidence).filter(
        DiscoveryEvidence.fetched_at < datetime.utcnow() - timedelta(days=max_age_days)).delete()
    if n:
        db.commit()
    return n


# --- pure scoring helpers -----------------------------------------------------------------

def clean_tag(t: str) -> str:
    return " ".join((t or "").lower().replace("-", " ").replace("_", " ").split())


def tag_profile(seed_tags: dict[str, list[str]], seed_weights: dict[str, float]) -> dict[str, float]:
    """Your taste as {tag: 0..1}: each seed's tags weighted by how much you play it."""
    acc: dict[str, float] = {}
    for seed, tags in seed_tags.items():
        w = seed_weights.get(seed, 0.0)
        for rank, t in enumerate(tags[:6]):
            ct = clean_tag(t)
            if ct:
                acc[ct] = acc.get(ct, 0.0) + w / (1 + 0.25 * rank)
    top = max(acc.values(), default=0.0)
    return {t: v / top for t, v in acc.items()} if top > 0 else {}


def tag_fit(tags: list[str], profile: dict[str, float]) -> float | None:
    """How much of your taste's top-5 tag mass this artist covers, 0..1. None when
    either side has no tags (unknown is not the same as a bad fit)."""
    ct = [clean_tag(t) for t in tags[:5] if clean_tag(t)]
    if not ct or not profile:
        return None
    ideal = sum(sorted(profile.values(), reverse=True)[:5])
    if ideal <= 0:
        return None
    return max(0.0, min(1.0, sum(profile.get(t, 0.0) for t in ct) / ideal))


def shared_tags(tags: list[str], profile: dict[str, float], limit: int = 3) -> list[str]:
    ct = [clean_tag(t) for t in tags if clean_tag(t)]
    return sorted([t for t in ct if profile.get(t, 0) > 0.15], key=lambda t: -profile[t])[:limit]


def obscurity(listeners: int | None, ceiling: int) -> float | None:
    """1.0 for a tiny artist fading to 0 at the ceiling (default 1M listeners)."""
    if listeners is None:
        return None
    ref = ceiling if ceiling and ceiling > 0 else 1_000_000
    return max(0.0, 1.0 - math.log1p(max(listeners, 0)) / math.log1p(ref))


def parse_release_date(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt, n in (("%Y-%m-%d", 10), ("%Y-%m", 7), ("%Y", 4)):
        try:
            return datetime.strptime(s[:n], fmt)
        except ValueError:
            continue
    return None


def freshness(release_date: str | None, window_days: int, now: datetime | None = None) -> float:
    """1.0 for a release today, fading to 0.5 at the window edge, 0 beyond it."""
    d = parse_release_date(release_date)
    if d is None or window_days <= 0:
        return 0.0
    age = ((now or datetime.utcnow()) - d).days
    if age < 0:
        age = 0  # a date this year with no month parses to Jan 1; do not treat as future
    return 0.0 if age > window_days else 1.0 - 0.5 * age / window_days


class FeedbackModel:
    """Soft preferences learned from More like this / Not for me."""

    def __init__(self) -> None:
        self.tag_w: dict[str, float] = {}
        self.seed_w: dict[str, float] = {}
        self.known: set[str] = set()

    def adjust(self, tags: list[str], seeds: list[str]) -> float:
        raw = sum(self.tag_w.get(clean_tag(t), 0.0) for t in tags[:5])
        raw += sum(self.seed_w.get(s, 0.0) for s in seeds[:3])
        return math.tanh(raw * 0.5)  # -1..1


def load_feedback(db, window_days: int = 180) -> FeedbackModel:
    m = FeedbackModel()
    since = datetime.utcnow() - timedelta(days=window_days)
    for f in db.query(DiscoveryFeedback).filter(DiscoveryFeedback.created_at >= since).all():
        tags = json.loads(f.tags) if f.tags else []
        seeds = json.loads(f.seeds) if f.seeds else []
        if f.kind == "known":
            m.known.add(f.artist_key)
            continue
        sign = 1.0 if f.kind == "more_like_this" else -1.0
        # "Not my genre" teaches the most about tags; "dislike this artist" teaches least.
        strength = 1.0 if (f.kind == "more_like_this" or f.reason == "genre") else 0.4
        for t in tags[:5]:
            ct = clean_tag(t)
            m.tag_w[ct] = m.tag_w.get(ct, 0.0) + sign * strength * 0.5
        if sign > 0:
            for s in seeds[:3]:
                m.seed_w[s] = m.seed_w.get(s, 0.0) + 0.5
    return m


def score_candidate(base: float, n_sources: int, fit: float | None, obsc: float | None,
                    fresh: float, fb: float, cfg: ArtistDiscoverySettings) -> tuple[float, dict[str, dict]]:
    """Blend the signals. Tag fit and obscurity are centred on 0.5 so an unknown or
    average artist is neither helped nor hurt; everything is shown in the breakdown."""
    parts: dict[str, dict] = {
        "similarity": {"value": round(base, 3), "weight": cfg.weight_similarity,
                       "points": base * cfg.weight_similarity},
        "agreement": {"value": n_sources, "weight": cfg.weight_agreement,
                      "points": cfg.weight_agreement * max(n_sources - 1, 0)},
        "tag_fit": {"value": None if fit is None else round(fit, 3), "weight": cfg.weight_tag_fit,
                    "points": 0.0 if fit is None else cfg.weight_tag_fit * (fit - 0.5)},
        "obscurity": {"value": None if obsc is None else round(obsc, 3), "weight": cfg.weight_obscurity,
                      "points": 0.0 if obsc is None else cfg.weight_obscurity * (obsc - 0.5)},
        "freshness": {"value": round(fresh, 3), "weight": cfg.weight_freshness,
                      "points": cfg.weight_freshness * fresh},
        "feedback": {"value": round(fb, 3), "weight": cfg.weight_feedback, "points": cfg.weight_feedback * fb},
    }
    total = sum(p["points"] for p in parts.values())
    for p in parts.values():
        p["points"] = round(p["points"], 3)
    return max(total, 0.0), parts


def assign_bucket(fit: float | None, cfg: ArtistDiscoverySettings) -> str:
    if fit is None:
        return "branching"  # no tag data: neither safe nor a stretch
    if fit >= cfg.close_fit:
        return "close"
    if fit < cfg.wildcard_fit:
        return "wildcard"
    return "branching"


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def diversify(items: list[dict], strength: float, max_per_seed: int) -> list[dict]:
    """Greedy maximal-marginal-relevance order. Each item: {score, seeds, tags, ...}.
    Items that would exceed `max_per_seed` for their lead seed are deferred, not dropped."""
    pool = sorted(items, key=lambda x: -x["score"])
    if not pool:
        return []
    top = max(pool[0]["score"], 1e-9)
    picked: list[dict] = []
    deferred: list[dict] = []
    lead_counts: dict[str, int] = {}
    while pool:
        best_i, best_v = 0, None
        for i, it in enumerate(pool):
            sim = 0.0
            sset, tset = set(it.get("seeds", [])[:3]), set(it.get("tags", [])[:5])
            for p in picked[-12:]:
                sim = max(sim, 0.6 * _jaccard(sset, set(p.get("seeds", [])[:3])) +
                          0.4 * _jaccard(tset, set(p.get("tags", [])[:5])))
            v = it["score"] / top - strength * sim
            if best_v is None or v > best_v:
                best_i, best_v = i, v
        it = pool.pop(best_i)
        lead = (it.get("seeds") or [""])[0]
        if max_per_seed > 0 and lead and lead_counts.get(lead, 0) >= max_per_seed:
            deferred.append(it)
            continue
        lead_counts[lead] = lead_counts.get(lead, 0) + 1
        picked.append(it)
    return picked + deferred


def build_reason(seeds: list[str], shared: list[str], bucket: str, sources: list[str],
                 release: dict | None = None) -> str:
    """The always-available, data-only "why" line shown on every card."""
    if seeds:
        lead = seeds[0]
        n = len(seeds)
        s = (f"Linked to {n} of your top artists, mostly {lead}" if n > 1 else f"Similar to {lead}")
    else:
        s = "Suggested from your listening"
    if shared:
        s += f". Shares {', '.join(shared)}"
    extra = [x for x in sources if x != "lastfm"]
    if extra:
        s += f". Also suggested by {', '.join(extra)}"
    if release and release.get("title") and release.get("date"):
        s += f". New: {release['title']} ({release['date'][:4]})"
    return s + "."


# --- gathering (network, cached) --------------------------------------------------------

async def gather_seed_tags(db, lastfm, seeds: list[dict], key_fn, ttl_days: int,
                           sem: asyncio.Semaphore) -> dict[str, list[str]]:
    """Top tags per seed, cached. Returns {seed name: tags}."""
    out: dict[str, list[str]] = {}

    async def one(seed: dict) -> None:
        key = key_fn(seed["name"])
        cached = cache_get(db, key, "lastfm", "tags", ttl_days)
        if cached is not None:
            out[seed["name"]] = cached.get("tags", [])
            return
        async with sem:
            try:
                tags = await lastfm.get_top_tags(seed["name"], seed.get("mbid"))
            except Exception:
                tags = []
        out[seed["name"]] = tags
        if tags:
            cache_put(db, key, seed["name"], "lastfm", "tags", {"tags": tags})

    await asyncio.gather(*[one(s) for s in seeds])
    return out


async def gather_candidate_info(db, lastfm, key: str, name: str, mbid: str | None,
                                ttl_days: int, sem: asyncio.Semaphore) -> dict[str, Any]:
    """{listeners, tags} for a candidate, cached. Falls back to get_top_tags alone
    when the client has no artist.getInfo (older client, tests)."""
    cached = cache_get(db, key, "lastfm", "info", ttl_days)
    if cached is not None:
        return cached
    info: dict[str, Any] = {}
    async with sem:
        fn = getattr(lastfm, "get_artist_info", None)
        try:
            if fn is not None:
                info = await fn(name, mbid) or {}
            if not info.get("tags"):
                tags = await lastfm.get_top_tags(name, mbid)
                info = {**info, "tags": tags or []}
        except Exception:
            info = info or {}
    if info.get("tags") or info.get("listeners") is not None:
        cache_put(db, key, name, "lastfm", "info", info, value=info.get("listeners"))
    return info


async def gather_release(db, key: str, name: str, mbid: str | None, ttl_days: int,
                         network: bool) -> dict | None:
    cached = cache_get(db, key, "musicbrainz", "release", ttl_days)
    if cached is not None:
        return cached or None
    if not (network and mbid):
        return None
    from app.integrations import musicbrainz
    rel = await musicbrainz.latest_release(mbid)
    cache_put(db, key, name, "musicbrainz", "release", rel or {})
    return rel


async def deezer_related(db, key: str, name: str, ttl_days: int, sem: asyncio.Semaphore) -> list[dict]:
    cached = cache_get(db, key, "deezer", "related", ttl_days)
    if cached is not None:
        return cached.get("artists", [])
    from app.integrations import deezer
    async with sem:
        rows = await deezer.get_related_artists(name, limit=20)
    cache_put(db, key, name, "deezer", "related", {"artists": rows})
    return rows


async def listenbrainz_similar(db, lb, key: str, name: str, mbid: str | None, algorithm: str,
                               ttl_days: int, sem: asyncio.Semaphore) -> list[dict]:
    if not mbid:
        return []
    cached = cache_get(db, key, "listenbrainz", "similar", ttl_days)
    if cached is not None:
        return cached.get("artists", [])
    async with sem:
        rows = await lb.similar_artists(mbid, algorithm, limit=30)
    cache_put(db, key, name, "listenbrainz", "similar", {"artists": rows})
    return rows
