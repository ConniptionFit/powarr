"""Ollama embeddings for Artist Discovery (vector generation, not chat completion —
kept separate from llm_assist.py which is chat/generate only). Fail-soft: any error
returns None, never raises, matching the rest of the optional-LLM-assist philosophy."""
from __future__ import annotations

import logging

import httpx

from app.services.llm_assist import COLD_START_GRACE, _base_url, _timeout, describe_error

logger = logging.getLogger(__name__)

EMBED_TIMEOUT = 30
_warm: set[tuple[str, str]] = set()


async def embed(host: str, model: str, text: str) -> list[float] | None:
    """Embed one string. Uses Ollama's current /api/embed ({"input"} -> {"embeddings":
    [[...]]}); falls back to the legacy /api/embeddings ({"prompt"} -> {"embedding"})
    only when the host 404s the new route (Ollama < 0.1.26)."""
    base = _base_url(host)
    if not base or not model or not text:
        return None
    key = (base, model)
    timeout = EMBED_TIMEOUT + (0 if key in _warm else COLD_START_GRACE)
    try:
        async with httpx.AsyncClient(timeout=_timeout(timeout)) as client:
            r = await client.post(f"{base}/api/embed", json={"model": model, "input": text})
            if r.status_code == 404 and "not found" not in (_error_text(r)).lower():
                r = await client.post(f"{base}/api/embeddings", json={"model": model, "prompt": text})
                r.raise_for_status()
                vector = r.json().get("embedding")
            else:
                r.raise_for_status()
                vectors = r.json().get("embeddings")
                vector = vectors[0] if isinstance(vectors, list) and vectors else None
            if isinstance(vector, list) and vector:
                _warm.add(key)
                return vector
            return None
    except Exception as e:
        logger.warning("embeddings: embed() failed for model=%s at %s: %s", model, base, describe_error(e))
        return None


def _error_text(r: httpx.Response) -> str:
    try:
        body = r.json()
        return str(body.get("error", "")) if isinstance(body, dict) else ""
    except Exception:
        return ""
