"""Ollama embeddings for Artist Discovery (vector generation, not chat completion —
kept separate from llm_assist.py which is chat/generate only). Fail-soft: any error
returns None, never raises, matching the rest of the optional-LLM-assist philosophy."""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


def _base_url(host: str) -> str:
    host = (host or "").strip().rstrip("/")
    if not host:
        return ""
    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"
    return host


async def embed(host: str, model: str, text: str) -> list[float] | None:
    base = _base_url(host)
    if not base or not model or not text:
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{base}/api/embeddings", json={"model": model, "prompt": text})
            r.raise_for_status()
            data = r.json()
            vector = data.get("embedding")
            return vector if isinstance(vector, list) else None
    except Exception as e:
        logger.warning("embeddings: embed() failed for model=%s: %s", model, e)
        return None
