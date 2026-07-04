"""Optional local-LLM assist. Every function fails soft: no response = no assist,
never an exception that blocks the caller. Only individual candidates are sent, never bulk data.

Supports two API styles: "ollama" (native /api/tags + /api/generate) and "openai"
(OpenAI-compatible /v1/models + /v1/chat/completions — LM Studio, llama.cpp server, etc.)."""
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


async def list_models(host: str, api_style: str = "ollama") -> dict[str, Any]:
    base = _base_url(host)
    if not base:
        return {"ok": False, "models": [], "message": "No LLM host configured"}
    try:
        async with httpx.AsyncClient(timeout=TAGS_TIMEOUT, follow_redirects=True) as client:
            if api_style == "openai":
                r = await client.get(f"{base}/v1/models")
                r.raise_for_status()
                models = [m.get("id") for m in r.json().get("data", []) if m.get("id")]
            else:
                r = await client.get(f"{base}/api/tags")
                r.raise_for_status()
                models = [m.get("name") for m in r.json().get("models", []) if m.get("name")]
            return {"ok": True, "models": models, "message": f"{len(models)} model(s) available"}
    except Exception as e:
        return {"ok": False, "models": [], "message": f"LLM host unreachable: {e}"}


async def test_connection(host: str, model: str = "", api_style: str = "ollama") -> dict[str, Any]:
    result = await list_models(host, api_style)
    if not result["ok"]:
        return {"ok": False, "message": result["message"], "version": None}
    if model and model not in result["models"]:
        return {"ok": True, "message": f"Connected, but model '{model}' not in list (may still work)", "version": None}
    return {"ok": True, "message": f"Connected — {result['message']}", "version": None}


async def _generate(host: str, model: str, prompt: str, api_style: str = "ollama",
                    json_format: bool = True) -> Optional[str]:
    """Single short completion. Returns raw text or None on any failure."""
    base = _base_url(host)
    if not base or not model:
        return None
    try:
        async with httpx.AsyncClient(timeout=GENERATE_TIMEOUT, follow_redirects=True) as client:
            if api_style == "openai":
                body: dict[str, Any] = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                    "max_tokens": 160,
                }
                if json_format:
                    body["response_format"] = {"type": "json_object"}
                r = await client.post(f"{base}/v1/chat/completions", json=body)
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"]
            body = {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.0, "num_predict": 160},
            }
            if json_format:
                body["format"] = "json"
            r = await client.post(f"{base}/api/generate", json=body)
            r.raise_for_status()
            return r.json().get("response", "")
    except Exception as e:
        logger.info(f"LLM assist unavailable: {e}")
        return None


def _parse_json(raw: str) -> Optional[dict]:
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw or "", re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None


async def score_candidate(host: str, model: str, release_title: str,
                          candidate_title: str, context: str = "",
                          api_style: str = "ollama") -> Optional[dict[str, Any]]:
    """Ask the local LLM how confident it is that a release belongs to a candidate.
    Returns {"confidence": float 0-1, "rationale": str} or None (= no assist available)."""
    prompt = (
        "You match download release names to media library entries.\n"
        f"Release name: {release_title}\n"
        f"Candidate library entry: {candidate_title}\n"
        f"{f'Context: {context}' if context else ''}\n"
        'Reply with ONLY a JSON object: {"confidence": <0.0-1.0>, "reason": "<short reason>"}'
    )
    raw = await _generate(host, model, prompt, api_style)
    if raw is None:
        return None
    parsed = _parse_json(raw)
    if not parsed:
        return None
    try:
        confidence = max(0.0, min(1.0, float(parsed.get("confidence"))))
    except (TypeError, ValueError):
        return None
    return {"confidence": confidence, "rationale": str(parsed.get("reason", ""))[:500]}


async def explain_deletion(host: str, model: str, item_summary: str,
                           api_style: str = "ollama") -> Optional[str]:
    """One-line rationale for why an item is (or isn't) a good deletion candidate.
    Returns the sentence or None (= no assist available)."""
    prompt = (
        "You review media-library deletion candidates. In ONE short sentence, say whether "
        "this item looks like a good deletion candidate and why.\n"
        f"Item: {item_summary}"
    )
    raw = await _generate(host, model, prompt, api_style, json_format=False)
    if not raw:
        return None
    return raw.strip().split("\n")[0][:300]
