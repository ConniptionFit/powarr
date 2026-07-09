"""Smart Playlists generator (MOD-01, v0.34.0).

Read-only against Qdrant. Plex writes only target playlists Powarr created
(plex_playlist_id stored on SmartPlaylist). Join: normalized artist_name ↔
MediaItem.parent_title for tracks.
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime
from typing import Any

from app.database import SessionLocal
from app.models.app_setting import AppSetting
from app.models.integration import Integration
from app.models.media import MediaItem
from app.models.smart_playlist import SmartPlaylist, SmartPlaylistCandidate
from app.schemas.settings import SmartPlaylistSettings

logger = logging.getLogger("powarr")


def _norm_artist(name: str) -> str:
    t = (name or "").lower().strip()
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def load_settings(db) -> SmartPlaylistSettings:
    row = db.query(AppSetting).filter_by(key="smart_playlists").first()
    if not row or not row.value:
        return SmartPlaylistSettings()
    return SmartPlaylistSettings(**json.loads(row.value))


async def generate_candidates(genre: str | None = None) -> dict[str, Any]:
    """Scroll Qdrant monitored artists, group by genre, upsert pending candidates."""
    db = SessionLocal()
    try:
        cfg = load_settings(db)
        if not cfg.enabled or not cfg.qdrant_url:
            return {"ok": False, "message": "Smart Playlists disabled or Qdrant URL not set",
                    "genres": 0, "candidates": 0}
        from app.integrations.qdrant import QdrantIntegration
        from app.services.secret_box import decrypt
        client = QdrantIntegration(
            cfg.qdrant_url, decrypt(cfg.qdrant_api_key) or "", cfg.collection)

        excluded = {g.lower() for g in (cfg.excluded_genres or [])}
        by_genre: dict[str, list[dict]] = defaultdict(list)
        offset = None
        pages = 0
        while pages < 40:  # hard cap
            points, offset = await client.scroll_monitored_artists(limit=256, offset=offset)
            pages += 1
            for p in points:
                payload = p.get("payload") or {}
                artist = (payload.get("artist_name") or "").strip()
                if not artist:
                    continue
                genres = payload.get("genres") or []
                if isinstance(genres, str):
                    genres = [genres]
                for g in genres:
                    g = (g or "").strip()
                    if not g or g.lower() in excluded:
                        continue
                    if genre and g.lower() != genre.lower():
                        continue
                    by_genre[g].append({
                        "artist_name": artist,
                        "musicbrainz_id": payload.get("musicbrainz_id"),
                        "payload": payload,
                    })
            if offset is None:
                break

        created_playlists = 0
        created_candidates = 0
        for g, artists in by_genre.items():
            # Dedupe artists per genre
            seen = set()
            uniq = []
            for a in artists:
                key = _norm_artist(a["artist_name"])
                if key in seen:
                    continue
                seen.add(key)
                uniq.append(a)
            if len(uniq) < cfg.min_artists_per_genre:
                continue
            pl = db.query(SmartPlaylist).filter_by(genre_tag=g).first()
            if not pl:
                pl = SmartPlaylist(genre_tag=g, title=f"Powarr · {g}", enabled=True)
                db.add(pl)
                db.flush()
                created_playlists += 1
            for a in uniq:
                exists = db.query(SmartPlaylistCandidate).filter_by(
                    playlist_id=pl.id, artist_name=a["artist_name"]).first()
                if exists:
                    continue
                db.add(SmartPlaylistCandidate(
                    playlist_id=pl.id,
                    artist_name=a["artist_name"],
                    musicbrainz_id=a.get("musicbrainz_id"),
                    status="pending",
                    source_payload=json.dumps({
                        "genres": (a.get("payload") or {}).get("genres"),
                        "musicbrainz_id": a.get("musicbrainz_id"),
                    })[:2000],
                ))
                created_candidates += 1
        db.commit()
        return {"ok": True, "message": "ok", "genres": len(by_genre),
                "playlists_created": created_playlists, "candidates": created_candidates}
    except Exception as e:
        logger.warning(f"Smart playlist generate failed: {e}")
        return {"ok": False, "message": str(e), "genres": 0, "candidates": 0}
    finally:
        db.close()


async def accept_candidate(candidate_id: int) -> dict[str, Any]:
    """Accept one candidate: ensure Powarr playlist exists, add artist's tracks."""
    db = SessionLocal()
    try:
        cfg = load_settings(db)
        cand = db.query(SmartPlaylistCandidate).filter_by(id=candidate_id).first()
        if not cand:
            return {"ok": False, "message": "Candidate not found"}
        if cand.status == "accepted":
            return {"ok": True, "message": "Already accepted", "added": 0}
        pl = db.query(SmartPlaylist).filter_by(id=cand.playlist_id).first()
        if not pl:
            return {"ok": False, "message": "Playlist definition missing"}

        plex_row = db.query(Integration).filter_by(name="plex", enabled=True).first()
        if not plex_row:
            return {"ok": False, "message": "Plex integration not enabled"}
        from app.api.v1.integrations import _get_client
        plex = _get_client(plex_row)

        if not pl.plex_playlist_id:
            # Manual Accept always creates the Powarr-owned playlist when missing;
            # auto_create_playlists gates scheduled/auto paths only.
            pid = await plex.create_playlist(pl.title, playlist_type="audio")
            if not pid:
                return {"ok": False, "message": "Plex playlist create failed"}
            pl.plex_playlist_id = pid
            db.commit()

        # Join tracks by normalized parent_title
        target = _norm_artist(cand.artist_name)
        tracks = db.query(MediaItem).filter(MediaItem.media_type == "track").all()
        keys = []
        for t in tracks:
            if _norm_artist(t.parent_title or "") == target:
                keys.append(t.plex_rating_key)
                if len(keys) >= cfg.max_tracks_per_playlist:
                    break
        if not keys:
            cand.status = "accepted"
            cand.resolved_at = datetime.utcnow()
            db.commit()
            return {"ok": True, "message": f"Accepted '{cand.artist_name}' — no Plex tracks matched",
                    "added": 0, "plex_playlist_id": pl.plex_playlist_id}

        added = await plex.add_to_playlist(pl.plex_playlist_id, keys)
        cand.status = "accepted"
        cand.resolved_at = datetime.utcnow()
        pl.updated_at = datetime.utcnow()
        db.commit()
        return {"ok": True, "message": f"Added {added} track(s) for '{cand.artist_name}'",
                "added": added, "plex_playlist_id": pl.plex_playlist_id}
    except Exception as e:
        logger.warning(f"Accept candidate {candidate_id} failed: {e}")
        return {"ok": False, "message": str(e)}
    finally:
        db.close()


def reject_candidate(candidate_id: int) -> dict[str, Any]:
    db = SessionLocal()
    try:
        cand = db.query(SmartPlaylistCandidate).filter_by(id=candidate_id).first()
        if not cand:
            return {"ok": False, "message": "Candidate not found"}
        cand.status = "rejected"
        cand.resolved_at = datetime.utcnow()
        db.commit()
        return {"ok": True, "message": "Rejected"}
    finally:
        db.close()
