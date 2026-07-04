"""Optional local-LLM (Ollama) assist. Every function fails soft: no response = no assist,
never an exception that blocks the caller. Only individual candidates are sent, never bulk data."""
import json
import logging
import re
from typing import Any, Optional

import httpx

logger = logging.getLogger("powarr")

TAGS_TIMEOUT = 5
GENERATE_TIMEOUT = 20


def _base_url(host: str) -> str:
    host = (host or "").strip().rstrip("/")
    if not host:
        return ""
    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"
    return host


async def list_models(host: str) -> dict[str, Any]:
    base = _base_url(host)
    if not base:
        return {"ok": False, "models": [], "message": "No Ollama host configured"}
    try:
        async with httpx.AsyncClient(timeout=TAGS_TIMEOUT, follow_redirects=True) as client:
            r = await client.get(f"{base}/api/tags")
            r.raise_for_status()
            models = [m.get("name") for m in r.json().get("models", []) if m.get("name")]
            return {"ok": True, "models": models, "message": f"{len(models)} model(s) available"}
    except Exception as e:
        return {"ok": False, "models": [], "message": f"Ollama unreachable: {e}"}


async def test_connection(host: str, model: str = "") -> dict[str, Any]:
    result = await list_models(host)
    if not result["ok"]:
        return {"ok": False, "message": result["message"], "version": None}
    if model and model not in result["models"]:
        return {"ok": True, "message": f"Connected, but model '{model}' not in list (may still work)", "version": None}
    return {"ok": True, "message": f"Connected — {result['message']}", "version": None}


async def score_candidate(host: str, model: str, release_title: str,
                          candidate_title: str, context: str = "") -> Optional[dict[str, Any]]:
    """Ask the local LLM how confident it is that a release belongs to a candidate.
    Returns {"confidence": float 0-1, "rationale": str} or None (= no assist available)."""
    base = _base_url(host)
    if not base or not model:
        return None
    prompt = (
        "You match download release names to media library entries.\n"
        f"Release name: {release_title}\n"
        f"Candidate library entry: {candidate_title}\n"
        f"{f'Context: {context}' if context else ''}\n"
        'Reply with ONLY a JSON object: {"confidence": <0.0-1.0>, "reason": "<short reason>"}'
    )
    try:
        async with httpx.AsyncClient(timeout=GENERATE_TIMEOUT, follow_redirects=True) as client:
            r = await client.post(f"{base}/api/generate", json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.0, "num_predict": 128},
            })
            r.raise_for_status()
            raw = r.json().get("response", "")
    except Exception as e:
        logger.info(f"LLM assist unavailable: {e}")
        return None

    try:
        parsed = json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        try:
            parsed = json.loads(m.group(0))
        except Exception:
            return None
    try:
        confidence = max(0.0, min(1.0, float(parsed.get("confidence"))))
    except (TypeError, ValueError):
        return None
    return {"confidence": confidence, "rationale": str(parsed.get("reason", ""))[:500]}
