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
from app.schemas.settings import ArtistDiscoverySettings

logger = logging.getLogger("powarr")


def _norm_artist(name: str) -> str:
    t = (name or "").lower().strip()
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def load_settings(db) -> ArtistDiscoverySettings:
    row = db.query(AppSetting).filter_by(key="artist_discovery").first()
    if not row or not row.value:
        return ArtistDiscoverySettings()
    return ArtistDiscoverySettings(**json.loads(row.value))


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


def _candidate_exists(db, mbid: str | None, name: str) -> bool:
    """Any prior row (any status) permanently blocks re-surfacing — a rejected
    candidate never comes back, same precedent as Smart Playlists' artist dedupe."""
    if mbid:
        if db.query(DiscoveredArtist).filter_by(musicbrainz_id=mbid).first():
            return True
    return db.query(DiscoveredArtist).filter_by(artist_name=name).first() is not None


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
        must=[{"key": "is_discovered", "match": {"value": False}}])
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
        genres_list = payload.get("genres") or enrichment["genres"]
        db.add(DiscoveredArtist(
            musicbrainz_id=mbid, artist_name=name,
            genres=json.dumps(genres_list),
            mood_tags=json.dumps(payload.get("mood_tags") or []),
            era=payload.get("era"), source="centroid",
            similarity_score=h.get("score"), status="pending",
            image_url=enrichment["image_url"], bio=enrichment["bio"],
            years_active=enrichment["years_active"],
        ))
        created += 1
    db.commit()
    return {"ok": True, "message": f"{created} new candidate(s)", "candidates": created}


# --- Related-artist graph sync ---------------------------------------------------

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
                    "plex_fulfillment": "none", "total_plays_global": 0,
                    "last_played_timestamp": 0, "is_discovered": False,
                    "associated_seed_mbids": [seed_mbid or seed_name],
                    "last_related_scan_timestamp": 0,
                }
                try:
                    await qdrant.upsert_points([{"id": rid, "vector": vector, "payload": epayload}])
                except Exception as e:
                    logger.warning(f"Artist Discovery graph sync: Qdrant upsert failed for {rname}: {e}")
                    continue

            conn_count = len(epayload.get("associated_seed_mbids") or [])
            if epayload.get("is_monitored_lidarr") or conn_count < cfg.auto_add_connection_threshold:
                continue
            if _candidate_exists(db, rmbid, rname):
                continue
            enrichment = await _enrich_candidate(db, rmbid, rname)
            genres_list = epayload.get("genres") or enrichment["genres"]
            cand = DiscoveredArtist(
                musicbrainz_id=rmbid, artist_name=rname,
                genres=json.dumps(genres_list),
                mood_tags=json.dumps(epayload.get("mood_tags") or []),
                era=epayload.get("era"), source="graph",
                associated_seed_mbids=json.dumps(epayload.get("associated_seed_mbids") or []),
                seed_artist_name=seed_name, status="pending",
                image_url=enrichment["image_url"], bio=enrichment["bio"],
                years_active=enrichment["years_active"],
            )
            db.add(cand)
            db.flush()
            if cfg.auto_promote:
                result = await add_to_lidarr(db, cand.id)
                if result.get("ok"):
                    promoted += 1
            else:
                created += 1

        try:
            await qdrant.set_payload([seed["id"]], {"last_related_scan_timestamp": int(datetime.utcnow().timestamp())})
        except Exception as e:
            logger.warning(f"Artist Discovery graph sync: seed timestamp update failed for {seed_name}: {e}")

    db.commit()
    return {"ok": True, "message": f"{created} new candidate(s), {promoted} auto-promoted",
            "candidates": created, "promoted": promoted}


# --- Orchestration -----------------------------------------------------------------

async def run_full_discovery_cycle(db=None) -> dict[str, Any]:
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
        ingest = await ingest_scrobbles(db, cfg)
        centroid = await run_centroid_discovery(db, cfg)
        graph = await run_graph_sync(db, cfg)
        found = centroid.get("candidates", 0) + graph.get("candidates", 0)
        added = graph.get("promoted", 0)
        run.candidates_found = found
        run.candidates_added = added
        run.finished_at = datetime.utcnow()
        run.message = f"Ingested {ingest.get('ingested', 0)}, {found} new candidate(s), {added} auto-promoted"
        db.commit()
        return {"ok": True, "message": run.message, "ingest": ingest, "centroid": centroid, "graph": graph}
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
        lidarr_row = db.query(Integration).filter_by(name="lidarr", enabled=True).first()
        if not lidarr_row:
            run.message = "Lidarr integration not enabled"
            run.finished_at = datetime.utcnow()
            db.commit()
            return {"ok": False, "message": run.message}
        from app.api.v1.integrations import _get_client
        lidarr = _get_client(lidarr_row)

        artists = await lidarr.get_artists()
        by_mbid = {a.get("foreignArtistId"): a for a in artists if a.get("foreignArtistId")}
        by_name = {_norm_artist(a.get("artistName") or ""): a for a in artists}

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
                updates: dict[str, Any] = {"is_monitored_lidarr": is_monitored, "plex_fulfillment": fulfillment}
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

    term = f"lidarr:{cand.musicbrainz_id}" if cand.musicbrainz_id else cand.artist_name
    try:
        results = await lidarr.lookup_artist(term)
    except Exception as e:
        return {"ok": False, "message": f"Lidarr lookup failed: {e}"}
    if not results:
        return {"ok": False, "message": "No Lidarr lookup results"}

    match = None
    if cand.musicbrainz_id:
        match = next((r for r in results if r.get("foreignArtistId") == cand.musicbrainz_id), None)
    if not match:
        target = _norm_artist(cand.artist_name)
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

    lidarr_artist_id = None
    try:
        added = await lidarr.add_artist(match)
        lidarr_artist_id = added.get("id")
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code == 400:
            try:
                existing = await lidarr.get_artists()
                found = next((a for a in existing
                             if a.get("foreignArtistId") == cand.musicbrainz_id
                             or _norm_artist(a.get("artistName") or "") == _norm_artist(cand.artist_name)), None)
                lidarr_artist_id = found.get("id") if found else None
            except Exception:
                lidarr_artist_id = None
        else:
            return {"ok": False, "message": f"Lidarr add failed: {e}"}
    except Exception as e:
        return {"ok": False, "message": f"Lidarr add failed: {e}"}

    qdrant = _qdrant(db)
    if qdrant:
        pid = qdrant.point_id(cand.musicbrainz_id, cand.artist_name)
        try:
            await qdrant.set_payload([pid], {"is_monitored_lidarr": True})
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
