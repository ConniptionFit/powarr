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
from typing import Any

from app.database import SessionLocal
from app.models.app_setting import AppSetting
from app.models.failed_import import FailedImport
from app.models.integration import Integration
from app.schemas.settings import ImportMatchingSettings, OllamaSettings
from app.services import llm_assist
from app.services.auto_eligible import passes_auto_thresholds

logger = logging.getLogger("powarr")

STUCK_STATES = {"importPending", "importFailed", "importBlocked"}
OPEN_STATUSES = ("suggested", "auto_resolved", "accepted", "rejected", "orphan_pending")
# Scan dedupe additions (v0.44.0): a resolve_failed row already sits in triage, and
# an orphaned row whose queue entry never left the *arr (still_in_queue) is the same
# dead download being re-detected — creating a fresh suggestion for either just loops
# the same release through the Import Queue forever (Prof single incident, 2026-07-11).

_SEASON_EP_RE = re.compile(r"[sS](\d{1,2})[eE](\d{1,3})")
_SEASON_RANGE_RE = re.compile(r"\b[sS](\d{1,2})\s*-\s*[sS]?(\d{1,2})\b")
_SEASON_ONLY_RE = re.compile(r"\b(?:[sS]|[sS]eason[ ._-])(\d{1,2})\b")
# FI-08 — plain numeric range with no S/E marker, the common anime batch-release
# style ("[Group] Show - 001-100 [1080p]"). Only tried when no season pattern
# matched (see _parse_release_numbers) and only ever consumed when the matched
# library series is seriesType=anime and the opt-in setting is on — never
# affects season-based shows, which always have an S/E or season marker instead.
_ABSOLUTE_RANGE_RE = re.compile(r"\b(\d{2,4})\s*-\s*(\d{2,4})\b")
_COMPLETE_RE = re.compile(r"\b(complete|collection|full[ ._-]?series)\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_JUNK_RE = re.compile(
    r"\b(2160p|1080p|720p|480p|576p|4k|uhd|x264|x265|h264|h265|hevc|avc|av1|"
    r"web[- ]?dl|webrip|bluray|blu[- ]?ray|bdrip|brrip|remux|hdtv|dvdrip|hdrip|"
    r"proper|repack|real|amzn|dsnp|nf|atvp|hulu|hmax|pmtv|itunes|"
    r"flac|alac|ape|wav|mp3|320|v0|v2|aac|dts|ac3|eac3|ddp?5\.?1|truehd|atmos|"
    r"dv|hdr(10)?(\+)?|dolby|vision|10bit|8bit|multi|dual|vostfr|internal|"
    r"sample|nfo|readnfo)\b", re.IGNORECASE)
# Music/book edition & media-format tags (v0.32.0) — never evidence for/against a match.
# Applied on Lidarr/Readarr release names (and optionally all apps for CD/vinyl noise).
# Separators include space/underscore/dot/hyphen so DELUXE_EDITION / Deluxe.Edition match.
_ED = r"[_\s.-]+"  # edition-phrase separator
_EDITION_JUNK_RE = re.compile(
    rf"\b(deluxe({_ED}edition)?|expanded({_ED}edition)?|limited({_ED}edition)?|"
    rf"special({_ED}edition)?|collector'?s?({_ED}edition)?|anniversary({_ED}edition)?|"
    rf"remaster(ed)?|remix|bonus|explicit|clean|instrumental|"
    rf"2cd|2disc|disc{_ED}?\d+|cd{_ED}?\d+|cd|dvd|bluray|vinyl|lp|ep|single|"
    rf"box{_ED}?set|digipak|japan(ese)?|eu|uk|us|fn|fathead)\b",
    re.IGNORECASE,
)
# Servarr "already have equal-or-better" rejection family (Sonarr episode /
# Radarr movie / Lidarr album|track). Also Lidarr's "Album already imported"
# which fires when the album is already in the library at equal-or-better quality.
_DOWNGRADE_RE = re.compile(
    r"not an upgrade|album already imported|movie already imported|"
    r"episode file already imported|already exists on disk",
    re.IGNORECASE,
)

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
    from app.services.secret_box import decrypt
    key = decrypt(row.api_key) or ""
    if name == "sonarr":
        from app.integrations.sonarr import SonarrIntegration
        return SonarrIntegration(row.url, key)
    if name == "radarr":
        from app.integrations.radarr import RadarrIntegration
        return RadarrIntegration(row.url, key)
    if name == "readarr":
        from app.integrations.readarr import ReadarrIntegration
        return ReadarrIntegration(row.url, key)
    from app.integrations.lidarr import LidarrIntegration
    return LidarrIntegration(row.url, key)


_QUALITY_GROUP_TAIL_RE = re.compile(
    r"(?i)\b(?:2160p|1080p|720p|480p|576p|4k|uhd|web-?dl|webrip|bluray|blu-?ray|"
    r"bdrip|brrip|remux|hdtv|dvdrip|x264|x265|h264|h265|hevc|av1)"
    r"[-.][A-Za-z][\w.-]{1,30}$"
)


def strip_release_junk(title: str, *, music: bool = False) -> str:
    """Deterministic release-name cleaner (v0.31.0+): quality/source/codec tags plus
    common uploader/release-group wrappers. Used before title similarity so groups
    like MeGusta / SubsPlease don't dilute the match.

    Conservative on trailing groups: only strip a hyphen/dot suffix when it follows
    a known quality token (e.g. ``1080p-MeGusta``), never a bare title word — that
    would eat episode titles like ``The.Winds.of.Winter``.

    ``music=True`` (Lidarr/Readarr) also strips edition/media-format tags
    (DELUXE, CD, 2CD, remaster, vinyl, …) that are not album-identity signals.
    """
    t = title or ""
    # Bracket tags: [SubsPlease], [A1B2C3D4], [1080p]
    t = re.sub(r"\[[^\]]{0,40}\]", " ", t)
    # Trailing parenthetical only when it looks like a hash/group, not a year
    t = re.sub(r"\((?!(?:19|20)\d{2}\))[^)]{0,40}\)$", " ", t)
    # Trailing -Group / .Group only after a quality/source token (replace the
    # whole match with just the quality token — drop the group suffix).
    t = _QUALITY_GROUP_TAIL_RE.sub(
        lambda m: re.split(r"[-.]", m.group(0))[0], t)
    t = _JUNK_RE.sub(" ", t)
    if music:
        t = _EDITION_JUNK_RE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def _normalize(title: str, *, is_release: bool = False, music: bool = False) -> str:
    t = title or ""
    if is_release:
        t = strip_release_junk(t, music=music)
    t = t.lower()
    t = re.sub(r"[._\-\[\]()+]", " ", t)
    # *arr library titles keep punctuation (commas, colons, apostrophes) that release
    # filenames never carry — left unstripped this breaks the substring-containment
    # bonus below on otherwise-exact title matches (e.g. "Life, Larry..." vs "Life
    # Larry..." from a dot-separated filename).
    t = re.sub(r"[,;:'’!?]", "", t)
    t = _JUNK_RE.sub(" ", t)
    if music and is_release:
        t = _EDITION_JUNK_RE.sub(" ", t)
    t = _SEASON_EP_RE.sub(" ", t)
    t = _YEAR_RE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def title_similarity(release_title: str, library_title: str, *,
                     source_app: str | None = None) -> float:
    music = source_app in ("lidarr", "readarr")
    a, b = _normalize(release_title, is_release=True, music=music), _normalize(library_title)
    if not a or not b:
        return 0.0
    ratio = SequenceMatcher(None, a, b).ratio()
    # Substring bonus: library title fully contained in the release name is a strong signal
    if b in a or a in b:
        ratio = max(ratio, 0.85)
    return min(1.0, ratio)


def extract_release_year(title: str) -> int | None:
    """First plausible 19xx/20xx year token in a release name, or None."""
    m = _YEAR_RE.search(title or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def candidate_year(lib_item: dict | None, title: str | None = None) -> int | None:
    """Year from a Sonarr/Radarr library row, falling back to a (YYYY) title suffix."""
    if lib_item:
        y = lib_item.get("year")
        if isinstance(y, int) and 1900 <= y <= 2100:
            return y
        if isinstance(y, str) and y.isdigit():
            yi = int(y)
            if 1900 <= yi <= 2100:
                return yi
    m = re.search(r"\(((?:19|20)\d{2})\)\s*$", title or "")
    if m:
        return int(m.group(1))
    return None


def format_alternate_titles(lib_item: dict | None, *, limit: int = 8) -> str:
    """Compact alternateTitles list from a Sonarr/Radarr library object."""
    if not lib_item:
        return ""
    alts = lib_item.get("alternateTitles") or []
    titles: list[str] = []
    seen: set[str] = set()
    primary = (lib_item.get("title") or lib_item.get("artistName")
               or lib_item.get("authorName") or "").strip().lower()
    for a in alts:
        t = (a.get("title") if isinstance(a, dict) else str(a) or "").strip()
        if not t:
            continue
        key = t.lower()
        if key == primary or key in seen:
            continue
        seen.add(key)
        titles.append(t)
        if len(titles) >= limit:
            break
    return ", ".join(titles)


def _parse_release_numbers(title: str) -> dict:
    """Best-effort numeric extraction from a release name.
    Returns {"season", "episode", "absolute", "pack_seasons", "complete", "absolute_range"}.
    The absolute candidate is the last standalone 2-4 digit number once S/E markers,
    years and quality junk are stripped — the common anime style ("Show - 1047 [Group]").
    A release with a season marker but no episode (S03, Season 3, S01-S03) or a
    complete-series marker is a pack: pack_seasons holds the covered seasons
    (None + complete=True = whole show). absolute_range (FI-08) is an anime
    batch-pack range like "001-100" with no S/E marker at all — only set when
    no season pattern matched."""
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
    # FI-08 — absolute episode-number pack range (anime batch releases with no
    # S/E marker at all, e.g. "001-100"). Only tried once every season-based
    # pattern above has failed, so it can never misfire on a normal S/E or
    # season-pack title. Guards against a year range ("2011-2015") and against
    # implausible spans (a >2000-episode "pack" is almost certainly two
    # unrelated numbers, not a real absolute range).
    absolute_range = None
    if season is None and pack_seasons is None and not complete:
        ar = _ABSOLUTE_RANGE_RE.search(t)
        if ar:
            lo, hi = sorted((int(ar.group(1)), int(ar.group(2))))
            is_year_pair = 1900 <= lo <= 2100 and 1900 <= hi <= 2100
            if not is_year_pair and hi > lo and (hi - lo) <= 2000:
                absolute_range = (lo, hi)
    cleaned = re.sub(r"[._\-\[\]()+]", " ", t)
    cleaned = _SEASON_EP_RE.sub(" ", cleaned)
    cleaned = _JUNK_RE.sub(" ", cleaned)
    cleaned = _YEAR_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\b[eE][pP]?(?=\d)", " ", cleaned)  # "E1047" / "Ep47" → bare number
    nums = re.findall(r"\b(\d{2,4})\b", cleaned)
    absolute = int(nums[-1]) if nums else None
    return {"season": season, "episode": episode, "absolute": absolute,
            "pack_seasons": pack_seasons, "complete": complete, "absolute_range": absolute_range}


def _se_label(season, episode) -> str:
    s = f"S{season:02d}" if season is not None else "S??"
    e = f"E{episode:02d}" if episode is not None else "E??"
    return s + e


def find_suspicious_files(candidates: list[dict], extensions: list[str]) -> list[str]:
    """Pure: filenames in a manual-import candidate list matching any of the given
    (case-insensitive) extensions. Unlike is_quality_downgrade, a single match is
    enough to flag the whole download — one malicious file hiding among otherwise
    legitimate ones is still a real risk. Returns the matched filenames (empty list
    = clean); extensions may be given with or without a leading dot."""
    exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions if e}
    if not exts:
        return []
    matches = []
    for f in candidates:
        path = f.get("path") or f.get("relativePath") or ""
        name = path.rsplit("/", 1)[-1]
        if not name:
            continue
        _, ext = os.path.splitext(name)
        if ext.lower() in exts:
            matches.append(name)
    return matches


def file_is_covered(candidate: dict) -> bool:
    """True when this manual-import candidate rejects as equal-or-better /
    already imported (not an upgrade). Pure, unit-tested."""
    reasons = [r.get("reason", "") for r in (candidate.get("rejections") or [])]
    return any(_DOWNGRADE_RE.search(r) for r in reasons)


def partition_import_candidates(candidates: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split manual-import candidates into (importable, covered).

    Importable = not covered by equal-or-better / already-imported rejections.
    Covered files are skipped at accept time (v0.32.0 partial import).
    """
    importable, covered = [], []
    for f in candidates:
        if file_is_covered(f):
            covered.append(f)
        else:
            importable.append(f)
    return importable, covered


def is_partial_import(candidates: list[dict]) -> bool:
    """True when some files are importable and some are already covered —
    a gap-fill pack/album (v0.32.0)."""
    if not candidates:
        return False
    importable, covered = partition_import_candidates(candidates)
    return bool(importable) and bool(covered)


def is_quality_downgrade(candidates: list[dict]) -> bool:
    """Pure: True when EVERY file in a manual-import candidate list rejects
    because the *arr app already has equal-or-better quality in the library.

    Covers Sonarr (\"Not an upgrade for existing episode file(s)\"), Radarr
    (movie equivalent), and Lidarr (\"Not an upgrade for existing album/track
    file(s)\" / \"Album already imported …\"). A release that will never
    successfully import as-is — safe to flag/auto-reject rather than clutter
    triage. A partial result (some files ok, some already-covered) returns
    False — only a clean all-files-covered release counts, since a mixed
    download may still be worth accepting for its new files."""
    if not candidates:
        return False
    for f in candidates:
        if not file_is_covered(f):
            return False
    return True


def queue_looks_like_quality_covered(message: str | None) -> bool:
    """True when the *arr queue statusMessages already establish that every
    track/episode is blocked as equal-or-better / already imported.

    Used as a Lidarr fallback when manualimport returns empty (filter/gone)
    or only unrelated rejections, but the queue itself says
    \"Not an upgrade for existing album file(s)\" / \"Album already imported\".
    Requires at least one upgrade/already-imported phrase and no signal that
    the failure is a match/parse problem instead."""
    if not message:
        return False
    m = message.lower()
    if not _DOWNGRADE_RE.search(m):
        return False
    # Don't treat match failures as quality-covered just because a sibling
    # phrase appears somewhere in a long multi-file message blob.
    blockers = (
        "couldn't find similar album",
        "unable to parse",
        "found multiple artists",
        "no files found are eligible",
    )
    if any(b in m for b in blockers):
        return False
    return True


def find_corroborating_episodes(candidates: list[dict], triggered_episode_id: int) -> list[dict] | None:
    """Pure: among Sonarr manual-import candidates for a download, find the file
    whose *resolved* episode list (Sonarr's own per-file scene-mapping, not our
    filename regex) includes the triggered episode. Returns that file's full
    episode list — often 2 entries for paired/segment-numbered releases, where
    an uploader packs multiple canonical episodes into one file under a
    different numbering scheme than the *arr library uses — or None if nothing
    corroborates. Used to rescue a naive S/E-parse mismatch."""
    for f in candidates:
        eps = f.get("episodes") or []
        if any(e.get("id") == triggered_episode_id for e in eps):
            return eps
    return None


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
                     cfg: ImportMatchingSettings,
                     absolute_range: tuple[int, int] | None = None) -> tuple[float, bool, list[str], str]:
    """Pack-level score for a season/complete-series download (Sonarr).

    Numeric corroboration comes from the sibling queue records sharing the
    downloadId (season consistency) and, when available, file/episode coverage
    (manual-import preview vs the season's aired episode list). Full coverage
    earns full numeric credit and the rationale suggests an entire-season
    (or entire-series) import. Returns (score, has_numeric, parts, pack_label).

    absolute_range (FI-08, opt-in via cfg.anime_absolute_pack_coverage): an
    anime batch pack ("001-100") with no season marker — mapped/total_episodes
    are then absolute-number-scoped (see _pack_coverage), not season-scoped,
    so the label/suggestion/near-half caveat below are worded for that case
    instead of a season pack."""
    if absolute_range:
        lo, hi = absolute_range
        label = f"{lo}-{hi} (absolute)"
    elif target_seasons:
        lo, hi = min(target_seasons), max(target_seasons)
        label = f"S{lo:02d}" if lo == hi else f"S{lo:02d}-S{hi:02d}"
    else:
        label = "complete series"
    if absolute_range:
        suggestion = "full absolute-range import"
    else:
        suggestion = "entire-series import" if (complete and not target_seasons) else "entire-season import"
    parts = [f"absolute-numbered pack detected ({label})"] if absolute_range \
        else [f"season pack detected ({label})"]

    numeric: float | None = None
    if sibling_seasons and target_seasons and any(s not in target_seasons for s in sibling_seasons):
        outside = sorted({s for s in sibling_seasons if s not in target_seasons})
        numeric = 0.25
        parts.append(f"queue maps episodes outside {label} (season(s) "
                     f"{', '.join(str(s) for s in outside)}) — hard penalty")
    elif mapped_episodes is not None and total_episodes:
        ratio = mapped_episodes / total_episodes
        # FI-02: some shows (e.g. Hey Arnold-style segmented/anthology numbering)
        # have Sonarr aired-episode counts roughly double the actual downloadable
        # unit count, so a genuinely complete pack still reads as ~50% covered.
        # A numeric ratio can't safely tell that apart from a real half-downloaded
        # season (both land at the same ratio) without risking false full-confidence
        # on a truly partial pack, so this stays a rationale caveat, not a score
        # change — triage-only, matches the item's own "harmless today" scope.
        # FI-08: doesn't apply to an absolute_range pack — that's exactly the
        # case this feature already re-scopes the denominator to fix, so the
        # near-50% ambiguity this caveat warns about isn't present here.
        near_half = (not absolute_range) and mapped_episodes >= 2 and 0.4 <= ratio <= 0.6
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
        if near_half:
            parts.append(f"coverage sits near 50% ({mapped_episodes}/{total_episodes}) — some shows "
                         f"double-count aired episodes (segmented/anthology numbering); this pack "
                         f"may actually be complete, verify manually before assuming it's partial")
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
                         target_seasons: set | None,
                         folder: str | None = None,
                         absolute_range: tuple[int, int] | None = None) -> tuple[int | None, int | None]:
    """(mapped_episodes, total_episodes) for a pack — every step fails soft.
    total = aired episodes in the target seasons (whole show minus specials when
    complete); mapped = distinct in-scope episodes the manual-import preview maps
    the download's files to (the "all episodes present in the dir" check).

    absolute_range (FI-08, opt-in): when given, scopes total/mapped to episodes
    whose absoluteEpisodeNumber falls in the range instead of season number —
    for an anime batch pack with no season marker at all ("001-100"), season-
    based scoping would instead count against the WHOLE aired show, diluting a
    genuinely complete pack down to a tiny fraction. Episodes with no populated
    absoluteEpisodeNumber (Sonarr hasn't backfilled it yet) are excluded from
    scope rather than guessed at."""
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

    if absolute_range:
        lo, hi = absolute_range
        in_scope = [e for e in eps if _aired(e) and e.get("absoluteEpisodeNumber") is not None
                   and lo <= e["absoluteEpisodeNumber"] <= hi]
    else:
        in_scope = [e for e in eps if _aired(e) and (
            e.get("seasonNumber") in target_seasons if target_seasons
            else (e.get("seasonNumber") or 0) > 0)]
    total = len(in_scope) or None
    scope_ids = {e.get("id") for e in in_scope}

    mapped = None
    if download_id and total:
        try:
            files = await client.get_manual_import(download_id, folder=folder)
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


_OUTPUT_PATH_IN_MSG_RE = re.compile(
    r"(?:eligible for import in|missing files[,:]?\s*|No files found[^\n]*?\s)"
    r"(/downloads/[^\s;]+|/media/[^\s;]+)",
    re.IGNORECASE,
)


def extract_output_path(rec: dict | None = None, messages: str | None = None,
                        raw_metadata: str | None = None) -> str | None:
    """Best-effort download folder path for the manualimport folder fallback.

    Sonarr often nulls `outputPath` once qBittorrent reports missing files, but
    the path still appears inside statusMessages (\"No files found are eligible
    for import in /downloads/...\"). Prefer the structured field, then parse
    stored raw_metadata messages, then any caller-supplied message string.

    Important: Accept may pass `messages=item.message` which is often a prior
    httpx 500 string with no path — never let that skip reading raw_metadata.
    """
    candidates: list[str] = []
    if rec:
        path = rec.get("outputPath")
        if path:
            return path
        qmsg = _queue_messages(rec)
        if qmsg:
            candidates.append(qmsg)
    if raw_metadata:
        try:
            meta = json.loads(raw_metadata)
            path = meta.get("outputPath")
            if path:
                return path
            meta_msg = meta.get("messages") or ""
            if meta_msg:
                candidates.append(meta_msg)
        except (ValueError, TypeError):
            pass
    if messages:
        candidates.append(messages)

    for text in candidates:
        m = _OUTPUT_PATH_IN_MSG_RE.search(text)
        if m:
            return m.group(1).rstrip("/.,;")
        for tok in re.findall(r"(/downloads/[^\s;]+|/media/[^\s;]+)", text):
            return tok.rstrip("/.,;")
    return None


# Per-app field names: (queue/history media-id key, library fetch method, library title key)
# Lidarr/Readarr use album/book libraries (v0.34.0) — artist/author name alone made the
# LLM disagree on every music row (release = album, candidate was only the artist).
APP_FIELDS = {
    "sonarr": ("seriesId", "get_series", "title"),
    "radarr": ("movieId", "get_movies", "title"),
    "lidarr": ("albumId", "get_albums", "title"),
    "readarr": ("bookId", "get_books", "title"),
}


def _album_display_title(item: dict) -> str:
    """'Artist - Album' for Lidarr album objects (artist may be nested or absent)."""
    album = (item.get("title") or "").strip()
    artist = ((item.get("artist") or {}).get("artistName")
              or item.get("artistName") or "").strip()
    if artist and album:
        return f"{artist} - {album}"
    return album or artist


def _book_display_title(item: dict) -> str:
    book = (item.get("title") or "").strip()
    author = ((item.get("author") or {}).get("authorName")
              or item.get("authorName") or "").strip()
    if author and book:
        return f"{author} - {book}"
    return book or author


# Containment-check confidence shaping (v0.37.0, FI-04 — user-confirmed 2026-07-10).
# The floor sits just BELOW high_confidence_threshold (0.90) on purpose: the check
# alone must never push a row over the auto-resolve line — raising it past 0.90 is
# a deliberate auto-action loosening that needs its own sign-off.
MUSIC_CHECK_FLOOR = 0.88          # artist AND album whole-word confirmed in release
MUSIC_CHECK_CAP_WRONG_ALBUM = 0.45  # artist present, album absent → likely wrong album
MUSIC_CHECK_CAP_NO_ARTIST_LINKED = 0.55  # artist absent but album confirmed on an id-linked match
MUSIC_CHECK_CAP_JUNK = 0.35       # nothing of the candidate is in the release name


def _apply_music_checks(app_name: str, raw_title: str, matched_title: str,
                        confidence: float, parts: list[str], linked: bool) -> float:
    """Shape a Lidarr match's confidence with the deterministic containment checks
    (same evidence the LLM sees, applied without the LLM). `linked` = the match
    came from a downloadId/artistId link rather than global fuzzy similarity —
    linked matches with artist-less release names (common scene style for singles)
    are capped gently instead of treated as junk."""
    if app_name != "lidarr" or not matched_title:
        return confidence
    grades = llm_assist.music_title_checks(raw_title, matched_title)
    if not grades:
        return confidence
    artist, album = grades["artist"], grades["album"]
    if artist == "strict" and album == "strict":
        if confidence < MUSIC_CHECK_FLOOR:
            parts.append(f"release name contains both the candidate artist and album — "
                         f"confidence raised to {MUSIC_CHECK_FLOOR:.2f}")
            return MUSIC_CHECK_FLOOR
        parts.append("release name contains both the candidate artist and album")
        return confidence
    if artist == "no" and album == "strict" and linked:
        parts.append(f"candidate artist absent from release name (album title present, "
                     f"id-linked) — capped at {MUSIC_CHECK_CAP_NO_ARTIST_LINKED:.2f}")
        return min(confidence, MUSIC_CHECK_CAP_NO_ARTIST_LINKED)
    if artist == "no" and (album != "strict" or not linked):
        parts.append(f"candidate artist not found in release name — capped at "
                     f"{MUSIC_CHECK_CAP_JUNK:.2f}")
        return min(confidence, MUSIC_CHECK_CAP_JUNK)
    if album == "no":
        parts.append(f"candidate album not found in release name — capped at "
                     f"{MUSIC_CHECK_CAP_WRONG_ALBUM:.2f}")
        return min(confidence, MUSIC_CHECK_CAP_WRONG_ALBUM)
    parts.append("artist/album only loosely locatable in release name — confidence unchanged")
    return confidence


def _lidarr_readarr_match(app_name: str, rec: dict, history: list[dict],
                          library: list[dict]) -> tuple[int | None, str | None, float, list[str]]:
    """Album/book-aware identity match for Lidarr/Readarr (v0.34.0).

    Queue often has artistId/authorId but not albumId/bookId; history usually has
    both. Prefer history album/book → artist-scoped fuzzy album → global fuzzy.
    v0.37.0: Lidarr confidence is shaped by the deterministic containment checks
    (_apply_music_checks) on every match path.
    """
    raw_title = rec.get("title") or ""
    id_key = "albumId" if app_name == "lidarr" else "bookId"
    parent_key = "artistId" if app_name == "lidarr" else "authorId"
    display = _album_display_title if app_name == "lidarr" else _book_display_title
    lib_by_id = {item["id"]: item for item in library}
    parts: list[str] = []

    download_id = rec.get("downloadId")
    hist = next((h for h in history if download_id and h.get("downloadId") == download_id), None)

    # 1. History album/book id — strongest for music/books
    if hist and hist.get(id_key) and hist[id_key] in lib_by_id:
        item = lib_by_id[hist[id_key]]
        title = display(item)
        sim = title_similarity(raw_title, title, source_app=app_name)
        # Also score against bare album/book title (release often omits artist prefix)
        sim = max(sim, title_similarity(raw_title, item.get("title") or "", source_app=app_name))
        parts.append(f"grab history links this downloadId to the library "
                     f"{'album' if app_name == 'lidarr' else 'book'} "
                     f"(title similarity {round(sim * 100)}%)")
        confidence = _apply_music_checks(app_name, raw_title, title,
                                         0.55 + 0.45 * sim, parts, linked=True)
        return hist[id_key], title, confidence, parts

    # 2. Queue/history parent id → fuzzy among that artist's/author's works
    parent_id = rec.get(parent_key) or (hist.get(parent_key) if hist else None)
    pool = library
    scoped = False
    if parent_id is not None:
        scoped_pool = [i for i in library
                       if i.get(parent_key) == parent_id
                       or (i.get("artist") or {}).get("id") == parent_id
                       or (i.get("author") or {}).get("id") == parent_id]
        if scoped_pool:
            pool = scoped_pool
            scoped = True

    best, best_score = None, 0.0
    for item in pool:
        s = max(
            title_similarity(raw_title, display(item), source_app=app_name),
            title_similarity(raw_title, item.get("title") or "", source_app=app_name),
        )
        if s > best_score:
            best, best_score = item, s
    if best and best_score > 0:
        title = display(best)
        if scoped:
            confidence = 0.55 + 0.45 * best_score
            parts.append(f"{app_name} queue maps artist/author; best "
                         f"{'album' if app_name == 'lidarr' else 'book'} match "
                         f"({round(best_score * 100)}% similarity)")
        else:
            confidence = 0.75 * best_score
            parts.append(f"fuzzy library {'album' if app_name == 'lidarr' else 'book'} "
                         f"match only ({round(best_score * 100)}% similarity)")
        confidence = _apply_music_checks(app_name, raw_title, title,
                                         confidence, parts, linked=scoped)
        return best["id"], title, confidence, parts

    return None, None, 0.0, parts


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

    if app_name in ("lidarr", "readarr"):
        matched_id, matched_title, confidence, parts = _lidarr_readarr_match(
            app_name, rec, history, library)
        if not matched_id:
            parts.append("no library match found")
    elif rec.get(id_key) and rec[id_key] in lib_by_id:
        # 1. Queue record already mapped by the *arr app itself — strongest signal
        matched_id = rec[id_key]
        matched_title = lib_by_id[matched_id].get(title_key, "")
        sim = title_similarity(raw_title, matched_title, source_app=app_name)
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
            sim = title_similarity(raw_title, matched_title, source_app=app_name)
            confidence = 0.45 + 0.45 * sim
            parts.append(f"grab history links this downloadId to the library entry "
                         f"(title similarity {round(sim * 100)}%)")
        else:
            # 3. Fuzzy title match against the library
            best, best_score = None, 0.0
            for item in library:
                s = title_similarity(raw_title, item.get(title_key, ""), source_app=app_name)
                if s > best_score:
                    best, best_score = item, s
            if best and best_score > 0:
                matched_id = best["id"]
                matched_title = best.get(title_key, "")
                confidence = 0.75 * best_score  # fuzzy-only match caps below auto-resolve range
                parts.append(f"fuzzy library title match only ({round(best_score * 100)}% similarity)")
            else:
                parts.append("no library match found")

    # Year hard-fail (v0.31.0): when both sides expose a year and they differ,
    # the match is the wrong movie/show remake — zero confidence, skip LLM.
    year_mismatch = False
    if matched_id and app_name in ("sonarr", "radarr"):
        rel_year = extract_release_year(raw_title)
        cand_year = candidate_year(lib_by_id.get(matched_id), matched_title)
        if rel_year is not None and cand_year is not None and rel_year != cand_year:
            year_mismatch = True
            confidence = 0.0
            parts.append(
                f"year mismatch (release {rel_year} vs library {cand_year}) — hard fail")

    # Episode/pack-level refinement — Sonarr only
    pack_label = None
    parsed = _parse_release_numbers(raw_title)
    series_type = (lib_by_id.get(matched_id) or rec.get("series") or {}).get("seriesType") or ""
    # FI-08 — an anime batch pack with no season marker ("001-100") only counts
    # as a pack at all when absolute-pack coverage is opted in; otherwise it's
    # left to the single-episode branch below same as before this feature.
    use_absolute_pack = (parsed["absolute_range"] is not None and series_type == "anime"
                         and cfg.anime_absolute_pack_coverage)
    is_pack = (app_name == "sonarr" and matched_id and parsed["episode"] is None
               and (parsed["pack_seasons"] or parsed["complete"] or use_absolute_pack))
    if not year_mismatch and is_pack:
        # Season/complete pack: corroborate via sibling queue records sharing the
        # downloadId (free) and file/episode coverage (one-time API calls per new row)
        download_id = rec.get("downloadId")
        siblings = ([r for r in queue if download_id and r.get("downloadId") == download_id]
                    if queue else [rec])
        sibling_seasons = [s for s in ((r.get("episode") or {}).get("seasonNumber")
                                       for r in siblings) if s is not None]
        mapped = total = None
        if client is not None:
            mapped, total = await _pack_coverage(
                client, download_id, matched_id, parsed["pack_seasons"],
                folder=extract_output_path(rec),
                absolute_range=parsed["absolute_range"] if use_absolute_pack else None,
            )
        # Sibling-season fallback only makes sense for season-scoped packs — an
        # absolute-range pack's siblings are still season numbers, not absolute
        # numbers, so counting them here would silently mix the two scopes.
        if mapped is None and total and sibling_seasons and not use_absolute_pack:
            mapped = len([s for s in sibling_seasons
                          if not parsed["pack_seasons"] or s in parsed["pack_seasons"]])
        title_sim = title_similarity(raw_title, matched_title, source_app=app_name)
        pack_score, has_numeric, pack_parts, pack_label = score_pack_match(
            title_sim, parsed["pack_seasons"], parsed["complete"],
            sibling_seasons, mapped, total, cfg,
            absolute_range=parsed["absolute_range"] if use_absolute_pack else None,
        )
        parts.extend(pack_parts)
        confidence = 0.5 * confidence + 0.5 * pack_score
        if not has_numeric and confidence > cfg.title_only_cap:
            confidence = cfg.title_only_cap
            parts.append(f"no numeric corroboration — confidence capped at {cfg.title_only_cap:.2f}")
    elif not year_mismatch and app_name == "sonarr" and matched_id and rec.get("episode"):
        series_type = (lib_by_id.get(matched_id) or rec.get("series") or {}).get("seriesType") or ""
        ep_score, has_numeric, ep_parts = score_episode_match(raw_title, rec["episode"], series_type, cfg)

        # A numeric mismatch here only means OUR filename regex disagrees with the
        # episode Sonarr's queue says it grabbed for — not that the file is wrong.
        # Paired/segment-numbered releases (uploader packs 2+ canonical episodes
        # per file under its own numbering) produce exactly this pattern. Sonarr's
        # manual-import preview already resolves the real per-file mapping via its
        # own scene data; check it once before trusting the naive parse.
        numeric_mismatch = (parsed["episode"] is not None and rec["episode"].get("episodeNumber") is not None
                            and parsed["episode"] != rec["episode"]["episodeNumber"])
        triggered_ep_id = rec["episode"].get("id")
        if numeric_mismatch and client is not None and rec.get("downloadId") and triggered_ep_id:
            try:
                candidates = await client.get_manual_import(
                    rec["downloadId"], folder=extract_output_path(rec),
                )
            except Exception as e:
                logger.info(f"Manual-import corroboration failed (non-fatal): {e}")
                candidates = []
            corroborating = find_corroborating_episodes(candidates, triggered_ep_id)
            if corroborating:
                ep_score, has_numeric = 1.0, True
                if len(corroborating) > 1:
                    ep_list = ", ".join(f"{_se_label(e.get('seasonNumber'), e.get('episodeNumber'))} "
                                        f"'{e.get('title')}'" for e in corroborating)
                    ep_parts = [f"manual-import preview maps this file to {len(corroborating)} episodes "
                               f"({ep_list}) — release likely uses paired/segment episode numbering; "
                               f"Sonarr's own file-level resolution corroborates the triggered episode "
                               f"despite the filename parsing to a different number"]
                else:
                    ep_parts = ["manual-import preview corroborates the triggered episode "
                               "despite the filename's own episode number not matching"]

        parts.extend(ep_parts)
        confidence = 0.5 * confidence + 0.5 * ep_score
        if not has_numeric and confidence > cfg.title_only_cap:
            confidence = cfg.title_only_cap
            parts.append(f"no numeric corroboration — confidence capped at {cfg.title_only_cap:.2f}")

    heuristic_confidence = round(min(1.0, confidence), 3)
    match_rationale = format_rationale(parts)
    llm_confidence = None
    llm_rationale = None
    llm_agrees = None
    llm_log = None
    # Year hard-fail skips the LLM — the deterministic scorer already settled it.
    if matched_title and not year_mismatch and ollama.task_enabled("match"):
        # Build comprehensive context: triggered series, queue state, pack info
        triggered_id = rec.get(id_key)
        triggered_item = lib_by_id.get(triggered_id) if triggered_id else None
        triggered_title = triggered_item.get(title_key) if triggered_item else None
        matched_item = lib_by_id.get(matched_id) if matched_id else None
        llm_context_parts = [
            f"Source app: {app_name}",
            f"Queue status: {_queue_messages(rec)[:150]}",
        ]
        if app_name == "lidarr":
            evidence = llm_assist.music_match_evidence(raw_title, matched_title)
            if evidence:
                # Right after the app line so CAP_CONTEXT truncation can't drop it.
                llm_context_parts.insert(1, evidence)
        if triggered_title and triggered_title != matched_title:
            llm_context_parts.append(
                f"Item that triggered download: '{triggered_title}' "
                f"(series ID {triggered_id}). This is the most reliable source of truth.")
        elif triggered_title:
            llm_context_parts.append(
                f"Item that triggered download: '{triggered_title}' — "
                f"usually matches the candidate unless it's a season pack.")
        alts = format_alternate_titles(matched_item)
        if alts:
            llm_context_parts.append(f"Library alternate titles: {alts}")
        if triggered_item and triggered_id != matched_id:
            trig_alts = format_alternate_titles(triggered_item)
            if trig_alts:
                llm_context_parts.append(f"Triggered-item alternate titles: {trig_alts}")
        if pack_label:
            llm_context_parts.append(
                f"Download type: season pack ({pack_label}). Accepting it imports every "
                f"mappable file from the pack, not a single episode.")
        llm_context = " | ".join(llm_context_parts)
        if getattr(ollama, "compact_det_summary", True):
            det_summary = llm_assist.compact_det_summary(
                match_rationale, heuristic_confidence, pack_label=pack_label)
        else:
            det_summary = f"{match_rationale} (heuristic confidence {heuristic_confidence})"
        capture: dict = {}
        llm = await llm_assist.review_match(
            ollama.host, ollama.model_for("match"), raw_title, matched_title,
            det_summary=det_summary,
            context=llm_context,
            api_style=ollama.api_style, template=ollama.match_prompt,
            verbosity=ollama.verbosity, model_size=ollama.model_size,
            keep_alive_minutes=ollama.keep_alive_minutes,
            reply_format="markdown", confidence_style=ollama.confidence_style,
            source_app=app_name,
            capture=capture,
            **llm_assist.prompt_kwargs(ollama),
            **llm_assist.inference_kwargs(ollama))
        checks = llm_assist.music_match_checks(raw_title, matched_title) \
            if app_name == "lidarr" else None
        pre_agrees = llm["agrees"] if llm else None
        if llm and app_name == "lidarr":
            llm = llm_assist.enforce_music_evidence(llm, checks)
        if capture.get("replied"):
            # Handed back to the scan loop, which writes the llm_match_log row
            # once the FailedImport row exists (LLM-LOG-01).
            llm_log = {
                "site": "scan", "source_app": app_name,
                "model": ollama.model_for("match"),
                "release_title": raw_title, "candidate_title": matched_title,
                "context": llm_context, "det_summary": det_summary,
                "capture": capture, "checks": checks,
                "agrees": llm["agrees"] if llm else None,
                "confidence_adjustment": llm["confidence_adjustment"] if llm else None,
                "enforced": bool(llm and pre_agrees is not None and llm["agrees"] != pre_agrees),
            }
        if llm:
            llm_confidence = round(max(0.0, min(1.0, heuristic_confidence + llm["confidence_adjustment"])), 3)
            llm_rationale = llm["rationale"]
            llm_agrees = llm["agrees"]
            confidence = blend_confidence(confidence, llm_confidence, cfg.llm_blend_weight)

    return {
        "matched_id": matched_id,
        "matched_title": matched_title,
        "confidence": round(min(1.0, confidence), 3),
        "heuristic_confidence": heuristic_confidence,
        "match_rationale": match_rationale,
        "pack": pack_label,
        "llm_confidence": llm_confidence,
        "llm_rationale": llm_rationale,
        "llm_agrees": llm_agrees,
        "llm_log": llm_log,
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


async def orphan_fs_state_async(output_path: str | None, timeout: float = 5.0) -> str:
    """orphan_fs_state run off the event loop with a bounded wall-time.

    A hung SMB/NFS mount can make os.stat block indefinitely — filesystem calls
    ignore httpx-style timeouts — which would freeze the poller AND every other
    async request sharing this event loop. Running the stat in a worker thread
    keeps the loop responsive; asyncio.wait bounds how long we wait for it (a stuck
    kernel stat can't actually be cancelled, so on timeout we abandon the thread to
    finish on its own and treat the row as un-stat-able — "error", skip the decision
    this cycle, never "gone")."""
    if not output_path:
        return "unknown"
    task = asyncio.ensure_future(asyncio.to_thread(orphan_fs_state, output_path))
    done, _pending = await asyncio.wait({task}, timeout=timeout)
    if task not in done:
        task.cancel()  # best-effort; a running stat thread ignores it and exits later
        return "error"
    try:
        return task.result()
    except Exception:
        return "error"


def decide_orphan_status(fs_state: str, auto_purge: bool) -> str | None:
    """Second gate after decide_orphans (pure, unit-tested): fold in the
    filesystem check and the auto-purge toggle. None = leave the row alone."""
    if fs_state in ("present", "error"):
        return None
    return "orphaned" if auto_purge else "orphan_pending"


def looks_like_missing_files(message: str | None) -> bool:
    """True when a push/accept message already established the download has
    no importable files left (empty manualimport / Servarr NullReference 500).
    Used to orphan at accept time and to clear stuck triage rows on the next scan."""
    if not message:
        return False
    m = message.lower()
    if "no importable files" in m or "download files are gone" in m:
        return True
    # Legacy raw httpx strings from before reason=no_files classification.
    # httpx often truncates the Servarr body, leaving only "500 … manualimport?downloadId=".
    if "500" in m and ("nullreference" in m or "object reference not set" in m
                       or "manualimport" in m):
        return True
    if "qbit" in m and "missing files" in m:
        return True
    if "no files found are eligible for import" in m:
        return True
    return False


def _row_output_path(row) -> str | None:
    return extract_output_path(raw_metadata=row.raw_metadata)


async def remove_from_download_clients(download_id: str, db) -> list[str]:
    """Try each enabled download-client integration until one removes the torrent
    (and, by default, its downloaded data — delete_download defaults to
    delete_files=True). Shared by the manual reject-and-remove-download flow
    (api/v1/imports.py) and the suspicious-file auto-reject-with-delete path
    below."""
    from app.api.v1.integrations import DOWNLOAD_CLIENT_NAMES
    from app.api.v1.integrations import _get_client as _download_client
    messages = []
    for name in DOWNLOAD_CLIENT_NAMES:
        row = db.query(Integration).filter_by(name=name, enabled=True).first()
        if not row or not row.url:
            continue
        client = _download_client(row)
        result = await client.delete_download(download_id)
        messages.append(f"{name}: {result['message']}")
        if result["ok"]:
            break
    return messages or ["No download client integration enabled"]


async def purge_dead_queue_entry(client, row, cfg: ImportMatchingSettings) -> None:
    """Remove the *arr's own queue record for a row orphaned because its download
    files are gone. The *arr keeps such dead entries indefinitely, and re-detecting
    the same stuck record every scan is what looped one release through the Import
    Queue forever (2026-07-11 incident). Same orphan_auto_purge opt-in as the
    download-client orphan purge; fails soft — a failed *arr call never blocks the
    local status change that triggered it."""
    if not cfg.orphan_auto_purge:
        return
    if not (row.queue_item_id and row.queue_item_id.isdigit()):
        return
    if await client.remove_from_queue(int(row.queue_item_id)):
        row.still_in_queue = False
        logger.info(f"Orphan cleanup: removed dead queue entry for '{row.raw_title}' "
                    f"from {row.source_app}")


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
    arr_clients: dict[str, Any] = {}

    def _arr_client(app_name: str):
        if app_name not in arr_clients:
            arr_row = db.query(Integration).filter_by(name=app_name, enabled=True).first()
            arr_clients[app_name] = _get_client(app_name, arr_row) if arr_row else None
        return arr_clients[app_name]

    for row in rows:
        if row.download_id.lower() not in orphaned:
            continue
        fs = await orphan_fs_state_async(_row_output_path(row))
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
            if row.queue_item_id and row.queue_item_id.isdigit():
                client = _arr_client(row.source_app)
                if client and await client.remove_from_queue(int(row.queue_item_id)):
                    logger.info(f"Orphan check: removed '{row.raw_title}' from {row.source_app}'s queue")
        else:
            row.message = ((row.message + " | ") if row.message else "") + \
                "Download no longer exists in any download client — confirm to mark orphaned"
            summary["orphan_pending"] += 1
            logger.info(f"Orphan check: '{row.raw_title}' ({row.source_app}) gone from "
                        f"all download clients — awaiting orphan confirmation")
    db.commit()


async def _orphan_known_missing_files(db, cfg: ImportMatchingSettings, summary: dict) -> None:
    """Clear triage rows whose last push already proved the download has no
    files left (empty *arr manualimport). Complements the download-client
    orphan check — Lidarr often returns zero candidates while the torrent
    hash is still listed, so client presence alone wouldn't orphan them.
    Also removes the dead *arr queue entry (orphan_auto_purge opt-in) so the
    same record doesn't come back as a fresh suggestion every scan."""
    rows = db.query(FailedImport).filter(
        FailedImport.status.in_(("suggested", "resolve_failed")),
    ).all()
    now = datetime.utcnow()
    changed = False
    arr_clients: dict[str, Any] = {}

    def _arr_client(app_name: str):
        if app_name not in arr_clients:
            arr_row = db.query(Integration).filter_by(name=app_name, enabled=True).first()
            arr_clients[app_name] = _get_client(app_name, arr_row) if arr_row else None
        return arr_clients[app_name]

    for row in rows:
        if not looks_like_missing_files(row.message):
            continue
        warn = "Download files are gone — nothing left to import"
        row.status = "orphaned"
        row.resolved_at = now
        row.verified = False
        if warn not in (row.message or ""):
            row.message = ((row.message + " | ") if row.message else "") + warn
        summary["orphaned"] += 1
        changed = True
        logger.warning(f"Orphan check: '{row.raw_title}' ({row.source_app}) — "
                       f"prior push found no files; marked orphaned")
        client = _arr_client(row.source_app)
        if client:
            await purge_dead_queue_entry(client, row, cfg)
    if changed:
        db.commit()


def _row_in_id_sets(row, download_ids: set, item_ids: set) -> bool:
    if row.download_id:
        return row.download_id in download_ids
    return bool(row.queue_item_id) and row.queue_item_id in item_ids


def _close_stale_rows(db, app_name: str, queue: list[dict], summary: dict,
                      include_stalled: bool = False) -> None:
    """Suggested rows whose download left the queue on its own → closed_external.
    Also refreshes still_in_queue (v0.35.0) from **stuck** queue records only
    (importPending/Failed/Blocked, etc.) so Still queued surfaces Accepted/
    Rejected leftovers that never cleared — not every actively-downloading item.

    Scoped queries only — never loads the full history table for the app."""
    from sqlalchemy import or_

    all_download_ids = {rec.get("downloadId") for rec in queue if rec.get("downloadId")}
    all_item_ids = {str(rec.get("id", "")) for rec in queue if rec.get("id") is not None}
    stuck = [rec for rec in queue if _is_stuck(rec, include_stalled)]
    stuck_download_ids = {rec.get("downloadId") for rec in stuck if rec.get("downloadId")}
    stuck_item_ids = {str(rec.get("id", "")) for rec in stuck if rec.get("id") is not None}

    # Clear / close rows currently marked in-queue (or still suggested).
    # Suggested→closed_external uses the full queue (left entirely); still_in_queue
    # uses the stuck subset only.
    tracked = db.query(FailedImport).filter(
        FailedImport.source_app == app_name,
        or_(FailedImport.still_in_queue.is_(True), FailedImport.status == "suggested"),
    ).all()
    for row in tracked:
        in_queue = _row_in_id_sets(row, all_download_ids, all_item_ids)
        stuck_now = _row_in_id_sets(row, stuck_download_ids, stuck_item_ids)
        if row.still_in_queue != stuck_now:
            row.still_in_queue = stuck_now
        if row.status == "suggested" and not in_queue:
            row.status = "closed_external"
            row.resolved_at = datetime.utcnow()
            row.message = ((row.message + " | ") if row.message else "") + "Left the queue on its own"
            summary["closed_external"] += 1

    # Mark rows matching stuck downloads
    if stuck_download_ids:
        for row in db.query(FailedImport).filter(
            FailedImport.source_app == app_name,
            FailedImport.download_id.in_(stuck_download_ids),
            or_(FailedImport.still_in_queue.is_(False), FailedImport.still_in_queue.is_(None)),
        ).all():
            row.still_in_queue = True
    if stuck_item_ids:
        for row in db.query(FailedImport).filter(
            FailedImport.source_app == app_name,
            FailedImport.download_id.is_(None),
            FailedImport.queue_item_id.in_(stuck_item_ids),
            or_(FailedImport.still_in_queue.is_(False), FailedImport.still_in_queue.is_(None)),
        ).all():
            row.still_in_queue = True
    db.commit()


async def _verify_resolved(db, app_name: str, client, cfg: ImportMatchingSettings, summary: dict,
                           history: list[dict] | None = None) -> None:
    """Confirm pushed imports actually landed: look for an import event in recent history.
    Unverified past the timeout → resolve_failed (surfaced back into triage), unless the
    download is already gone from every download client — then orphan it instead of
    bouncing a dead push back into triage.

    SCAL-03: `history` is optional so callers with their own already-fetched full-app
    history (unfiltered, eventType=None — a superset of this function's needs) can pass
    it straight through and skip a second get_history() round-trip for the same app in
    the same scan cycle. Falls back to fetching its own when not provided."""
    pending = db.query(FailedImport).filter(
        FailedImport.source_app == app_name,
        FailedImport.status.in_(("auto_resolved", "accepted")),
        FailedImport.verified.is_(None),
    ).all()
    if not pending:
        return
    if history is None:
        try:
            history = await client.get_history(event_type=None)
        except Exception as e:
            logger.warning(f"Import verify: {app_name} history fetch failed: {e}")
            return
    imported_ids = {h.get("downloadId") for h in history
                    if "imported" in str(h.get("eventType", "")).lower() and h.get("downloadId")}
    now = datetime.utcnow()
    timed_out = []
    for row in pending:
        if row.download_id and row.download_id in imported_ids:
            row.verified = True
            summary["verified"] += 1
        elif row.resolved_at and now - row.resolved_at > timedelta(minutes=cfg.verify_timeout_minutes):
            timed_out.append(row)
    if timed_out:
        gone = await _download_ids_confirmed_gone(
            db, {r.download_id for r in timed_out if r.download_id})
        for row in timed_out:
            row.verified = False
            if row.download_id and row.download_id.lower() in gone:
                fs = await orphan_fs_state_async(_row_output_path(row))
                if fs == "present":
                    # Files still on disk — keep as resolve_failed so the user can retry
                    row.status = "resolve_failed"
                    row.message = ((row.message + " | ") if row.message else "") + \
                        f"Import not confirmed in history within {cfg.verify_timeout_minutes} min"
                    summary["resolve_failed"] += 1
                    logger.warning(f"Import verify: '{row.raw_title}' ({app_name}) push not "
                                   f"confirmed — marked resolve_failed (files still on disk)")
                elif fs == "error":
                    row.status = "resolve_failed"
                    row.message = ((row.message + " | ") if row.message else "") + \
                        f"Import not confirmed in history within {cfg.verify_timeout_minutes} min"
                    summary["resolve_failed"] += 1
                    logger.warning(f"Import verify: '{row.raw_title}' ({app_name}) push not "
                                   f"confirmed — marked resolve_failed (couldn't stat path)")
                else:
                    row.status = "orphaned"
                    row.resolved_at = now
                    warn = "Download files are gone — nothing left to import"
                    row.message = ((row.message + " | ") if row.message else "") + warn
                    summary["orphaned"] += 1
                    logger.warning(f"Import verify: '{row.raw_title}' ({app_name}) push not "
                                   f"confirmed and download is gone — marked orphaned")
                    await purge_dead_queue_entry(client, row, cfg)
            else:
                row.status = "resolve_failed"
                row.message = ((row.message + " | ") if row.message else "") + \
                    f"Import not confirmed in history within {cfg.verify_timeout_minutes} min"
                summary["resolve_failed"] += 1
                logger.warning(f"Import verify: '{row.raw_title}' ({app_name}) push not confirmed — marked resolve_failed")
    db.commit()


async def _download_ids_confirmed_gone(db, download_ids: set[str]) -> set[str]:
    """Subset of download_ids confirmed absent from every configured download client.
    Empty set when no clients are configured or any client is unreachable (fail-soft)."""
    from app.api.v1.integrations import DOWNLOAD_CLIENT_NAMES
    from app.api.v1.integrations import _get_client as _download_client
    if not download_ids:
        return set()
    clients = []
    for name in DOWNLOAD_CLIENT_NAMES:
        row = db.query(Integration).filter_by(name=name, enabled=True).first()
        if row and row.url:
            clients.append((name, _download_client(row)))
    if not clients:
        return set()
    ids = {d.lower() for d in download_ids if d}
    results: list[set[str] | None] = []
    for name, client in clients:
        found = await client.check_downloads(ids)
        if found is None:
            logger.warning(f"Orphan check: {name} unreachable — skipping gone-confirm this cycle")
        results.append(found)
    gone = decide_orphans(ids, results)
    return {d.lower() for d in (gone or set())}


async def scan_once() -> dict:
    """One detection cycle across all enabled *arr apps. Thin wrapper around
    _scan_once_inner() so the tracked task always gets marked done/failed —
    including on an exception the inner function doesn't itself catch —
    without needing a second, deeply-nested try/finally around that whole
    body."""
    from app.services import tasks
    task_id = tasks.create_task("scan", "Scanning *arr apps for stuck imports")
    try:
        summary = await _scan_once_inner(task_id)
        tasks.finish_task(task_id, "done",
                          f"{summary['new_suggestions']} new suggestion(s), "
                          f"{summary['auto_resolved']} auto-resolved")
        return summary
    except Exception as e:
        tasks.finish_task(task_id, "failed", str(e))
        raise


async def _scan_once_inner(task_id: str) -> dict:
    """Returns a per-app summary. See scan_once() for the tracked-task wrapper."""
    from app.services import tasks
    summary: dict = {"scanned": [], "new_suggestions": 0, "auto_resolved": 0, "skipped_existing": 0,
                     "below_floor": 0, "in_grace": 0, "closed_external": 0, "verified": 0,
                     "resolve_failed": 0, "orphaned": 0, "orphan_pending": 0,
                     "quality_downgrade_auto_rejected": 0, "suspicious_auto_rejected": 0,
                     "backlog_auto_queued": 0, "new_suggestion_ids": []}
    db = SessionLocal()
    scanned_apps = 0
    try:
        cfg, ollama = load_settings(db)
        enabled_apps = [name for name in ("sonarr", "radarr", "lidarr", "readarr")
                        if getattr(cfg, f"{name}_enabled", True)
                        and db.query(Integration).filter_by(name=name, enabled=True).first()]
        tasks.update_task(task_id, total=len(enabled_apps))
        for app_name in ("sonarr", "radarr", "lidarr", "readarr"):
            if app_name not in enabled_apps:
                continue
            scanned_apps += 1
            tasks.update_task(task_id, current=scanned_apps, message=f"Scanning {app_name}…")
            row = db.query(Integration).filter_by(name=app_name, enabled=True).first()
            client = _get_client(app_name, row)
            from app.services import circuit_breaker, dep_health
            if circuit_breaker.breaker_open(app_name):
                logger.warning(f"Import scan: {app_name} skipped — circuit breaker open")
                dep_health.record(app_name, False, "circuit breaker open", source="scan")
                continue
            try:
                queue = await client.get_queue()
                circuit_breaker.record_result(app_name, True)
                dep_health.record(app_name, True, "queue ok", source="scan")
            except Exception as e:
                circuit_breaker.record_result(app_name, False, str(e))
                dep_health.record(app_name, False, str(e), source="scan")
                logger.warning(f"Import scan: {app_name} queue fetch failed: {e}")
                continue

            _close_stale_rows(db, app_name, queue, summary, include_stalled=cfg.include_stalled)

            stuck = [rec for rec in queue if _is_stuck(rec, cfg.include_stalled)]
            summary["scanned"].append({"app": app_name, "queue": len(queue), "stuck": len(stuck)})

            # SCAL-03: one unfiltered history fetch per app per cycle serves both
            # _verify_resolved (needs every event type) and the stuck-item matcher
            # below (needs "grabbed" events only, client-filtered from the same
            # list) instead of two separate get_history() round-trips to the *arr.
            needs_verify = db.query(FailedImport).filter(
                FailedImport.source_app == app_name,
                FailedImport.status.in_(("auto_resolved", "accepted")),
                FailedImport.verified.is_(None),
            ).first() is not None
            history: list[dict] = []
            if needs_verify or stuck:
                try:
                    history = await client.get_history(event_type=None)
                    circuit_breaker.record_result(app_name, True)
                except Exception as e:
                    circuit_breaker.record_result(app_name, False, str(e))
                    logger.warning(f"Import scan: {app_name} history fetch failed: {e}")
                    history = []

            if needs_verify:
                await _verify_resolved(db, app_name, client, cfg, summary, history=history)

            if not stuck:
                continue

            grabbed_history = [h for h in history if h.get("eventType") == "grabbed"]
            _, lib_method, _ = APP_FIELDS[app_name]
            try:
                library = await getattr(client, lib_method)()
                circuit_breaker.record_result(app_name, True)
            except Exception as e:
                circuit_breaker.record_result(app_name, False, str(e))
                logger.warning(f"Import scan: {app_name} library fetch failed: {e}")
                library = []

            for rec in stuck:
                if _within_grace(rec, cfg.grace_period_minutes):
                    summary["in_grace"] += 1
                    continue
                download_id = rec.get("downloadId")
                queue_item_id = str(rec.get("id", ""))
                from sqlalchemy import and_, or_
                dedupe = db.query(FailedImport).filter(
                    FailedImport.source_app == app_name,
                    or_(
                        FailedImport.status.in_(OPEN_STATUSES + ("resolve_failed",)),
                        # Orphaned rows block only while the same dead queue entry
                        # is still present (_close_stale_rows clears the flag once
                        # it leaves, so a genuine later re-grab surfaces normally).
                        and_(FailedImport.status == "orphaned",
                             FailedImport.still_in_queue.is_(True)),
                    ),
                )
                if download_id:
                    dedupe = dedupe.filter(FailedImport.download_id == download_id)
                else:
                    dedupe = dedupe.filter(FailedImport.queue_item_id == queue_item_id)
                if dedupe.first():
                    summary["skipped_existing"] += 1
                    continue

                match = await _match_record(app_name, rec, grabbed_history, library, cfg, ollama,
                                            queue=queue, client=client)
                if match["confidence"] < cfg.low_confidence_floor:
                    logger.info(
                        f"Import scan: '{rec.get('title')}' ({app_name}) below confidence floor "
                        f"({match['confidence']:.2f} < {cfg.low_confidence_floor}) — logged only")
                    summary["below_floor"] += 1
                    continue

                q_messages = _queue_messages(rec)
                output_path = extract_output_path(rec, messages=q_messages)
                item = FailedImport(
                    source_app=app_name,
                    queue_item_id=queue_item_id,
                    download_id=download_id,
                    raw_title=rec.get("title") or "(unknown)",
                    raw_metadata=json.dumps({
                        "status": rec.get("status"),
                        "trackedDownloadState": rec.get("trackedDownloadState"),
                        "trackedDownloadStatus": rec.get("trackedDownloadStatus"),
                        "outputPath": output_path,
                        "protocol": rec.get("protocol"),
                        "messages": q_messages,
                        "match_rationale": match["match_rationale"],
                        "pack": match["pack"],
                        # Cached for on-demand LLM rescore (v0.31.0) — avoids a
                        # second full-library fetch just to re-inject alt titles.
                        "alternate_titles": format_alternate_titles(
                            next((x for x in library if x.get("id") == match["matched_id"]), None)
                        ) if match.get("matched_id") else "",
                        # Parent ids for Lidarr/Readarr rematch (v0.34.0)
                        **({"artistId": rec.get("artistId")} if app_name == "lidarr" and rec.get("artistId") else {}),
                        **({"authorId": rec.get("authorId")} if app_name == "readarr" and rec.get("authorId") else {}),
                    }),
                    matched_title=match["matched_title"],
                    matched_id=match["matched_id"],
                    confidence=match["confidence"],
                    heuristic_confidence=match["heuristic_confidence"],
                    llm_confidence=match["llm_confidence"],
                    llm_rationale=match["llm_rationale"],
                    llm_agrees=match["llm_agrees"],
                    status="suggested",
                    still_in_queue=True,
                    message=q_messages[:500] or None,
                )

                # Quality-covered + suspicious-file checks (v0.17.0/v0.19.0/v0.29.0) —
                # one manual-import call per NEW row, shared by both checks, bounded
                # the same way as pack coverage/corroboration (never on an existing
                # row's re-poll). Suspicious-file detection runs for every app; equal-
                # or-better library coverage (quality_downgrade flag) runs for every
                # *arr app via manualimport rejections, with a Lidarr/queue-message
                # fallback when the *arr already said "not an upgrade" / "album
                # already imported" on the queue record itself.
                mi_candidates: list[dict] = []
                if download_id:
                    try:
                        mi_candidates = await client.get_manual_import(
                            download_id, folder=output_path,
                        )
                    except Exception as e:
                        logger.info(f"Manual-import check failed (non-fatal): {e}")
                if mi_candidates:
                    suspicious = find_suspicious_files(mi_candidates, cfg.suspicious_extensions)
                    item.suspicious_files = json.dumps(suspicious) if suspicious else None
                    item.quality_downgrade = is_quality_downgrade(mi_candidates)
                    item.partial_import = is_partial_import(mi_candidates)
                if not item.quality_downgrade and queue_looks_like_quality_covered(q_messages):
                    item.quality_downgrade = True
                    item.partial_import = False

                if item.suspicious_files and cfg.suspicious_extension_auto_reject:
                    matched = json.loads(item.suspicious_files)
                    item.status = "rejected"
                    item.resolved_at = datetime.utcnow()
                    item.message = ((item.message + " | ") if item.message else "") + \
                        f"Auto-rejected: suspicious file type(s) detected ({', '.join(matched)})"
                    summary["suspicious_auto_rejected"] += 1
                    if queue_item_id and queue_item_id.isdigit():
                        if await client.remove_from_queue(int(queue_item_id)):
                            logger.info(f"Suspicious file: removed '{item.raw_title}' from {app_name}'s queue")
                    if cfg.suspicious_extension_delete_from_disk and download_id:
                        dl_messages = await remove_from_download_clients(download_id, db)
                        item.message += " | " + "; ".join(dl_messages)
                        logger.warning(f"Suspicious file: deleted download for '{item.raw_title}' "
                                       f"from disk — {'; '.join(dl_messages)}")
                elif item.quality_downgrade and cfg.quality_downgrade_auto_reject:
                    item.status = "rejected"
                    item.resolved_at = datetime.utcnow()
                    item.message = ((item.message + " | ") if item.message else "") + \
                        "Auto-rejected: library already has equal or better quality for every file"
                    summary["quality_downgrade_auto_rejected"] += 1
                    if queue_item_id and queue_item_id.isdigit():
                        if await client.remove_from_queue(int(queue_item_id)):
                            logger.info(f"Quality covered: removed '{item.raw_title}' from {app_name}'s queue")
                elif (cfg.auto_resolve_enabled and match["matched_id"] and download_id
                        and passes_auto_thresholds(
                            match["heuristic_confidence"], match["llm_confidence"], cfg)):
                    result = await client.push_import_command(
                        download_id, match["matched_id"], folder=output_path)
                    if result["ok"]:
                        item.status = "auto_resolved"
                        item.resolved_at = datetime.utcnow()
                        summary["auto_resolved"] += 1
                        logger.info(f"Import scan: auto-resolved '{item.raw_title}' ({app_name}, "
                                    f"confidence {item.confidence:.2f}): {result['message']}")
                    elif result.get("reason") == "no_files":
                        # Files already gone — don't leave a suggested row that can never import
                        item.status = "orphaned"
                        item.resolved_at = datetime.utcnow()
                        item.verified = False
                        summary["orphaned"] += 1
                        logger.warning(f"Import scan: '{item.raw_title}' ({app_name}) files gone "
                                       f"at auto-resolve — marked orphaned")
                        await purge_dead_queue_entry(client, item, cfg)
                    item.message = result["message"]

                if item.status == "suggested":
                    summary["new_suggestions"] += 1
                db.add(item)
                db.commit()
                if match.get("llm_log"):
                    from app.services import llm_match_log
                    llm_match_log.record(db, failed_import_id=item.id, **match["llm_log"])
                    db.commit()
                if item.status == "suggested":
                    summary["new_suggestion_ids"].append(item.id)
        await _check_orphans(db, cfg, summary)
        await _orphan_known_missing_files(db, cfg, summary)

        # Auto-import backlog drain (v0.44.0): suggested rows that meet the
        # auto-import thresholds NOW (settings changed, or an LLM score landed
        # after row creation) go through the same coalesced accept pipeline a
        # user Accept uses — auto-resolve is no longer only a row-creation-time
        # decision. resolve_failed rows are deliberately excluded (a push that
        # already failed once shouldn't retry unattended every scan).
        if cfg.auto_resolve_enabled:
            from app.services import auto_eligible
            backlog = [r.id for r in auto_eligible.auto_eligible_query(db, cfg)
                       .filter(FailedImport.status == "suggested").all()]
            if backlog:
                summary["backlog_auto_queued"] = len(backlog)
                await _queue_auto_imports(backlog)

        # Last-scan timestamp for the dashboard's "next scan" countdown — updated on
        # every completed cycle, manual "Scan Now" included, so it reflects reality
        # even if the background poller has been off.
        setting = db.query(AppSetting).filter_by(key="last_scan_at").first()
        if not setting:
            setting = AppSetting(key="last_scan_at")
            db.add(setting)
        setting.value = datetime.utcnow().isoformat()
        db.commit()
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
            await _notify_actionable_suggestions(db, summary)
        finally:
            db.close()
    except Exception as e:
        logger.info(f"Scan notification failed (non-fatal): {e}")


async def _notify_actionable_suggestions(db, summary: dict) -> None:
    """Per-item ntfy notification with Accept/Reject action buttons (signed
    one-time tokens, v0.26.0) — on top of the aggregate summary above. Opt-in
    (NotificationSettings.actionable_new_suggestions) and needs a reachable
    public_base_url to build the action links; skipped entirely otherwise.
    Capped at actionable_max_per_scan — a big batch falls back to the aggregate
    summary only, so a large scan doesn't fire a wall of notifications."""
    ids = summary.get("new_suggestion_ids") or []
    if not ids:
        return
    from app.services import notifier
    cfg = notifier.load_settings(db)
    if not cfg.actionable_new_suggestions or not cfg.public_base_url:
        return
    if len(ids) > cfg.actionable_max_per_scan:
        return
    from app.services.action_tokens import make_action_token
    base = cfg.public_base_url.rstrip("/")
    for item_id in ids:
        item = db.query(FailedImport).filter_by(id=item_id).first()
        if not item:
            continue
        accept_token = make_action_token(db, item_id, "accept")
        reject_token = make_action_token(db, item_id, "reject")
        actions = [
            f"http, Accept, {base}/api/v1/imports/notify-action?token={accept_token}, method=GET, clear=true",
            f"http, Reject, {base}/api/v1/imports/notify-action?token={reject_token}, method=GET, clear=true",
        ]
        pct = round((item.confidence or 0) * 100)
        await notifier.notify(
            db, f"Powarr: new suggestion — {item.raw_title[:60]}",
            f"Matched to {item.matched_title or 'unknown'} ({pct}% confidence)",
            priority="default", tags="mag", actions=actions,
        )


def blend_confidence(deterministic: float, llm: float, weight: float) -> float:
    """Deterministic/LLM confidence blend. weight = the LLM's share (0-1, clamped);
    user-adjustable since v0.12.0 (previously hardcoded 0.7/0.3)."""
    w = max(0.0, min(1.0, weight))
    return round(min(1.0, (1.0 - w) * deterministic + w * llm), 3)


def llm_run_active() -> bool:
    # Delegates to the shared single-flight slot in llm_assist, so a batch run and
    # the Cleanup page's per-item explain can never hit the LLM host concurrently.
    return llm_assist.slot_active()


# FIFO queue of on-demand runs requested while another is already active — a run
# no longer fails with "already in progress"; it queues and starts automatically
# once the current one releases the slot (see llm_rescore's finally block).
_llm_queue: list[list[int] | None] = []


# Shared tray card for coalesced on-demand LLM runs (v0.33.0) — additional
# Run-LLM clicks bump this card's total instead of stacking duplicate llm_run cards.
_llm_tray_task_id: str | None = None
_llm_tray_scored = 0
_llm_tray_skipped = 0


def _estimate_llm_items(ids: list[int] | None, limit: int = 50) -> int:
    if ids:
        return min(len(ids), limit)
    return 1  # backlog size unknown until the run opens a DB session


def queue_llm_run(ids: list[int] | None) -> int:
    """Append a pending on-demand run; returns its 1-based position in the queue.
    Bumps the running llm_run tray card's total when one exists (v0.33.0)."""
    from app.services import tasks
    _llm_queue.append(ids)
    position = len(_llm_queue)
    add = _estimate_llm_items(ids)
    if _llm_tray_task_id and tasks.get_task(_llm_tray_task_id):
        t = tasks.get_task(_llm_tray_task_id)
        if t and t.status == "running":
            new_total = (t.total or 0) + add
            tasks.bump_total(
                _llm_tray_task_id, add,
                label=f"Scoring {new_total} import(s) with the LLM",
                message=f"+{add} queued (position {position}) — {new_total} total",
            )
    publish({"type": "llm_queued", "position": position})
    return position


def llm_queue_depth() -> int:
    return len(_llm_queue)


async def llm_rescore(ids: list[int] | None = None, limit: int = 50,
                      *, continue_tray: bool = False) -> dict:
    """On-demand LLM scoring of failed-import rows — either the given ids, or the
    backlog of open rows that never got an LLM signal. Sequential (one call at a
    time) to be gentle on the LLM host. Publishes an SSE event when done, and when
    it releases the slot pulls the next queued run (if any) automatically. Thin
    wrapper around _llm_rescore_inner() so the tracked task always resolves
    (done/failed) without a third level of nested try/finally.

    continue_tray (v0.33.0): when True, reuse the existing coalesced llm_run tray
    card instead of creating a new one (used when draining the on-demand queue).
    """
    global _llm_tray_task_id, _llm_tray_scored, _llm_tray_skipped
    if not llm_assist.acquire_slot():
        return {"scored": 0, "skipped": 0, "message": "An LLM run is already in progress"}
    publish({"type": "llm_run_started"})
    from app.services import tasks
    if continue_tray and _llm_tray_task_id and tasks.get_task(_llm_tray_task_id) \
            and tasks.get_task(_llm_tray_task_id).status == "running":
        task_id = _llm_tray_task_id
    else:
        _llm_tray_scored = _llm_tray_skipped = 0
        est = _estimate_llm_items(ids, limit)
        task_id = tasks.create_task(
            "llm_run", f"Scoring {est} import(s) with the LLM", total=est)
        _llm_tray_task_id = task_id
    try:
        result = await _llm_rescore_inner(ids, limit, task_id)
        # Only finish the tray card when nothing else is queued behind us.
        if not _llm_queue:
            tasks.finish_task(
                task_id, "done",
                f"{_llm_tray_scored} scored, {_llm_tray_skipped} skipped")
            _llm_tray_task_id = None
        return result
    except Exception as e:
        tasks.finish_task(task_id, "failed", str(e))
        _llm_tray_task_id = None
        raise
    finally:
        llm_assist.release_slot()
        if _llm_queue:
            next_ids = _llm_queue.pop(0)
            from app.services import tasks
            tasks.spawn_background(llm_rescore(next_ids, continue_tray=True))


async def rematch_music_row(db, row: FailedImport, lib_cache: dict[str, list],
                            hist_cache: dict[str, list]) -> bool:
    """Re-run the album/book matcher for one Lidarr/Readarr row against a fresh
    (per-call-cached) library + history fetch. Updates the row's match, heuristic
    confidence, and stored rationale in place; returns True when a match was
    written. Shared by llm_rescore (pre-LLM rematch, v0.34.0) and the no-LLM
    rescore_music (v0.37.0)."""
    if row.source_app not in ("lidarr", "readarr"):
        return False
    arr_row = db.query(Integration).filter_by(
        name=row.source_app, enabled=True).first()
    if not arr_row:
        return False
    client = _get_client(row.source_app, arr_row)
    if row.source_app not in lib_cache:
        _, lib_method, _ = APP_FIELDS[row.source_app]
        try:
            lib_cache[row.source_app] = await getattr(client, lib_method)()
            hist_cache[row.source_app] = await client.get_history()
        except Exception as e:
            logger.info(f"Rematch library fetch failed ({row.source_app}): {e}")
            lib_cache[row.source_app] = []
            hist_cache[row.source_app] = []
    rec = {"title": row.raw_title, "downloadId": row.download_id}
    try:
        meta = json.loads(row.raw_metadata or "{}")
    except (ValueError, TypeError):
        meta = {}
    # Preserve parent id if we stored it
    if row.source_app == "lidarr" and meta.get("artistId"):
        rec["artistId"] = meta["artistId"]
    if row.source_app == "readarr" and meta.get("authorId"):
        rec["authorId"] = meta["authorId"]
    mid, title, conf, parts = _lidarr_readarr_match(
        row.source_app, rec, hist_cache.get(row.source_app) or [],
        lib_cache.get(row.source_app) or [])
    if not (mid and title):
        return False
    row.matched_id = mid
    row.matched_title = title
    row.heuristic_confidence = round(min(1.0, conf), 3)
    row.confidence = row.heuristic_confidence
    meta["match_rationale"] = format_rationale(parts)
    row.raw_metadata = json.dumps(meta)
    return True


# Percentages ("92%") and confidence values ("0.88") — bolded in Match Notes.
_RATIONALE_NUM_RE = re.compile(r"(\d+(?:\.\d+)?%|\b[01]\.\d+\b)")


def format_rationale(parts: list[str]) -> str:
    """Render the deterministic scorer's findings as a Markdown bullet list
    (v0.37.3) — same shape as the LLM Notes column: one '- ' bullet per finding,
    capitalized, with percentages and confidence values bolded. Pre-v0.37.3 rows
    keep their prose form until rescored/rescanned."""
    bullets = []
    for part in parts:
        part = (part or "").strip()
        if not part:
            continue
        part = part[0].upper() + part[1:]
        bullets.append(f"- {_RATIONALE_NUM_RE.sub(r'**\1**', part)}")
    return "\n".join(bullets)


def collect_auto_eligible(rows, cfg) -> list[int]:
    """Ids among freshly rescored rows whose new blended confidence qualifies for
    auto-import (same predicate as Process N Items — auto_resolve_enabled,
    open status, matched, confidence >= high_confidence_threshold)."""
    from app.services import auto_eligible
    return [r.id for r in rows if auto_eligible.is_auto_eligible(r, cfg)]


async def _queue_auto_imports(ids: list[int]) -> int:
    """Push auto-eligible rescored rows through the canonical coalesced accept
    pipeline (tray card, pack mapping, partial import, orphan detection at
    accept — identical to a user Accept). Lazy import: the pipeline lives in
    the API layer, which imports this module."""
    if not ids:
        return 0
    from app.api.v1.imports import _enqueue_accepts
    await _enqueue_accepts(ids)
    logger.info(f"Rescore: queued {len(ids)} auto-eligible row(s) for import")
    return len(ids)


async def rescore_music(ids: list[int] | None = None, limit: int = 500) -> dict:
    """Deterministic (no-LLM) rescore for Lidarr/Readarr rows (v0.37.0): re-runs
    the containment-check-aware album/book matcher and rewrites heuristic
    confidence + rationale immediately — no LLM round-trip. An existing LLM
    signal on the row is kept and re-blended. Publishes a 'rescore' SSE event."""
    rescored = skipped = 0
    db = SessionLocal()
    try:
        cfg, _ = load_settings(db)
        q = db.query(FailedImport).filter(
            FailedImport.source_app.in_(("lidarr", "readarr")))
        if ids:
            q = q.filter(FailedImport.id.in_(ids))
        else:
            q = q.filter(FailedImport.status.in_(("suggested", "resolve_failed")))
        rows = q.order_by(FailedImport.created_at.desc()).limit(limit).all()
        lib_cache: dict[str, list] = {}
        hist_cache: dict[str, list] = {}
        for row in rows:
            try:
                updated = await rematch_music_row(db, row, lib_cache, hist_cache)
            except Exception as e:
                logger.info(f"Rescore skipped for {row.id}: {e}")
                skipped += 1
                continue
            if not updated:
                skipped += 1
                continue
            if row.llm_confidence is not None:
                row.confidence = blend_confidence(
                    row.heuristic_confidence, row.llm_confidence, cfg.llm_blend_weight)
            rescored += 1
        db.commit()
        auto_queued = await _queue_auto_imports(collect_auto_eligible(rows, cfg))
        # Only publish event if rows were actually processed (v0.43.0: silence idle runs)
        if rescored + skipped > 0:
            publish({"type": "rescore", "rescored": rescored, "skipped": skipped,
                     "auto_queued": auto_queued})
        return {"rescored": rescored, "skipped": skipped, "auto_queued": auto_queued}
    finally:
        db.close()


async def _llm_rescore_inner(ids: list[int] | None, limit: int, task_id: str) -> dict:
    global _llm_tray_scored, _llm_tray_skipped
    from app.services import tasks
    scored = skipped = 0
    auto_ids: list[int] = []
    db = SessionLocal()
    try:
        cfg, ollama = load_settings(db)
        if not ollama.task_enabled("match"):
            return {"scored": 0, "skipped": 0, "message": "LLM assist is not configured/enabled for import matching"}
        q = db.query(FailedImport)
        if ids:
            q = q.filter(FailedImport.id.in_(ids))
        else:
            q = q.filter(FailedImport.status.in_(("suggested", "resolve_failed")),
                         FailedImport.llm_confidence.is_(None))
        rows = q.order_by(FailedImport.created_at.desc()).limit(limit).all()
        # Align tray total with the real row count for this batch (may differ from
        # the estimate used at create/bump time). Never shrink below current.
        t = tasks.get_task(task_id)
        base_current = (t.current or 0) if t else 0
        needed = base_current + len(rows)
        if t and (t.total or 0) < needed:
            tasks.update_task(task_id, total=needed,
                              label=f"Scoring {needed} import(s) with the LLM")
        elif t and not t.total:
            tasks.update_task(task_id, total=len(rows),
                              label=f"Scoring {len(rows)} import(s) with the LLM")
        # Cache album/book libraries once per app for rematch (v0.34.0)
        _lib_cache: dict[str, list] = {}
        _hist_cache: dict[str, list] = {}

        for i, row in enumerate(rows, 1):
            cur = base_current + i
            try:
                await rematch_music_row(db, row, _lib_cache, _hist_cache)
            except Exception as e:
                logger.info(f"LLM rematch skipped for {row.id}: {e}")
            if not row.matched_title:
                skipped += 1
                _llm_tray_skipped += 1
                tasks.update_task(
                    task_id, current=cur,
                    message=f"{_llm_tray_scored} scored, {_llm_tray_skipped} skipped")
                continue
            if row.heuristic_confidence is None:
                row.heuristic_confidence = row.confidence
            if getattr(ollama, "compact_det_summary", True):
                det_summary = llm_assist.compact_det_summary(
                    row.match_rationale or "series/title heuristics only",
                    row.heuristic_confidence,
                    pack_label=row.pack if getattr(row, "pack", None) else None)
            else:
                det_summary = (row.match_rationale or "series/title heuristics only") + \
                    f" (heuristic confidence {row.heuristic_confidence})"
            try:
                meta = json.loads(row.raw_metadata or "{}")
            except (ValueError, TypeError):
                meta = {}
            ctx_parts = [
                f"Source app: {row.source_app}",
                f"Queue error: {(row.message or '')[:200]}",
            ]
            if row.source_app == "lidarr":
                evidence = llm_assist.music_match_evidence(
                    row.raw_title, row.matched_title)
                if evidence:
                    ctx_parts.insert(1, evidence)
            alts = (meta.get("alternate_titles") or "").strip()
            if alts:
                ctx_parts.append(f"Library alternate titles: {alts}")
            if row.pack:
                ctx_parts.append(f"Download type: season pack ({row.pack}).")
            capture: dict = {}
            rescore_context = " | ".join(ctx_parts)
            llm = await llm_assist.review_match(
                ollama.host, ollama.model_for("match"), row.raw_title, row.matched_title,
                det_summary=det_summary,
                context=rescore_context,
                api_style=ollama.api_style, template=ollama.match_prompt,
                # On-demand runs always ask for verdict + bullets (verbose tier).
                verbosity="verbose", model_size=ollama.model_size,
                keep_alive_minutes=ollama.keep_alive_minutes,
                reply_format="markdown", confidence_style=ollama.confidence_style,
                source_app=row.source_app,
                capture=capture,
                **llm_assist.prompt_kwargs(ollama),
                **llm_assist.inference_kwargs(ollama))
            checks = llm_assist.music_match_checks(row.raw_title, row.matched_title) \
                if row.source_app == "lidarr" else None
            pre_agrees = llm["agrees"] if llm else None
            if llm and row.source_app == "lidarr":
                llm = llm_assist.enforce_music_evidence(llm, checks)
            if capture.get("replied"):
                from app.services import llm_match_log
                llm_match_log.record(
                    db, failed_import_id=row.id, site="rescore",
                    source_app=row.source_app, model=ollama.model_for("match"),
                    release_title=row.raw_title, candidate_title=row.matched_title,
                    context=rescore_context, det_summary=det_summary,
                    capture=capture, checks=checks,
                    agrees=llm["agrees"] if llm else None,
                    confidence_adjustment=llm["confidence_adjustment"] if llm else None,
                    enforced=bool(llm and pre_agrees is not None and llm["agrees"] != pre_agrees))
                db.commit()
            if not llm:
                skipped += 1
                _llm_tray_skipped += 1
                tasks.update_task(
                    task_id, current=cur,
                    message=f"{_llm_tray_scored} scored, {_llm_tray_skipped} skipped")
                continue
            row.llm_confidence = round(
                max(0.0, min(1.0, row.heuristic_confidence + llm["confidence_adjustment"])), 3)
            row.llm_rationale = llm["rationale"]
            row.llm_agrees = llm["agrees"]
            row.confidence = blend_confidence(row.heuristic_confidence, row.llm_confidence,
                                              cfg.llm_blend_weight)
            db.commit()
            scored += 1
            _llm_tray_scored += 1
            tasks.update_task(
                task_id, current=cur,
                message=f"{_llm_tray_scored} scored, {_llm_tray_skipped} skipped")
            if ollama.batch_delay_ms > 0:
                await asyncio.sleep(ollama.batch_delay_ms / 1000)
        auto_ids = collect_auto_eligible(rows, cfg)
    finally:
        db.close()
    logger.info(f"LLM rescore: {scored} scored, {skipped} skipped")
    auto_queued = await _queue_auto_imports(auto_ids)
    # Only publish event if items were actually processed (v0.43.0: silence idle runs)
    if scored + skipped > 0:
        publish({"type": "llm_run", "scored": scored, "skipped": skipped,
                 "auto_queued": auto_queued})
    return {"scored": scored, "skipped": skipped, "auto_queued": auto_queued,
            "message": f"{scored} scored, {skipped} skipped"}


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
