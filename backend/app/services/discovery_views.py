"""Read models for the Discover page (v0.101.0): shelves, the taste summary, the
scorecard, the preview sampler, feedback, and the genre explorer. Everything here
reads what the direct lane already stored; nothing calls Last.fm except the genre
explorer and the sampler (Deezer previews)."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from app.models.artist_discovery import DiscoveredArtist
from app.models.discovery_v2 import DiscoveryFeedback, ListenEvent
from app.schemas.settings import ArtistDiscoverySettings
from app.services import discovery_ranker as rk
from app.services import listening

logger = logging.getLogger("powarr")

SHELF_SIZE = 12


def _j(text: str | None, default: Any) -> Any:
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default


def card(row: DiscoveredArtist, top_score: float) -> dict[str, Any]:
    """One result card. `match` is a 0..100 meter relative to the best current candidate,
    so it reads as "how strong compared with the rest", not a raw score."""
    bd = _j(row.breakdown, {})
    seeds = _j(row.seed_artist_names, [])
    tags = _j(row.tags, []) or _j(row.genres, [])
    score = row.rank_score or 0.0
    return {
        "id": row.id, "artist_name": row.artist_name, "musicbrainz_id": row.musicbrainz_id,
        "image_url": row.image_url, "bucket": row.bucket, "rank_score": round(score, 3),
        "match": int(round(100 * min(score / top_score, 1.0))) if top_score > 0 else 0,
        "reason": row.reason or (f"Similar to {seeds[0]}" if seeds else "Suggested from your listening"),
        "blurb": row.blurb, "tags": tags[:4], "seeds": seeds[:4], "listeners": row.listeners,
        "latest_release": _j(row.latest_release, None), "sources": bd.get("_sources", ["lastfm"]),
        "years_active": row.years_active, "created_at": row.created_at,
        "breakdown": {k: v for k, v in bd.items() if not k.startswith("_")},
    }


def build_shelves(db, cfg: ArtistDiscoverySettings, adventure: float | None = None) -> dict[str, Any]:
    adv = cfg.default_adventure if adventure is None else max(0.0, min(1.0, adventure))
    rows = (db.query(DiscoveredArtist).filter(DiscoveredArtist.status == "pending")
            .order_by(DiscoveredArtist.rank_score.desc().nullslast(), DiscoveredArtist.created_at.desc())
            .limit(400).all())
    ranked = [r for r in rows if r.rank_score is not None]
    legacy = [r for r in rows if r.rank_score is None]
    top = max((r.rank_score for r in ranked), default=0.0)
    cards = {r.id: card(r, top) for r in ranked}
    shelves: list[dict[str, Any]] = []

    def add(sid: str, title: str, subtitle: str, items: list[dict]) -> None:
        if items:
            shelves.append({"id": sid, "title": title, "subtitle": subtitle, "items": items[:SHELF_SIZE]})

    by_bucket: dict[str, list[dict]] = {b: [] for b in rk.BUCKETS}
    for r in ranked:
        by_bucket.setdefault(r.bucket or "branching", []).append(cards[r.id])

    # Mixed for you: quotas follow the Familiar <-> Adventurous slider, bounded by the admin cap.
    wild = round(SHELF_SIZE * cfg.wildcard_max_share * adv)
    branch = round(SHELF_SIZE * (0.15 + 0.45 * adv))
    close = max(SHELF_SIZE - wild - branch, 0)
    mixed: list[dict] = []
    taken: set[int] = set()
    for bucket, n in (("close", close), ("branching", branch), ("wildcard", wild)):
        for c in by_bucket.get(bucket, [])[:n]:
            mixed.append(c)
            taken.add(c["id"])
    if len(mixed) < SHELF_SIZE:  # a thin bucket is topped up from the best remaining
        for r in ranked:
            if len(mixed) >= SHELF_SIZE:
                break
            if r.id not in taken and (wild > 0 or r.bucket != "wildcard"):
                mixed.append(cards[r.id])
                taken.add(r.id)
    mixed.sort(key=lambda c: -c["rank_score"])
    add("mixed", "Mixed for you", f"Your mix at adventure {int(adv * 100)}%", mixed)

    # Because you play X: one shelf per top lead seed with enough results.
    lead: dict[str, list[dict]] = {}
    for r in ranked:
        seeds = _j(r.seed_artist_names, [])
        if seeds:
            lead.setdefault(seeds[0], []).append(cards[r.id])
    for seed, items in sorted(lead.items(), key=lambda kv: -len(kv[1]))[:3]:
        if len(items) >= 3:
            add(f"seed:{seed}", f"Because you play {seed}", "Artists most strongly linked to it", items)

    add("close", rk.BUCKET_LABELS["close"], "Strong matches for what you already play", by_bucket["close"])
    add("branching", rk.BUCKET_LABELS["branching"], "Nearby sounds that widen your taste", by_bucket["branching"])
    obscure = sorted([cards[r.id] for r in ranked if r.listeners is not None and r.listeners <= 50_000],
                     key=lambda c: -c["rank_score"])
    add("under_radar", "Under the radar", "Smaller artists with a real link to your taste", obscure)
    cutoff = datetime.utcnow() - timedelta(days=max(cfg.freshness_days, 1))
    fresh = []
    for r in ranked:
        rel = _j(r.latest_release, None)
        d = rk.parse_release_date((rel or {}).get("date"))
        if d and d >= cutoff:
            fresh.append((d, cards[r.id]))
    add("new_releases", "New from artists like your favourites", "Released recently",
        [c for _d, c in sorted(fresh, key=lambda x: x[0], reverse=True)])
    add("wildcard", rk.BUCKET_LABELS["wildcard"], "Further from your usual, with a real link", by_bucket["wildcard"])
    if legacy:
        add("other", "More suggestions", "Found by earlier discovery lanes",
            [card(r, top or 1.0) | {"match": 0} for r in legacy])
    return {"adventure": adv, "total_pending": len(rows), "shelves": shelves}


def taste_summary(db, cfg: ArtistDiscoverySettings) -> dict[str, Any]:
    seeds = listening.seed_plays(db, cfg.seed_period, cfg.seed_half_life_days)[:25]
    weights = {s["name"]: s["weighted"] for s in seeds}
    tags_by_seed: dict[str, list[str]] = {}
    for s in seeds:
        c = rk.cache_get(db, listening.norm_artist(s["name"]), "lastfm", "tags", 3650)
        if c:
            tags_by_seed[s["name"]] = c.get("tags", [])
    profile = rk.tag_profile(tags_by_seed, weights)
    excl = {listening.norm_artist(n) for n in cfg.seed_exclude_artists}
    return {
        "period": cfg.seed_period, "log_events": listening.log_size(db),
        "top_artists": [{"name": s["name"], "plays": s["plays"],
                         "excluded": listening.norm_artist(s["name"]) in excl} for s in seeds[:15]],
        "top_tags": [{"tag": t, "weight": round(w, 3)} for t, w in sorted(profile.items(), key=lambda kv: -kv[1])[:15]],
        "excluded_artists": cfg.seed_exclude_artists, "excluded_tags": cfg.seed_exclude_tags,
        "users": listening.known_users(db), "counted_users": cfg.listening_users,
    }


def record_feedback(db, candidate_id: int, kind: str, reason: str | None = None) -> dict[str, Any]:
    from app.services import artist_discovery as ad
    if kind not in ("more_like_this", "not_for_me", "known"):
        return {"ok": False, "message": "kind must be more_like_this, not_for_me or known"}
    row = db.query(DiscoveredArtist).filter_by(id=candidate_id).first()
    if not row:
        return {"ok": False, "message": "Candidate not found"}
    tags = _j(row.tags, []) or _j(row.genres, [])
    db.add(DiscoveryFeedback(
        artist_key=listening.norm_artist(row.artist_name), artist_name=row.artist_name, kind=kind,
        reason=reason if kind == "not_for_me" else None, tags=json.dumps(tags[:8]),
        seeds=json.dumps(_j(row.seed_artist_names, [])[:4])))
    db.commit()
    if kind in ("not_for_me", "known"):
        ad.reject_candidate(db, candidate_id)  # never resurfaces; the preference stays as feedback
    msg = {"more_like_this": "Noted. Similar artists will rank higher.",
           "not_for_me": "Noted. Similar artists will rank lower.",
           "known": "Marked as already known."}[kind]
    return {"ok": True, "message": msg}


def scorecard(db, cfg: ArtistDiscoverySettings, days: int = 90) -> dict[str, Any]:
    """Did suggestions turn into listening? A "hit" is at least one real play of the
    artist (from any source, so YouTube Music via Last.fm counts) within the window after
    the suggestion was added or queued."""
    since = datetime.utcnow() - timedelta(days=days)
    rows = db.query(DiscoveredArtist).filter(DiscoveredArtist.created_at >= since,
                                             DiscoveredArtist.source == "direct").all()
    window = timedelta(days=max(cfg.scorecard_window_days, 1))
    groups: dict[str, dict[str, dict[str, int]]] = {"bucket": {}, "source": {}}
    totals = {"suggested": 0, "added": 0, "rejected": 0, "pending": 0, "hits": 0, "measurable": 0}

    def bump(kind: str, key: str, field: str) -> None:
        g = groups[kind].setdefault(key, {"suggested": 0, "added": 0, "rejected": 0, "hits": 0, "measurable": 0})
        g[field] += 1

    now = datetime.utcnow()
    for r in rows:
        key = listening.norm_artist(r.artist_name)
        anchor = r.resolved_at if r.status == "accepted" and r.resolved_at else r.created_at
        measurable = now >= anchor + window or r.status == "accepted"
        end = min(anchor + window, now)
        hit = (db.query(ListenEvent).filter(
            ListenEvent.is_primary == 1, ListenEvent.artist_key == key,
            ListenEvent.played_at >= anchor, ListenEvent.played_at <= end).count() > 0)
        sources = _j(r.breakdown, {}).get("_sources", ["lastfm"])
        keys = [("bucket", r.bucket or "unknown")] + [("source", s) for s in sources]
        totals["suggested"] += 1
        totals["added" if r.status == "accepted" else "rejected" if r.status == "rejected" else "pending"] += 1
        if measurable:
            totals["measurable"] += 1
            totals["hits"] += 1 if hit else 0
        for kind, k in keys:
            bump(kind, k, "suggested")
            if r.status == "accepted":
                bump(kind, k, "added")
            elif r.status == "rejected":
                bump(kind, k, "rejected")
            if measurable:
                bump(kind, k, "measurable")
                if hit:
                    bump(kind, k, "hits")

    def table(kind: str) -> list[dict]:
        out = []
        for k, g in sorted(groups[kind].items(), key=lambda kv: -kv[1]["suggested"]):
            out.append({"key": k, **g, "hit_rate": round(g["hits"] / g["measurable"], 3) if g["measurable"] else None})
        return out

    by_bucket, by_source = table("bucket"), table("source")
    insights = []
    ranked = [g for g in by_source + by_bucket if g["measurable"] >= 5 and g["hit_rate"] is not None]
    if ranked:
        best = max(ranked, key=lambda g: g["hit_rate"])
        worst = min(ranked, key=lambda g: g["hit_rate"])
        insights.append(f"Best performer: {best['key']} ({round(best['hit_rate'] * 100)}% played).")
        if worst is not best:
            insights.append(f"Weakest: {worst['key']} ({round(worst['hit_rate'] * 100)}% played).")
    return {"days": days, "window_days": cfg.scorecard_window_days, "totals": totals,
            "hit_rate": round(totals["hits"] / totals["measurable"], 3) if totals["measurable"] else None,
            "by_bucket": by_bucket, "by_source": by_source, "insights": insights}


async def sampler(db, limit: int = 10) -> list[dict[str, Any]]:
    """One 30-second Deezer clip per top pending candidate, for back-to-back listening."""
    from app.integrations import deezer
    rows = (db.query(DiscoveredArtist).filter(DiscoveredArtist.status == "pending")
            .order_by(DiscoveredArtist.rank_score.desc().nullslast()).limit(limit * 2).all())
    sem = asyncio.Semaphore(4)

    async def one(r: DiscoveredArtist):
        async with sem:
            tracks = await deezer.get_artist_top_tracks(r.artist_name, limit=3)
        t = next((x for x in tracks if x.get("preview")), None)
        if not t:
            return None
        return {"id": r.id, "artist_name": r.artist_name, "image_url": r.image_url, "bucket": r.bucket,
                "title": t.get("title"), "preview": t["preview"], "album_cover": t.get("album_cover")}

    got = await asyncio.gather(*[one(r) for r in rows], return_exceptions=True)
    return [g for g in got if isinstance(g, dict)][:limit]


async def genre_explore(db, tag: str, limit: int = 30, offset: int = 0) -> dict[str, Any]:
    """Last.fm's top artists for a tag, minus what you own, have played, or already
    triaged. Dig deeper with `offset` for less obvious names."""
    from app.services import artist_discovery as ad
    lastfm = ad._lastfm_client(db)
    if not lastfm:
        return {"ok": False, "message": "Last.fm is not configured", "results": []}
    try:
        got = await lastfm.get_tag_top_artists(tag, limit=min(offset + limit * 3, 200))
    except Exception as e:
        return {"ok": False, "message": f"Last.fm failed: {e}", "results": []}
    idx = await ad._lidarr_artist_index(db) or ({}, {})
    by_mbid, by_name = idx
    plex = ad._plex_artist_names(db)
    played = {r[0] for r in db.query(ListenEvent.artist_key).filter(ListenEvent.is_primary == 1).distinct().all()}
    triaged = {listening.norm_artist(r[0]) for r in db.query(DiscoveredArtist.artist_name).all()}
    out = []
    for i, a in enumerate(got):
        if i < offset:
            continue
        name = (a.get("name") or "").strip()
        key = listening.norm_artist(name)
        mbid = (a.get("mbid") or "").strip() or None
        if not name or key in by_name or (mbid and mbid in by_mbid) or key in plex or key in played:
            continue
        out.append({"artist_name": name, "musicbrainz_id": mbid, "rank": i + 1,
                    "already_triaged": key in triaged})
        if len(out) >= limit:
            break
    return {"ok": True, "tag": tag, "offset": offset, "results": out,
            "message": "" if out else "Nothing new for that tag. Try another or a larger offset."}
