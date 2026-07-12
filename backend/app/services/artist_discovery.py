"""Artist Discovery — native port of the n8n Music Curator (Last.fm scrobbles →
Ollama embeddings → Qdrant taste-centroid similarity + related-artist graph → Lidarr).

Writes to the same `music_affinity_space` Qdrant collection Smart Playlists reads —
soft-delete semantics throughout (never delete a point, only flip flags), matching
the n8n curator's rule. See vault [[Artist Discovery]].

Plain SessionLocal() per function (no FastAPI Depends) so these are callable from
both API routes and the scheduler, mirroring playlist_generator.py.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

import httpx

from app.database import SessionLocal
from app.models.app_setting import AppSetting
from app.models.artist_discovery import ArtistDiscoveryRun, DiscoveredArtist
from app.models.integration import Integration
from app.models.media import MediaItem
from app.schemas.settings import ArtistDiscoverySettings

logger = logging.getLogger("powarr")


def _norm_artist(name: str) -> str:
    t = (name or "").lower().strip()
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


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
            "mood_tags": [],
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

async def compute_taste_centroid(db) -> list[float] | None:
    qdrant = _qdrant(db)
    if not qdrant:
        return None
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
    if not points:
        return None
    points.sort(key=lambda p: (p.get("payload") or {}).get("total_plays_global", 0), reverse=True)
    vectors = [p["vector"] for p in points[:15] if p.get("vector")]
    if not vectors:
        return None
    dim = len(vectors[0])
    return [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]


async def run_centroid_discovery(db, cfg: ArtistDiscoverySettings) -> dict[str, Any]:
    centroid = await compute_taste_centroid(db)
    if not centroid:
        return {"ok": True, "message": "No taste centroid yet (no discovered artists)", "candidates": 0}
    qdrant = _qdrant(db)
    if not qdrant:
        return {"ok": False, "message": "Qdrant not configured (Settings → Integrations)", "candidates": 0}
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
            era=clean_era(payload.get("era")), source="centroid",
            similarity_score=h.get("score"), status="pending",
            image_url=enrichment["image_url"], bio=enrichment["bio"],
            years_active=enrichment["years_active"],
        ))
        created += 1
    db.commit()
    return {"ok": True, "message": f"{created} new candidate(s)", "candidates": created}


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
                    "mood_tags": [], "era": "", "is_monitored_lidarr": False,
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
        tasks.update_task(task_id, message="Running taste-centroid discovery…")
        centroid = await run_centroid_discovery(db, cfg)
        tasks.update_task(task_id, message="Expanding related-artist graph…")
        graph = await run_graph_sync(db, cfg)
        tasks.update_task(task_id, message="Enriching candidate images/bios…")
        await re_enrich_missing(db)
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

        updated = 0
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
                }
                plays = play_counts.get(name_key)
                if plays is not None:
                    updates["total_plays_global"] = max(payload.get("total_plays_global") or 0, plays)
                try:
                    await qdrant.set_payload([p["id"]], updates)
                    updated += 1
                except Exception as e:
                    logger.warning(f"Artist Discovery sync: set_payload failed for point {p.get('id')}: {e}")
            if offset is None:
                break

        run.finished_at = datetime.utcnow()
        run.message = f"Synced {updated} artist point(s)"
        db.commit()
        return {"ok": True, "message": run.message, "updated": updated}
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
        return {"ok": True, "message": f"Added '{name}' to Lidarr", "lidarr_artist_id": added.get("id")}
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
            return {"ok": True, "message": f"Added '{name}' to Lidarr", "lidarr_artist_id": lidarr_artist_id}
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

async def search_related_artists(db, artist: str, limit: int = 50) -> dict[str, Any]:
    artist = (artist or "").strip()
    if not artist:
        return {"ok": False, "message": "Enter an artist name", "results": []}
    lastfm = _lastfm_client(db)
    if not lastfm:
        return {"ok": False, "message": "Last.fm integration not configured", "results": []}

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

    results = []
    for r in related:
        rname = (r.get("name") or "").strip()
        if not rname:
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
        })
    return {"ok": True, "message": f"{len(results)} related artist(s) for '{artist}'", "results": results}


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
    return await _add_artist_to_lidarr(lidarr, cfg, mbid, name)


# --- Misc -------------------------------------------------------------------------

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
