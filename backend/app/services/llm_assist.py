"""Optional local-LLM assist. Every function fails soft: no response = no assist,
never an exception that blocks the caller. Only individual candidates are sent, never bulk data.

Supports two API styles: "ollama" (native /api/tags + /api/generate) and "openai"
(OpenAI-compatible /v1/models + /v1/chat/completions — LM Studio, llama.cpp server, etc.).

Prompts are templated: users may override the defaults in Settings → LLM Assist.
Placeholders — match: {release} {candidate} {context}; explain: {item}. The JSON
reply instruction is always appended for match prompts so custom templates still parse."""
import json
import logging
import re
from typing import Any, Optional

import httpx

logger = logging.getLogger("powarr")

TAGS_TIMEOUT = 5
GENERATE_TIMEOUT = 20
GENERATE_TIMEOUT_VERBOSE = 45

DEFAULT_MATCH_PROMPT = (
    "You match download release names to media library entries.\n"
    "Release name: {release}\n"
    "Candidate library entry: {candidate}\n"
    "Context: {context}"
)

DEFAULT_EXPLAIN_PROMPT = (
    "You review media-library deletion candidates. Assess whether this item looks "
    "like a good deletion candidate and why.\n"
    "Item: {item}"
)


def _base_url(host: str) -> str:
    host = (host or "").strip().rstrip("/")
    if not host:
        return ""
    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"
    return host


def build_match_prompt(template: str, release: str, candidate: str, context: str,
                       verbose: bool = False) -> str:
    tpl = (template or "").strip() or DEFAULT_MATCH_PROMPT
    prompt = (tpl.replace("{release}", release)
                 .replace("{candidate}", candidate)
                 .replace("{context}", context))
    reason_spec = ("a detailed 2-3 sentence explanation citing the specific factors"
                   if verbose else "<short reason>")
    prompt += ('\nReply with ONLY a JSON object: '
               f'{{"confidence": <0.0-1.0>, "reason": "{reason_spec}"}}')
    return prompt


def build_explain_prompt(template: str, item_summary: str, verbose: bool = False) -> str:
    tpl = (template or "").strip() or DEFAULT_EXPLAIN_PROMPT
    prompt = tpl.replace("{item}", item_summary)
    prompt += ("\nAnswer in 3-4 sentences citing the concrete factors."
               if verbose else "\nAnswer in ONE short sentence.")
    return prompt


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
                    json_format: bool = True, verbose: bool = False) -> Optional[str]:
    """Single short completion. Returns raw text or None on any failure."""
    base = _base_url(host)
    if not base or not model:
        return None
    timeout = GENERATE_TIMEOUT_VERBOSE if verbose else GENERATE_TIMEOUT
    max_tokens = 400 if verbose else 160
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            if api_style == "openai":
                body: dict[str, Any] = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                    "max_tokens": max_tokens,
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
                "options": {"temperature": 0.0, "num_predict": max_tokens},
            }
            if json_format:
                body["format"] = "json"
            r = await client.post(f"{base}/api/generate", json=body)
            r.raise_for_status()
            return r.json().get("response", "")
    except Exception as e:
        logger.info(f"LLM assist unavailable: {e}")
        return None


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_think(text: str) -> str:
    """Reasoning models (e.g. lfm2.5, deepseek-r1) emit <think>...</think> blocks —
    strip them so chain-of-thought never leaks into stored output or JSON parsing."""
    return _THINK_RE.sub("", text or "").strip()


def _parse_json(raw: str) -> Optional[dict]:
    raw = _strip_think(raw)
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
                          api_style: str = "ollama", template: str = "",
                          verbose: bool = False) -> Optional[dict[str, Any]]:
    """Ask the local LLM how confident it is that a release belongs to a candidate.
    Returns {"confidence": float 0-1, "rationale": str} or None (= no assist available)."""
    prompt = build_match_prompt(template, release_title, candidate_title, context, verbose)
    raw = await _generate(host, model, prompt, api_style, verbose=verbose)
    if raw is None:
        return None
    parsed = _parse_json(raw)
    if not parsed:
        return None
    try:
        confidence = max(0.0, min(1.0, float(parsed.get("confidence"))))
    except (TypeError, ValueError):
        return None
    limit = 1500 if verbose else 500
    return {"confidence": confidence, "rationale": str(parsed.get("reason", ""))[:limit]}


async def explain_deletion(host: str, model: str, item_summary: str,
                           api_style: str = "ollama", template: str = "",
                           verbose: bool = False) -> Optional[str]:
    """Deletion-candidate rationale. Returns the text or None (= no assist available)."""
    prompt = build_explain_prompt(template, item_summary, verbose)
    raw = await _generate(host, model, prompt, api_style, json_format=False, verbose=verbose)
    if not raw:
        return None
    text = _strip_think(raw)
    if not text:
        return None
    return (text if verbose else text.split("\n")[0])[:1500 if verbose else 300]


async def refine_prompt(host: str, model: str, draft: str, task: str,
                        api_style: str = "ollama") -> Optional[str]:
    """Clean up a user's rough prompt draft into a solid template. Fails soft."""
    placeholders = "{release}, {candidate}, {context}" if task == "match" else "{item}"
    prompt = (
        "You improve prompt templates for a media-management tool.\n"
        f"Task the template is for: {'matching download release names to library entries' if task == 'match' else 'explaining whether a media item is a good deletion candidate'}.\n"
        f"Rewrite the rough draft below into a clear, effective prompt template. "
        f"Keep it concise. You MUST preserve these placeholders exactly: {placeholders}. "
        "Do not add a reply-format instruction (the app appends one). "
        "Reply with ONLY the improved template text.\n\n"
        f"Rough draft:\n{draft}"
    )
    raw = await _generate(host, model, prompt, api_style, json_format=False, verbose=True)
    refined = _strip_think(raw) if raw else ""
    return refined or None
