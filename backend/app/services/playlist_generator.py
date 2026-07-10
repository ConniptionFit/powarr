"""Smart Playlists generator (MOD-01, v0.35+).

Read-only against Qdrant. Plex writes only target playlists Powarr created
(plex_playlist_id stored on SmartPlaylist). Join: normalized artist_name ↔
MediaItem.parent_title for tracks. Scheduler integration + auto-add support.
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
from app.models.smart_playlist import (
    SmartPlaylist, SmartPlaylistCandidate, SmartPlaylistRun, SmartPlaylistTrack
)
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
        from app.services import qdrant_config
        client = qdrant_config.client(db)
        if not cfg.enabled or not client:
            return {"ok": False, "message": "Smart Playlists disabled or Qdrant not configured "
                                             "(Settings → Integrations → Qdrant)",
                    "genres": 0, "candidates": 0}

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


async def accept_candidate(candidate_id: int, max_tracks_override: int | None = None) -> dict[str, Any]:
    """Accept one candidate: ensure Powarr playlist exists, add artist's tracks.

    Args:
        candidate_id: SmartPlaylistCandidate.id
        max_tracks_override: Optional per-call override for max tracks (for testing/batch ops)
    """
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

        # Determine max_tracks for this operation
        max_tracks = (max_tracks_override or pl.max_tracks_override
                      or cfg.max_tracks_per_playlist)

        # Join tracks by normalized parent_title
        target = _norm_artist(cand.artist_name)
        tracks = db.query(MediaItem).filter(
            MediaItem.media_type == "track",
            MediaItem.is_monitored_lidarr == True  # type: ignore
        ).all()
        keys = []
        added_tracks = []
        for t in tracks:
            if _norm_artist(t.parent_title or "") == target:
                # Check if already in playlist (dedup)
                exists = db.query(SmartPlaylistTrack).filter_by(
                    playlist_id=pl.id, plex_key=t.plex_rating_key).first()
                if not exists:
                    keys.append(t.plex_rating_key)
                    added_tracks.append(t)
                    if len(keys) >= max_tracks:
                        break

        if not keys:
            cand.status = "accepted"
            cand.resolved_at = datetime.utcnow()
            db.commit()
            return {"ok": True, "message": f"Accepted '{cand.artist_name}' — no new Plex tracks to add",
                    "added": 0, "plex_playlist_id": pl.plex_playlist_id}

        added = await plex.add_to_playlist(pl.plex_playlist_id, keys)

        # Track added tracks for future dedup
        for t in added_tracks[:added]:  # only track successfully added ones
            db.add(SmartPlaylistTrack(
                playlist_id=pl.id,
                plex_key=t.plex_rating_key,
                artist_name=cand.artist_name,
                track_title=t.title,
                plex_metadata=json.dumps({
                    "title": t.title,
                    "library": t.library_section,
                    "added_at": datetime.utcnow().isoformat(),
                })[:2000]
            ))

        cand.status = "accepted"
        cand.resolved_at = datetime.utcnow()
        pl.track_count = (pl.track_count or 0) + added
        pl.last_generated_at = datetime.utcnow()
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


async def run_scheduled_generation() -> dict[str, Any]:
    """Scheduled Smart Playlists generation with auto-add.

    Runs generation for playlists on interval, auto-accepting if configured.
    Respects per-playlist overrides and global settings.
    """
    db = SessionLocal()
    try:
        cfg = load_settings(db)
        if not cfg.enabled or not cfg.schedule_enabled:
            return {"ok": True, "message": "Smart Playlists or scheduling disabled", "playlists": 0}

        now = datetime.utcnow()
        playlists = db.query(SmartPlaylist).filter_by(enabled=True).all()

        results = []
        for pl in playlists:
            # Check if this playlist needs regeneration
            if pl.last_generated_at:
                elapsed = now - pl.last_generated_at
                elapsed_hours = elapsed.total_seconds() / 3600
            else:
                elapsed_hours = float('inf')

            if elapsed_hours < cfg.schedule_interval_hours:
                continue  # Not yet time

            # Run generation for this playlist's genre
            gen_result = await generate_candidates(genre=pl.genre_tag)
            if not gen_result.get("ok"):
                logger.warning(f"Scheduled generation failed for {pl.genre_tag}: {gen_result.get('message')}")
                pl.last_run_message = gen_result.get("message")
                db.commit()
                continue

            # Check auto_add setting (per-playlist override or global default)
            auto_add = (pl.auto_add_override
                       if pl.auto_add_override is not None
                       else cfg.auto_add_tracks_default)

            if auto_add:
                # Auto-accept all pending candidates for this playlist
                cands = db.query(SmartPlaylistCandidate).filter_by(
                    playlist_id=pl.id, status="pending").all()
                accepted = 0
                for cand in cands:
                    result = await accept_candidate(
                        cand.id,
                        max_tracks_override=pl.max_tracks_override
                    )
                    if result.get("ok"):
                        accepted += 1

                msg = f"Auto-added {accepted}/{len(cands)} candidates"
            else:
                msg = "Generated candidates (manual review required)"

            pl.last_run_message = msg
            db.commit()
            results.append({"playlist": pl.genre_tag, "message": msg})

        return {
            "ok": True,
            "message": f"Scheduled generation completed for {len(results)} playlist(s)",
            "playlists": len(results),
            "results": results
        }
    except Exception as e:
        logger.error(f"Scheduled playlist generation failed: {e}", exc_info=True)
        return {"ok": False, "message": f"Error: {str(e)}", "playlists": 0}
    finally:
        db.close()
