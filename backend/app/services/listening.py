"""Unified listening log (v0.101.0).

Discovery used to see only Last.fm. This module pulls plays from Last.fm, Plex (directly,
or through Tautulli), and ListenBrainz into one table and merges the reports of the
same play, so a track Plex played AND the Last.fm scrobbler reported counts once.

Merge rule (all tunable in ArtistDiscoverySettings):
  * same normalised artist + track,
  * reported by DIFFERENT sources (one source twice is a real replay),
  * start times within max(tolerance, half the track length), capped at 10 minutes,
  * each existing play can absorb at most one report per source.
The play keeps the highest-priority source's timestamp; the others are linked to it
(is_primary = 0, canonical_id = primary id) and kept so the preview can show them.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.models.app_setting import AppSetting
from app.models.discovery_v2 import ListenEvent
from app.models.integration import Integration
from app.schemas.settings import ArtistDiscoverySettings

logger = logging.getLogger("powarr")

MAX_TOLERANCE_S = 600
_CURSOR_KEY = "listening_cursor"
_TRACK_NOISE = re.compile(
    r"\s*[\(\[][^)\]]*(remaster|remastered|deluxe|live|mono|stereo|version|edit|bonus)[^)\]]*[\)\]]"
    r"|\s*-\s*(\d{4}\s+)?(remaster(ed)?|mono|stereo|live).*$", re.I)


def norm_artist(name: str) -> str:
    t = (name or "").lower().strip()
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def norm_track(title: str) -> str:
    t = _TRACK_NOISE.sub("", title or "")
    t = re.sub(r"\b(feat|ft|featuring)\b.*$", "", t, flags=re.I)
    return norm_artist(t)


@dataclass
class RawPlay:
    source: str
    artist: str
    track: str
    ts: datetime
    user: str = ""
    album: str = ""
    mbid: str | None = None
    duration_s: int | None = None
    played_s: int | None = None  # how long was actually played, when the source knows
    event_id: str = ""
    db_id: int | None = None

    @property
    def key(self) -> tuple[str, str]:
        return norm_artist(self.artist), norm_track(self.track)


@dataclass
class Group:
    """One real play: the winning report plus the others that were the same play."""
    primary: RawPlay
    members: list[RawPlay] = field(default_factory=list)

    @property
    def sources(self) -> set[str]:
        return {m.source for m in self.members}


def _rank(source: str, priority: list[str]) -> int:
    return priority.index(source) if source in priority else len(priority)


def _tolerance(play: RawPlay, other: RawPlay, base_s: int) -> float:
    dur = play.duration_s or other.duration_s or 0
    return min(max(base_s, dur / 2), MAX_TOLERANCE_S)


def place(groups: dict[tuple[str, str], list[Group]], play: RawPlay,
          base_tolerance_s: int, priority: list[str]) -> tuple[Group, bool]:
    """Attach `play` to the existing play it duplicates, or start a new one.
    Returns (group, created). Mutates `groups`."""
    cands = groups.setdefault(play.key, [])
    best: Group | None = None
    best_gap = None
    for g in cands:
        if play.source in g.sources:
            continue  # same source twice = a replay, not a duplicate
        gap = min(abs((play.ts - m.ts).total_seconds()) for m in g.members)
        if gap <= _tolerance(play, g.primary, base_tolerance_s) and (best_gap is None or gap < best_gap):
            best, best_gap = g, gap
    if best is None:
        g = Group(primary=play, members=[play])
        cands.append(g)
        return g, True
    best.members.append(play)
    if _rank(play.source, priority) < _rank(best.primary.source, priority):
        best.primary = play
    return best, False


def merge_plays(plays: list[RawPlay], base_tolerance_s: int = 180,
                priority: list[str] | None = None) -> list[Group]:
    """Pure merge of a batch of reports into real plays (used by tests and previews)."""
    priority = priority or ["plex", "tautulli", "lastfm", "listenbrainz"]
    groups: dict[tuple[str, str], list[Group]] = {}
    for p in sorted(plays, key=lambda x: x.ts):
        place(groups, p, base_tolerance_s, priority)
    return [g for gs in groups.values() for g in gs]


# --- collectors -----------------------------------------------------------------------

def _integration(db, name: str):
    row = db.query(Integration).filter_by(name=name, enabled=True).first()
    if not row:
        return None
    from app.api.v1.integrations import _get_client
    return _get_client(row)


def _user_ok(cfg: ArtistDiscoverySettings, user: str) -> bool:
    if not cfg.listening_users:
        return True
    return (user or "").strip().lower() in {u.strip().lower() for u in cfg.listening_users}


def _event_id(source: str, artist: str, track: str, ts: int, given: str = "") -> str:
    return f"{source}:{given}" if given else f"{source}:{ts}:{norm_artist(artist)}:{norm_track(track)}"


async def _collect_lastfm(db, since: datetime) -> list[RawPlay]:
    from app.services import artist_discovery as ad
    lf = ad._lastfm_client(db)
    if not lf:
        return []
    out: list[RawPlay] = []
    from_ts = int(since.timestamp())
    for page in range(1, 11):
        try:
            tracks = await lf.get_recent_tracks(from_ts=from_ts, limit=200, page=page)
        except Exception as e:
            logger.warning(f"Listening: Last.fm fetch failed: {e}")
            break
        if not tracks:
            break
        for t in tracks:
            if (t.get("@attr") or {}).get("nowplaying"):
                continue
            uts = (t.get("date") or {}).get("uts")
            artist = t.get("artist") or {}
            name = (artist.get("#text") or artist.get("name") or "").strip() if isinstance(artist, dict) else str(artist).strip()
            title = (t.get("name") or "").strip()
            if not (uts and name and title):
                continue
            album = t.get("album") or {}
            ts = int(uts)
            out.append(RawPlay(
                source="lastfm", artist=name, track=title, ts=datetime.utcfromtimestamp(ts),
                album=(album.get("#text") if isinstance(album, dict) else str(album or "")) or "",
                mbid=((artist.get("mbid") if isinstance(artist, dict) else None) or None),
                event_id=_event_id("lastfm", name, title, ts)))
        if len(tracks) < 200:
            break
    return out


async def _collect_plex(db, cfg: ArtistDiscoverySettings, since: datetime) -> list[RawPlay]:
    out: list[RawPlay] = []
    use_tautulli = cfg.plex_history_via == "tautulli"
    plex = None if use_tautulli else _integration(db, "plex")
    taut = _integration(db, "tautulli") if (use_tautulli or plex is None) else None
    if plex is not None:
        for r in await plex.get_track_history(int(since.timestamp())):
            if not _user_ok(cfg, r["user"]):
                continue
            out.append(RawPlay(source="plex", artist=r["artist"], track=r["track"], album=r["album"],
                               ts=datetime.utcfromtimestamp(r["ts"]), user=r["user"],
                               duration_s=r.get("duration_s"),
                               event_id=_event_id("plex", r["artist"], r["track"], r["ts"], r["id"])))
    elif taut is not None:
        days = max(1, (datetime.utcnow() - since).days + 1)
        for r in await taut.get_music_history(days=days):
            if not _user_ok(cfg, r["user"]):
                continue
            ts = datetime.utcfromtimestamp(r["ts"])
            if ts < since:
                continue
            out.append(RawPlay(source="tautulli", artist=r["artist"], track=r["track"], album=r["album"],
                               ts=ts, user=r["user"], played_s=r.get("played_s") or None,
                               event_id=_event_id("tautulli", r["artist"], r["track"], r["ts"], r["id"])))
    return out


async def _collect_listenbrainz(db, since: datetime) -> list[RawPlay]:
    lb = _integration(db, "listenbrainz")
    if not lb or not lb.username:
        return []
    out = []
    for r in await lb.get_listens(int(since.timestamp())):
        out.append(RawPlay(source="listenbrainz", artist=r["artist"], track=r["track"], album=r["album"],
                           mbid=r.get("mbid"), duration_s=r.get("duration_s"),
                           ts=datetime.utcfromtimestamp(r["ts"]),
                           event_id=_event_id("listenbrainz", r["artist"], r["track"], r["ts"])))
    return out


# --- persistence ----------------------------------------------------------------------

def _load_cursor(db) -> dict[str, int]:
    row = db.query(AppSetting).filter_by(key=_CURSOR_KEY).first()
    try:
        return json.loads(row.value) if row and row.value else {}
    except ValueError:
        return {}


def _save_cursor(db, cur: dict[str, int]) -> None:
    row = db.query(AppSetting).filter_by(key=_CURSOR_KEY).first()
    if not row:
        row = AppSetting(key=_CURSOR_KEY)
        db.add(row)
    row.value = json.dumps(cur)
    db.commit()


def _row_to_raw(r: ListenEvent) -> RawPlay:
    return RawPlay(source=r.source, artist=r.artist, track=r.track, ts=r.played_at,
                   user=r.source_user or "", album=r.album or "", mbid=r.mbid,
                   duration_s=r.duration_s, event_id=r.source_event_id or "", db_id=r.id)


def ingest(db, plays: list[RawPlay], cfg: ArtistDiscoverySettings) -> dict[str, int]:
    """Store new reports, merging each into the play it duplicates. Idempotent: a
    report already stored (same source + event id) is skipped."""
    plays = [p for p in plays
             if p.source in cfg.listening_sources or p.source == "tautulli" and "plex" in cfg.listening_sources]
    plays = [p for p in plays if p.played_s is None or p.played_s >= cfg.listening_min_play_s]
    if not plays:
        return {"new": 0, "merged": 0}
    lo = min(p.ts for p in plays) - timedelta(seconds=MAX_TOLERANCE_S)
    hi = max(p.ts for p in plays) + timedelta(seconds=MAX_TOLERANCE_S)
    existing = db.query(ListenEvent).filter(ListenEvent.played_at >= lo, ListenEvent.played_at <= hi).all()
    seen_ids = {(r.source, r.source_event_id) for r in existing if r.source_event_id}
    by_id: dict[int, ListenEvent] = {r.id: r for r in existing}

    groups: dict[tuple[str, str], list[Group]] = {}
    group_of_canon: dict[int, Group] = {}
    for r in sorted(existing, key=lambda x: x.played_at):
        raw = _row_to_raw(r)
        cid = r.canonical_id or r.id
        g = group_of_canon.get(cid)
        if g is None:
            g = Group(primary=raw, members=[raw])
            group_of_canon[cid] = g
            groups.setdefault(raw.key, []).append(g)
        else:
            g.members.append(raw)
        if r.is_primary:
            g.primary = raw

    new = merged = 0
    pending: list[tuple[RawPlay, Group]] = []
    for p in sorted(plays, key=lambda x: x.ts):
        if p.event_id and (p.source, p.event_id) in seen_ids:
            continue
        if p.event_id:
            seen_ids.add((p.source, p.event_id))
        g, created = place(groups, p, cfg.listening_dedupe_tolerance_s, cfg.listening_source_priority)
        pending.append((p, g))
        if created:
            new += 1
        else:
            merged += 1

    rows: dict[int, ListenEvent] = {}  # id(RawPlay) -> row
    for p, _g in pending:
        row = ListenEvent(
            source=p.source, source_user=p.user or None, source_event_id=p.event_id or None,
            artist=p.artist, artist_key=norm_artist(p.artist), track=p.track, track_key=norm_track(p.track),
            album=p.album or None, mbid=p.mbid, played_at=p.ts, duration_s=p.duration_s, is_primary=0)
        db.add(row)
        rows[id(p)] = row
    db.flush()
    for p, _g in pending:
        p.db_id = rows[id(p)].id
        by_id[p.db_id] = rows[id(p)]

    # Settle primaries: every member of a group points at its primary, and only the
    # primary has is_primary = 1.
    touched = {id(g): g for _p, g in pending}
    for g in touched.values():
        prim_id = g.primary.db_id
        if prim_id is None:
            continue
        for m in g.members:
            if m.db_id is None:
                continue
            row = by_id.get(m.db_id)
            if row is None:
                continue
            row.canonical_id = prim_id
            row.is_primary = 1 if m is g.primary else 0
    db.commit()
    return {"new": new, "merged": merged}


def prune(db, retention_days: int) -> int:
    cutoff = datetime.utcnow() - timedelta(days=max(retention_days, 7))
    n = db.query(ListenEvent).filter(ListenEvent.played_at < cutoff).delete()
    if n:
        db.commit()
    return n


async def sync_listening(db, cfg: ArtistDiscoverySettings | None = None) -> dict[str, Any]:
    """Pull every enabled source since its cursor, merge, store. Fail-soft per source."""
    from app.services import artist_discovery as ad
    cfg = cfg or ad.load_settings(db)
    result: dict[str, Any] = {"ok": True, "sources": {}, "new": 0, "merged": 0, "message": ""}
    if not cfg.listening_enabled:
        result.update(ok=False, message="Listening log is switched off.")
        return result
    cursor = _load_cursor(db)
    now = datetime.utcnow()
    backfill = now - timedelta(days=max(cfg.listening_backfill_days, 1))
    collected: list[RawPlay] = []
    jobs = []
    if "lastfm" in cfg.listening_sources:
        jobs.append(("lastfm", lambda s: _collect_lastfm(db, s)))
    if "plex" in cfg.listening_sources:
        jobs.append(("plex", lambda s: _collect_plex(db, cfg, s)))
    if "listenbrainz" in cfg.listening_sources:
        jobs.append(("listenbrainz", lambda s: _collect_listenbrainz(db, s)))
    for name, fn in jobs:
        since = backfill
        if cursor.get(name):
            since = max(backfill, datetime.utcfromtimestamp(cursor[name]) - timedelta(hours=6))
        try:
            got = await fn(since)
            result["sources"][name] = {"fetched": len(got), "since": since.isoformat()}
            collected.extend(got)
            cursor[name] = int(now.timestamp())
        except Exception as e:
            logger.warning(f"Listening: {name} collector failed: {e}")
            result["sources"][name] = {"fetched": 0, "error": str(e)}
    stats = ingest(db, collected, cfg)
    _save_cursor(db, cursor)
    prune(db, cfg.listening_retention_days)
    result.update(stats)
    result["message"] = (f"{stats['new']} new play(s), {stats['merged']} duplicate report(s) merged "
                         f"from {len(collected)} fetched")
    return result


# --- queries --------------------------------------------------------------------------

_PERIOD_DAYS = {"7day": 7, "1month": 30, "3month": 90, "6month": 180, "12month": 365, "overall": None}


def log_size(db, days: int | None = None) -> int:
    q = db.query(ListenEvent).filter(ListenEvent.is_primary == 1)
    if days:
        q = q.filter(ListenEvent.played_at >= datetime.utcnow() - timedelta(days=days))
    return q.count()


def seed_plays(db, period: str, half_life_days: int = 0) -> list[dict]:
    """Per-artist play counts from the de-duplicated log for a seed period, most played
    first: [{name, mbid, plays, weighted}]. `weighted` applies the optional time decay."""
    days = _PERIOD_DAYS.get(period, 90)
    q = db.query(ListenEvent).filter(ListenEvent.is_primary == 1)
    now = datetime.utcnow()
    if days:
        q = q.filter(ListenEvent.played_at >= now - timedelta(days=days))
    agg: dict[str, dict] = {}
    for r in q.all():
        a = agg.setdefault(r.artist_key, {"names": {}, "mbid": None, "plays": 0, "weighted": 0.0})
        a["names"][r.artist] = a["names"].get(r.artist, 0) + 1
        a["mbid"] = a["mbid"] or r.mbid
        a["plays"] += 1
        age = max((now - r.played_at).total_seconds() / 86400.0, 0.0)
        a["weighted"] += 0.5 ** (age / half_life_days) if half_life_days > 0 else 1.0
    out = [{"name": max(v["names"], key=v["names"].get), "mbid": v["mbid"],
            "plays": v["plays"], "weighted": v["weighted"]} for v in agg.values()]
    out.sort(key=lambda x: -x["weighted"])
    return out


def played_since(db, artist_key: str, since: datetime) -> int:
    return (db.query(ListenEvent).filter(ListenEvent.is_primary == 1, ListenEvent.artist_key == artist_key,
                                         ListenEvent.played_at >= since).count())


def dedupe_preview(db, days: int = 7, examples: int = 8) -> dict[str, Any]:
    """"412 raw reports, 301 real plays, 96 seen by two sources" plus examples."""
    since = datetime.utcnow() - timedelta(days=max(days, 1))
    rows = db.query(ListenEvent).filter(ListenEvent.played_at >= since).all()
    by_canon: dict[int, list[ListenEvent]] = {}
    for r in rows:
        by_canon.setdefault(r.canonical_id or r.id, []).append(r)
    per_source: dict[str, int] = {}
    for r in rows:
        per_source[r.source] = per_source.get(r.source, 0) + 1
    multi = [g for g in by_canon.values() if len({m.source for m in g}) > 1]
    multi.sort(key=lambda g: max(m.played_at for m in g), reverse=True)
    return {
        "days": days, "raw_reports": len(rows), "real_plays": len(by_canon),
        "duplicates_merged": len(rows) - len(by_canon), "seen_by_multiple_sources": len(multi),
        "per_source": per_source,
        "examples": [{
            "artist": g[0].artist, "track": g[0].track,
            "played_at": min(m.played_at for m in g).isoformat() + "Z",
            "sources": sorted({m.source for m in g}),
            "gap_seconds": int((max(m.played_at for m in g) - min(m.played_at for m in g)).total_seconds()),
        } for g in multi[:examples]],
    }


def source_status(db, cfg: ArtistDiscoverySettings) -> list[dict[str, Any]]:
    """Which sources are configured and when they last delivered."""
    cursor = _load_cursor(db)
    out = []
    for name in ("lastfm", "plex", "listenbrainz"):
        if name == "plex":
            via = cfg.plex_history_via
            ok = bool(_integration(db, "tautulli" if via == "tautulli" else "plex")
                      or _integration(db, "tautulli") or _integration(db, "plex"))
            label = f"Plex ({'Tautulli' if via == 'tautulli' else 'direct'})"
        else:
            ok = bool(db.query(Integration).filter_by(name=name, enabled=True).first())
            label = "Last.fm" if name == "lastfm" else "ListenBrainz"
        last = db.query(ListenEvent).filter_by(source=name).order_by(ListenEvent.played_at.desc()).first()
        n = db.query(ListenEvent).filter_by(source=name).count()
        out.append({"source": name, "label": label, "enabled": name in cfg.listening_sources,
                    "configured": ok, "events": n,
                    "last_play": last.played_at.isoformat() + "Z" if last else None,
                    "last_sync": datetime.utcfromtimestamp(cursor[name]).isoformat() + "Z" if cursor.get(name) else None})
    return out


def known_users(db) -> list[str]:
    rows = (db.query(ListenEvent.source_user).filter(ListenEvent.source_user.isnot(None)).distinct().all())
    return sorted({r[0] for r in rows if r[0]})
