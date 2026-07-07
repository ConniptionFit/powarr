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

# Hard backstop caps on every value substituted into a prompt (chars). Normal
# values never come close — these only trigger on pathological release names or
# huge queue/Seerr messages, keeping a single bad field from blowing the context.
CAP_RELEASE = 300
CAP_CANDIDATE = 300
CAP_CONTEXT = 400
CAP_ITEM = 500
CAP_DET_SUMMARY = 600
CAP_FILES = 800

DEFAULT_MATCH_PROMPT = (
    "You match download release names to media library entries.\n"
    "Release name: {release}\n"
    "Candidate library entry: {candidate}\n"
    "Context: {context}"
)

DEFAULT_PACK_PROMPT = (
    "You match individual files within a season/series pack to episodes in a media library.\n"
    "Pack: {release}\n"
    "Library entry: {candidate}\n"
    "Files in download: {files}\n"
    "Context: {context}"
)

DEFAULT_EXPLAIN_PROMPT = (
    "You review media-library deletion candidates. Assess whether this item looks "
    "like a good deletion candidate and why.\n"
    "Item: {item}"
)


def _truncate(text: Any, limit: int) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _base_url(host: str) -> str:
    host = (host or "").strip().rstrip("/")
    if not host:
        return ""
    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"
    return host


def build_review_prompt(template: str, release: str, candidate: str, context: str,
                        det_summary: str, verbosity: str = "brief",
                        reply_format: str = "json",
                        confidence_style: str = "numeric") -> str:
    """Static scaffold around the (optionally user-templated) match prompt. The
    deterministic scorer's per-variable results are always injected verbatim —
    the LLM reviews the deterministic decision, it never replaces it.

    verbosity: minimal (bare verdict, no adjustment/reason) | brief | verbose.
    reply_format: json | simple (one pipe-separated line, for models that can't
    reliably produce JSON) | markdown (same JSON shape as "json", but the "reason"
    field is asked for in Markdown — bold/bullets — for a richer rendered display).
    confidence_style: numeric (model picks a ±0.3 float) | classified (model picks
    more/less/same — mapped to fixed steps internally, since small models classify
    far better than they calibrate numbers)."""
    tpl = (template or "").strip() or DEFAULT_MATCH_PROMPT
    prompt = (tpl.replace("{release}", _truncate(release, CAP_RELEASE))
                 .replace("{candidate}", _truncate(candidate, CAP_CANDIDATE))
                 .replace("{context}", _truncate(context, CAP_CONTEXT)))
    prompt += ("\nDeterministic scorer result (computed from title/season/episode/"
               f"absolute-number comparisons): {_truncate(det_summary, CAP_DET_SUMMARY)}")
    prompt += (
        "\nJudging guidance: ignore file-quality/format/codec/audio details and uploader or "
        "release-group identifiers (resolution, source, codec, audio channels, HDR, encoder, "
        "release-group tag) — these describe the file, not the show or movie, and are never "
        "evidence for or against a match. If the release name is in a different language or "
        "script than the candidate's title (common for anime/international content), consider "
        "whether it could be a known translation, transliteration, or alternate title for the "
        "same work rather than judging on string similarity alone."
    )
    if reply_format == "simple":
        if verbosity == "minimal":
            prompt += "\nDo you agree with this match? Reply with ONLY one word: agree or disagree."
        elif confidence_style == "classified":
            prompt += ("\nDo you agree with this match, and does the evidence make you more, "
                       "less, or same confident than the scorer? Reply with ONLY one line, "
                       "exactly this form:\nagree or disagree | more or less or same | short reason")
        else:
            prompt += ("\nDo you agree with this match? Reply with ONLY one line, exactly this "
                       "form:\nagree or disagree | confidence adjustment between -0.3 and 0.3 | short reason")
    else:
        if verbosity == "minimal":
            prompt += ('\nDo you agree with this match? Reply with ONLY a JSON object: '
                       '{"agrees": true|false}')
        else:
            reason_spec = ("a detailed 2-3 sentence explanation citing the specific factors"
                           if verbosity == "verbose" else "<short reason>")
            if reply_format == "markdown":
                reason_spec += (" — write it using Markdown formatting (bold key terms with "
                                "**text**, use a bullet list with \"- \" if citing multiple "
                                "factors) for a nicer-looking display")
            if confidence_style == "classified":
                prompt += ('\nDo you agree with this match? Reply with ONLY a JSON object: '
                           f'{{"agrees": true|false, "confidence_shift": "more"|"less"|"same", "reason": "{reason_spec}"}}')
            else:
                prompt += ('\nDo you agree with this match? Reply with ONLY a JSON object: '
                           f'{{"agrees": true|false, "confidence_adjustment": <-0.3 to 0.3>, "reason": "{reason_spec}"}}')
    return prompt


def build_explain_prompt(template: str, item_summary: str, verbosity: str = "brief") -> str:
    tpl = (template or "").strip() or DEFAULT_EXPLAIN_PROMPT
    prompt = tpl.replace("{item}", _truncate(item_summary, CAP_ITEM))
    if verbosity == "minimal":
        prompt += "\nReply with ONLY one word: KEEP or DELETE."
    elif verbosity == "verbose":
        prompt += "\nAnswer in 3-4 sentences citing the concrete factors."
    else:
        prompt += "\nAnswer in ONE short sentence."
    return prompt


def build_pack_prompt(template: str, release: str, candidate: str, files_list: str,
                      context: str, verbosity: str = "brief") -> str:
    """Pack-matching prompt: match individual files in a download to episodes.
    Files should be: "filename1.mkv, filename2.mkv, ..." (list only, no paths)."""
    tpl = (template or "").strip() or DEFAULT_PACK_PROMPT
    prompt = (tpl.replace("{release}", _truncate(release, CAP_RELEASE))
                 .replace("{candidate}", _truncate(candidate, CAP_CANDIDATE))
                 .replace("{files}", _truncate(files_list, CAP_FILES))
                 .replace("{context}", _truncate(context, CAP_CONTEXT)))
    if verbosity == "minimal":
        prompt += ('\nFor each file, reply with ONLY a JSON array: '
                   '[{"file": "filename.mkv", "season": 1, "episode": 1, "confidence": "high"|"medium"|"low"}]')
    else:
        reason_spec = ("with brief reasoning" if verbosity == "verbose"
                      else "with brief reasoning if any uncertainty")
        prompt += (f'\nFor each file, reply with ONLY a JSON array: '
                   f'[{{"file": "filename.mkv", "season": 1, "episode": 1, '
                   f'"confidence": "high"|"medium"|"low", "reason": "{reason_spec}"}}]')
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


async def get_model_context_length(host: str, model: str,
                                    api_style: str = "ollama") -> Optional[int]:
    """The model's real context window, from Ollama's /api/show model_info (the
    field name varies by family, e.g. "llama.context_length"). No standard way to
    query this for openai-style servers — returns None there, and fails soft to
    None on any error or unrecognized payload."""
    base = _base_url(host)
    if not base or not model or api_style == "openai":
        return None
    try:
        async with httpx.AsyncClient(timeout=TAGS_TIMEOUT, follow_redirects=True) as client:
            r = await client.post(f"{base}/api/show", json={"model": model, "name": model})
            r.raise_for_status()
            info = r.json().get("model_info") or {}
            for key, value in info.items():
                if key.endswith(".context_length") and isinstance(value, (int, float)):
                    return int(value)
    except Exception as e:
        logger.info(f"Context-length lookup unavailable: {e}")
    return None


async def test_connection(host: str, model: str = "", api_style: str = "ollama") -> dict[str, Any]:
    result = await list_models(host, api_style)
    if not result["ok"]:
        return {"ok": False, "message": result["message"], "version": None}
    if model and model not in result["models"]:
        return {"ok": True, "message": f"Connected, but model '{model}' not in list (may still work)", "version": None}
    return {"ok": True, "message": f"Connected — {result['message']}", "version": None}


def _limits(model_size: str, verbose: bool) -> tuple[int, int]:
    """(max_tokens, timeout_s) scaled by the model-size profile. `small` is capped
    hard even in verbose mode — a 1-3B model asked for paragraphs tends to ramble
    or hallucinate past what it can coherently produce."""
    if model_size == "small":
        return 96, 15
    if verbose:
        return (600, 60) if model_size == "large" else (400, GENERATE_TIMEOUT_VERBOSE)
    return 160, GENERATE_TIMEOUT


async def _generate(host: str, model: str, prompt: str, api_style: str = "ollama",
                    json_format: bool = True, verbose: bool = False,
                    model_size: str = "medium", keep_alive_minutes: int = 10) -> Optional[str]:
    """Single short completion. Returns raw text or None on any failure."""
    base = _base_url(host)
    if not base or not model:
        return None
    max_tokens, timeout = _limits(model_size, verbose)
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
            if keep_alive_minutes and keep_alive_minutes > 0:
                # Keeps the model loaded between sequential calls in a batch run —
                # ollama-only; the openai style (LM Studio, llama.cpp) has no equivalent.
                body["keep_alive"] = f"{int(keep_alive_minutes)}m"
            if json_format:
                body["format"] = "json"
            r = await client.post(f"{base}/api/generate", json=body)
            r.raise_for_status()
            return r.json().get("response", "")
    except Exception as e:
        logger.info(f"LLM assist unavailable: {e}")
        return None


_THINK_RE = re.compile(r"<think>.*?(?:</think>|\Z)", re.DOTALL | re.IGNORECASE)
# A partial "<think>" tag prefix at the very end of accumulated stream text —
# held back until enough bytes arrive to know whether it's a real tag.
_PARTIAL_THINK_RE = re.compile(r"<(?:t(?:h(?:i(?:n(?:k)?)?)?)?)?$", re.IGNORECASE)


def _stream_visible(acc: str) -> str:
    """The safely-displayable prefix of accumulated streaming text: complete and
    unclosed <think> blocks removed, plus any trailing partial '<think' tag held
    back (it may become a full tag on the next chunk). Monotonic in acc, so a
    streamer can emit only the delta beyond what it already sent."""
    vis = _THINK_RE.sub("", acc)
    m = _PARTIAL_THINK_RE.search(vis)
    if m:
        vis = vis[:m.start()]
    return vis


def _strip_think(text: str) -> str:
    """Reasoning models (e.g. lfm2.5, deepseek-r1) emit <think>...</think> blocks —
    strip them so chain-of-thought never leaks into stored output or JSON parsing.
    Also matches an *unclosed* block: when the token cap cuts generation off before
    </think>, everything from <think> on is chain-of-thought and must go (the reply
    then fails soft to None rather than leaking)."""
    return _THINK_RE.sub("", text or "").strip()


async def _generate_stream(host: str, model: str, prompt: str, api_style: str = "ollama",
                           verbose: bool = False, model_size: str = "medium",
                           keep_alive_minutes: int = 10):
    """Streaming counterpart of _generate: an async generator of raw text chunks.
    Fail-soft like everything else — any error just ends the stream."""
    base = _base_url(host)
    if not base or not model:
        return
    max_tokens, timeout = _limits(model_size, verbose)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            if api_style == "openai":
                body: dict[str, Any] = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                    "max_tokens": max_tokens,
                    "stream": True,
                }
                async with client.stream("POST", f"{base}/v1/chat/completions", json=body) as r:
                    r.raise_for_status()
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        delta = (json.loads(data).get("choices") or [{}])[0].get("delta", {}).get("content")
                        if delta:
                            yield delta
                return
            body = {
                "model": model,
                "prompt": prompt,
                "stream": True,
                "options": {"temperature": 0.0, "num_predict": max_tokens},
            }
            if keep_alive_minutes and keep_alive_minutes > 0:
                body["keep_alive"] = f"{int(keep_alive_minutes)}m"
            async with client.stream("POST", f"{base}/api/generate", json=body) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    obj = json.loads(line)
                    if obj.get("response"):
                        yield obj["response"]
                    if obj.get("done"):
                        break
    except Exception as e:
        logger.info(f"LLM stream unavailable: {e}")


async def explain_deletion_stream(host: str, model: str, item_summary: str,
                                  api_style: str = "ollama", template: str = "",
                                  verbosity: str = "brief", model_size: str = "medium",
                                  keep_alive_minutes: int = 10):
    """Streaming deletion rationale: yields displayable (think-stripped) text
    chunks, honoring the same caps as explain_deletion — brief stops at the first
    line / 300 chars, verbose at 1500. Minimal isn't streamed (a one-word verdict
    has nothing to stream) — callers use explain_deletion for it."""
    verbose = verbosity == "verbose"
    limit = 1500 if verbose else 300
    prompt = build_explain_prompt(template, item_summary, verbosity)
    emitted = 0
    acc = ""
    async for chunk in _generate_stream(host, model, prompt, api_style, verbose=verbose,
                                        model_size=model_size,
                                        keep_alive_minutes=keep_alive_minutes):
        acc += chunk
        vis = _stream_visible(acc).lstrip()
        done = False
        if not verbose and "\n" in vis:
            vis = vis.split("\n")[0]
            done = True
        if len(vis) >= limit:
            vis = vis[:limit]
            done = True
        if len(vis) > emitted:
            yield vis[emitted:]
            emitted = len(vis)
        if done:
            return


# Fixed steps for confidence_style="classified" — the model only classifies,
# it never chooses the magnitude.
_SHIFT_STEPS = {"more": 0.15, "same": 0.0, "less": -0.15}


def _parse_simple(raw: str) -> Optional[dict]:
    """Lenient parser for the non-JSON reply format:
    "agree|disagree [| <±float or more/less/same> [| reason]]" — also accepts a
    bare verdict word (minimal tier). Tolerant of missing pieces; returns the same
    key shape as the JSON reply, or None if no verdict is recognizable."""
    text = _strip_think(raw)
    if not text:
        return None
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    parts = [p.strip() for p in line.split("|")]
    head = parts[0].lower()
    if "{" in head:
        # JSON-looking reply — a key like "agrees" would false-positive the word
        # match below; let _parse_json handle it instead.
        return None
    # Only the words the prompt asks for count as a verdict — matching stray
    # "yes"/"no" in prose would turn arbitrary rambling into a verdict.
    if "disagree" in head:
        agrees = False
    elif "agree" in head:
        agrees = True
    else:
        return None
    out: dict[str, Any] = {"agrees": agrees}
    rest = parts[1:]
    if rest:
        num = re.search(r"[+-]?\d+(?:\.\d+)?", rest[0])
        shift = next((k for k in _SHIFT_STEPS if k in rest[0].lower()), None)
        if num:
            out["confidence_adjustment"] = float(num.group(0))
            rest = rest[1:]
        elif shift:
            out["confidence_shift"] = shift
            rest = rest[1:]
    if rest:
        out["reason"] = " | ".join(rest)
    return out


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


def _parse_pack_matches(raw: str) -> Optional[list[dict]]:
    """Parse per-file episode matches from LLM response. Expects JSON array."""
    raw = _strip_think(raw)
    if not raw:
        return None
    try:
        result = json.loads(raw)
        if isinstance(result, list):
            return result
    except Exception:
        m = re.search(r"\[.*\]", raw or "", re.DOTALL)
        if not m:
            return None
        try:
            result = json.loads(m.group(0))
            if isinstance(result, list):
                return result
        except Exception:
            pass
    return None


_slot_active = False


def acquire_slot() -> bool:
    """Single-flight guard shared by every on-demand LLM entry point (batch import
    runs and per-item explain) — weak hardware can usually serve one generation at
    a time. Synchronous check-and-set, so it's race-free under one event loop.
    Returns False if another task already holds the slot."""
    global _slot_active
    if _slot_active:
        return False
    _slot_active = True
    return True


def release_slot() -> None:
    global _slot_active
    _slot_active = False


def slot_active() -> bool:
    return _slot_active


def _extract_adjustment(parsed: dict) -> float:
    """Numeric adjustment from either reply shape: an explicit float (clamped ±0.3)
    or a more/less/same classification mapped to fixed steps."""
    try:
        adjustment = max(-0.3, min(0.3, float(parsed.get("confidence_adjustment") or 0.0)))
    except (TypeError, ValueError):
        adjustment = 0.0
    if not adjustment and "confidence_shift" in parsed:
        adjustment = _SHIFT_STEPS.get(str(parsed["confidence_shift"]).lower().strip(), 0.0)
    return adjustment


async def review_match(host: str, model: str, release_title: str,
                       candidate_title: str, det_summary: str, context: str = "",
                       api_style: str = "ollama", template: str = "",
                       verbosity: str = "brief", model_size: str = "medium",
                       keep_alive_minutes: int = 10, reply_format: str = "json",
                       confidence_style: str = "numeric") -> Optional[dict[str, Any]]:
    """Single structured LLM review of a deterministic match decision (one call —
    no separate match/explain prompts). Returns
    {"agrees": bool, "confidence_adjustment": float ±0.3, "rationale": str}
    or None (= no assist available). Supplements the deterministic rationale,
    never replaces it."""
    verbose = verbosity == "verbose"
    prompt = build_review_prompt(template, release_title, candidate_title, context,
                                 det_summary, verbosity, reply_format, confidence_style)
    raw = await _generate(host, model, prompt, api_style,
                          json_format=reply_format != "simple", verbose=verbose,
                          model_size=model_size, keep_alive_minutes=keep_alive_minutes)
    if raw is None:
        return None
    # Whichever format was asked for, accept the other as a fallback — a "json"
    # model that wrapped its verdict in prose, or a "simple" model that emitted
    # JSON anyway, still parses instead of being dropped.
    if reply_format == "simple":
        parsed = _parse_simple(raw) or _parse_json(raw)
    else:
        parsed = _parse_json(raw) or _parse_simple(raw)
    if (not parsed or "agrees" not in parsed) and verbosity == "minimal":
        # Reasoning models that emit <think> as plain text (e.g. lfm2.5) can burn
        # the whole token cap thinking and never emit the answer. Minimal asks for
        # one verdict word — salvaging the model's last stated verdict from the raw
        # text is not CoT leakage (one word, exactly what was asked), and in
        # minimal mode the verdict carries no confidence adjustment regardless.
        words = re.findall(r"\b(disagree|agree)", (raw or "").lower())
        if words:
            parsed = {"agrees": words[-1] == "agree"}
    if not parsed or "agrees" not in parsed:
        return None
    agrees = bool(parsed["agrees"])
    if verbosity == "minimal":
        # Minimal tier asks for the verdict only — never trust stray extras.
        adjustment, reason = 0.0, ""
    else:
        adjustment = _extract_adjustment(parsed)
        reason = str(parsed.get("reason", ""))
    if not agrees:
        # Small models sometimes disagree yet return a positive adjustment —
        # a disagreement must never raise confidence.
        adjustment = min(0.0, adjustment)
    limit = 1500 if verbose else 500
    return {"agrees": agrees,
            "confidence_adjustment": adjustment,
            "rationale": reason[:limit]}


async def review_pack_files(host: str, model: str, release_title: str,
                             candidate_title: str, file_names: list[str],
                             api_style: str = "ollama", template: str = "",
                             verbosity: str = "brief", model_size: str = "medium",
                             keep_alive_minutes: int = 10) -> Optional[list[dict]]:
    """Per-file episode matching for season/series packs. Returns list of
    [{"file": "...", "season": int, "episode": int, "confidence": "high|medium|low", "reason": "..."}]
    or None if unavailable. Fails soft."""
    verbose = verbosity == "verbose"
    files_str = ", ".join(file_names[:50]) if file_names else "No files listed"
    context = f"This is a multi-file download (pack). Match each file to its episode."
    prompt = build_pack_prompt(template, release_title, candidate_title, files_str,
                              context, verbosity)
    raw = await _generate(host, model, prompt, api_style, json_format=True,
                         verbose=verbose, model_size=model_size,
                         keep_alive_minutes=keep_alive_minutes)
    if raw is None:
        return None
    parsed = _parse_pack_matches(raw)
    if not parsed or not isinstance(parsed, list):
        return None
    # Validate and clean results — ensure each has file, season, episode
    validated = []
    for item in parsed:
        if isinstance(item, dict) and "file" in item and "season" in item and "episode" in item:
            try:
                validated.append({
                    "file": str(item["file"])[:200],
                    "season": int(item.get("season", 0)),
                    "episode": int(item.get("episode", 0)),
                    "confidence": str(item.get("confidence", "medium")).lower()[:20],
                    "reason": str(item.get("reason", ""))[:300]
                })
            except (TypeError, ValueError):
                continue
    return validated if validated else None


async def explain_deletion(host: str, model: str, item_summary: str,
                           api_style: str = "ollama", template: str = "",
                           verbosity: str = "brief", model_size: str = "medium",
                           keep_alive_minutes: int = 10) -> Optional[str]:
    """Deletion-candidate rationale. Returns the text or None (= no assist available).
    Minimal verbosity returns a bare KEEP/DELETE verdict."""
    verbose = verbosity == "verbose"
    prompt = build_explain_prompt(template, item_summary, verbosity)
    raw = await _generate(host, model, prompt, api_style, json_format=False, verbose=verbose,
                          model_size=model_size, keep_alive_minutes=keep_alive_minutes)
    if not raw:
        return None
    text = _strip_think(raw)
    if verbosity == "minimal":
        # Same salvage rule as review_match: if a plain-text-thinking model got cut
        # off mid-<think>, its last stated KEEP/DELETE is still the one word we
        # asked for — extracting it is not CoT leakage.
        matches = re.findall(r"\b(keep|delete)\b", text or raw, re.IGNORECASE)
        if matches:
            return matches[-1].upper()
        return _truncate(text.split("\n")[0], 60) if text else None
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
