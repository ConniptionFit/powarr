"""One-line "why you might like this" blurbs from the local LLM (v0.101.0).

Built only from the evidence Powarr already holds (shared tags, the seeds that point at
the artist, listener count, latest release), never from the model's memory of the
artist, so it cannot invent facts. Fail-soft end to end: with no LLM configured, a
tripped breaker, or a busy slot, cards simply keep their data-based reason line.
"""
from __future__ import annotations

import json
import logging

from app.models.app_setting import AppSetting
from app.models.artist_discovery import DiscoveredArtist
from app.schemas.settings import OllamaSettings

logger = logging.getLogger("powarr")


def _llm_cfg(db) -> OllamaSettings | None:
    row = db.query(AppSetting).filter_by(key="ollama").first()
    try:
        cfg = OllamaSettings(**json.loads(row.value)) if row and row.value else OllamaSettings()
    except Exception:
        return None
    return cfg if (cfg.enabled and cfg.host and cfg.model_for("explain")) else None


def build_prompt(row: DiscoveredArtist) -> str:
    tags = json.loads(row.tags) if row.tags else (json.loads(row.genres) if row.genres else [])
    seeds = json.loads(row.seed_artist_names) if row.seed_artist_names else []
    facts = [f"Artist: {row.artist_name}"]
    if tags:
        facts.append("Tags: " + ", ".join(tags[:6]))
    if seeds:
        facts.append("Similar to artists the listener plays: " + ", ".join(seeds[:4]))
    if row.listeners:
        facts.append(f"Last.fm listeners: {row.listeners}")
    if row.latest_release:
        try:
            rel = json.loads(row.latest_release)
            facts.append(f"Latest release: {rel.get('title')} ({(rel.get('date') or '')[:4]})")
        except ValueError:
            pass
    return ("Write ONE short sentence (at most 25 words) telling a listener why they might like this artist. "
            "Use ONLY the facts below. Do not add anything else you think you know about the artist. "
            "No quotation marks, no preamble.\n\n" + "\n".join(facts))


async def blurb_for(db, row: DiscoveredArtist, cfg: OllamaSettings | None = None) -> str | None:
    from app.services import llm_assist
    cfg = cfg or _llm_cfg(db)
    if cfg is None:
        return None
    raw = await llm_assist._generate(
        cfg.host, cfg.model_for("explain"), build_prompt(row), cfg.api_style, json_format=False,
        model_size=cfg.model_size, keep_alive_minutes=cfg.keep_alive_minutes,
        temperature=0.3, max_tokens=80, timeout_seconds=cfg.timeout_seconds)
    if not raw:
        return None
    text = llm_assist._strip_think(raw).strip().strip('"').split("\n")[0].strip()
    return text[:220] or None


async def fill_blurbs(db, limit: int) -> int:
    """Generate blurbs for the best pending candidates that lack one. Returns how many."""
    cfg = _llm_cfg(db)
    if cfg is None or limit <= 0:
        return 0
    from app.services import llm_assist
    if not llm_assist.acquire_slot():
        return 0
    done = 0
    try:
        rows = (db.query(DiscoveredArtist)
                .filter(DiscoveredArtist.status == "pending", DiscoveredArtist.blurb.is_(None),
                        DiscoveredArtist.rank_score.isnot(None))
                .order_by(DiscoveredArtist.rank_score.desc()).limit(limit).all())
        for row in rows:
            if llm_assist.breaker_open():
                break
            text = await blurb_for(db, row, cfg)
            if text:
                row.blurb = text
                done += 1
        db.commit()
    finally:
        llm_assist.release_slot()
    return done
