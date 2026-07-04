"""Failed-import detection & confidence matching against the connected *arr apps.

Independent of the deletion-scoring flow: operates on FailedImport rows only, sharing
the *arr integration clients. Confidence blends queue/history/library heuristics with
an optional local-LLM signal (never the sole source of truth)."""
import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from app.database import SessionLocal
from app.models.app_setting import AppSetting
from app.models.failed_import import FailedImport
from app.models.integration import Integration
from app.schemas.settings import ImportMatchingSettings, OllamaSettings
from app.services import llm_assist

logger = logging.getLogger("powarr")

STUCK_STATES = {"importPending", "importFailed", "importBlocked"}
OPEN_STATUSES = ("suggested", "auto_resolved", "accepted", "rejected")

_SEASON_EP_RE = re.compile(r"[sS](\d{1,2})[eE](\d{1,3})")
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_JUNK_RE = re.compile(
    r"\b(2160p|1080p|720p|480p|x264|x265|h264|h265|hevc|web[- ]?dl|webrip|bluray|blu-ray|"
    r"remux|hdtv|dvdrip|proper|repack|amzn|dsnp|nf|atvp|hulu|flac|mp3|320|v0|aac|dts|"
    r"truehd|atmos|dv|hdr(10)?|10bit|8bit|multi|vostfr|internal)\b", re.IGNORECASE)

# --- SSE fan-out: scan cycles publish events, /imports/events subscribers consume them ---
_subscribers: set[asyncio.Queue] = set()


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def publish(event: dict) -> None:
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


def load_settings(db) -> tuple[ImportMatchingSettings, OllamaSettings]:
    def _load(key, schema):
        row = db.query(AppSetting).filter_by(key=key).first()
        if not row or not row.value:
            return schema()
        return schema(**json.loads(row.value))
    return _load("import_matching", ImportMatchingSettings), _load("ollama", OllamaSettings)


def _get_client(name: str, row: Integration):
    if name == "sonarr":
        from app.integrations.sonarr import SonarrIntegration
        return SonarrIntegration(row.url, row.api_key)
    if name == "radarr":
        from app.integrations.radarr import RadarrIntegration
        return RadarrIntegration(row.url, row.api_key)
    if name == "readarr":
        from app.integrations.readarr import ReadarrIntegration
        return ReadarrIntegration(row.url, row.api_key)
    from app.integrations.lidarr import LidarrIntegration
    return LidarrIntegration(row.url, row.api_key)


def _normalize(title: str) -> str:
    t = (title or "").lower()
    t = re.sub(r"[._\-\[\]()+]", " ", t)
    t = _JUNK_RE.sub(" ", t)
    t = _SEASON_EP_RE.sub(" ", t)
    t = _YEAR_RE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def title_similarity(release_title: str, library_title: str) -> float:
    a, b = _normalize(release_title), _normalize(library_title)
    if not a or not b:
        return 0.0
    ratio = SequenceMatcher(None, a, b).ratio()
    # Substring bonus: library title fully contained in the release name is a strong signal
    if b in a or a in b:
        ratio = max(ratio, 0.85)
    return min(1.0, ratio)


def _is_stuck(rec: dict, include_stalled: bool = False) -> bool:
    if rec.get("trackedDownloadState") in STUCK_STATES:
        return True
    if rec.get("status") == "completed" and rec.get("trackedDownloadStatus") == "warning":
        return True
    if include_stalled and "stalled" in (rec.get("errorMessage") or "").lower():
        return True
    return False


def _within_grace(rec: dict, grace_minutes: int) -> bool:
    """True if the queue item is younger than the grace period (skip it — *arr may self-retry)."""
    if grace_minutes <= 0:
        return False
    added = rec.get("added")
    if not added:
        return False
    try:
        added_dt = datetime.fromisoformat(str(added).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return False
    return datetime.utcnow() - added_dt < timedelta(minutes=grace_minutes)


def _queue_messages(rec: dict) -> str:
    msgs = []
    for sm in rec.get("statusMessages", []) or []:
        msgs.extend(sm.get("messages", []) or [])
        if sm.get("title"):
            msgs.append(sm["title"])
    if rec.get("errorMessage"):
        msgs.append(rec["errorMessage"])
    return "; ".join(msgs)[:1000]


# Per-app field names: (queue/history media-id key, library fetch method, library title key)
APP_FIELDS = {
    "sonarr": ("seriesId", "get_series", "title"),
    "radarr": ("movieId", "get_movies", "title"),
    "lidarr": ("artistId", "get_artists", "artistName"),
    "readarr": ("authorId", "get_authors", "authorName"),
}


async def _match_record(app_name: str, rec: dict, history: list[dict],
                        library: list[dict], ollama: OllamaSettings) -> dict:
    """Produce {matched_id, matched_title, confidence, llm_confidence, llm_rationale}."""
    id_key, _, title_key = APP_FIELDS[app_name]
    raw_title = rec.get("title") or ""
    lib_by_id = {item["id"]: item for item in library}

    matched_id = None
    matched_title = None
    confidence = 0.0

    # 1. Queue record already mapped by the *arr app itself — strongest signal
    if rec.get(id_key) and rec[id_key] in lib_by_id:
        matched_id = rec[id_key]
        matched_title = lib_by_id[matched_id].get(title_key, "")
        confidence = 0.55 + 0.45 * title_similarity(raw_title, matched_title)
    else:
        # 2. Grab history with the same downloadId tells us what this download was grabbed for
        download_id = rec.get("downloadId")
        hist = next((h for h in history if download_id and h.get("downloadId") == download_id), None)
        if hist and hist.get(id_key) and hist[id_key] in lib_by_id:
            matched_id = hist[id_key]
            matched_title = lib_by_id[matched_id].get(title_key, "")
            confidence = 0.45 + 0.45 * title_similarity(raw_title, matched_title)
        else:
            # 3. Fuzzy title match against the library
            best, best_score = None, 0.0
            for item in library:
                s = title_similarity(raw_title, item.get(title_key, ""))
                if s > best_score:
                    best, best_score = item, s
            if best and best_score > 0:
                matched_id = best["id"]
                matched_title = best.get(title_key, "")
                confidence = 0.75 * best_score  # fuzzy-only match caps below auto-resolve range

    llm_confidence = None
    llm_rationale = None
    if matched_title and ollama.enabled and ollama.host and ollama.model:
        llm = await llm_assist.score_candidate(
            ollama.host, ollama.model, raw_title, matched_title,
            context=f"Source app: {app_name}. Queue error: {_queue_messages(rec)[:200]}",
            api_style=ollama.api_style)
        if llm:
            llm_confidence = llm["confidence"]
            llm_rationale = llm["rationale"]
            confidence = 0.7 * confidence + 0.3 * llm_confidence

    return {
        "matched_id": matched_id,
        "matched_title": matched_title,
        "confidence": round(min(1.0, confidence), 3),
        "llm_confidence": llm_confidence,
        "llm_rationale": llm_rationale,
    }


def _close_stale_rows(db, app_name: str, queue: list[dict], summary: dict) -> None:
    """Suggested rows whose download left the queue on its own no longer need triage."""
    queue_download_ids = {rec.get("downloadId") for rec in queue if rec.get("downloadId")}
    queue_item_ids = {str(rec.get("id", "")) for rec in queue}
    open_rows = db.query(FailedImport).filter(
        FailedImport.source_app == app_name,
        FailedImport.status == "suggested",
    ).all()
    for row in open_rows:
        still_queued = (row.download_id in queue_download_ids if row.download_id
                        else row.queue_item_id in queue_item_ids)
        if not still_queued:
            row.status = "closed_external"
            row.resolved_at = datetime.utcnow()
            row.message = ((row.message + " | ") if row.message else "") + "Left the queue on its own"
            summary["closed_external"] += 1
    db.commit()


async def _verify_resolved(db, app_name: str, client, cfg: ImportMatchingSettings, summary: dict) -> None:
    """Confirm pushed imports actually landed: look for an import event in recent history.
    Unverified past the timeout → resolve_failed (surfaced back into triage)."""
    pending = db.query(FailedImport).filter(
        FailedImport.source_app == app_name,
        FailedImport.status.in_(("auto_resolved", "accepted")),
        FailedImport.verified.is_(None),
    ).all()
    if not pending:
        return
    try:
        history = await client.get_history(event_type=None)
    except Exception as e:
        logger.warning(f"Import verify: {app_name} history fetch failed: {e}")
        return
    imported_ids = {h.get("downloadId") for h in history
                    if "imported" in str(h.get("eventType", "")).lower() and h.get("downloadId")}
    now = datetime.utcnow()
    for row in pending:
        if row.download_id and row.download_id in imported_ids:
            row.verified = True
            summary["verified"] += 1
        elif row.resolved_at and now - row.resolved_at > timedelta(minutes=cfg.verify_timeout_minutes):
            row.verified = False
            row.status = "resolve_failed"
            row.message = ((row.message + " | ") if row.message else "") + \
                f"Import not confirmed in history within {cfg.verify_timeout_minutes} min"
            summary["resolve_failed"] += 1
            logger.warning(f"Import verify: '{row.raw_title}' ({app_name}) push not confirmed — marked resolve_failed")
    db.commit()


async def scan_once() -> dict:
    """One detection cycle across all enabled *arr apps. Returns a per-app summary."""
    summary: dict = {"scanned": [], "new_suggestions": 0, "auto_resolved": 0, "skipped_existing": 0,
                     "below_floor": 0, "in_grace": 0, "closed_external": 0, "verified": 0, "resolve_failed": 0}
    db = SessionLocal()
    try:
        cfg, ollama = load_settings(db)
        for app_name in ("sonarr", "radarr", "lidarr", "readarr"):
            if not getattr(cfg, f"{app_name}_enabled", True):
                continue
            row = db.query(Integration).filter_by(name=app_name, enabled=True).first()
            if not row or not row.url or not row.api_key:
                continue
            client = _get_client(app_name, row)
            try:
                queue = await client.get_queue()
            except Exception as e:
                logger.warning(f"Import scan: {app_name} queue fetch failed: {e}")
                continue

            _close_stale_rows(db, app_name, queue, summary)
            await _verify_resolved(db, app_name, client, cfg, summary)

            stuck = [rec for rec in queue if _is_stuck(rec, cfg.include_stalled)]
            summary["scanned"].append({"app": app_name, "queue": len(queue), "stuck": len(stuck)})
            if not stuck:
                continue

            try:
                history = await client.get_history()
            except Exception as e:
                logger.warning(f"Import scan: {app_name} history fetch failed: {e}")
                history = []
            _, lib_method, _ = APP_FIELDS[app_name]
            try:
                library = await getattr(client, lib_method)()
            except Exception as e:
                logger.warning(f"Import scan: {app_name} library fetch failed: {e}")
                library = []

            for rec in stuck:
                if _within_grace(rec, cfg.grace_period_minutes):
                    summary["in_grace"] += 1
                    continue
                download_id = rec.get("downloadId")
                queue_item_id = str(rec.get("id", ""))
                dedupe = db.query(FailedImport).filter(
                    FailedImport.source_app == app_name,
                    FailedImport.status.in_(OPEN_STATUSES),
                )
                if download_id:
                    dedupe = dedupe.filter(FailedImport.download_id == download_id)
                else:
                    dedupe = dedupe.filter(FailedImport.queue_item_id == queue_item_id)
                if dedupe.first():
                    summary["skipped_existing"] += 1
                    continue

                match = await _match_record(app_name, rec, history, library, ollama)
                if match["confidence"] < cfg.low_confidence_floor:
                    logger.info(
                        f"Import scan: '{rec.get('title')}' ({app_name}) below confidence floor "
                        f"({match['confidence']:.2f} < {cfg.low_confidence_floor}) — logged only")
                    summary["below_floor"] += 1
                    continue

                item = FailedImport(
                    source_app=app_name,
                    queue_item_id=queue_item_id,
                    download_id=download_id,
                    raw_title=rec.get("title") or "(unknown)",
                    raw_metadata=json.dumps({
                        "status": rec.get("status"),
                        "trackedDownloadState": rec.get("trackedDownloadState"),
                        "trackedDownloadStatus": rec.get("trackedDownloadStatus"),
                        "outputPath": rec.get("outputPath"),
                        "protocol": rec.get("protocol"),
                        "messages": _queue_messages(rec),
                    }),
                    matched_title=match["matched_title"],
                    matched_id=match["matched_id"],
                    confidence=match["confidence"],
                    llm_confidence=match["llm_confidence"],
                    llm_rationale=match["llm_rationale"],
                    status="suggested",
                    message=_queue_messages(rec)[:500] or None,
                )

                if (cfg.auto_resolve_enabled and match["matched_id"]
                        and match["confidence"] >= cfg.high_confidence_threshold and download_id):
                    result = await client.push_import_command(download_id, match["matched_id"])
                    if result["ok"]:
                        item.status = "auto_resolved"
                        item.resolved_at = datetime.utcnow()
                        summary["auto_resolved"] += 1
                        logger.info(f"Import scan: auto-resolved '{item.raw_title}' ({app_name}, "
                                    f"confidence {item.confidence:.2f}): {result['message']}")
                    item.message = result["message"]

                if item.status == "suggested":
                    summary["new_suggestions"] += 1
                db.add(item)
                db.commit()
    finally:
        db.close()

    if any(summary[k] for k in ("new_suggestions", "auto_resolved", "closed_external", "resolve_failed")):
        publish({"type": "scan", **{k: summary[k] for k in
                                    ("new_suggestions", "auto_resolved", "closed_external", "resolve_failed")}})
    return summary


async def poller_loop():
    """Background polling loop. Interval and enablement re-read each cycle, so settings
    changes apply without a restart. Never a tight loop: 60s minimum."""
    logger.info("Failed-import poller started")
    while True:
        interval = 300
        try:
            db = SessionLocal()
            try:
                cfg, _ = load_settings(db)
            finally:
                db.close()
            interval = max(60, int(cfg.poll_interval_seconds))
            if cfg.enabled:
                summary = await scan_once()
                if summary["new_suggestions"] or summary["auto_resolved"]:
                    logger.info(f"Import scan summary: {summary}")
        except asyncio.CancelledError:
            logger.info("Failed-import poller stopped")
            raise
        except Exception as e:
            logger.error(f"Import poller cycle failed: {e}")
        await asyncio.sleep(interval)
