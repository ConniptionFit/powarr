"""Smart Playlists generator (MOD-01, v0.35+; SP-01 blacklist-only v0.42.1).

Read-only against Qdrant. Plex writes only target playlists Powarr created
(plex_playlist_id stored on SmartPlaylist). Join: normalized artist_name ↔
MediaItem.parent_title for tracks.

SP-01: all monitored artists in a genre are eligible unless blacklisted — no
per-artist Accept/Reject queue. New genre playlists stay as Suggested drafts
until Approve pushes them to Plex; Managed playlists auto-update on generate.
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
    """Load Smart Playlist settings with SP-05 default migration."""
    row = db.query(AppSetting).filter_by(key="smart_playlists").first()
    if not row or not row.value:
        return SmartPlaylistSettings()
    data = json.loads(row.value)
    dirty = False
    if "auto_update_playlists" not in data:
        data["auto_update_playlists"] = bool(data.get("auto_add_tracks_default", True))
        dirty = True
    if "blacklisted_artists" not in data:
        data["blacklisted_artists"] = []
        dirty = True
    if dirty:
        row.value = json.dumps(data)
        db.commit()
    return SmartPlaylistSettings(**data)


def save_settings(db, cfg: SmartPlaylistSettings) -> None:
    row = db.query(AppSetting).filter_by(key="smart_playlists").first()
    if not row:
        row = AppSetting(key="smart_playlists")
        db.add(row)
    row.value = cfg.model_dump_json()
    db.commit()


def _blacklist_set(cfg: SmartPlaylistSettings) -> set[str]:
    return {_norm_artist(a) for a in (cfg.blacklisted_artists or []) if a and a.strip()}


def _is_blacklisted(artist_name: str, blocked: set[str]) -> bool:
    return bool(blocked) and _norm_artist(artist_name) in blocked


async def _playlist_title(db, cfg: SmartPlaylistSettings, genre: str,
                          artist_names: list[str]) -> str:
    fallback = f"Powarr · {genre}"
    if not cfg.llm_playlist_names:
        return fallback
    name = await suggest_playlist_name_for(db, genre, artist_names)
    return name or fallback


async def suggest_playlist_name_for(db, genre: str, artist_names: list[str] | None = None) -> str | None:
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


def _plex_client(db):
    plex_row = db.query(Integration).filter_by(name="plex", enabled=True).first()
    if not plex_row:
        return None
    from app.api.v1.integrations import _get_client
    return _get_client(plex_row)


async def _artists_by_genre(db, cfg: SmartPlaylistSettings,
                            genre: str | None = None) -> dict[str, list[dict]]:
    """Scroll Qdrant monitored artists → genre → unique non-blacklisted artists."""
    from app.services import qdrant_config
    client = qdrant_config.client(db)
    if not client:
        return {}
    excluded = {g.lower() for g in (cfg.excluded_genres or [])}
    blocked = _blacklist_set(cfg)
    by_genre: dict[str, list[dict]] = defaultdict(list)
    offset = None
    pages = 0
    while pages < 40:
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
                })
        if offset is None:
            break

    out: dict[str, list[dict]] = {}
    for g, artists in by_genre.items():
        seen: set[str] = set()
        uniq = []
        for a in artists:
            key = _norm_artist(a["artist_name"])
            if key in seen:
                continue
            seen.add(key)
            uniq.append(a)
        if len(uniq) >= cfg.min_artists_per_genre:
            out[g] = uniq
    return out


async def _sonic_bias(db, plex, pl: SmartPlaylist, candidates: list[MediaItem]) -> list[MediaItem]:
    """SP-02: reorder candidates so tracks sonically close (Plex's own Sonic
    Analysis, /nearest) to the playlist's most-recently-added track sort first.
    Pure re-ranking, never filters — Qdrant genre/artist eligibility is untouched.
    Fails soft to the original order on any error or when analysis isn't available."""
    seed = db.query(SmartPlaylistTrack).filter_by(playlist_id=pl.id).order_by(
        SmartPlaylistTrack.added_at.desc()).first()
    if not seed:
        return candidates
    near = set(await plex.sonically_similar_keys(seed.plex_key))
    if not near:
        return candidates
    preferred = [t for t in candidates if t.plex_rating_key in near]
    rest = [t for t in candidates if t.plex_rating_key not in near]
    return preferred + rest


async def _add_artist_tracks_to_db(db, pl: SmartPlaylist, artist_name: str,
                             max_tracks: int, *, plex=None,
                             cfg: SmartPlaylistSettings | None = None) -> list[MediaItem]:
    """Collect MediaItem tracks for artist not yet in the playlist ledger.
    SP-02: when cfg.sonic_similarity_enabled, candidates are sonic-biased before
    the max_tracks cut (see _sonic_bias).

    Bug fix 2026-07-11: this filtered on `MediaItem.is_monitored_lidarr`, a field
    that only ever existed on Qdrant payloads (see integrations/qdrant.py) — the
    MediaItem model has no such column. Introduced in v0.42.1 (450fc7f), every
    call raised AttributeError, meaning Smart Playlists has never actually been
    able to add a track to Plex since that version — silently swallowed by the
    broad except in every caller (generate/approve/accept all reported success
    with misleadingly-zero tracks added). Monitored-artist eligibility is already
    fully established upstream by _artists_by_genre's Qdrant scroll before this
    function is ever called — no second gate is needed here."""
    target = _norm_artist(artist_name)
    tracks = db.query(MediaItem).filter(MediaItem.media_type == "track").all()
    candidates: list[MediaItem] = []
    for t in tracks:
        if _norm_artist(t.parent_title or "") != target:
            continue
        exists = db.query(SmartPlaylistTrack).filter_by(
            playlist_id=pl.id, plex_key=t.plex_rating_key).first()
        if exists:
            continue
        candidates.append(t)
    if candidates and plex and cfg and cfg.sonic_similarity_enabled:
        candidates = await _sonic_bias(db, plex, pl, candidates)
    return candidates[:max_tracks]


async def _sync_tracks_to_plex(db, plex, pl: SmartPlaylist, artists: list[dict],
                               cfg: SmartPlaylistSettings) -> int:
    """Push all eligible artists' tracks into an existing Plex playlist. Returns tracks added."""
    if not pl.plex_playlist_id:
        return 0
    max_tracks = pl.max_tracks_override or cfg.max_tracks_per_playlist
    total_added = 0
    remaining = max_tracks - (pl.track_count or 0)
    if remaining <= 0:
        remaining = max_tracks  # still allow refresh of missing ledger entries

    for a in artists:
        name = a["artist_name"]
        if _is_blacklisted(name, _blacklist_set(cfg)):
            continue
        batch = await _add_artist_tracks_to_db(db, pl, name, max(1, remaining or max_tracks),
                                               plex=plex, cfg=cfg)
        if not batch:
            # Record artist as included even with no local tracks
            _mark_artist_included(db, pl, a)
            continue
        keys = [t.plex_rating_key for t in batch]
        added = await plex.add_to_playlist(pl.plex_playlist_id, keys)
        for t in batch[:added]:
            db.add(SmartPlaylistTrack(
                playlist_id=pl.id,
                plex_key=t.plex_rating_key,
                artist_name=name,
                track_title=t.title,
                plex_metadata=json.dumps({
                    "title": t.title,
                    "library": t.library_section,
                    "added_at": datetime.utcnow().isoformat(),
                })[:2000],
            ))
        total_added += added
        _mark_artist_included(db, pl, a)
        remaining = max(0, remaining - added)
        if remaining <= 0 and (pl.track_count or 0) + total_added >= max_tracks:
            break

    pl.track_count = (pl.track_count or 0) + total_added
    pl.last_generated_at = datetime.utcnow()
    pl.updated_at = datetime.utcnow()
    return total_added


def _mark_artist_included(db, pl: SmartPlaylist, artist: dict) -> None:
    """Bookkeeping row — accepted means included (no pending queue)."""
    name = artist["artist_name"]
    row = db.query(SmartPlaylistCandidate).filter_by(
        playlist_id=pl.id, artist_name=name).first()
    if row:
        if row.status != "accepted":
            row.status = "accepted"
            row.resolved_at = datetime.utcnow()
        return
    db.add(SmartPlaylistCandidate(
        playlist_id=pl.id,
        artist_name=name,
        musicbrainz_id=artist.get("musicbrainz_id"),
        status="accepted",
        resolved_at=datetime.utcnow(),
        source_payload=json.dumps({"musicbrainz_id": artist.get("musicbrainz_id")})[:2000],
    ))


async def generate_candidates(genre: str | None = None) -> dict[str, Any]:
    """Discover genre playlists + sync Managed ones. Suggested drafts are created
    without pushing to Plex. No per-artist pending queue (SP-01)."""
    db = SessionLocal()
    try:
        cfg = load_settings(db)
        if not cfg.enabled:
            return {"ok": False, "message": "Smart Playlists disabled",
                    "genres": 0, "candidates": 0, "playlists_created": 0, "tracks_added": 0}

        by_genre = await _artists_by_genre(db, cfg, genre)
        if not by_genre and genre is None:
            from app.services import qdrant_config
            if not qdrant_config.client(db):
                return {"ok": False, "message": "Qdrant not configured (Settings → Integrations → Qdrant)",
                        "genres": 0, "candidates": 0, "playlists_created": 0, "tracks_added": 0}

        created_playlists = 0
        tracks_added = 0
        synced = 0
        plex = _plex_client(db)

        for g, artists in by_genre.items():
            pl = db.query(SmartPlaylist).filter_by(genre_tag=g).first()
            if not pl:
                title = await _playlist_title(db, cfg, g, [a["artist_name"] for a in artists])
                pl = SmartPlaylist(genre_tag=g, title=title, enabled=True)
                db.add(pl)
                db.flush()
                created_playlists += 1
                pl.last_run_message = f"Suggested — {len(artists)} artist(s), awaiting Approve"
                continue

            # Managed (on Plex): auto-include all non-blacklisted artists
            if pl.plex_playlist_id and pl.enabled:
                should_update = (pl.auto_add_override
                                 if pl.auto_add_override is not None
                                 else (cfg.auto_update_playlists or cfg.auto_add_tracks_default))
                if should_update:
                    if not plex:
                        pl.last_run_message = "Plex not enabled — cannot sync"
                        continue
                    added = await _sync_tracks_to_plex(db, plex, pl, artists, cfg)
                    tracks_added += added
                    synced += 1
                    pl.last_run_message = f"Synced {len(artists)} artist(s), +{added} track(s)"
                else:
                    pl.last_run_message = "Auto-update off for this playlist"
            else:
                # Suggested draft — refresh artist count message only
                pl.last_run_message = f"Suggested — {len(artists)} artist(s), awaiting Approve"

        db.commit()
        return {
            "ok": True,
            "message": "ok",
            "genres": len(by_genre),
            "playlists_created": created_playlists,
            "candidates": 0,
            "tracks_added": tracks_added,
            "playlists_synced": synced,
        }
    except Exception as e:
        logger.warning(f"Smart playlist generate failed: {e}")
        return {"ok": False, "message": str(e), "genres": 0, "candidates": 0,
                "playlists_created": 0, "tracks_added": 0}
    finally:
        db.close()


async def approve_playlist(playlist_id: int) -> dict[str, Any]:
    """Push a Suggested playlist to Plex and sync all non-blacklisted artists."""
    db = SessionLocal()
    try:
        cfg = load_settings(db)
        pl = db.query(SmartPlaylist).filter_by(id=playlist_id).first()
        if not pl:
            return {"ok": False, "message": "Playlist not found"}
        plex = _plex_client(db)
        if not plex:
            return {"ok": False, "message": "Plex integration not enabled"}
        if not pl.plex_playlist_id:
            pid = await plex.create_playlist(pl.title, playlist_type="audio")
            if not pid:
                return {"ok": False, "message": "Plex playlist create failed"}
            pl.plex_playlist_id = pid
            db.commit()

        by_genre = await _artists_by_genre(db, cfg, pl.genre_tag)
        artists = by_genre.get(pl.genre_tag) or []
        added = await _sync_tracks_to_plex(db, plex, pl, artists, cfg)
        pl.last_run_message = f"Approved — {len(artists)} artist(s), +{added} track(s)"
        db.commit()
        return {
            "ok": True,
            "message": f"Approved — synced {len(artists)} artist(s), added {added} track(s)",
            "plex_playlist_id": pl.plex_playlist_id,
            "artists": len(artists),
            "added": added,
        }
    except Exception as e:
        logger.warning(f"Approve playlist {playlist_id} failed: {e}")
        return {"ok": False, "message": str(e)}
    finally:
        db.close()


# --- Legacy candidate endpoints (kept for API compat; no UI) -------------------

async def accept_candidate(candidate_id: int, max_tracks_override: int | None = None,
                           *, allow_create_plex: bool = True) -> dict[str, Any]:
    """Legacy single-artist accept — prefer approve_playlist / generate sync."""
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
        plex = _plex_client(db)
        if not plex:
            return {"ok": False, "message": "Plex integration not enabled"}
        if not pl.plex_playlist_id:
            if not allow_create_plex:
                return {"ok": False, "message": "Playlist not yet approved for Plex",
                        "skipped_create": True}
            pid = await plex.create_playlist(pl.title, playlist_type="audio")
            if not pid:
                return {"ok": False, "message": "Plex playlist create failed"}
            pl.plex_playlist_id = pid
            db.commit()
        max_tracks = (max_tracks_override or pl.max_tracks_override
                      or cfg.max_tracks_per_playlist)
        batch = await _add_artist_tracks_to_db(db, pl, cand.artist_name, max_tracks,
                                               plex=plex, cfg=cfg)
        if not batch:
            cand.status = "accepted"
            cand.resolved_at = datetime.utcnow()
            db.commit()
            return {"ok": True, "message": f"Accepted '{cand.artist_name}' — no new tracks",
                    "added": 0, "plex_playlist_id": pl.plex_playlist_id}
        keys = [t.plex_rating_key for t in batch]
        added = await plex.add_to_playlist(pl.plex_playlist_id, keys)
        for t in batch[:added]:
            db.add(SmartPlaylistTrack(
                playlist_id=pl.id, plex_key=t.plex_rating_key,
                artist_name=cand.artist_name, track_title=t.title,
                plex_metadata=json.dumps({"title": t.title})[:2000],
            ))
        cand.status = "accepted"
        cand.resolved_at = datetime.utcnow()
        pl.track_count = (pl.track_count or 0) + added
        pl.last_generated_at = datetime.utcnow()
        db.commit()
        return {"ok": True, "message": f"Added {added} track(s) for '{cand.artist_name}'",
                "added": added, "plex_playlist_id": pl.plex_playlist_id}
    except Exception as e:
        logger.warning(f"Accept candidate {candidate_id} failed: {e}")
        return {"ok": False, "message": str(e)}
    finally:
        db.close()


def reject_candidate(candidate_id: int) -> dict[str, Any]:
    """Legacy — prefer blacklist. Marks candidate rejected."""
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


async def rename_playlist(playlist_id: int, title: str) -> dict[str, Any]:
    title = (title or "").strip()
    if not title:
        return {"ok": False, "message": "Title is required"}
    db = SessionLocal()
    try:
        pl = db.query(SmartPlaylist).filter_by(id=playlist_id).first()
        if not pl:
            return {"ok": False, "message": "Playlist not found"}
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
            msg += " (suggested — nothing on Plex)"
        return {"ok": True, "message": msg, "plex_deleted": plex_deleted}
    except Exception as e:
        logger.warning(f"Delete playlist {playlist_id} failed: {e}")
        return {"ok": False, "message": str(e)}
    finally:
        db.close()


def update_blacklist(artists: list[str]) -> dict[str, Any]:
    """Replace the artist blacklist (normalized display names preserved as entered)."""
    db = SessionLocal()
    try:
        cfg = load_settings(db)
        cleaned: list[str] = []
        seen: set[str] = set()
        for a in artists or []:
            s = (a or "").strip()
            if not s:
                continue
            key = _norm_artist(s)
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(s)
        cfg.blacklisted_artists = cleaned
        save_settings(db, cfg)
        return {"ok": True, "blacklisted_artists": cleaned}
    finally:
        db.close()


async def run_scheduled_generation() -> dict[str, Any]:
    """Scheduled discovery + sync of Managed playlists (SP-01/SP-05/SP-06)."""
    db = SessionLocal()
    try:
        cfg = load_settings(db)
        if not cfg.enabled or not cfg.schedule_enabled:
            return {"ok": True, "message": "Smart Playlists or scheduling disabled", "playlists": 0}

        # Full generate: creates Suggested drafts + syncs Managed
        result = await generate_candidates()
        if not result.get("ok"):
            return {"ok": False, "message": result.get("message"), "playlists": 0}

        # Optionally auto-approve new drafts when auto_create is on
        if cfg.auto_create_playlists:
            drafts = db.query(SmartPlaylist).filter(
                SmartPlaylist.enabled == True,  # type: ignore
                SmartPlaylist.plex_playlist_id.is_(None),
            ).all()
            for pl in drafts:
                await approve_playlist(pl.id)

        return {
            "ok": True,
            "message": result.get("message") or "ok",
            "playlists": result.get("playlists_synced") or 0,
            "playlists_created": result.get("playlists_created") or 0,
            "tracks_added": result.get("tracks_added") or 0,
        }
    except Exception as e:
        logger.error(f"Scheduled playlist generation failed: {e}", exc_info=True)
        return {"ok": False, "message": f"Error: {str(e)}", "playlists": 0}
    finally:
        db.close()
