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
    """Load Smart Playlist settings with SP-05 default migration.

    Pre-v0.42 defaulted auto_add_tracks_default to False and had no
    auto_update_playlists / blacklisted_artists. Existing rows without the new
    keys keep their stored auto_add value and gain auto_update_playlists=True
    only when auto_add_tracks_default was already True (or missing → True for
    brand-new semantics on fresh installs via Pydantic defaults).
    """
    row = db.query(AppSetting).filter_by(key="smart_playlists").first()
    if not row or not row.value:
        return SmartPlaylistSettings()
    data = json.loads(row.value)
    dirty = False
    if "auto_update_playlists" not in data:
        # Prefer explicit auto_add if present; otherwise default ON (SP-05).
        data["auto_update_playlists"] = bool(data.get("auto_add_tracks_default", True))
        dirty = True
    if "blacklisted_artists" not in data:
        data["blacklisted_artists"] = []
        dirty = True
    if dirty:
        row.value = json.dumps(data)
        db.commit()
    return SmartPlaylistSettings(**data)


def _blacklist_set(cfg: SmartPlaylistSettings) -> set[str]:
    return {_norm_artist(a) for a in (cfg.blacklisted_artists or []) if a and a.strip()}


def _is_blacklisted(artist_name: str, blocked: set[str]) -> bool:
    return bool(blocked) and _norm_artist(artist_name) in blocked


async def _playlist_title(db, cfg: SmartPlaylistSettings, genre: str,
                          artist_names: list[str]) -> str:
    """SP-04/SP-08 — optionally LLM-generated display name for a *new* playlist;
    fails soft to the 'Powarr · {genre}' template in every failure mode."""
    fallback = f"Powarr · {genre}"
    if not cfg.llm_playlist_names:
        return fallback
    name = await suggest_playlist_name_for(db, genre, artist_names)
    return name or fallback


async def suggest_playlist_name_for(db, genre: str, artist_names: list[str] | None = None) -> str | None:
    """SP-08 — on-demand LLM name suggestion (minimal context). Returns None on failure."""
    try:
        from app.schemas.settings import OllamaSettings
        row = db.query(AppSetting).filter_by(key="ollama").first()
        ollama = OllamaSettings(**json.loads(row.value)) if row and row.value else OllamaSettings()
        if not ollama.enabled or not ollama.host or not ollama.model:
            return None
        from app.services import llm_assist
        return await llm_assist.suggest_playlist_name(
            ollama.host, ollama.model, genre, artist_names or [],
            api_style=ollama.api_style, model_size=ollama.model_size,
            keep_alive_minutes=ollama.keep_alive_minutes,
            forbid_thinking=getattr(ollama, "forbid_thinking", True))
    except Exception as e:
        logger.info(f"Smart playlist LLM naming failed for '{genre}': {e}")
        return None


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
        blocked = _blacklist_set(cfg)
        by_genre: dict[str, list[dict]] = defaultdict(list)
        offset = None
        pages = 0
        while pages < 40:  # hard cap
            points, offset = await client.scroll_monitored_artists(limit=256, offset=offset)
            pages += 1
            for p in points:
                payload = p.get("payload") or {}
                artist = (payload.get("artist_name") or "").strip()
                if not artist or _is_blacklisted(artist, blocked):
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
                title = await _playlist_title(db, cfg, g, [a["artist_name"] for a in uniq])
                pl = SmartPlaylist(genre_tag=g, title=title, enabled=True)
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


async def accept_candidate(candidate_id: int, max_tracks_override: int | None = None,
                           *, allow_create_plex: bool = True) -> dict[str, Any]:
    """Accept one candidate: ensure Powarr playlist exists, add artist's tracks.

    Args:
        candidate_id: SmartPlaylistCandidate.id
        max_tracks_override: Optional per-call override for max tracks (for testing/batch ops)
        allow_create_plex: When False (scheduled path + auto_create off), skip creating a
            new Plex playlist — only update playlists that already have plex_playlist_id.
    """
    db = SessionLocal()
    try:
        cfg = load_settings(db)
        cand = db.query(SmartPlaylistCandidate).filter_by(id=candidate_id).first()
        if not cand:
            return {"ok": False, "message": "Candidate not found"}
        if cand.status == "accepted":
            return {"ok": True, "message": "Already accepted", "added": 0}
        if _is_blacklisted(cand.artist_name, _blacklist_set(cfg)):
            cand.status = "rejected"
            cand.resolved_at = datetime.utcnow()
            db.commit()
            return {"ok": False, "message": f"'{cand.artist_name}' is blacklisted"}
        pl = db.query(SmartPlaylist).filter_by(id=cand.playlist_id).first()
        if not pl:
            return {"ok": False, "message": "Playlist definition missing"}

        plex_row = db.query(Integration).filter_by(name="plex", enabled=True).first()
        if not plex_row:
            return {"ok": False, "message": "Plex integration not enabled"}
        from app.api.v1.integrations import _get_client
        plex = _get_client(plex_row)

        if not pl.plex_playlist_id:
            if not allow_create_plex:
                return {"ok": False, "message": "Playlist not yet approved for Plex "
                        "(auto-create off — approve manually first)", "skipped_create": True}
            # Manual Accept / Approve always creates the Powarr-owned playlist.
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


async def approve_playlist(playlist_id: int) -> dict[str, Any]:
    """SP-05 — push a draft playlist to Plex (create if needed) and auto-accept
    pending non-blacklisted candidates. Manual approval gate when auto_create is off."""
    db = SessionLocal()
    try:
        pl = db.query(SmartPlaylist).filter_by(id=playlist_id).first()
        if not pl:
            return {"ok": False, "message": "Playlist not found"}
        plex_row = db.query(Integration).filter_by(name="plex", enabled=True).first()
        if not plex_row:
            return {"ok": False, "message": "Plex integration not enabled"}
        from app.api.v1.integrations import _get_client
        plex = _get_client(plex_row)
        if not pl.plex_playlist_id:
            pid = await plex.create_playlist(pl.title, playlist_type="audio")
            if not pid:
                return {"ok": False, "message": "Plex playlist create failed"}
            pl.plex_playlist_id = pid
            db.commit()
        cands = db.query(SmartPlaylistCandidate).filter_by(
            playlist_id=pl.id, status="pending").all()
        accepted = 0
        for cand in cands:
            result = await accept_candidate(cand.id, max_tracks_override=pl.max_tracks_override,
                                            allow_create_plex=True)
            if result.get("ok"):
                accepted += 1
        return {"ok": True, "message": f"Approved — accepted {accepted}/{len(cands)} candidate(s)",
                "plex_playlist_id": pl.plex_playlist_id, "accepted": accepted}
    except Exception as e:
        logger.warning(f"Approve playlist {playlist_id} failed: {e}")
        return {"ok": False, "message": str(e)}
    finally:
        db.close()


def _plex_client(db):
    plex_row = db.query(Integration).filter_by(name="plex", enabled=True).first()
    if not plex_row:
        return None
    from app.api.v1.integrations import _get_client
    return _get_client(plex_row)


async def rename_playlist(playlist_id: int, title: str) -> dict[str, Any]:
    """Rename a smart playlist in Powarr and, if pushed, on Plex."""
    title = (title or "").strip()
    if not title:
        return {"ok": False, "message": "Title is required"}
    db = SessionLocal()
    try:
        pl = db.query(SmartPlaylist).filter_by(id=playlist_id).first()
        if not pl:
            return {"ok": False, "message": "Playlist not found"}
        old = pl.title
        if title == old and not pl.plex_playlist_id:
            return {"ok": True, "message": "Unchanged", "title": title}

        plex_renamed = False
        if pl.plex_playlist_id:
            plex = _plex_client(db)
            if not plex:
                return {"ok": False, "message": "Plex integration not enabled — cannot rename on Plex"}
            ok = await plex.rename_playlist(pl.plex_playlist_id, title)
            if not ok:
                return {"ok": False, "message": "Plex rename failed"}
            plex_renamed = True

        pl.title = title
        pl.updated_at = datetime.utcnow()
        db.commit()
        msg = f"Renamed to '{title}'"
        if plex_renamed:
            msg += " (Plex updated)"
        return {"ok": True, "message": msg, "title": title, "plex_renamed": plex_renamed}
    except Exception as e:
        logger.warning(f"Rename playlist {playlist_id} failed: {e}")
        return {"ok": False, "message": str(e)}
    finally:
        db.close()


async def delete_playlist(playlist_id: int) -> dict[str, Any]:
    """Delete a smart playlist from Powarr and remove it from Plex when present.

    Only deletes Plex playlists Powarr created (plex_playlist_id set). Related
    candidates/tracks/runs are removed with the definition.
    """
    db = SessionLocal()
    try:
        pl = db.query(SmartPlaylist).filter_by(id=playlist_id).first()
        if not pl:
            return {"ok": False, "message": "Playlist not found"}

        plex_deleted = False
        plex_id = pl.plex_playlist_id
        if plex_id:
            plex = _plex_client(db)
            if not plex:
                return {"ok": False, "message": "Plex integration not enabled — cannot remove from Plex"}
            ok = await plex.delete_playlist(plex_id)
            if not ok:
                return {"ok": False, "message": "Plex delete failed — Powarr row left intact"}
            plex_deleted = True

        title = pl.title
        db.query(SmartPlaylistTrack).filter_by(playlist_id=pl.id).delete()
        db.query(SmartPlaylistCandidate).filter_by(playlist_id=pl.id).delete()
        db.query(SmartPlaylistRun).filter_by(playlist_id=pl.id).delete()
        db.delete(pl)
        db.commit()
        msg = f"Deleted '{title}'"
        if plex_deleted:
            msg += " (removed from Plex)"
        elif plex_id is None:
            msg += " (draft — nothing on Plex)"
        return {"ok": True, "message": msg, "plex_deleted": plex_deleted}
    except Exception as e:
        logger.warning(f"Delete playlist {playlist_id} failed: {e}")
        return {"ok": False, "message": str(e)}
    finally:
        db.close()


async def run_scheduled_generation() -> dict[str, Any]:
    """Scheduled Smart Playlists generation with auto-update of approved playlists.

    SP-05: auto_create_playlists gates Plex creation for draft playlists;
    auto_update_playlists (default ON) auto-accepts pending candidates on
    playlists that already have a plex_playlist_id.
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

            # Refresh playlist row (generate_candidates uses its own session)
            pl = db.query(SmartPlaylist).filter_by(id=pl.id).first()
            if not pl:
                continue

            # Per-playlist override wins; else auto_update for approved, auto_create for drafts
            if pl.auto_add_override is not None:
                should_auto = pl.auto_add_override
            elif pl.plex_playlist_id:
                should_auto = cfg.auto_update_playlists or cfg.auto_add_tracks_default
            else:
                should_auto = cfg.auto_create_playlists

            allow_create = bool(pl.plex_playlist_id) or cfg.auto_create_playlists

            if should_auto:
                cands = db.query(SmartPlaylistCandidate).filter_by(
                    playlist_id=pl.id, status="pending").all()
                accepted = 0
                skipped = 0
                for cand in cands:
                    result = await accept_candidate(
                        cand.id,
                        max_tracks_override=pl.max_tracks_override,
                        allow_create_plex=allow_create,
                    )
                    if result.get("ok"):
                        accepted += 1
                    elif result.get("skipped_create"):
                        skipped += 1
                if skipped and not pl.plex_playlist_id:
                    msg = (f"Draft — {len(cands)} candidate(s) awaiting Approve "
                           f"(auto-create off)")
                else:
                    msg = f"Auto-updated {accepted}/{len(cands)} candidates"
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
