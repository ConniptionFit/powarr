"""Plex library sync + Seerr request protection, callable from the API endpoint
and the background scheduler alike."""
import json
import logging
from datetime import datetime
from collections import defaultdict

from app.models.app_setting import AppSetting
from app.models.integration import Integration
from app.models.media import MediaItem
from app.schemas.settings import ScoringWeights, ScoringProfiles, CleanupSettings
from app.services.scorer import score_item, weights_for_library, load_scoring_profiles

logger = logging.getLogger("powarr")


def _get_setting(db, key: str, schema):
    row = db.query(AppSetting).filter_by(key=key).first()
    if not row or not row.value:
        return schema()
    return schema(**json.loads(row.value))


def _set_setting(db, key: str, value: str) -> None:
    row = db.query(AppSetting).filter_by(key=key).first()
    if not row:
        row = AppSetting(key=key)
        db.add(row)
    row.value = value


def _series_index_from_payload(items: list[dict], existing_by_key: dict) -> dict[str, dict]:
    """Build parent_title → {watched, last} from the incoming sync payload plus
    any already-stored rows (covers episodes not in this payload page)."""
    idx: dict[str, dict] = defaultdict(lambda: {"watched": False, "last": None})

    def _ingest(parent, watch_count, last_watched, media_type):
        if not parent or media_type not in ("episode", "track"):
            return
        entry = idx[parent]
        if (watch_count or 0) > 0 or last_watched:
            entry["watched"] = True
        if last_watched and (entry["last"] is None or last_watched > entry["last"]):
            entry["last"] = last_watched

    for m in existing_by_key.values():
        _ingest(m.parent_title, m.watch_count, m.last_watched_at, m.media_type)
    for d in items:
        _ingest(d.get("parent_title"), d.get("watch_count"), d.get("last_watched_at"),
                d.get("media_type"))
    return idx


def upsert_media_items(db, items: list[dict], weights: ScoringWeights,
                       profiles: ScoringProfiles | None = None,
                       progress=None) -> int:
    """Upsert Plex items by plex_rating_key + rescore with series-aware watch
    and per-library weight overlays (v0.30.0)."""
    if profiles is None:
        profiles = load_scoring_profiles(db)
    existing_by_key = {m.plex_rating_key: m for m in db.query(MediaItem).all()}
    series_idx = _series_index_from_payload(items, existing_by_key)
    total = len(items)
    upserted = 0
    for item_data in items:
        key = item_data["plex_rating_key"]
        existing = existing_by_key.get(key)
        parent = item_data.get("parent_title")
        series = series_idx.get(parent or "") if parent else None
        score_input = {
            **item_data,
            "series_watched": bool(series and series["watched"])
                if item_data.get("media_type") in ("episode", "track") else False,
            "series_last_watched_at": (series or {}).get("last")
                if item_data.get("media_type") in ("episode", "track") else None,
        }
        eff = weights_for_library(weights, profiles, item_data.get("library_section"))
        new_score = score_item(score_input, eff)
        if existing:
            for k, v in item_data.items():
                setattr(existing, k, v)
            existing.score = new_score
        else:
            data = dict(item_data)
            data["score"] = new_score
            new_item = MediaItem(**data)
            db.add(new_item)
            existing_by_key[key] = new_item
        upserted += 1
        if progress and (upserted % 50 == 0 or upserted == total):
            progress(upserted)
    return upserted


async def run_plex_sync(db) -> dict:
    """Full library sync from Plex; upserts + rescores. Raises on Plex being unconfigured."""
    row = db.query(Integration).filter_by(name="plex").first()
    if not row or not row.enabled:
        raise ValueError("Plex integration not enabled")

    from app.services import tasks
    task_id = tasks.create_task("plex_sync", "Syncing Plex library")
    try:
        from app.integrations.plex import PlexIntegration
        from app.services.secret_box import decrypt
        plex = PlexIntegration(row.url, decrypt(row.api_key) or "")
        weights = _get_setting(db, "scoring_weights", ScoringWeights)
        profiles = load_scoring_profiles(db)

        items = await plex.fetch_media_items()
        tasks.update_task(task_id, total=len(items))
        # SCAL-02 (v0.34.0): bulk upsert is sync ORM — run off the event loop so
        # a 100k+ library doesn't freeze every async request mid-sync.
        # Progress callback omitted inside the thread (not loop-safe); one update after.
        import asyncio
        upserted = await asyncio.to_thread(
            upsert_media_items, db, items, weights, profiles, None)
        tasks.update_task(task_id, current=upserted)

        _set_setting(db, "last_synced", datetime.utcnow().isoformat())
        db.commit()

        cleanup = _get_setting(db, "cleanup", CleanupSettings)
        protected = 0
        watch_protected = 0
        if cleanup.protect_requested:
            tasks.update_task(task_id, message="Refreshing Seerr protection…")
            try:
                protected = await refresh_seerr_protection(db)
            except Exception as e:
                logger.warning(f"Seerr protection refresh failed (non-fatal): {e}")
        if cleanup.protect_other_users:
            tasks.update_task(task_id, message="Refreshing Tautulli multi-user protection…")
            try:
                watch_protected = await refresh_tautulli_watch_protection(db)
            except Exception as e:
                logger.warning(f"Tautulli watch protection refresh failed (non-fatal): {e}")
        seeding_protected = 0
        if cleanup.protect_seeding_torrents:
            tasks.update_task(task_id, message="Refreshing seeding-torrent protection…")
            try:
                seeding_protected = await refresh_seeding_protection(db)
            except Exception as e:
                logger.warning(f"Seeding protection refresh failed (non-fatal): {e}")

        tasks.update_task(task_id, message="Linking *arr IDs…")
        try:
            from app.services.arr_link import link_arr_ids
            linked = await link_arr_ids(db)
        except Exception as e:
            logger.warning(f"arr-link failed (non-fatal): {e}")
            linked = {}

        tasks.finish_task(task_id, "done", f"Synced {upserted} item(s)")
        return {"synced": upserted, "protected": protected,
                "watch_protected": watch_protected, "seeding_protected": seeding_protected,
                "linked": linked}
    except Exception as e:
        tasks.finish_task(task_id, "failed", str(e))
        raise


async def refresh_seerr_protection(db) -> int:
    """Mark media items matching recent Seerr requests as protected. Resets prior
    flags first so items fall out of protection when requests age out. No-op
    (returns 0) when Seerr isn't configured."""
    row = db.query(Integration).filter_by(name="seerr", enabled=True).first()
    if not row or not row.url or not row.api_key:
        return 0

    from app.integrations.seerr import SeerrIntegration
    from app.services.secret_box import decrypt
    seerr = SeerrIntegration(row.url, decrypt(row.api_key) or "")
    requested = await seerr.get_requested_titles()

    db.query(MediaItem).filter(MediaItem.protected.is_(True)).update({"protected": False})

    count = 0
    for req in requested:
        title = req["title"]
        if req["media_type"] == "movie":
            q = db.query(MediaItem).filter(MediaItem.media_type == "movie", MediaItem.title.ilike(title))
            if req["year"]:
                q = q.filter(MediaItem.year.in_((req["year"] - 1, req["year"], req["year"] + 1)))
        else:
            q = db.query(MediaItem).filter(MediaItem.media_type == "episode",
                                           MediaItem.parent_title.ilike(title))
        count += q.update({"protected": True}, synchronize_session=False)
    db.commit()
    logger.info(f"Seerr protection: {count} item(s) protected across {len(requested)} request(s)")
    return count


async def refresh_tautulli_watch_protection(db) -> int:
    """Mark items watched by another Tautulli user within N days as watch_protected."""
    cleanup = _get_setting(db, "cleanup", CleanupSettings)
    if not cleanup.protect_other_users:
        return 0
    row = db.query(Integration).filter_by(name="tautulli", enabled=True).first()
    if not row or not row.url or not row.api_key:
        return 0

    from app.integrations.tautulli import TautulliIntegration
    from app.services.secret_box import decrypt
    tautulli = TautulliIntegration(row.url, decrypt(row.api_key) or "")
    history = await tautulli.get_recent_history(days=cleanup.other_user_watch_days)
    primary = (cleanup.primary_tautulli_user or "").strip().lower()

    protect_keys: set[str] = set()
    for h in history:
        name = (h.get("friendly_name") or h.get("user") or "").strip().lower()
        if primary and name == primary:
            continue
        if not name:
            continue
        rk = h.get("rating_key")
        if rk:
            protect_keys.add(str(rk))

    db.query(MediaItem).filter(MediaItem.watch_protected.is_(True)).update(
        {"watch_protected": False}, synchronize_session=False)

    count = 0
    if protect_keys:
        keys = list(protect_keys)
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            count += (db.query(MediaItem)
                      .filter(MediaItem.plex_rating_key.in_(chunk))
                      .update({"watch_protected": True}, synchronize_session=False))
    db.commit()
    logger.info(f"Tautulli watch protection: {count} item(s) protected "
                f"from {len(protect_keys)} rating key(s) in history "
                f"(last {cleanup.other_user_watch_days}d)")
    return count


def _is_seeding_path(file_path: str, seeding_paths: set[str]) -> bool:
    """A MediaItem's file is inside a seeding torrent if its path exactly matches
    a torrent's content path (single-file torrent) or lives under one (a torrent
    directory containing the file). Pure, unit-tested."""
    for p in seeding_paths:
        if file_path == p or file_path.startswith(p.rstrip("/") + "/"):
            return True
    return False


async def refresh_seeding_protection(db) -> int:
    """Mark media items whose file lives inside an actively-seeding torrent as
    seeding_protected (LIB-05). No-op (returns 0) when the setting is off or no
    download client is enabled.

    Fail-soft: a positive answer is required from EVERY enabled download client
    this cycle — if any is unreachable, the whole refresh is aborted (prior
    flags are left untouched) rather than clearing protection based on
    incomplete data, mirroring the orphan-cleanup fail-soft rule."""
    cleanup = _get_setting(db, "cleanup", CleanupSettings)
    if not cleanup.protect_seeding_torrents:
        return 0

    from app.api.v1.integrations import DOWNLOAD_CLIENT_NAMES
    from app.api.v1.integrations import _get_client as _download_client
    rows = (db.query(Integration)
            .filter(Integration.name.in_(DOWNLOAD_CLIENT_NAMES), Integration.enabled.is_(True))
            .all())
    if not rows:
        return 0

    seeding_paths: set[str] = set()
    for row in rows:
        client = _download_client(row)
        paths = await client.get_seeding_paths()
        if paths is None:
            logger.warning(f"Seeding protection refresh aborted — {row.name} unreachable "
                           f"(fail-soft: leaving prior protection flags untouched)")
            return 0
        seeding_paths |= paths

    db.query(MediaItem).filter(MediaItem.seeding_protected.is_(True)).update(
        {"seeding_protected": False}, synchronize_session=False)

    count = 0
    if seeding_paths:
        items = (db.query(MediaItem)
                 .filter(MediaItem.file_path.isnot(None))
                 .all())
        matched_ids = [item.id for item in items if _is_seeding_path(item.file_path, seeding_paths)]
        for i in range(0, len(matched_ids), 500):
            chunk = matched_ids[i:i + 500]
            count += (db.query(MediaItem)
                      .filter(MediaItem.id.in_(chunk))
                      .update({"seeding_protected": True}, synchronize_session=False))
    db.commit()
    logger.info(f"Seeding protection: {count} item(s) protected across "
                f"{len(seeding_paths)} seeding torrent(s)")
    return count
