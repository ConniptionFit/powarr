"""Failed-import detection & confidence matching against the connected *arr apps.

Independent of the deletion-scoring flow: operates on FailedImport rows only, sharing
the *arr integration clients. Confidence blends queue/history/library heuristics with
an optional local-LLM signal (never the sole source of truth)."""
import asyncio
import json
import logging
import os
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
OPEN_STATUSES = ("suggested", "auto_resolved", "accepted", "rejected", "orphan_pending")

_SEASON_EP_RE = re.compile(r"[sS](\d{1,2})[eE](\d{1,3})")
_SEASON_RANGE_RE = re.compile(r"\b[sS](\d{1,2})\s*-\s*[sS]?(\d{1,2})\b")
_SEASON_ONLY_RE = re.compile(r"\b(?:[sS]|[sS]eason[ ._-])(\d{1,2})\b")
_COMPLETE_RE = re.compile(r"\b(complete|collection|full[ ._-]?series)\b", re.IGNORECASE)
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


def _parse_release_numbers(title: str) -> dict:
    """Best-effort numeric extraction from a release name.
    Returns {"season", "episode", "absolute", "pack_seasons", "complete"}.
    The absolute candidate is the last standalone 2-4 digit number once S/E markers,
    years and quality junk are stripped — the common anime style ("Show - 1047 [Group]").
    A release with a season marker but no episode (S03, Season 3, S01-S03) or a
    complete-series marker is a pack: pack_seasons holds the covered seasons
    (None + complete=True = whole show)."""
    t = title or ""
    season = episode = None
    pack_seasons = None
    complete = bool(_COMPLETE_RE.search(t))
    m = _SEASON_EP_RE.search(t)
    if m:
        season, episode = int(m.group(1)), int(m.group(2))
    else:
        mr = _SEASON_RANGE_RE.search(t)
        if mr:
            lo, hi = sorted((int(mr.group(1)), int(mr.group(2))))
            if hi - lo <= 50:
                pack_seasons = set(range(lo, hi + 1))
        else:
            ms = _SEASON_ONLY_RE.search(t)
            if ms:
                pack_seasons = {int(ms.group(1))}
    cleaned = re.sub(r"[._\-\[\]()+]", " ", t)
    cleaned = _SEASON_EP_RE.sub(" ", cleaned)
    cleaned = _JUNK_RE.sub(" ", cleaned)
    cleaned = _YEAR_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\b[eE][pP]?(?=\d)", " ", cleaned)  # "E1047" / "Ep47" → bare number
    nums = re.findall(r"\b(\d{2,4})\b", cleaned)
    absolute = int(nums[-1]) if nums else None
    return {"season": season, "episode": episode, "absolute": absolute,
            "pack_seasons": pack_seasons, "complete": complete}


def _se_label(season, episode) -> str:
    s = f"S{season:02d}" if season is not None else "S??"
    e = f"E{episode:02d}" if episode is not None else "E??"
    return s + e


def _numeric_se_score(parsed: dict, cand_season, cand_ep) -> tuple[float | None, list[str]]:
    """Standard season/episode comparison. None = nothing to compare (no corroboration)."""
    if parsed["episode"] is None or cand_ep is None:
        return None, ["no season/episode number parsed from release — title-only match"]
    if parsed["episode"] != cand_ep:
        return 0.0, [f"episode number mismatch ({_se_label(parsed['season'], parsed['episode'])} "
                     f"vs {_se_label(cand_season, cand_ep)})"]
    if parsed["season"] is not None and cand_season is not None and parsed["season"] != cand_season:
        # Hard penalty, not automatically disqualifying
        return 0.25, [f"episode number matched but season mismatch "
                      f"(S{parsed['season']:02d} vs S{cand_season:02d}) — hard penalty"]
    return 1.0, [f"season/episode {_se_label(cand_season, cand_ep)} matched"]


def score_pack_match(title_sim: float, target_seasons: set | None, complete: bool,
                     sibling_seasons: list[int], mapped_episodes: int | None,
                     total_episodes: int | None,
                     cfg: ImportMatchingSettings) -> tuple[float, bool, list[str], str]:
    """Pack-level score for a season/complete-series download (Sonarr).

    Numeric corroboration comes from the sibling queue records sharing the
    downloadId (season consistency) and, when available, file/episode coverage
    (manual-import preview vs the season's aired episode list). Full coverage
    earns full numeric credit and the rationale suggests an entire-season
    (or entire-series) import. Returns (score, has_numeric, parts, pack_label)."""
    if target_seasons:
        lo, hi = min(target_seasons), max(target_seasons)
        label = f"S{lo:02d}" if lo == hi else f"S{lo:02d}-S{hi:02d}"
    else:
        label = "complete series"
    suggestion = "entire-series import" if (complete and not target_seasons) else "entire-season import"
    parts = [f"season pack detected ({label})"]

    numeric: float | None = None
    if sibling_seasons and target_seasons and any(s not in target_seasons for s in sibling_seasons):
        outside = sorted({s for s in sibling_seasons if s not in target_seasons})
        numeric = 0.25
        parts.append(f"queue maps episodes outside {label} (season(s) "
                     f"{', '.join(str(s) for s in outside)}) — hard penalty")
    elif mapped_episodes is not None and total_episodes:
        ratio = mapped_episodes / total_episodes
        if ratio >= 0.9:
            numeric = 1.0
            parts.append(f"{mapped_episodes}/{total_episodes} episodes of {label} present in the "
                         f"download — {suggestion} suggested (accepting imports them all)")
        elif ratio >= 0.5:
            numeric = 0.75
            parts.append(f"partial pack coverage ({mapped_episodes}/{total_episodes} episodes of {label})")
        else:
            numeric = 0.5
            parts.append(f"sparse pack coverage ({mapped_episodes}/{total_episodes} episodes of {label})")
    elif sibling_seasons:
        numeric = 0.75
        parts.append(f"{len(sibling_seasons)} queue record(s) map into {label} (full coverage unverified)")
    else:
        parts.append("no queue/episode data to corroborate the pack — title-only match")

    if numeric is None:
        return round(title_sim, 3), False, parts, label
    total_w = cfg.title_weight + cfg.number_weight
    score = (cfg.title_weight * title_sim + cfg.number_weight * numeric) / (total_w or 1.0)
    return round(score, 3), True, parts, label


async def _pack_coverage(client, download_id: str | None, series_id: int,
                         target_seasons: set | None) -> tuple[int | None, int | None]:
    """(mapped_episodes, total_episodes) for a pack — every step fails soft.
    total = aired episodes in the target seasons (whole show minus specials when
    complete); mapped = distinct in-scope episodes the manual-import preview maps
    the download's files to (the "all episodes present in the dir" check)."""
    try:
        eps = await client.get_episodes(series_id)
    except Exception as e:
        logger.info(f"Pack coverage: episode fetch failed (non-fatal): {e}")
        return None, None
    now = datetime.utcnow()

    def _aired(e: dict) -> bool:
        ad = e.get("airDateUtc")
        if not ad:
            return False
        try:
            return datetime.fromisoformat(str(ad).replace("Z", "+00:00")).replace(tzinfo=None) <= now
        except ValueError:
            return False

    in_scope = [e for e in eps if _aired(e) and (
        e.get("seasonNumber") in target_seasons if target_seasons
        else (e.get("seasonNumber") or 0) > 0)]
    total = len(in_scope) or None
    scope_ids = {e.get("id") for e in in_scope}

    mapped = None
    if download_id and total:
        try:
            files = await client.get_manual_import(download_id)
            mapped_ids = set()
            for f in files:
                for e in f.get("episodes") or []:
                    if e.get("id") in scope_ids:
                        mapped_ids.add(e["id"])
            mapped = len(mapped_ids)
        except Exception as e:
            logger.info(f"Pack coverage: manual-import preview failed (non-fatal): {e}")
    return mapped, total


def score_episode_match(raw_title: str, episode: dict, series_type: str,
                        cfg: ImportMatchingSettings) -> tuple[float, bool, list[str]]:
    """Episode-level multi-variable score for a Sonarr queue record.

    Independently evaluates episode-title similarity (heaviest factor, non-overriding)
    and numeric corroboration — S/E for standard series, absoluteEpisodeNumber for
    anime (with fallback to S/E when the absolute mapping is unpopulated, and a
    sanity guard against stale Sonarr absolute numbers). Returns
    (score 0-1, has_numeric_corroboration, human-readable per-variable parts)."""
    parts: list[str] = []
    parsed = _parse_release_numbers(raw_title)

    ep_title = episode.get("title") or ""
    title_sim = title_similarity(raw_title, ep_title) if ep_title else 0.0
    if ep_title:
        parts.append(f"episode title similarity {round(title_sim * 100)}% vs “{ep_title}”")

    cand_season = episode.get("seasonNumber")
    cand_ep = episode.get("episodeNumber")
    cand_abs = episode.get("absoluteEpisodeNumber")
    is_anime = series_type == "anime" and cfg.anime_absolute_numbering

    numeric: float | None = None
    numeric_parts: list[str] = []
    if is_anime and cand_abs is not None and parsed["absolute"] is not None:
        # Guard: Sonarr absolute numbers can be stale for long-running anime —
        # past season 1 a true absolute number should exceed the relative episode number.
        implausible = (cand_season or 0) > 1 and cand_ep is not None and cand_abs <= cand_ep
        if parsed["absolute"] == cand_abs:
            if implausible:
                numeric = 0.5
                numeric_parts.append(
                    f"absolute episode #{cand_abs} matched but looks implausible "
                    f"(≤ episode {cand_ep} past season 1 — possibly stale Sonarr data) — down-weighted")
            else:
                numeric = 1.0
                numeric_parts.append(f"absolute episode #{cand_abs} matched (anime numbering)")
                if parsed["season"] is not None and cand_season is not None and parsed["season"] != cand_season:
                    numeric_parts.append(
                        f"season/episode mismatch ({_se_label(parsed['season'], parsed['episode'])} vs "
                        f"{_se_label(cand_season, cand_ep)}) explained by anime absolute numbering")
        else:
            # Absolute contradicts — an S/E match can still rescue it
            se_score, se_parts = _numeric_se_score(parsed, cand_season, cand_ep)
            if se_score == 1.0:
                numeric = 1.0
                numeric_parts.append(
                    f"absolute number mismatch (#{parsed['absolute']} vs #{cand_abs}) but " + se_parts[0])
            else:
                numeric = 0.0
                numeric_parts.append(f"absolute episode number mismatch (#{parsed['absolute']} vs #{cand_abs})")
    elif is_anime and cand_abs is None:
        # Absence of an absolute mapping ≠ mismatch — Sonarr simply hasn't populated it
        numeric, numeric_parts = _numeric_se_score(parsed, cand_season, cand_ep)
        if numeric is not None:
            numeric_parts.append("no absolute-number mapping in Sonarr — fell back to season/episode")
    else:
        numeric, numeric_parts = _numeric_se_score(parsed, cand_season, cand_ep)

    parts.extend(numeric_parts)
    if numeric is None:
        return round(title_sim, 3), False, parts
    total = cfg.title_weight + cfg.number_weight
    score = (cfg.title_weight * title_sim + cfg.number_weight * numeric) / (total or 1.0)
    return round(score, 3), True, parts


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


async def _match_record(app_name: str, rec: dict, history: list[dict], library: list[dict],
                        cfg: ImportMatchingSettings, ollama: OllamaSettings,
                        queue: list[dict] | None = None, client=None) -> dict:
    """Produce {matched_id, matched_title, confidence, heuristic_confidence,
    match_rationale, pack, llm_confidence, llm_rationale}. The rationale is a
    deterministic per-variable readout of the scorer's own comparisons — never an
    LLM output. queue/client enable pack corroboration (Sonarr season packs)."""
    id_key, _, title_key = APP_FIELDS[app_name]
    raw_title = rec.get("title") or ""
    lib_by_id = {item["id"]: item for item in library}

    matched_id = None
    matched_title = None
    confidence = 0.0
    parts: list[str] = []

    # 1. Queue record already mapped by the *arr app itself — strongest signal
    if rec.get(id_key) and rec[id_key] in lib_by_id:
        matched_id = rec[id_key]
        matched_title = lib_by_id[matched_id].get(title_key, "")
        sim = title_similarity(raw_title, matched_title)
        confidence = 0.55 + 0.45 * sim
        parts.append(f"{app_name} queue already maps this download to the library entry "
                     f"(series/media title similarity {round(sim * 100)}%)")
    else:
        # 2. Grab history with the same downloadId tells us what this download was grabbed for
        download_id = rec.get("downloadId")
        hist = next((h for h in history if download_id and h.get("downloadId") == download_id), None)
        if hist and hist.get(id_key) and hist[id_key] in lib_by_id:
            matched_id = hist[id_key]
            matched_title = lib_by_id[matched_id].get(title_key, "")
            sim = title_similarity(raw_title, matched_title)
            confidence = 0.45 + 0.45 * sim
            parts.append(f"grab history links this downloadId to the library entry "
                         f"(title similarity {round(sim * 100)}%)")
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
                parts.append(f"fuzzy library title match only ({round(best_score * 100)}% similarity)")

    if not matched_id:
        parts.append("no library match found")

    # Episode/pack-level refinement — Sonarr only
    pack_label = None
    parsed = _parse_release_numbers(raw_title)
    is_pack = (app_name == "sonarr" and matched_id and parsed["episode"] is None
               and (parsed["pack_seasons"] or parsed["complete"]))
    if is_pack:
        # Season/complete pack: corroborate via sibling queue records sharing the
        # downloadId (free) and file/episode coverage (one-time API calls per new row)
        download_id = rec.get("downloadId")
        siblings = ([r for r in queue if download_id and r.get("downloadId") == download_id]
                    if queue else [rec])
        sibling_seasons = [s for s in ((r.get("episode") or {}).get("seasonNumber")
                                       for r in siblings) if s is not None]
        mapped = total = None
        if client is not None:
            mapped, total = await _pack_coverage(client, download_id, matched_id,
                                                 parsed["pack_seasons"])
        if mapped is None and total and sibling_seasons:
            mapped = len([s for s in sibling_seasons
                          if not parsed["pack_seasons"] or s in parsed["pack_seasons"]])
        title_sim = title_similarity(raw_title, matched_title)
        pack_score, has_numeric, pack_parts, pack_label = score_pack_match(
            title_sim, parsed["pack_seasons"], parsed["complete"],
            sibling_seasons, mapped, total, cfg)
        parts.extend(pack_parts)
        confidence = 0.5 * confidence + 0.5 * pack_score
        if not has_numeric and confidence > cfg.title_only_cap:
            confidence = cfg.title_only_cap
            parts.append(f"no numeric corroboration — confidence capped at {cfg.title_only_cap:.2f}")
    elif app_name == "sonarr" and matched_id and rec.get("episode"):
        series_type = (lib_by_id.get(matched_id) or rec.get("series") or {}).get("seriesType") or ""
        ep_score, has_numeric, ep_parts = score_episode_match(raw_title, rec["episode"], series_type, cfg)
        parts.extend(ep_parts)
        confidence = 0.5 * confidence + 0.5 * ep_score
        if not has_numeric and confidence > cfg.title_only_cap:
            confidence = cfg.title_only_cap
            parts.append(f"no numeric corroboration — confidence capped at {cfg.title_only_cap:.2f}")

    heuristic_confidence = round(min(1.0, confidence), 3)
    match_rationale = "; ".join(parts)
    llm_confidence = None
    llm_rationale = None
    if matched_title and ollama.enabled and ollama.host and ollama.model:
        llm_context = f"Source app: {app_name}. Queue error: {_queue_messages(rec)[:200]}"
        if pack_label:
            llm_context += (f" This download is a season pack ({pack_label}) — "
                            "accepting it imports every mappable file, not one episode.")
        llm = await llm_assist.review_match(
            ollama.host, ollama.model, raw_title, matched_title,
            det_summary=f"{match_rationale} (heuristic confidence {heuristic_confidence})",
            context=llm_context,
            api_style=ollama.api_style, template=ollama.match_prompt,
            verbosity=ollama.verbosity, model_size=ollama.model_size,
            keep_alive_minutes=ollama.keep_alive_minutes,
            reply_format=ollama.reply_format, confidence_style=ollama.confidence_style)
        if llm:
            llm_confidence = round(max(0.0, min(1.0, heuristic_confidence + llm["confidence_adjustment"])), 3)
            llm_rationale = f"[{'agrees' if llm['agrees'] else 'disagrees'}] {llm['rationale']}"
            confidence = 0.7 * confidence + 0.3 * llm_confidence

    return {
        "matched_id": matched_id,
        "matched_title": matched_title,
        "confidence": round(min(1.0, confidence), 3),
        "heuristic_confidence": heuristic_confidence,
        "match_rationale": match_rationale,
        "pack": pack_label,
        "llm_confidence": llm_confidence,
        "llm_rationale": llm_rationale,
    }


def decide_orphans(download_ids: set[str], client_results: list[set[str] | None]) -> set[str] | None:
    """Positive-confirmation orphan decision (pure, unit-tested).

    client_results holds one entry per enabled download client: the lowercased
    subset of download_ids it reported present, or None if that client didn't
    answer. Any None — or no clients at all — aborts the decision (returns None):
    a download is only "orphaned" when every configured client confirmed it's gone.
    Never infer absence from an error."""
    if not client_results or any(r is None for r in client_results):
        return None
    present: set[str] = set().union(*client_results)
    return {d for d in download_ids if d.lower() not in present}


def orphan_fs_state(output_path: str | None) -> str:
    """Filesystem leg of the orphan presence check (pure, unit-tested).

    "present"  — the queue record's output path still exists on disk (the file
                 landed even though the torrent is gone) → not an orphan.
    "absent"   — the path is confirmed gone.
    "unknown"  — no output path was recorded; nothing to check.
    "error"    — the path couldn't be stat'ed (permissions, mount trouble) —
                 same rule as an unreachable client: never read an error as "gone".
    An unmounted media path stats as FileNotFoundError → "absent", which just
    falls back to the client-only decision (v0.6.0 behavior)."""
    if not output_path:
        return "unknown"
    try:
        os.stat(output_path)
        return "present"
    except (FileNotFoundError, NotADirectoryError):
        return "absent"
    except OSError:
        return "error"


def decide_orphan_status(fs_state: str, auto_purge: bool) -> str | None:
    """Second gate after decide_orphans (pure, unit-tested): fold in the
    filesystem check and the auto-purge toggle. None = leave the row alone."""
    if fs_state in ("present", "error"):
        return None
    return "orphaned" if auto_purge else "orphan_pending"


def _row_output_path(row) -> str | None:
    try:
        return json.loads(row.raw_metadata or "{}").get("outputPath")
    except (ValueError, TypeError):
        return None


async def _check_orphans(db, cfg: ImportMatchingSettings, summary: dict) -> None:
    """Pending rows whose download vanished from every configured download client
    (and whose output path isn't on disk) can never be completed. Default: mark
    them orphan_pending and let the user confirm in triage; with orphan_auto_purge
    on they go straight to orphaned (terminal).
    Runs inside the existing scan cycle: one batched presence query per client."""
    from app.api.v1.integrations import DOWNLOAD_CLIENT_NAMES
    from app.api.v1.integrations import _get_client as _download_client
    rows = db.query(FailedImport).filter(
        FailedImport.status.in_(("suggested", "resolve_failed")),
        FailedImport.download_id.isnot(None),
        FailedImport.download_id != "",
    ).all()
    if not rows:
        return
    clients = []
    for name in DOWNLOAD_CLIENT_NAMES:
        row = db.query(Integration).filter_by(name=name, enabled=True).first()
        if row and row.url:
            clients.append((name, _download_client(row)))
    if not clients:
        return
    ids = {r.download_id.lower() for r in rows}
    results: list[set[str] | None] = []
    for name, client in clients:
        found = await client.check_downloads(ids)
        if found is None:
            logger.warning(f"Orphan check: {name} unreachable — skipping orphan cleanup this cycle")
        results.append(found)
    orphaned = decide_orphans(ids, results)
    if not orphaned:
        return
    now = datetime.utcnow()
    for row in rows:
        if row.download_id.lower() not in orphaned:
            continue
        fs = orphan_fs_state(_row_output_path(row))
        new_status = decide_orphan_status(fs, cfg.orphan_auto_purge)
        if new_status is None:
            if fs == "present":
                logger.info(f"Orphan check: '{row.raw_title}' ({row.source_app}) gone from all "
                            f"download clients but its output path still exists on disk — not orphaned")
            else:
                logger.warning(f"Orphan check: couldn't stat output path for '{row.raw_title}' "
                               f"({row.source_app}) — skipping the orphan decision this cycle")
            continue
        row.status = new_status
        if new_status == "orphaned":
            row.resolved_at = now
            row.message = ((row.message + " | ") if row.message else "") + \
                "Download no longer exists in any download client"
            summary["orphaned"] += 1
            logger.info(f"Orphan check: '{row.raw_title}' ({row.source_app}) gone from "
                        f"all download clients — marked orphaned (auto-purge on)")
        else:
            row.message = ((row.message + " | ") if row.message else "") + \
                "Download no longer exists in any download client — confirm to mark orphaned"
            summary["orphan_pending"] += 1
            logger.info(f"Orphan check: '{row.raw_title}' ({row.source_app}) gone from "
                        f"all download clients — awaiting orphan confirmation")
    db.commit()


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
                     "below_floor": 0, "in_grace": 0, "closed_external": 0, "verified": 0,
                     "resolve_failed": 0, "orphaned": 0, "orphan_pending": 0}
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

                match = await _match_record(app_name, rec, history, library, cfg, ollama,
                                            queue=queue, client=client)
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
                        "match_rationale": match["match_rationale"],
                        "pack": match["pack"],
                    }),
                    matched_title=match["matched_title"],
                    matched_id=match["matched_id"],
                    confidence=match["confidence"],
                    heuristic_confidence=match["heuristic_confidence"],
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
        await _check_orphans(db, cfg, summary)
    finally:
        db.close()

    if any(summary[k] for k in ("new_suggestions", "auto_resolved", "closed_external",
                                "resolve_failed", "orphaned", "orphan_pending")):
        publish({"type": "scan", **{k: summary[k] for k in
                                    ("new_suggestions", "auto_resolved", "closed_external",
                                     "resolve_failed", "orphaned", "orphan_pending")}})
        await _notify_scan(summary)
    return summary


async def _notify_scan(summary: dict) -> None:
    """Optional ntfy push after a cycle that changed anything. Fails soft."""
    try:
        from app.services import notifier
        db = SessionLocal()
        try:
            parts = []
            if summary["new_suggestions"]:
                parts.append(f"{summary['new_suggestions']} new suggestion(s)")
            if summary["auto_resolved"]:
                parts.append(f"{summary['auto_resolved']} auto-resolved")
            if summary["resolve_failed"]:
                parts.append(f"{summary['resolve_failed']} push failure(s)")
            if summary.get("orphaned"):
                parts.append(f"{summary['orphaned']} orphaned (download gone)")
            if summary.get("orphan_pending"):
                parts.append(f"{summary['orphan_pending']} confirmed missing — awaiting orphan confirmation")
            if not parts:
                return
            priority = "high" if summary["resolve_failed"] else "default"
            await notifier.notify(db, "Powarr: failed-import scan",
                                  ", ".join(parts) + " — review in Cleanup → Failed Imports",
                                  priority=priority, tags="arrows_counterclockwise")
        finally:
            db.close()
    except Exception as e:
        logger.info(f"Scan notification failed (non-fatal): {e}")


def llm_run_active() -> bool:
    # Delegates to the shared single-flight slot in llm_assist, so a batch run and
    # the Cleanup page's per-item explain can never hit the LLM host concurrently.
    return llm_assist.slot_active()


async def llm_rescore(ids: list[int] | None = None, limit: int = 50) -> dict:
    """On-demand LLM scoring of failed-import rows — either the given ids, or the
    backlog of open rows that never got an LLM signal. Sequential (one call at a
    time) to be gentle on the LLM host. Publishes an SSE event when done."""
    if not llm_assist.acquire_slot():
        return {"scored": 0, "skipped": 0, "message": "An LLM run is already in progress"}
    scored = skipped = 0
    try:
        db = SessionLocal()
        try:
            _, ollama = load_settings(db)
            if not (ollama.enabled and ollama.host and ollama.model):
                return {"scored": 0, "skipped": 0, "message": "LLM assist is not configured/enabled"}
            q = db.query(FailedImport)
            if ids:
                q = q.filter(FailedImport.id.in_(ids))
            else:
                q = q.filter(FailedImport.status.in_(("suggested", "resolve_failed")),
                             FailedImport.llm_confidence.is_(None))
            rows = q.order_by(FailedImport.created_at.desc()).limit(limit).all()
            for row in rows:
                if not row.matched_title:
                    skipped += 1
                    continue
                if row.heuristic_confidence is None:
                    row.heuristic_confidence = row.confidence
                det_summary = (row.match_rationale or "series/title heuristics only") + \
                    f" (heuristic confidence {row.heuristic_confidence})"
                llm = await llm_assist.review_match(
                    ollama.host, ollama.model, row.raw_title, row.matched_title,
                    det_summary=det_summary,
                    context=f"Source app: {row.source_app}. Queue error: {(row.message or '')[:200]}",
                    api_style=ollama.api_style, template=ollama.match_prompt,
                    verbosity=ollama.verbosity, model_size=ollama.model_size,
                    keep_alive_minutes=ollama.keep_alive_minutes,
                    reply_format=ollama.reply_format, confidence_style=ollama.confidence_style)
                if not llm:
                    skipped += 1
                    continue
                row.llm_confidence = round(
                    max(0.0, min(1.0, row.heuristic_confidence + llm["confidence_adjustment"])), 3)
                row.llm_rationale = f"[{'agrees' if llm['agrees'] else 'disagrees'}] {llm['rationale']}"
                row.confidence = round(min(1.0, 0.7 * row.heuristic_confidence + 0.3 * row.llm_confidence), 3)
                db.commit()
                scored += 1
                if ollama.batch_delay_ms > 0:
                    await asyncio.sleep(ollama.batch_delay_ms / 1000)
        finally:
            db.close()
    finally:
        llm_assist.release_slot()
    logger.info(f"LLM rescore: {scored} scored, {skipped} skipped")
    publish({"type": "llm_run", "scored": scored, "skipped": skipped})
    return {"scored": scored, "skipped": skipped, "message": f"{scored} scored, {skipped} skipped"}


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
