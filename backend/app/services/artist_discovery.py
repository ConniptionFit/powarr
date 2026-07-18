"""Artist Discovery — native port of the n8n Music Curator (Last.fm scrobbles →
Ollama embeddings → Qdrant taste-centroid similarity + related-artist graph → Lidarr).

Writes to the same `music_affinity_space` Qdrant collection Smart Playlists reads —
soft-delete semantics throughout (never delete a point, only flip flags), matching
the n8n curator's rule. See vault [[Artist Discovery]].

Plain SessionLocal() per function (no FastAPI Depends) so these are callable from
both API routes and the scheduler, mirroring playlist_generator.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

import httpx

from app.database import SessionLocal
from app.models.app_setting import AppSetting
from app.models.artist_add_log import ArtistAddLog
from app.models.artist_discovery import ArtistDiscoveryRun, DiscoveredArtist
from app.models.integration import Integration
from app.models.media import MediaItem
from app.schemas.settings import ArtistDiscoverySettings

logger = logging.getLogger("powarr")


def _norm_artist(name: str) -> str:
    t = (name or "").lower().strip()
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _normalize_mood_key(mood: str) -> str:
    """AD-19 — a configured mood ("Feel Good") into a stable source-tag suffix
    ("feel_good"), so DiscoveredArtist.source stays a clean identifier."""
    t = (mood or "").strip().lower()
    t = re.sub(r"[^\w]+", "_", t)
    return t.strip("_") or "mood"


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

# Placeholder values Lidarr/MusicBrainz/Last.fm metadata gaps produce — a literal
# "Unknown" genre chip adds no signal on a candidate card (AD-06).
_PLACEHOLDER_TAGS = {"unknown", "none", "n/a", "na", ""}


def clean_tags(tags: list[str] | None) -> list[str]:
    out: list[str] = []
    for t in tags or []:
        s = (t or "").strip()
        if s.lower() in _PLACEHOLDER_TAGS or s in out:
            continue
        out.append(s)
    return out


def clean_era(era: str | None) -> str | None:
    s = (era or "").strip()
    return None if s.lower() in _PLACEHOLDER_TAGS else s


# SP-15 — Last.fm's artist.gettoptags returns freeform user tags with no
# genre/mood distinction (a "genres" list already carries all of them). This
# is a curated keyword set of tags that describe a listening mood/vibe rather
# than a genre, matched case-insensitively against those same already-fetched
# tags — purely a classification of existing data, never a second API call.
# Left as a static list rather than a user-editable map (unlike genre_aliases)
# since these aren't a taxonomy judgment call the way genre equivalence is.
_MOOD_KEYWORDS = {
    "chill", "chillout", "mellow", "relaxing", "relax", "calm", "calming",
    "sad", "sadcore", "melancholy", "melancholic", "bittersweet", "moody",
    "happy", "feel good", "feelgood", "uplifting", "upbeat", "energetic",
    "aggressive", "angry", "dark", "dreamy", "ethereal", "atmospheric",
    "romantic", "sensual", "party", "fun", "introspective", "nostalgic",
    "hopeful", "ambient", "soothing", "peaceful", "intense", "epic",
    "dramatic", "groovy", "driving", "motivational", "late night",
}


def classify_mood_tags(tags: list[str] | None) -> list[str]:
    """Subset of an artist's Last.fm tags that read as a mood/vibe rather than
    a genre. Pure and cheap — no I/O, safe to recompute every sync cycle."""
    out: list[str] = []
    for t in tags or []:
        s = (t or "").strip()
        if s and s.lower() in _MOOD_KEYWORDS and s not in out:
            out.append(s)
    return out


async def _resolve_seed_names(qdrant, seed_keys: list[str] | None) -> list[str]:
    """associated_seed_mbids holds a mix of MBIDs and bare names (for mbid-less
    seeds). Resolve the MBIDs back to artist names via their Qdrant points so the
    review queue can show every contributing seed artist (AD-05)."""
    by_mbid: dict[str, str] = {}
    mbids = [k for k in seed_keys or [] if k and _UUID_RE.match(k)]
    if mbids and qdrant:
        try:
            points = await qdrant.retrieve_points([qdrant.point_id(m, "") for m in mbids])
            for p in points:
                payload = p.get("payload") or {}
                if payload.get("musicbrainz_id") and payload.get("artist_name"):
                    by_mbid[payload["musicbrainz_id"]] = payload["artist_name"]
        except Exception as e:
            logger.debug(f"Artist Discovery: seed name resolution failed: {e}")
    names: list[str] = []
    for k in seed_keys or []:
        name = by_mbid.get(k, "") if (k and _UUID_RE.match(k)) else (k or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def load_settings(db) -> ArtistDiscoverySettings:
    """Load Artist Discovery settings with AD-07 dual-threshold migration.

    Pre-v0.42 used a single `auto_add_connection_threshold` as the suggest/qualify
    gate plus a boolean `auto_promote`. New installs: suggest=3, auto_add=0 (off).
    Existing rows without `suggest_connection_threshold` get suggest = old threshold;
    auto_add stays at the old value only when `auto_promote` was on, else 0.
    """
    row = db.query(AppSetting).filter_by(key="artist_discovery").first()
    if not row or not row.value:
        return ArtistDiscoverySettings()
    data = json.loads(row.value)
    if "suggest_connection_threshold" not in data:
        old_threshold = int(data.get("auto_add_connection_threshold") or 3)
        data["suggest_connection_threshold"] = old_threshold
        if not data.get("auto_promote"):
            data["auto_add_connection_threshold"] = 0
        # else keep auto_add_connection_threshold at the old value (auto-add at same bar)
        row.value = json.dumps(data)
        db.commit()
    return ArtistDiscoverySettings(**data)


def save_settings(db, cfg: ArtistDiscoverySettings) -> None:
    row = db.query(AppSetting).filter_by(key="artist_discovery").first()
    if not row:
        row = AppSetting(key="artist_discovery")
        db.add(row)
    row.value = cfg.model_dump_json()
    db.commit()


def _load_state(db) -> dict:
    row = db.query(AppSetting).filter_by(key="artist_discovery_state").first()
    if not row or not row.value:
        return {}
    return json.loads(row.value)


def _save_state(db, state: dict) -> None:
    row = db.query(AppSetting).filter_by(key="artist_discovery_state").first()
    if not row:
        row = AppSetting(key="artist_discovery_state")
        db.add(row)
    row.value = json.dumps(state)
    db.commit()


def _qdrant(db):
    """Shared Qdrant connection (Settings -> Integrations), not per-module config."""
    from app.services import qdrant_config
    return qdrant_config.client(db)


def _lastfm_client(db):
    row = db.query(Integration).filter_by(name="lastfm").first()
    if not row or not row.enabled or not row.api_key or not row.username:
        return None
    from app.api.v1.integrations import _get_client
    return _get_client(row)


def _lidarr_client(db):
    row = db.query(Integration).filter_by(name="lidarr", enabled=True).first()
    if not row:
        return None
    from app.api.v1.integrations import _get_client
    return _get_client(row)


def _plex_client(db):
    row = db.query(Integration).filter_by(name="plex", enabled=True).first()
    if not row:
        return None
    from app.api.v1.integrations import _get_client
    return _get_client(row)


def _plex_artist_names(db) -> set[str]:
    """Normalized artist names actually present in the locally-synced Plex library
    (MediaItem tracks, from the regular Plex sync) — a real "already own this in
    Plex" signal, distinct from `plex_fulfillment` which is derived from Lidarr's
    own download-percentage stats, not Plex itself."""
    rows = (db.query(MediaItem.parent_title)
            .filter(MediaItem.media_type == "track", MediaItem.parent_title.isnot(None))
            .distinct().all())
    return {_norm_artist(r[0]) for r in rows if r[0]}


async def _lidarr_artist_index(db) -> tuple[dict[str, dict], dict[str, dict]] | None:
    """(by_mbid, by_name) of every Lidarr artist, monitored or not — "already in
    Lidarr" for exclusion purposes is a broader question than `is_monitored_lidarr`
    (which only tracks the monitored subset used as graph-sync seeds). None means
    Lidarr isn't configured at all — distinct from a configured-but-empty library."""
    lidarr_row = db.query(Integration).filter_by(name="lidarr", enabled=True).first()
    if not lidarr_row:
        return None
    from app.api.v1.integrations import _get_client
    lidarr = _get_client(lidarr_row)
    artists = await lidarr.get_artists()
    by_mbid = {a.get("foreignArtistId"): a for a in artists if a.get("foreignArtistId")}
    by_name = {_norm_artist(a.get("artistName") or ""): a for a in artists}
    return by_mbid, by_name


async def _enrich_candidate(db, mbid: str | None, name: str) -> dict[str, Any]:
    """Best-effort image/bio/genres/years_active — never raises, never blocks
    candidate creation on failure."""
    try:
        from app.services import artist_enrichment
        return await artist_enrichment.enrich(_lidarr_client(db), mbid, name)
    except Exception as e:
        logger.debug(f"Artist Discovery: enrichment failed for {name}: {e}")
        return {"image_url": None, "bio": None, "genres": [], "years_active": None}


async def _embed_artist(cfg: ArtistDiscoverySettings, name: str, tags: list[str]) -> list[float] | None:
    """Artist Discovery's Ollama connection is fully standalone — it never falls
    back to (or depends on) the separate Local LLM Assist Ollama configuration,
    even though both may point at the same host in practice."""
    from app.services import embeddings
    text = f"Artist: {name}. Tags: {', '.join(tags or [])}."
    return await embeddings.embed(cfg.ollama_host, cfg.embed_model, text)


def _find_candidate(db, mbid: str | None, name: str) -> DiscoveredArtist | None:
    if mbid:
        row = db.query(DiscoveredArtist).filter_by(musicbrainz_id=mbid).first()
        if row:
            return row
    return db.query(DiscoveredArtist).filter_by(artist_name=name).first()


def _candidate_exists(db, mbid: str | None, name: str) -> bool:
    """Any prior row (any status) permanently blocks re-surfacing — a rejected
    candidate never comes back, same precedent as Smart Playlists' artist dedupe."""
    return _find_candidate(db, mbid, name) is not None


# --- Ingestion -----------------------------------------------------------------

async def ingest_scrobbles(db, cfg: ArtistDiscoverySettings) -> dict[str, Any]:
    """Pull Last.fm top artists as taste seeds, embed new ones, upsert into Qdrant
    as is_discovered=true. Simplified from n8n's precise recent-tracks delta cursor —
    uses top-artists (by playcount) as the seed set instead."""
    lastfm = _lastfm_client(db)
    if not lastfm:
        return {"ok": False, "message": "Last.fm integration not configured", "ingested": 0}
    qdrant = _qdrant(db)
    if not qdrant:
        return {"ok": False, "message": "Qdrant not configured (Settings → Integrations)", "ingested": 0}
    try:
        top_artists = await lastfm.get_top_artists(limit=200)
    except Exception as e:
        return {"ok": False, "message": f"Last.fm fetch failed: {e}", "ingested": 0}

    ingested = 0
    cap = max(cfg.max_candidates_per_run * 4, 10)
    for a in top_artists:
        if ingested >= cap:
            break
        name = (a.get("name") or "").strip()
        if not name:
            continue
        mbid = (a.get("mbid") or "").strip() or None
        plays = int(a.get("playcount") or 0)
        pid = qdrant.point_id(mbid, name)
        try:
            existing = await qdrant.retrieve_points([pid])
        except Exception as e:
            logger.warning(f"Artist Discovery ingest: Qdrant retrieve failed for {name}: {e}")
            continue
        if existing and (existing[0].get("payload") or {}).get("is_discovered"):
            continue  # already tracked as a discovered seed
        try:
            tags = await lastfm.get_top_tags(name, mbid)
        except Exception:
            tags = []
        vector = await _embed_artist(cfg, name, tags)
        if not vector:
            continue
        payload = {
            "musicbrainz_id": mbid or "",
            "artist_name": name,
            "genres": tags,
            "mood_tags": classify_mood_tags(tags),
            "era": "",
            "is_monitored_lidarr": False,
            "plex_fulfillment": "none",
            "in_lidarr": False,
            "in_plex": False,
            "total_plays_global": plays,
            "last_played_timestamp": int(datetime.utcnow().timestamp()),
            "is_discovered": True,
            "associated_seed_mbids": [],
            "last_related_scan_timestamp": 0,
        }
        if existing:
            payload = {**(existing[0].get("payload") or {}), **payload}
        try:
            await qdrant.upsert_points([{"id": pid, "vector": vector, "payload": payload}])
        except Exception as e:
            logger.warning(f"Artist Discovery ingest: Qdrant upsert failed for {name}: {e}")
            continue
        ingested += 1

    state = _load_state(db)
    state["last_lastfm_scrobble_time"] = int(datetime.utcnow().timestamp())
    _save_state(db, state)
    return {"ok": True, "message": f"Ingested {ingested} artist(s)", "ingested": ingested}


# --- Centroid similarity search --------------------------------------------------

async def _discovered_points(qdrant) -> list[dict]:
    """All is_discovered=True Qdrant points with vectors — shared by both
    discovery lanes so they always average over the exact same source set."""
    points: list[dict] = []
    offset = None
    pages = 0
    while pages < 10:
        batch, offset = await qdrant.scroll(
            filter={"must": [{"key": "is_discovered", "match": {"value": True}}]},
            limit=256, offset=offset, with_vector=True)
        points.extend(batch)
        pages += 1
        if offset is None:
            break
    return points


def _average_vector(vectors: list[list[float]]) -> list[float] | None:
    if not vectors:
        return None
    dim = len(vectors[0])
    return [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]


async def compute_taste_centroid(db) -> list[float] | None:
    """All-time taste centroid — average of the top-15 most-played (by
    total_plays_global) discovered artists. See compute_recent_taste_centroid()
    (AD-17) for the recently-listened counterpart lane."""
    qdrant = _qdrant(db)
    if not qdrant:
        return None
    points = await _discovered_points(qdrant)
    if not points:
        return None
    points.sort(key=lambda p: (p.get("payload") or {}).get("total_plays_global", 0), reverse=True)
    vectors = [p["vector"] for p in points[:15] if p.get("vector")]
    return _average_vector(vectors)


async def compute_recent_taste_centroid(db, lookback_days: int) -> list[float] | None:
    """AD-17 — second discovery lane seeded from artists actually listened to
    within the recent window, distinct from compute_taste_centroid()'s
    all-time most-played average. A single blended centroid tends to produce
    samey recommendations that drift toward old favorites; this lane lets a
    genuine recent shift in taste surface its own candidates. Reuses the same
    recently-listened key resolution AD-07 already established (mbid/name/
    normalized-name match against Last.fm's recent tracks), so "recent" means
    the same thing in both places. Returns None (fail-soft) when Last.fm
    isn't configured or nothing recently listened-to is in the taste space
    yet — callers should fall back to the all-time lane only."""
    qdrant = _qdrant(db)
    lastfm = _lastfm_client(db)
    if not qdrant or not lastfm:
        return None
    recent_keys = await _recently_listened_keys(lastfm, lookback_days)
    if not recent_keys:
        return None
    points = await _discovered_points(qdrant)
    recent_points = []
    for p in points:
        payload = p.get("payload") or {}
        mbid = payload.get("musicbrainz_id")
        name = (payload.get("artist_name") or "").strip()
        if (mbid and mbid in recent_keys) or (
                name and (name in recent_keys or _norm_artist(name) in recent_keys)):
            recent_points.append(p)
    vectors = [p["vector"] for p in recent_points if p.get("vector")]
    return _average_vector(vectors)


async def compute_mood_centroid(db, mood: str) -> list[float] | None:
    """AD-19 — a discovery lane sliced by one configured mood tag, distinct
    from both the all-time and recently-listened lanes. Averages only over
    discovered points whose SP-15 mood_tags contains this mood (case-
    insensitive) — fail-soft to None (caller skips the lane) whenever nothing
    in the taste space carries this mood tag yet."""
    qdrant = _qdrant(db)
    if not qdrant or not (mood or "").strip():
        return None
    target = mood.strip().lower()
    points = await _discovered_points(qdrant)
    mood_points = [
        p for p in points
        if target in {(m or "").strip().lower() for m in (p.get("payload") or {}).get("mood_tags") or []}
    ]
    vectors = [p["vector"] for p in mood_points if p.get("vector")]
    return _average_vector(vectors)


async def _run_centroid_lane(db, qdrant, centroid: list[float], cfg: ArtistDiscoverySettings,
                             source_label: str) -> int:
    """One discovery-lane search + candidate-creation pass. Shared by the
    all-time (AD original) and recently-listened (AD-17) lanes so both create
    candidates identically — only the seed centroid and the resulting
    `source` tag differ. Relies on SQLAlchemy's autoflush so a candidate the
    all-time lane just added (uncommitted) is still visible to the recent
    lane's _candidate_exists() check within the same run — no duplicate rows
    when both lanes surface the same artist."""
    hits = await qdrant.search(
        centroid, limit=cfg.max_candidates_per_run, score_threshold=cfg.similarity_threshold,
        must=[{"key": "is_discovered", "match": {"value": False}}],
        must_not=[{"key": "in_plex", "match": {"value": True}},
                  {"key": "in_lidarr", "match": {"value": True}}])
    created = 0
    for h in hits:
        payload = h.get("payload") or {}
        name = (payload.get("artist_name") or "").strip()
        if not name:
            continue
        mbid = payload.get("musicbrainz_id") or None
        if _candidate_exists(db, mbid, name):
            continue
        enrichment = await _enrich_candidate(db, mbid, name)
        genres_list = clean_tags(payload.get("genres")) or clean_tags(enrichment["genres"])
        db.add(DiscoveredArtist(
            musicbrainz_id=mbid, artist_name=name,
            genres=json.dumps(genres_list),
            mood_tags=json.dumps(clean_tags(payload.get("mood_tags"))),
            era=clean_era(payload.get("era")), source=source_label,
            similarity_score=h.get("score"), status="pending",
            image_url=enrichment["image_url"], bio=enrichment["bio"],
            years_active=enrichment["years_active"],
        ))
        created += 1
    return created


async def run_centroid_discovery(db, cfg: ArtistDiscoverySettings) -> dict[str, Any]:
    qdrant = _qdrant(db)
    if not qdrant:
        return {"ok": False, "message": "Qdrant not configured (Settings → Integrations)", "candidates": 0}

    centroid = await compute_taste_centroid(db)
    if not centroid:
        return {"ok": True, "message": "No taste centroid yet (no discovered artists)", "candidates": 0}

    created = await _run_centroid_lane(db, qdrant, centroid, cfg, "centroid")

    # AD-17 — second lane from recently-listened artists, distinct from the
    # all-time-most-played centroid above. Purely additive: fail-soft to
    # "nothing extra" whenever Last.fm isn't configured or nothing recent
    # is in the taste space yet, never blocks the all-time lane's results.
    recent_created = 0
    if cfg.recent_taste_lane_enabled:
        recent_centroid = await compute_recent_taste_centroid(db, cfg.scrobble_lookback_days)
        if recent_centroid:
            recent_created = await _run_centroid_lane(db, qdrant, recent_centroid, cfg, "centroid_recent")

    # AD-19 — one lane per user-configured mood tag (empty by default), each
    # sliced from the SP-15 mood_tags now populated on discovered points.
    # Purely additive like the recent-taste lane above: a mood with nothing
    # in the taste space yet just contributes 0 candidates this run.
    mood_created = 0
    mood_breakdown: list[str] = []
    for mood in cfg.mood_discovery_lanes or []:
        mood_centroid = await compute_mood_centroid(db, mood)
        if not mood_centroid:
            continue
        n = await _run_centroid_lane(db, qdrant, mood_centroid, cfg, f"centroid_mood_{_normalize_mood_key(mood)}")
        mood_created += n
        if n:
            mood_breakdown.append(f"{n} {mood}")

    db.commit()
    total = created + recent_created + mood_created
    message = f"{total} new candidate(s)"
    extras = []
    if recent_created:
        extras.append(f"{recent_created} recent-taste")
    extras.extend(mood_breakdown)
    if extras:
        message += f" ({created} all-time, {', '.join(extras)})"
    return {"ok": True, "message": message, "candidates": total}


# --- Related-artist graph sync ---------------------------------------------------

async def _recently_listened_keys(lastfm, lookback_days: int) -> set[str]:
    """AD-07 — set of seed keys (MBID and/or normalized name) heard within the
    scrobble lookback window. Connection counts for suggest/auto-add use only
    these keys — not every monitored Lidarr artist."""
    if lookback_days <= 0 or not lastfm:
        return set()
    from_ts = int((datetime.utcnow() - timedelta(days=lookback_days)).timestamp())
    keys: set[str] = set()
    try:
        tracks = await lastfm.get_recent_tracks(from_ts=from_ts, limit=200)
    except Exception as e:
        logger.warning(f"Artist Discovery: recent tracks fetch failed: {e}")
        return keys
    for t in tracks or []:
        artist = t.get("artist") or {}
        if isinstance(artist, dict):
            name = (artist.get("#text") or artist.get("name") or "").strip()
            mbid = (artist.get("mbid") or "").strip() or None
        else:
            name = str(artist).strip()
            mbid = None
        if mbid:
            keys.add(mbid)
        if name:
            keys.add(name)
            keys.add(_norm_artist(name))
    return keys


def _recent_connection_count(seeds_list: list[str], recent_keys: set[str]) -> int:
    """Count associated_seed_mbids entries that resolve to a recently-listened seed.
    Falls back to the full list length when recent_keys is empty (Last.fm unavailable)
    so graph sync still surfaces candidates rather than going silent."""
    if not seeds_list:
        return 0
    if not recent_keys:
        return len(seeds_list)
    n = 0
    for k in seeds_list:
        if not k:
            continue
        if k in recent_keys or _norm_artist(k) in recent_keys:
            n += 1
    return n


def _effective_auto_add_threshold(cfg: ArtistDiscoverySettings) -> int:
    """0 disables auto-add. Legacy auto_promote=True with threshold 0 → use suggest."""
    if cfg.auto_add_connection_threshold and cfg.auto_add_connection_threshold > 0:
        return cfg.auto_add_connection_threshold
    if cfg.auto_promote:
        return max(cfg.suggest_connection_threshold, 1)
    return 0


async def run_graph_sync(db, cfg: ArtistDiscoverySettings) -> dict[str, Any]:
    lastfm = _lastfm_client(db)
    if not lastfm:
        return {"ok": False, "message": "Last.fm integration not configured", "candidates": 0, "promoted": 0}
    qdrant = _qdrant(db)
    if not qdrant:
        return {"ok": False, "message": "Qdrant not configured (Settings → Integrations)", "candidates": 0, "promoted": 0}
    cutoff = int((datetime.utcnow() - timedelta(days=cfg.related_artists_refresh_days)).timestamp())
    seeds, _ = await qdrant.scroll(
        filter={"must": [{"key": "is_monitored_lidarr", "match": {"value": True}}]}, limit=256)
    stale = [s for s in seeds
             if ((s.get("payload") or {}).get("last_related_scan_timestamp") or 0) < cutoff][:5]

    recent_keys = await _recently_listened_keys(lastfm, cfg.scrobble_lookback_days)
    suggest_n = max(cfg.suggest_connection_threshold, 1)
    auto_add_n = _effective_auto_add_threshold(cfg)
    # Live ownership check (not the possibly-stale Qdrant flags — a related artist
    # can be seen here for the first time ever, with no prior point to have been
    # synced) — an artist already in Plex or in Lidarr (monitored or not) is never
    # worth suggesting, so it's excluded before any embedding/Qdrant work happens.
    lidarr_by_mbid, lidarr_by_name = await _lidarr_artist_index(db) or ({}, {})
    plex_names = _plex_artist_names(db)

    created = 0
    promoted = 0
    for seed in stale:
        payload = seed.get("payload") or {}
        seed_name = (payload.get("artist_name") or "").strip()
        seed_mbid = payload.get("musicbrainz_id") or None
        if not seed_name:
            continue
        try:
            related = await lastfm.get_similar_artists(seed_name, seed_mbid, limit=15)
        except Exception as e:
            logger.warning(f"Artist Discovery graph sync: Last.fm lookup failed for {seed_name}: {e}")
            related = []

        for r in related[:cfg.related_artists_limit]:
            rname = (r.get("name") or "").strip()
            if not rname:
                continue
            rmbid = (r.get("mbid") or "").strip() or None
            rname_norm = _norm_artist(rname)
            already_owned = bool(lidarr_by_mbid.get(rmbid) or lidarr_by_name.get(rname_norm)) \
                or rname_norm in plex_names
            if already_owned:
                continue
            rid = qdrant.point_id(rmbid, rname)
            try:
                existing = await qdrant.retrieve_points([rid])
            except Exception as e:
                logger.warning(f"Artist Discovery graph sync: Qdrant retrieve failed for {rname}: {e}")
                continue

            if existing:
                epayload = dict(existing[0].get("payload") or {})
                seeds_list = list(epayload.get("associated_seed_mbids") or [])
                seed_key = seed_mbid or seed_name
                if seed_key not in seeds_list:
                    seeds_list.append(seed_key)
                    epayload["associated_seed_mbids"] = seeds_list
                    try:
                        await qdrant.set_payload([rid], {"associated_seed_mbids": seeds_list})
                    except Exception as e:
                        logger.warning(f"Artist Discovery graph sync: Qdrant set_payload failed for {rname}: {e}")
            else:
                try:
                    tags = await lastfm.get_top_tags(rname, rmbid)
                except Exception:
                    tags = []
                vector = await _embed_artist(cfg, rname, tags)
                if not vector:
                    continue
                epayload = {
                    "musicbrainz_id": rmbid or "", "artist_name": rname, "genres": tags,
                    "mood_tags": classify_mood_tags(tags), "era": "", "is_monitored_lidarr": False,
                    "plex_fulfillment": "none", "in_lidarr": False, "in_plex": False,
                    "total_plays_global": 0,
                    "last_played_timestamp": 0, "is_discovered": False,
                    "associated_seed_mbids": [seed_mbid or seed_name],
                    "last_related_scan_timestamp": 0,
                }
                try:
                    await qdrant.upsert_points([{"id": rid, "vector": vector, "payload": epayload}])
                except Exception as e:
                    logger.warning(f"Artist Discovery graph sync: Qdrant upsert failed for {rname}: {e}")
                    continue

            seeds_list = list(epayload.get("associated_seed_mbids") or [])
            recent_n = _recent_connection_count(seeds_list, recent_keys)
            row = _find_candidate(db, rmbid, rname)
            if row is not None:
                # New connections keep accumulating after the row was created —
                # keep a pending row's seed list (and its resolved names) current
                # so the card can show every contributing artist (AD-05).
                if row.status == "pending":
                    stored = set(json.loads(row.associated_seed_mbids)) if row.associated_seed_mbids else set()
                    if set(seeds_list) != stored:
                        row.associated_seed_mbids = json.dumps(seeds_list)
                        row.seed_artist_names = json.dumps(await _resolve_seed_names(qdrant, seeds_list))
                    # AD-07 — pending row crossed auto-add band → promote in place
                    if auto_add_n and recent_n >= auto_add_n:
                        result = await add_to_lidarr(db, row.id)
                        if result.get("ok"):
                            promoted += 1
                continue  # any prior row (any status) blocks re-creation
            # already_owned was checked before any point/candidate work began above,
            # so no is_monitored_lidarr re-check is needed here — just the threshold.
            if recent_n < suggest_n:
                continue
            # AD-07 auto-add band: add to Lidarr, no suggested-queue row
            if auto_add_n and recent_n >= auto_add_n:
                enrichment = await _enrich_candidate(db, rmbid, rname)
                genres_list = clean_tags(epayload.get("genres")) or clean_tags(enrichment["genres"])
                cand = DiscoveredArtist(
                    musicbrainz_id=rmbid, artist_name=rname,
                    genres=json.dumps(genres_list),
                    mood_tags=json.dumps(clean_tags(epayload.get("mood_tags"))),
                    era=clean_era(epayload.get("era")), source="graph",
                    associated_seed_mbids=json.dumps(seeds_list),
                    seed_artist_name=seed_name,
                    seed_artist_names=json.dumps(await _resolve_seed_names(qdrant, seeds_list)),
                    status="pending",
                    image_url=enrichment["image_url"], bio=enrichment["bio"],
                    years_active=enrichment["years_active"],
                )
                db.add(cand)
                db.flush()
                result = await add_to_lidarr(db, cand.id)
                if result.get("ok"):
                    promoted += 1
                else:
                    created += 1  # left pending if Lidarr add failed
                continue
            # Suggest band: show in review queue
            enrichment = await _enrich_candidate(db, rmbid, rname)
            genres_list = clean_tags(epayload.get("genres")) or clean_tags(enrichment["genres"])
            cand = DiscoveredArtist(
                musicbrainz_id=rmbid, artist_name=rname,
                genres=json.dumps(genres_list),
                mood_tags=json.dumps(clean_tags(epayload.get("mood_tags"))),
                era=clean_era(epayload.get("era")), source="graph",
                associated_seed_mbids=json.dumps(seeds_list),
                seed_artist_name=seed_name,
                seed_artist_names=json.dumps(await _resolve_seed_names(qdrant, seeds_list)),
                status="pending",
                image_url=enrichment["image_url"], bio=enrichment["bio"],
                years_active=enrichment["years_active"],
            )
            db.add(cand)
            created += 1

        try:
            await qdrant.set_payload([seed["id"]], {"last_related_scan_timestamp": int(datetime.utcnow().timestamp())})
        except Exception as e:
            logger.warning(f"Artist Discovery graph sync: seed timestamp update failed for {seed_name}: {e}")

    db.commit()
    return {"ok": True, "message": f"{created} new candidate(s), {promoted} auto-promoted",
            "candidates": created, "promoted": promoted}


# --- AD-08 thumbnail retention ---------------------------------------------------

def purge_stale_thumbnails(db, retention_days: int | None = None) -> int:
    """Clear image_url on accepted artists older than retention_days so the DB
    does not retain enrichment art indefinitely (AD-08). Bio/years kept for digests."""
    cfg = load_settings(db)
    days = retention_days if retention_days is not None else cfg.thumbnail_retention_days
    if days <= 0:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = (db.query(DiscoveredArtist)
            .filter(DiscoveredArtist.status == "accepted",
                    DiscoveredArtist.image_url.isnot(None),
                    DiscoveredArtist.resolved_at.isnot(None),
                    DiscoveredArtist.resolved_at < cutoff)
            .all())
    for row in rows:
        row.image_url = None
    if rows:
        db.commit()
        logger.info(f"Artist Discovery: purged thumbnails on {len(rows)} accepted artist(s) older than {days}d")
    return len(rows)


# --- Re-enrichment backfill --------------------------------------------------------

async def re_enrich_missing(db, limit: int = 25) -> dict[str, Any]:
    """Backfill display fields on pending candidates still missing an image, bio,
    years-active, or (graph rows) resolved seed names — rows created before the
    wikidata/Deezer fallbacks existed, or when an upstream source was down at
    creation time (AD-04/AD-05). Bounded per pass; MusicBrainz stays rate-limited."""
    rows = (db.query(DiscoveredArtist).filter_by(status="pending")
            .filter((DiscoveredArtist.image_url.is_(None))
                    | (DiscoveredArtist.bio.is_(None))
                    | (DiscoveredArtist.years_active.is_(None))
                    | ((DiscoveredArtist.associated_seed_mbids.isnot(None))
                       & (DiscoveredArtist.seed_artist_names.is_(None))))
            .order_by(DiscoveredArtist.created_at.desc()).limit(limit).all())
    qdrant = _qdrant(db)
    updated = 0
    for row in rows:
        changed = False
        if not row.image_url or not row.bio or not row.years_active:
            enrichment = await _enrich_candidate(db, row.musicbrainz_id, row.artist_name)
            if not row.image_url and enrichment["image_url"]:
                row.image_url = enrichment["image_url"]
                changed = True
            if not row.bio and enrichment["bio"]:
                row.bio = enrichment["bio"]
                changed = True
            if not row.years_active and enrichment["years_active"]:
                row.years_active = enrichment["years_active"]
                changed = True
            if (not row.genres or row.genres == "[]") and clean_tags(enrichment["genres"]):
                row.genres = json.dumps(clean_tags(enrichment["genres"]))
                changed = True
        if row.associated_seed_mbids and not row.seed_artist_names:
            names = await _resolve_seed_names(qdrant, json.loads(row.associated_seed_mbids))
            if names:
                row.seed_artist_names = json.dumps(names)
                changed = True
        if changed:
            updated += 1
    db.commit()
    return {"ok": True, "message": f"Re-enriched {updated} of {len(rows)} candidate(s)",
            "updated": updated, "checked": len(rows)}


# --- Orchestration -----------------------------------------------------------------

async def run_discovery(db=None) -> dict[str, Any]:
    """AD-02: tray-tracked wrapper for a user-triggered "Run Discovery Now" —
    mirrors import_matcher.scan_once()'s thin-wrapper-around-the-real-worker
    shape, so the tracked task always gets marked done/failed (including on an
    exception run_full_discovery_cycle doesn't itself catch) without adding a
    second nested try/finally to that already-long function. The scheduler's
    own background cycle calls run_full_discovery_cycle() directly and stays
    silent — the tray is for user-triggered operations only, per tasks.py."""
    from app.services import tasks
    task_id = tasks.create_task("artist_discovery", "Running artist discovery")
    try:
        result = await run_full_discovery_cycle(db, task_id=task_id)
        tasks.finish_task(task_id, "done" if result.get("ok") else "failed", result.get("message"))
        return result
    except Exception as e:
        tasks.finish_task(task_id, "failed", str(e))
        raise


async def run_full_discovery_cycle(db=None, task_id: str | None = None) -> dict[str, Any]:
    """AD-02: `task_id` is optional so existing callers/tests are unaffected —
    when supplied (by run_discovery()'s tray wrapper), each phase reports its
    progress via tasks.update_task(). No natural item count exists up front for
    this pipeline, so the tray renders it indeterminate, same as an llm_run."""
    from app.services import tasks
    own_session = db is None
    if own_session:
        db = SessionLocal()
    run = ArtistDiscoveryRun(run_type="full", started_at=datetime.utcnow())
    db.add(run)
    db.flush()
    try:
        cfg = load_settings(db)
        if not cfg.enabled or not _qdrant(db):
            run.message = "Artist Discovery disabled or Qdrant not configured"
            run.finished_at = datetime.utcnow()
            db.commit()
            return {"ok": False, "message": run.message}
        # Freshen Qdrant before the centroid/graph steps: pull current Lidarr
        # monitored/fulfillment state and Last.fm play counts into every point,
        # so this run's taste centroid and connection counts reflect recent play
        # history rather than the last standalone sync. Fail-soft — a sync
        # failure (e.g. Lidarr down) still lets discovery run on existing state.
        tasks.update_task(task_id, message="Syncing Lidarr/Last.fm state into Qdrant…")
        sync = await run_differential_sync(db)
        if sync.get("ok"):
            sync_row = db.query(AppSetting).filter_by(key="last_artist_discovery_sync").first()
            if not sync_row:
                sync_row = AppSetting(key="last_artist_discovery_sync")
                db.add(sync_row)
            sync_row.value = datetime.utcnow().isoformat()
            db.commit()
        else:
            logger.warning(f"Artist Discovery: pre-run differential sync failed "
                           f"({sync.get('message')}) — continuing with existing Qdrant state")
        tasks.update_task(task_id, message="Ingesting Last.fm scrobble history…")
        ingest = await ingest_scrobbles(db, cfg)
        # Commit between phases rather than only once at the very end — each phase
        # below makes its own sequence of external Last.fm/Ollama/Qdrant/Lidarr calls
        # (ingest_scrobbles alone can loop over dozens of artists with zero commits of
        # its own), and holding the session's transaction open across all four phases
        # is the same held-transaction anti-pattern already fixed in the sync phase
        # above and in generate_candidates() (v0.74.1) — confirmed live (idle-in-
        # transaction sessions on "SELECT app_settings…", health endpoint timing out).
        db.commit()
        tasks.update_task(task_id, message="Running taste-centroid discovery…")
        centroid = await run_centroid_discovery(db, cfg)
        db.commit()
        tasks.update_task(task_id, message="Expanding related-artist graph…")
        graph = await run_graph_sync(db, cfg)
        db.commit()
        tasks.update_task(task_id, message="Enriching candidate images/bios…")
        await re_enrich_missing(db)
        db.commit()
        found = centroid.get("candidates", 0) + graph.get("candidates", 0)
        added = graph.get("promoted", 0)
        run.candidates_found = found
        run.candidates_added = added
        run.finished_at = datetime.utcnow()
        run.message = (f"Synced {sync.get('updated', 0)} point(s), ingested {ingest.get('ingested', 0)}, "
                       f"{found} new candidate(s), {added} auto-promoted")
        db.commit()
        return {"ok": True, "message": run.message, "sync": sync, "ingest": ingest,
                "centroid": centroid, "graph": graph}
    except Exception as e:
        logger.error(f"Artist Discovery full cycle failed: {e}", exc_info=True)
        run.message = f"Error: {e}"
        run.finished_at = datetime.utcnow()
        db.commit()
        return {"ok": False, "message": str(e)}
    finally:
        if own_session:
            db.close()


async def run_differential_sync(db=None) -> dict[str, Any]:
    """Sync Lidarr monitored/fulfillment state + Last.fm play counts back into every
    Qdrant point. Never deletes points — soft-delete semantics (n8n's rule)."""
    own_session = db is None
    if own_session:
        db = SessionLocal()
    run = ArtistDiscoveryRun(run_type="sync", started_at=datetime.utcnow())
    db.add(run)
    db.flush()
    try:
        cfg = load_settings(db)
        qdrant = _qdrant(db)
        if not cfg.enabled or not qdrant:
            run.message = "Artist Discovery disabled or Qdrant not configured"
            run.finished_at = datetime.utcnow()
            db.commit()
            return {"ok": False, "message": run.message}
        lidarr_index = await _lidarr_artist_index(db)
        if lidarr_index is None:
            run.message = "Lidarr integration not enabled"
            run.finished_at = datetime.utcnow()
            db.commit()
            return {"ok": False, "message": run.message}
        by_mbid, by_name = lidarr_index
        plex_names = _plex_artist_names(db)

        lastfm = _lastfm_client(db)
        play_counts: dict[str, int] = {}
        if lastfm:
            try:
                top = await lastfm.get_top_artists(limit=500)
                play_counts = {_norm_artist(a.get("name") or ""): int(a.get("playcount") or 0) for a in top}
            except Exception as e:
                logger.warning(f"Artist Discovery sync: Last.fm top artists fetch failed: {e}")

        # SP-15 — mood_tags is a pure reclassification of the genres already on
        # the point (no I/O), so it's cheap to keep fresh on every point every
        # cycle. era needs a MusicBrainz life-span lookup, which is
        # rate-limited (~1 req/sec) — bounded per run like re_enrich_missing's
        # own backfill, so a large library just takes a few cycles rather than
        # one run holding this sync open for a very long time.
        from app.integrations import musicbrainz
        _ERA_BACKFILL_CAP = 20
        era_backfilled = 0

        updated = 0
        unchanged = 0
        offset = None
        pages = 0
        while pages < 80:
            points, offset = await qdrant.scroll(limit=256, offset=offset)
            pages += 1
            for p in points:
                payload = p.get("payload") or {}
                mbid = payload.get("musicbrainz_id") or None
                name_key = _norm_artist(payload.get("artist_name") or "")
                lidarr_artist = by_mbid.get(mbid) or by_name.get(name_key)
                is_monitored = bool(lidarr_artist and lidarr_artist.get("monitored"))
                fulfillment = "none"
                if lidarr_artist:
                    stats = lidarr_artist.get("statistics") or {}
                    pct = stats.get("percentOfTracks") or 0
                    fulfillment = "complete" if pct >= 100 else ("partial" if pct > 0 else "none")
                updates: dict[str, Any] = {
                    "is_monitored_lidarr": is_monitored, "plex_fulfillment": fulfillment,
                    "in_lidarr": bool(lidarr_artist), "in_plex": name_key in plex_names,
                    "mood_tags": classify_mood_tags(payload.get("genres")),
                }
                plays = play_counts.get(name_key)
                if plays is not None:
                    updates["total_plays_global"] = max(payload.get("total_plays_global") or 0, plays)
                if not payload.get("era") and mbid and era_backfilled < _ERA_BACKFILL_CAP:
                    era_backfilled += 1
                    try:
                        mb = await musicbrainz.get_artist(mbid)
                        decade = musicbrainz.era_decade(mb) if mb else None
                        if decade:
                            updates["era"] = decade
                    except Exception as e:
                        logger.warning(f"Artist Discovery sync: MusicBrainz era lookup failed for {mbid}: {e}")
                if all(payload.get(k) == v for k, v in updates.items()):
                    unchanged += 1  # every field already matches the point — skip the write
                    continue
                try:
                    await qdrant.set_payload([p["id"]], updates)
                    updated += 1
                except Exception as e:
                    logger.warning(f"Artist Discovery sync: set_payload failed for point {p.get('id')}: {e}")
            # Commit after each page rather than once at the very end — this loop can
            # make many rate-limited MusicBrainz calls (era backfill) and Qdrant calls
            # per page, and holding the `run` row's transaction open across all of them
            # is the exact same anti-pattern fixed in generate_candidates() (v0.74.1
            # live incident): a long-lived idle-in-transaction session starves the pool
            # and can make the health check's own SELECT 1 start timing out.
            db.commit()
            if offset is None:
                break

        run.finished_at = datetime.utcnow()
        run.message = f"Synced {updated} artist point(s)" + (
            f" ({unchanged} unchanged, skipped)" if unchanged else "")
        db.commit()
        return {"ok": True, "message": run.message, "updated": updated, "unchanged": unchanged}
    except Exception as e:
        logger.error(f"Artist Discovery differential sync failed: {e}", exc_info=True)
        run.message = f"Error: {e}"
        run.finished_at = datetime.utcnow()
        db.commit()
        return {"ok": False, "message": str(e)}
    finally:
        if own_session:
            db.close()


# --- Review queue actions --------------------------------------------------------

async def _add_artist_to_lidarr(lidarr, cfg: ArtistDiscoverySettings,
                                mbid: str | None, name: str) -> dict[str, Any]:
    """Core Lidarr lookup -> profile resolution -> add. Shared by the Discovery
    accept flow (add_to_lidarr, below) and the standalone Related Artists
    search's add action — neither DiscoveredArtist nor Qdrant bookkeeping
    happens here, callers own that around this call. A 400 from Lidarr is
    treated as "already there, not a failure" (matches the pre-extraction
    behavior) — it looks up the existing artist's id instead of erroring."""
    term = f"lidarr:{mbid}" if mbid else name
    try:
        results = await lidarr.lookup_artist(term)
    except Exception as e:
        return {"ok": False, "message": f"Lidarr lookup failed: {e}"}
    if not results:
        return {"ok": False, "message": "No Lidarr lookup results"}

    match = None
    if mbid:
        match = next((r for r in results if r.get("foreignArtistId") == mbid), None)
    if not match:
        target = _norm_artist(name)
        match = next((r for r in results if _norm_artist(r.get("artistName") or "") == target), None)
    if not match:
        match = results[0]

    root_folder = cfg.root_folder_path or None
    quality_profile_id = cfg.quality_profile_id or None
    metadata_profile_id = cfg.metadata_profile_id or None
    if not (root_folder and quality_profile_id and metadata_profile_id):
        try:
            folders = [] if root_folder else await lidarr.get_root_folders()
            qps = [] if quality_profile_id else await lidarr.get_quality_profiles()
            mps = [] if metadata_profile_id else await lidarr.get_metadata_profiles()
        except Exception as e:
            return {"ok": False, "message": f"Lidarr profile lookup failed: {e}"}
        root_folder = root_folder or (folders[0]["path"] if folders else None)
        quality_profile_id = quality_profile_id or (qps[0]["id"] if qps else None)
        metadata_profile_id = metadata_profile_id or (mps[0]["id"] if mps else None)
    if not (root_folder and quality_profile_id and metadata_profile_id):
        return {"ok": False, "message": "No Lidarr root folder / quality profile / metadata profile available"}

    match["rootFolderPath"] = root_folder
    match["qualityProfileId"] = quality_profile_id
    match["metadataProfileId"] = metadata_profile_id
    match["monitored"] = True
    match["addOptions"] = {"searchForMissingAlbums": True}

    try:
        added = await lidarr.add_artist(match)
        return {"ok": True, "message": f"Added '{name}' to Lidarr",
                "lidarr_artist_id": added.get("id"), "already_existed": False}
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code == 400:
            lidarr_artist_id = None
            try:
                existing = await lidarr.get_artists()
                found = next((a for a in existing
                             if a.get("foreignArtistId") == mbid
                             or _norm_artist(a.get("artistName") or "") == _norm_artist(name)), None)
                lidarr_artist_id = found.get("id") if found else None
            except Exception:
                lidarr_artist_id = None
            return {"ok": True, "message": f"Added '{name}' to Lidarr",
                    "lidarr_artist_id": lidarr_artist_id, "already_existed": True}
        return {"ok": False, "message": f"Lidarr add failed: {e}"}
    except Exception as e:
        return {"ok": False, "message": f"Lidarr add failed: {e}"}


async def add_to_lidarr(db, candidate_id: int) -> dict[str, Any]:
    cand = db.query(DiscoveredArtist).filter_by(id=candidate_id).first()
    if not cand:
        return {"ok": False, "message": "Candidate not found"}
    if cand.status == "accepted":
        return {"ok": True, "message": "Already accepted", "lidarr_artist_id": cand.lidarr_artist_id}

    lidarr_row = db.query(Integration).filter_by(name="lidarr", enabled=True).first()
    if not lidarr_row:
        return {"ok": False, "message": "Lidarr integration not enabled"}
    from app.api.v1.integrations import _get_client
    lidarr = _get_client(lidarr_row)
    cfg = load_settings(db)

    result = await _add_artist_to_lidarr(lidarr, cfg, cand.musicbrainz_id, cand.artist_name)
    if not result.get("ok"):
        return result
    lidarr_artist_id = result.get("lidarr_artist_id")

    qdrant = _qdrant(db)
    if qdrant:
        pid = qdrant.point_id(cand.musicbrainz_id, cand.artist_name)
        try:
            await qdrant.set_payload([pid], {"is_monitored_lidarr": True, "in_lidarr": True})
        except Exception as e:
            logger.warning(f"Artist Discovery: Qdrant flag update failed for {cand.artist_name}: {e}")

    cand.status = "accepted"
    cand.lidarr_artist_id = lidarr_artist_id
    cand.resolved_at = datetime.utcnow()
    if not result.get("already_existed"):
        db.add(ArtistAddLog(artist_name=cand.artist_name, musicbrainz_id=cand.musicbrainz_id,
                            source="discovery", lidarr_artist_id=lidarr_artist_id))
    db.commit()
    return {"ok": True, "message": f"Added '{cand.artist_name}' to Lidarr", "lidarr_artist_id": lidarr_artist_id}


def reject_candidate(db, candidate_id: int) -> dict[str, Any]:
    cand = db.query(DiscoveredArtist).filter_by(id=candidate_id).first()
    if not cand:
        return {"ok": False, "message": "Candidate not found"}
    cand.status = "rejected"
    cand.resolved_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "message": "Rejected"}


# --- Related Artists search (ad-hoc, read-only) ------------------------------------
# On-demand "who's similar to X" for any artist, not just your own monitored
# ones. Deliberately independent of the taste-model pipeline above: no Qdrant
# writes, no DiscoveredArtist rows. Reuses the same Last.fm similar-artist call
# and enrichment helper the discovery pipeline uses, so results read the same
# as Discovery candidates; adding one goes straight to Lidarr via
# _add_artist_to_lidarr, bypassing the review queue entirely.

# Last.fm has served this same star graphic instead of real artist photos for
# years — treat it as no image so local/enrichment sources can fill in (AD-21).
_LASTFM_PLACEHOLDER = "2a96cbd8b46e442fc41c2b86b821562f"


def _lastfm_image(images: list[dict] | None) -> str | None:
    by_size = {i.get("size"): (i.get("#text") or "").strip() for i in (images or [])}
    for size in ("large", "medium", "extralarge", "small"):
        url = by_size.get(size)
        if url and _LASTFM_PLACEHOLDER not in url:
            return url
    return None


async def search_artist_names(db, query: str, limit: int = 8) -> dict[str, Any]:
    """Name-completion typeahead for the Related Artists search box — distinct
    from search_related_artists() (which finds artists similar TO a seed).
    Deliberately lightweight for as-you-type use: Last.fm's artist.search
    already returns a thumbnail with no extra call, and genres come from one
    parallel get_top_tags() call per result (not the full Lidarr/MusicBrainz/
    Wikipedia/Deezer enrich() chain used elsewhere — far too slow per
    keystroke). No bio — not useful at this size and not worth the latency."""
    query = (query or "").strip()
    if not query:
        return {"ok": True, "results": []}
    lastfm = _lastfm_client(db)
    if not lastfm:
        return {"ok": False, "message": "Last.fm integration not configured", "results": []}
    try:
        matches = await lastfm.search_artists(query, limit=limit)
    except Exception as e:
        return {"ok": False, "message": f"Last.fm search failed: {e}", "results": []}

    async def _with_tags(m: dict) -> dict:
        name = (m.get("name") or "").strip()
        mbid = (m.get("mbid") or "").strip() or None
        try:
            tags = await lastfm.get_top_tags(name, mbid)
        except Exception:
            tags = []
        return {
            "artist_name": name,
            "musicbrainz_id": mbid,
            "image_url": _lastfm_image(m.get("image")),
            "genres": clean_tags(tags)[:5],
        }

    results = await asyncio.gather(*(_with_tags(m) for m in matches if (m.get("name") or "").strip()))
    results = [r for r in results if r["artist_name"]]
    # AD-21 — Last.fm's search images are blank/placeholder for most artists;
    # overlay the locally-kept library-artist thumbnails wherever they match.
    try:
        from app.services.artist_thumbnails import thumbnails_for
        local = thumbnails_for(db, [r["artist_name"] for r in results])
        for r in results:
            url = local.get(_norm_artist(r["artist_name"]))
            if url:
                r["image_url"] = url
    except Exception as e:
        logger.debug(f"Artist Discovery: thumbnail overlay failed: {e}")
    return {"ok": True, "results": results}


async def search_related_artists_tracked(db, artist: str, limit: int = 50) -> dict[str, Any]:
    """Tray-tracked wrapper for a user-triggered Related Artists search — mirrors
    run_discovery()'s thin-wrapper shape. Enrichment (one Lidarr/MusicBrainz/
    Wikipedia/Deezer round-trip per candidate) is the slow part of a search —
    users reported no feedback that it was working, since the page had nothing
    beyond a static "Searching…" button label. The tray card gives a
    determinate current/total bar once the Last.fm candidate count is known."""
    from app.services import tasks
    task_id = tasks.create_task("related_search", f"Searching for “{artist.strip()}”")
    try:
        result = await search_related_artists(db, artist, limit=limit, task_id=task_id)
        tasks.finish_task(task_id, "done" if result.get("ok") else "failed", result.get("message"))
        return result
    except Exception as e:
        tasks.finish_task(task_id, "failed", str(e))
        raise


async def search_related_artists(db, artist: str, limit: int = 50,
                                  task_id: str | None = None) -> dict[str, Any]:
    """task_id is optional so existing callers/tests are unaffected — when
    supplied (by search_related_artists_tracked()), progress reports to the
    Active Processes tray via tasks.update_task()."""
    from app.services import tasks
    artist = (artist or "").strip()
    if not artist:
        return {"ok": False, "message": "Enter an artist name", "results": []}
    lastfm = _lastfm_client(db)
    if not lastfm:
        return {"ok": False, "message": "Last.fm integration not configured", "results": []}

    tasks.update_task(task_id, message=f"Searching Last.fm for artists similar to '{artist}'…")
    limit = max(1, min(int(limit or 50), 200))
    try:
        related = await lastfm.get_similar_artists(artist, limit=limit)
    except Exception as e:
        return {"ok": False, "message": f"Last.fm lookup failed: {e}", "results": []}
    if not related:
        return {"ok": False,
                "message": f"No related artists found for '{artist}' — check the spelling or try a different name.",
                "results": []}

    lidarr_by_mbid, lidarr_by_name = await _lidarr_artist_index(db) or ({}, {})
    plex_names = _plex_artist_names(db)

    tasks.update_task(task_id, current=0, total=len(related), message=f"Enriching {len(related)} candidate(s)…")
    results = []
    for i, r in enumerate(related):
        rname = (r.get("name") or "").strip()
        if not rname:
            tasks.update_task(task_id, current=i + 1)
            continue
        rmbid = (r.get("mbid") or "").strip() or None
        rname_norm = _norm_artist(rname)
        already_owned = bool(lidarr_by_mbid.get(rmbid) or lidarr_by_name.get(rname_norm)) \
            or rname_norm in plex_names
        try:
            match_score = float(r.get("match") or 0.0)
        except (TypeError, ValueError):
            match_score = 0.0
        enrichment = await _enrich_candidate(db, rmbid, rname)
        results.append({
            "musicbrainz_id": rmbid, "artist_name": rname, "match_score": match_score,
            "already_owned": already_owned,
            "image_url": enrichment.get("image_url"), "bio": enrichment.get("bio"),
            "genres": clean_tags(enrichment.get("genres")), "years_active": enrichment.get("years_active"),
            "similarity_sources": ["lastfm"],
        })
        tasks.update_task(task_id, current=i + 1, message=f"Enriched '{rname}'")

    tasks.update_task(task_id, message="Checking Plex for additional matches…")
    await _augment_with_plex_similarity(db, artist, results, lidarr_by_name, plex_names)
    return {"ok": True, "message": f"{len(results)} related artist(s) for '{artist}'", "results": results}


async def _augment_with_plex_similarity(db, seed_artist: str, results: list[dict],
                                         lidarr_by_name: dict, plex_names: set[str]) -> None:
    """AD-14: best-effort second/third similarity source on top of Last.fm —
    Plex's own Sonic Analysis and metadata-based "Related" recommendations for
    the seed artist, badged onto matching results (or added as new plex-only
    entries when Plex surfaces a name Last.fm didn't). Only contributes anything
    when the seed artist is actually found in the user's Plex library — Related
    Artists is meant to work for artists you don't own, so a miss here just
    means no Plex badges, never an error surfaced to the user. The two Plex
    integration calls are unverified against a live server as of v0.49.0 (see
    integrations/plex.py) — if the assumed response shape is wrong they simply
    return [] and this function is a no-op."""
    plex = _plex_client(db)
    if not plex:
        return
    try:
        seed = await plex.find_artist(seed_artist)
    except Exception:
        seed = None
    if not seed:
        return

    try:
        sonic_names = await plex.sonically_similar_artists(seed["ratingKey"])
    except Exception:
        sonic_names = []
    try:
        related_names = await plex.related_artists(seed["ratingKey"])
    except Exception:
        related_names = []
    if not sonic_names and not related_names:
        return

    seed_norm = _norm_artist(seed_artist)
    by_name_norm = {_norm_artist(r["artist_name"]): r for r in results}
    for source_key, names in (("plex_sonic", sonic_names), ("plex_similar", related_names)):
        for pname in names:
            pname = (pname or "").strip()
            if not pname:
                continue
            norm = _norm_artist(pname)
            if norm == seed_norm:
                continue  # Plex sometimes includes the seed itself in these lists
            existing = by_name_norm.get(norm)
            if existing:
                if source_key not in existing["similarity_sources"]:
                    existing["similarity_sources"].append(source_key)
                continue
            already_owned = bool(lidarr_by_name.get(norm)) or norm in plex_names
            enrichment = await _enrich_candidate(db, None, pname)
            new_entry = {
                "musicbrainz_id": None, "artist_name": pname, "match_score": 0.0,
                "already_owned": already_owned,
                "image_url": enrichment.get("image_url"), "bio": enrichment.get("bio"),
                "genres": clean_tags(enrichment.get("genres")), "years_active": enrichment.get("years_active"),
                "similarity_sources": [source_key],
            }
            results.append(new_entry)
            by_name_norm[norm] = new_entry


async def add_related_artist(db, mbid: str | None, name: str) -> dict[str, Any]:
    """Add a Related Artists search result straight to Lidarr — no queue row."""
    name = (name or "").strip()
    if not name:
        return {"ok": False, "message": "Artist name required"}
    lidarr_row = db.query(Integration).filter_by(name="lidarr", enabled=True).first()
    if not lidarr_row:
        return {"ok": False, "message": "Lidarr integration not enabled"}
    from app.api.v1.integrations import _get_client
    lidarr = _get_client(lidarr_row)
    cfg = load_settings(db)
    result = await _add_artist_to_lidarr(lidarr, cfg, mbid, name)
    if result.get("ok") and not result.get("already_existed"):
        db.add(ArtistAddLog(artist_name=name, musicbrainz_id=mbid, source="related",
                            lidarr_artist_id=result.get("lidarr_artist_id")))
        db.commit()
    return result


# --- Misc -------------------------------------------------------------------------

def list_recent_adds(db, limit: int = 10) -> list[dict[str, Any]]:
    """AD-22 — the last N artists actually added to Lidarr (ArtistAddLog rows,
    written only for genuine adds — Lidarr's "already existed" 400 never logs),
    enriched for display: a discovery add pulls its DiscoveredArtist row's
    why-suggested fields/image/bio; a Related Artists add has no candidate row,
    so its reasoning is just its source. Any still-missing image falls back to
    the AD-21 library thumbnail cache — these artists are in the library now,
    so the daily refresh covers them even after AD-08 purges the accepted
    candidate's enrichment image. Read-only, local tables only."""
    from app.services.artist_thumbnails import thumbnails_for
    logs = (db.query(ArtistAddLog)
            .order_by(ArtistAddLog.added_at.desc(), ArtistAddLog.id.desc())
            .limit(limit).all())
    if not logs:
        return []
    mbids = [log.musicbrainz_id for log in logs if log.musicbrainz_id]
    names = [log.artist_name for log in logs]
    cands = (db.query(DiscoveredArtist)
             .filter(DiscoveredArtist.musicbrainz_id.in_(mbids)).all()) if mbids else []
    by_mbid = {c.musicbrainz_id: c for c in cands}
    # Name fallback for log rows without an mbid (or whose candidate predates it)
    remaining = [n for n in names]
    name_cands = (db.query(DiscoveredArtist)
                  .filter(DiscoveredArtist.artist_name.in_(remaining)).all()) if remaining else []
    by_name = {c.artist_name.lower(): c for c in name_cands}
    thumbs = thumbnails_for(db, names)
    out: list[dict[str, Any]] = []
    for log in logs:
        cand = by_mbid.get(log.musicbrainz_id) if log.musicbrainz_id else None
        if cand is None:
            cand = by_name.get(log.artist_name.lower())
        image = (cand.image_url if cand else None) or thumbs.get(_norm_artist(log.artist_name))
        out.append({
            "id": log.id,
            "artist_name": log.artist_name,
            "musicbrainz_id": log.musicbrainz_id,
            "source": log.source,
            "lidarr_artist_id": log.lidarr_artist_id,
            "added_at": log.added_at,
            "discovery_source": cand.source if cand else None,
            "similarity_score": cand.similarity_score if cand else None,
            "seed_artist_name": cand.seed_artist_name if cand else None,
            "seed_artist_names": json.loads(cand.seed_artist_names) if cand and cand.seed_artist_names else [],
            "associated_seed_mbids": json.loads(cand.associated_seed_mbids) if cand and cand.associated_seed_mbids else [],
            "genres": clean_tags(json.loads(cand.genres) if cand and cand.genres else []),
            "years_active": cand.years_active if cand else None,
            "image_url": image,
            "bio": cand.bio if cand else None,
        })
    return out


async def get_lidarr_profiles(db) -> dict[str, Any]:
    lidarr_row = db.query(Integration).filter_by(name="lidarr", enabled=True).first()
    if not lidarr_row:
        return {"root_folders": [], "quality_profiles": [], "metadata_profiles": []}
    from app.api.v1.integrations import _get_client
    lidarr = _get_client(lidarr_row)
    folders = await lidarr.get_root_folders()
    qps = await lidarr.get_quality_profiles()
    mps = await lidarr.get_metadata_profiles()
    return {
        "root_folders": [{"path": f.get("path")} for f in folders],
        "quality_profiles": [{"id": q.get("id"), "name": q.get("name")} for q in qps],
        "metadata_profiles": [{"id": m.get("id"), "name": m.get("name")} for m in mps],
    }


async def get_stats(db) -> dict[str, Any]:
    cfg = load_settings(db)
    pending = db.query(DiscoveredArtist).filter_by(status="pending").count()
    accepted = db.query(DiscoveredArtist).filter_by(status="accepted").count()
    rejected = db.query(DiscoveredArtist).filter_by(status="rejected").count()
    tracked = None
    qdrant = _qdrant(db)
    if cfg.enabled and qdrant:
        try:
            info = await qdrant.get_collection_info()
            tracked = (info or {}).get("points_count")
        except Exception:
            tracked = None
    last_run = db.query(ArtistDiscoveryRun).order_by(ArtistDiscoveryRun.started_at.desc()).first()
    return {
        "pending": pending, "accepted": accepted, "rejected": rejected,
        "tracked_artists": tracked,
        "last_run_at": last_run.started_at.isoformat() if last_run else None,
        "last_run_message": last_run.message if last_run else None,
    }
