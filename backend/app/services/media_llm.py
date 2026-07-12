"""LLM deletion-rationale caching + on-demand batch run for Cleanup candidates.

Mirrors the Failed Imports LLM flow: sequential, single-flight (the shared slot in
llm_assist), fail-soft, one item per call. The cache is keyed by a hash of the
prompt template, model config, and the item's scoring-relevant fields — so a
changed template, model, verbosity, or score makes the cache miss without any
explicit invalidation bookkeeping."""
import asyncio
import hashlib
import json
import logging
from datetime import datetime

from app.database import SessionLocal
from app.models.app_setting import AppSetting
from app.models.media import MediaItem
from app.schemas.settings import CleanupSettings, LlmPolicies, OllamaSettings, ScoringWeights
from app.services import llm_assist
from app.services.import_matcher import publish
from app.services.scorer import (
    _item_score_dict, _series_watch_index, load_scoring_profiles, score_breakdown,
    weights_for_library,
)

logger = logging.getLogger("powarr")


def _explain_policy(ollama: OllamaSettings, policies: LlmPolicies,
                    library_section: str | None) -> tuple[bool, str]:
    """Effective (enabled, model) for one Plex library (LLM-08) — the global
    explain config with any per-library overlay applied on top. An overlay can
    only turn explain ON for a library when the master switch/host/model are
    configured at all; it can turn a specific library OFF (or override its
    model) independent of the others."""
    overlay = (policies.by_library or {}).get(library_section or "") or {}
    model = (overlay.get("explain_model") or "").strip() or ollama.model_for("explain")
    toggle = overlay.get("explain_enabled", ollama.explain_enabled)
    enabled = bool(ollama.enabled and ollama.host and model and toggle)
    return enabled, model


def rationale_key(ollama: OllamaSettings, item: MediaItem, policies: LlmPolicies | None = None) -> str:
    """Cache key for a stored rationale: prompt/model config + the item fields the
    prompt is built from. Any change to either regenerates on next request."""
    _, effective_model = _explain_policy(ollama, policies or LlmPolicies(),
                                         getattr(item, "library_section", None))
    payload = json.dumps({
        "template": ollama.explain_prompt,
        # Effective model for the explain task — the per-library override
        # (LLM-08) when set, otherwise == `model_for("explain")` (so pre-v0.68.0
        # cached rationales stay valid when no library override is configured).
        "model": effective_model,
        "api_style": ollama.api_style,
        "verbosity": ollama.verbosity,
        "model_size": ollama.model_size,
        "score": item.score,
        "watch_count": item.watch_count,
        "last_watched_at": item.last_watched_at.isoformat() if item.last_watched_at else None,
        "file_size": item.file_size,
        "library_section": getattr(item, "library_section", None),
        "protected": getattr(item, "protected", False),
        "watch_protected": getattr(item, "watch_protected", False),
        "seeding_protected": getattr(item, "seeding_protected", False),
        "progress_protected": getattr(item, "progress_protected", False),
        # Bump when item_summary shape changes so cached prose regenerates.
        # v3 (LLM-07): added protection-flag context below.
        "summary_v": 3,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def item_summary(item: MediaItem, db=None, *, series_idx: dict | None = None,
                  weights: ScoringWeights | None = None,
                  profiles=None) -> str:
    """Compact deletion-candidate line for the explain prompt (v0.31.0).

    Includes library section + per-factor score breakdown when a DB session (or
    preloaded series index / weights) is available; falls back to the aggregate
    score alone otherwise.
    """
    parts = [
        f"{item.title} ({item.year or 'unknown year'})",
        item.media_type or "unknown",
    ]
    if item.library_section:
        parts.append(f"library={item.library_section}")
    if item.parent_title and item.media_type in ("episode", "track"):
        parts.append(f"series={item.parent_title}")
    parts.append(f"{round((item.file_size or 0) / 1024**3, 1)} GB")
    parts.append(f"watched {item.watch_count or 0}x")
    parts.append(f"last watched {item.last_watched_at or 'never'}")

    # LLM-07 — name protection flags explicitly rather than leaving them implicit
    # in the score breakdown, so a "risky delete" second opinion (or the primary
    # rationale) can reason about the actual conflict instead of just the number.
    protections = []
    if item.protected:
        protections.append("Seerr-requested")
    if item.watch_protected:
        protections.append("watched by another household user recently")
    if item.seeding_protected:
        protections.append("actively seeding")
    if item.progress_protected:
        protections.append("in-progress watch")
    if protections:
        parts.append(f"PROTECTED: {', '.join(protections)}")

    breakdown = None
    if db is not None or (series_idx is not None and weights is not None):
        try:
            if weights is None and db is not None:
                weights = _load(db, "scoring_weights", ScoringWeights)
            if profiles is None and db is not None:
                profiles = load_scoring_profiles(db)
            if series_idx is None and db is not None:
                series_idx = _series_watch_index(db)
            eff = weights_for_library(weights, profiles, item.library_section)
            breakdown = score_breakdown(_item_score_dict(item, series_idx or {}), eff)
        except Exception as e:
            logger.info(f"item_summary breakdown unavailable: {e}")

    if breakdown:
        factors = breakdown["factors"]
        factor_bits = ", ".join(f"{k}={v:.2f}" for k, v in factors.items())
        series_note = "; series watched" if breakdown.get("series_watched") else ""
        parts.append(
            f"deletion score {breakdown['score']}/100 "
            f"(factors: {factor_bits or 'none'}{series_note})")
    else:
        parts.append(f"deletion score {item.score}/100")
    return ", ".join(parts)


async def generate_and_store(item: MediaItem, ollama: OllamaSettings, db,
                             policies: LlmPolicies | None = None) -> str | None:
    """One LLM call for one item; persists rationale + timestamp + cache key on
    success. Fail-soft: returns None and stores nothing on no-response."""
    policies = policies or LlmPolicies()
    _, effective_model = _explain_policy(ollama, policies, getattr(item, "library_section", None))
    rationale = await llm_assist.explain_deletion(
        ollama.host, effective_model, item_summary(item, db), ollama.api_style,
        template=ollama.explain_prompt, verbosity=ollama.verbosity,
        model_size=ollama.model_size, keep_alive_minutes=ollama.keep_alive_minutes,
        **llm_assist.prompt_kwargs(ollama),
        **llm_assist.inference_kwargs(ollama))
    if rationale:
        item.llm_rationale = rationale
        item.llm_rationale_at = datetime.utcnow()
        item.llm_rationale_key = rationale_key(ollama, item, policies)
        db.commit()
    return rationale


def _load(db, key: str, schema):
    row = db.query(AppSetting).filter_by(key=key).first()
    if not row or not row.value:
        return schema()
    return schema(**json.loads(row.value))


async def llm_media_run(ids: list[int] | None = None, limit: int = 50) -> dict:
    """On-demand LLM rationale generation for deletion candidates — the given ids,
    or the backlog of candidates without a current-key cached rationale. Sequential,
    holds the shared single-flight slot, publishes an SSE event when done."""
    if not llm_assist.acquire_slot():
        return {"scored": 0, "skipped": 0, "message": "An LLM run is already in progress"}
    from app.services import tasks
    task_id = tasks.create_task("llm_run", "Generating deletion rationales with the LLM")
    scored = skipped = 0
    try:
        db = SessionLocal()
        try:
            ollama = _load(db, "ollama", OllamaSettings)
            policies = _load(db, "llm_policies", LlmPolicies)
            # Coarse "connected at all" gate — a per-library override (LLM-08)
            # can enable explain for a specific library even when the global
            # explain_enabled toggle is off; real per-item enablement is
            # resolved per library_section inside eligible_candidates below.
            if not (ollama.enabled and ollama.host and ollama.model_for("explain")):
                tasks.finish_task(task_id, "done", "LLM assist is not configured/enabled for deletion rationales")
                return {"scored": 0, "skipped": 0, "message": "LLM assist is not configured/enabled for deletion rationales"}
            candidates = eligible_candidates(db, ollama, ids, limit, policies)
            tasks.update_task(task_id, total=len(candidates))
            for i, item in enumerate(candidates, 1):
                rationale = await generate_and_store(item, ollama, db, policies)
                if rationale:
                    scored += 1
                else:
                    skipped += 1
                tasks.update_task(task_id, current=i, message=f"{scored} scored, {skipped} skipped")
                if ollama.batch_delay_ms > 0:
                    await asyncio.sleep(ollama.batch_delay_ms / 1000)
        finally:
            db.close()
    except Exception as e:
        tasks.finish_task(task_id, "failed", str(e))
        raise
    finally:
        llm_assist.release_slot()
    logger.info(f"Media LLM run: {scored} scored, {skipped} skipped")
    publish({"type": "media_llm_run", "scored": scored, "skipped": skipped})
    tasks.finish_task(task_id, "done", f"{scored} scored, {skipped} skipped")
    return {"scored": scored, "skipped": skipped, "message": f"{scored} scored, {skipped} skipped"}


def _deletion_candidate_query(db):
    """Base query for the Cleanup deletion-candidate pool — same filters the
    Cleanup list applies. Shared by explain and second-opinion candidate
    selection; each caller still applies its own cache-key skip."""
    weights = _load(db, "scoring_weights", ScoringWeights)
    cleanup = _load(db, "cleanup", CleanupSettings)
    q = (db.query(MediaItem)
         .filter(MediaItem.score >= weights.min_score_threshold,
                 MediaItem.ignored.is_(False),
                 MediaItem.protected.isnot(True),
                 MediaItem.watch_protected.isnot(True),
                 MediaItem.pending_delete_at.is_(None)))
    if cleanup.excluded_libraries:
        q = q.filter(~MediaItem.library_section.in_(cleanup.excluded_libraries))
    return q.order_by(MediaItem.score.desc())


def eligible_candidates(db, ollama: OllamaSettings, ids: list[int] | None,
                        limit: int = 50, policies: LlmPolicies | None = None) -> list[MediaItem]:
    """Explicit ids as given; otherwise deletion candidates (same filters the
    Cleanup list applies). Either way, items with a current-key cached rationale
    are skipped — regenerating one is the per-item force=true explain path.
    Items whose library has explain disabled via a per-library override
    (LLM-08) are always skipped, even when explicit ids are given."""
    policies = policies or LlmPolicies()
    if ids:
        rows = db.query(MediaItem).filter(MediaItem.id.in_(ids)).all()
        return [r for r in rows
                if _explain_policy(ollama, policies, r.library_section)[0]
                and not (r.llm_rationale and r.llm_rationale_key == rationale_key(ollama, r, policies))
                ][:limit]
    out = []
    for item in _deletion_candidate_query(db).all():
        if not _explain_policy(ollama, policies, item.library_section)[0]:
            continue
        if item.llm_rationale and item.llm_rationale_key == rationale_key(ollama, item, policies):
            continue
        out.append(item)
        if len(out) >= limit:
            break
    return out


def second_opinion_key(ollama: OllamaSettings, item: MediaItem) -> str:
    """Cache key for a stored second opinion — same shape as rationale_key but
    keyed to the second_opinion task's own model/enable config, so switching
    models or re-scoring an item regenerates it independently of the primary
    rationale cache."""
    payload = json.dumps({
        "model": ollama.model_for("second_opinion"),
        "api_style": ollama.api_style,
        "model_size": ollama.model_size,
        "score": item.score,
        "watch_count": item.watch_count,
        "last_watched_at": item.last_watched_at.isoformat() if item.last_watched_at else None,
        "file_size": item.file_size,
        "library_section": getattr(item, "library_section", None),
        "protected": getattr(item, "protected", False),
        "watch_protected": getattr(item, "watch_protected", False),
        "seeding_protected": getattr(item, "seeding_protected", False),
        "progress_protected": getattr(item, "progress_protected", False),
        "summary_v": 3,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


async def generate_and_store_second_opinion(item: MediaItem, ollama: OllamaSettings, db) -> str | None:
    """LLM-07 — one bare KEEP/DELETE second-opinion call for one item; persists
    verdict + timestamp + cache key on success. Fail-soft: returns None and
    stores nothing on no-response. Reuses explain_deletion's minimal-verbosity
    salvage logic — no new prompt scaffold needed."""
    verdict = await llm_assist.explain_deletion(
        ollama.host, ollama.model_for("second_opinion"), item_summary(item, db), ollama.api_style,
        template=ollama.explain_prompt, verbosity="minimal",
        model_size=ollama.model_size, keep_alive_minutes=ollama.keep_alive_minutes,
        **llm_assist.prompt_kwargs(ollama),
        **llm_assist.inference_kwargs(ollama))
    if verdict:
        item.llm_second_opinion = verdict
        item.llm_second_opinion_at = datetime.utcnow()
        item.llm_second_opinion_key = second_opinion_key(ollama, item)
        db.commit()
    return verdict


def eligible_second_opinion_candidates(db, ollama: OllamaSettings, ids: list[int] | None,
                                       limit: int = 50) -> list[MediaItem]:
    """Same candidate pool as eligible_candidates, keyed to the second-opinion
    cache instead of the primary rationale cache."""
    if ids:
        rows = db.query(MediaItem).filter(MediaItem.id.in_(ids)).all()
        return [r for r in rows
                if not (r.llm_second_opinion and r.llm_second_opinion_key == second_opinion_key(ollama, r))
                ][:limit]
    out = []
    for item in _deletion_candidate_query(db).all():
        if item.llm_second_opinion and item.llm_second_opinion_key == second_opinion_key(ollama, item):
            continue
        out.append(item)
        if len(out) >= limit:
            break
    return out


async def llm_second_opinion_run(ids: list[int] | None = None, limit: int = 50) -> dict:
    """LLM-07 — on-demand batch second-opinion run, mirrors llm_media_run."""
    if not llm_assist.acquire_slot():
        return {"scored": 0, "skipped": 0, "message": "An LLM run is already in progress"}
    from app.services import tasks
    task_id = tasks.create_task("llm_second_opinion_run", "Running LLM second opinions on deletion candidates")
    scored = skipped = 0
    try:
        db = SessionLocal()
        try:
            ollama = _load(db, "ollama", OllamaSettings)
            if not ollama.task_enabled("second_opinion"):
                tasks.finish_task(task_id, "done", "LLM second opinion is not configured/enabled")
                return {"scored": 0, "skipped": 0, "message": "LLM second opinion is not configured/enabled"}
            candidates = eligible_second_opinion_candidates(db, ollama, ids, limit)
            tasks.update_task(task_id, total=len(candidates))
            for i, item in enumerate(candidates, 1):
                verdict = await generate_and_store_second_opinion(item, ollama, db)
                if verdict:
                    scored += 1
                else:
                    skipped += 1
                tasks.update_task(task_id, current=i, message=f"{scored} scored, {skipped} skipped")
                if ollama.batch_delay_ms > 0:
                    await asyncio.sleep(ollama.batch_delay_ms / 1000)
        finally:
            db.close()
    except Exception as e:
        tasks.finish_task(task_id, "failed", str(e))
        raise
    finally:
        llm_assist.release_slot()
    logger.info(f"Media LLM second-opinion run: {scored} scored, {skipped} skipped")
    publish({"type": "media_llm_second_opinion_run", "scored": scored, "skipped": skipped})
    tasks.finish_task(task_id, "done", f"{scored} scored, {skipped} skipped")
    return {"scored": scored, "skipped": skipped, "message": f"{scored} scored, {skipped} skipped"}
