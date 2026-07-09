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
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger("powarr")


# --- Call stats + circuit breaker (v0.27.0, Approved Queue #7) -----------------
# In-memory only (like services/tasks.py) — resets on restart, no DB. Every real
# LLM call funnels through _generate/_generate_stream, so recording there covers
# match review, pack review, rationales, streams, and previews alike. The breaker
# preserves the fail-soft contract: while open, calls return None immediately
# instead of hammering a downed/overloaded host on every scan cycle.

_breaker_threshold = 5       # consecutive failures that open the breaker; 0 = disabled
_breaker_cooldown_s = 600.0  # how long an open breaker rejects calls before retrying


def _fresh_stats() -> dict[str, Any]:
    return {"calls": 0, "successes": 0, "failures": 0, "consecutive_failures": 0,
            "total_latency_ms": 0, "last_error": None, "last_error_at": None,
            "last_success_at": None, "breaker_open_until": 0.0, "breaker_trips": 0}


_stats = _fresh_stats()


def set_breaker_config(threshold: int, cooldown_minutes: int) -> None:
    """Applied at startup and whenever the LLM settings are saved."""
    global _breaker_threshold, _breaker_cooldown_s
    _breaker_threshold = max(0, int(threshold or 0))
    _breaker_cooldown_s = max(1, int(cooldown_minutes or 0)) * 60.0


def breaker_open(now: Optional[float] = None) -> bool:
    return (now if now is not None else time.monotonic()) < _stats["breaker_open_until"]


def record_result(ok: bool, latency_ms: int, error: str = "",
                  now: Optional[float] = None) -> None:
    """One call's outcome. A success closes the breaker and resets the failure
    streak; hitting the threshold-th consecutive failure opens it for the cooldown."""
    now = now if now is not None else time.monotonic()
    _stats["calls"] += 1
    if ok:
        # Latency is averaged over successes only — a failure's "latency" is
        # usually just the timeout and would skew the readout meaninglessly.
        _stats["total_latency_ms"] += latency_ms
        _stats["successes"] += 1
        _stats["consecutive_failures"] = 0
        _stats["breaker_open_until"] = 0.0
        _stats["last_success_at"] = time.time()
        return
    _stats["failures"] += 1
    _stats["consecutive_failures"] += 1
    _stats["last_error"] = error[:300] or None
    _stats["last_error_at"] = time.time()
    if _breaker_threshold and _stats["consecutive_failures"] >= _breaker_threshold \
            and not breaker_open(now):
        _stats["breaker_open_until"] = now + _breaker_cooldown_s
        _stats["breaker_trips"] += 1
        logger.warning(
            f"LLM circuit breaker opened after {_stats['consecutive_failures']} consecutive "
            f"failures — pausing LLM calls for {_breaker_cooldown_s / 60:.0f} min")


def reset_breaker() -> None:
    """Manual close from the UI: clears the open window and the failure streak
    (cumulative counters are kept — only a restart zeroes those)."""
    _stats["breaker_open_until"] = 0.0
    _stats["consecutive_failures"] = 0


def get_stats() -> dict[str, Any]:
    now = time.monotonic()
    open_ = breaker_open(now)
    return {
        "calls": _stats["calls"],
        "successes": _stats["successes"],
        "failures": _stats["failures"],
        "consecutive_failures": _stats["consecutive_failures"],
        "avg_latency_ms": round(_stats["total_latency_ms"] / _stats["successes"])
                          if _stats["successes"] else None,
        "last_error": _stats["last_error"],
        "last_error_at": _stats["last_error_at"],
        "last_success_at": _stats["last_success_at"],
        "breaker_open": open_,
        "breaker_seconds_remaining": round(_stats["breaker_open_until"] - now) if open_ else 0,
        "breaker_trips": _stats["breaker_trips"],
        "breaker_threshold": _breaker_threshold,
    }

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
# Max filenames per pack LLM call (v0.31.0). Larger packs are chunked and merged
# so small models don't collapse a 40-file reply into a single object.
PACK_CHUNK_SIZE = 15

DEFAULT_MATCH_PROMPT = (
    "You review whether a download release matches a library entry.\n"
    "Release: {release}\n"
    "Candidate: {candidate}\n"
    "Context: {context}"
)

DEFAULT_PACK_PROMPT = (
    "You map files in a season/series pack to library episodes.\n"
    "Pack release: {release}\n"
    "Library entry: {candidate}\n"
    "Files: {files}\n"
    "Context: {context}"
)

DEFAULT_EXPLAIN_PROMPT = (
    "You assess whether a media item is a good deletion candidate.\n"
    "Item: {item}"
)

# Fixed reply envelope (v0.30.0): JSON with Markdown-capable reason (bullets/bold).
# Not exposed in Settings — kept as a constant so future rich-text (italics, color)
# can expand the reason grammar without a format picker.
REPLY_FORMAT = "markdown"

_NO_THINK_INSTRUCTION = (
    "\nAnswer only. Do NOT write chain-of-thought, step-by-step reasoning, or "
    "<think></think> blocks. Output the final JSON (or requested line) immediately."
)

_REASON_BULLETS = (
    "a brief Markdown verdict line, then 2–4 bullet reasons using \"- \" "
    "(bold key terms with **text**). No prose paragraphs."
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


def compact_det_summary(match_rationale: str, heuristic_confidence: float | None,
                        *, pack_label: str | None = None) -> str:
    """Compress a prose match_rationale into a short structured line for LLM context."""
    parts = []
    if heuristic_confidence is not None:
        parts.append(f"heuristic={heuristic_confidence:.2f}")
    if pack_label:
        parts.append(f"pack={pack_label}")
    # Pull common tokens from the rationale without shipping the whole essay
    low = (match_rationale or "").lower()
    for label, needles in (
        ("title", ("title", "similarity")),
        ("numeric", ("episode", "season", "absolute", "s0", "numeric")),
        ("coverage", ("coverage", "mapped", "entire-season")),
        ("capped", ("capped", "title-only")),
    ):
        if any(n in low for n in needles):
            parts.append(label)
    # Keep a short slice of the original for any unique detail
    snippet = (match_rationale or "").replace("\n", " ")
    if len(snippet) > 180:
        snippet = snippet[:179].rstrip() + "…"
    if snippet:
        parts.append(f"notes={snippet}")
    return " | ".join(parts) if parts else "heuristic=unknown"


def build_review_prompt(template: str, release: str, candidate: str, context: str,
                        det_summary: str, verbosity: str = "brief",
                        reply_format: str = "markdown",
                        confidence_style: str = "numeric",
                        forbid_thinking: bool = True) -> str:
    """Static scaffold around the (optionally user-templated) match prompt.

    v0.30.0: reframed as \"disagree only with a concrete contradiction\"; answer-only
    (no CoT); reason = brief verdict + Markdown bullets. reply_format is fixed to
    markdown-capable JSON at call sites (Settings no longer exposes a picker).
    """
    # Force rich-text-capable envelope; keep "simple" only if an old caller passes it
    # for tiny models that can't emit JSON at all.
    tpl = (template or "").strip() or DEFAULT_MATCH_PROMPT
    prompt = (tpl.replace("{release}", _truncate(release, CAP_RELEASE))
                 .replace("{candidate}", _truncate(candidate, CAP_CANDIDATE))
                 .replace("{context}", _truncate(context, CAP_CONTEXT)))
    prompt += (
        f"\nScorer result (title/season/episode/absolute comparisons): "
        f"{_truncate(det_summary, CAP_DET_SUMMARY)}"
    )
    prompt += (
        "\nStrip from the release name before judging: resolution, source (WEB-DL/BluRay), "
        "codec, audio, HDR, encoder, and uploader/release-group tags (e.g. MeGusta, "
        "FLUX, SubsPlease) — never evidence for/against a match."
        "\nAnime & foreign titles: absolute episode numbers, romaji/native/English "
        "translations, and alternate titles of the same work count as matches. Prefer "
        "the triggered series in Context over string similarity alone."
        "\nDefault to AGREE with the scorer. Reply DISAGREE only if you can cite a "
        "concrete contradiction (wrong series/movie, wrong episode/absolute number, "
        "or wrong year). Cosmetic filename differences are not contradictions."
    )
    if forbid_thinking:
        prompt += _NO_THINK_INSTRUCTION

    if reply_format == "simple":
        if verbosity == "minimal":
            prompt += "\nReply with ONLY one word: agree or disagree."
        elif confidence_style == "classified":
            prompt += ("\nReply with ONLY one line:\n"
                       "agree or disagree | more or less or same | short reason")
        else:
            prompt += ("\nReply with ONLY one line:\n"
                       "agree or disagree | adjustment -0.3..0.3 | short reason")
    else:
        if verbosity == "minimal":
            prompt += ('\nReply with ONLY JSON: {"agrees": true|false}')
        else:
            reason_spec = _REASON_BULLETS
            if confidence_style == "classified":
                prompt += (
                    '\nReply with ONLY JSON: '
                    f'{{"agrees": true|false, "confidence_shift": "more"|"less"|"same", '
                    f'"reason": "{reason_spec}"}}'
                )
            else:
                prompt += (
                    '\nReply with ONLY JSON: '
                    f'{{"agrees": true|false, "confidence_adjustment": <-0.3 to 0.3>, '
                    f'"reason": "{reason_spec}"}}'
                )
    return prompt


def build_explain_prompt(template: str, item_summary: str, verbosity: str = "brief",
                         forbid_thinking: bool = True) -> str:
    tpl = (template or "").strip() or DEFAULT_EXPLAIN_PROMPT
    prompt = tpl.replace("{item}", _truncate(item_summary, CAP_ITEM))
    if forbid_thinking:
        prompt += _NO_THINK_INSTRUCTION
    if verbosity == "minimal":
        prompt += "\nReply with ONLY one word: KEEP or DELETE."
    else:
        prompt += (
            "\nReply with a one-line KEEP or DELETE verdict, then 2–4 Markdown "
            "bullet reasons (\"- \") citing concrete score factors. No paragraphs."
        )
    return prompt


# Concise, closed vocabulary for *why* a pack file was mapped to an episode —
# shown as a badge in the triage UI instead of free-text prose, so a user can
# tell at a glance which files need a second look. Order matters: the prompt
# lists them in this same order, roughly strongest-to-weakest evidence.
PACK_MATCH_TYPES = [
    "Exact Match",           # filename's S/E number AND episode title both match
    "Title Match",           # episode title in the filename matches the official title
    "Number Match",          # season/episode number matches exactly, no title to corroborate
    "Absolute Number Match", # anime-style absolute episode numbering matched
    "Multi-Episode File",    # one file covers multiple episodes (e.g. a double-length release)
    "Sequence Match",        # matched only by file position/order in the pack — weakest signal
    "Low Confidence",        # evidence is weak or ambiguous
]

_PACK_MATCH_TYPE_DEFS = (
    "Match type definitions (pick exactly one per file):\n"
    "- Exact Match: the filename's season/episode number AND the episode title both match this episode\n"
    "- Title Match: the episode title in the filename matches this episode's official title, "
    "even if numbering is absent or uncertain\n"
    "- Number Match: the season/episode number in the filename matches exactly, with no title to corroborate\n"
    "- Absolute Number Match: matched via anime-style absolute episode numbering\n"
    "- Multi-Episode File: this single file covers multiple episodes (e.g. a double-length release)\n"
    "- Sequence Match: matched only by the file's position/order in the pack, with no explicit number or title evidence\n"
    "- Low Confidence: the evidence is weak or ambiguous"
)


def build_pack_prompt(template: str, release: str, candidate: str, files_list: str,
                      context: str, verbosity: str = "brief",
                      forbid_thinking: bool = True,
                      folder_name: str = "") -> str:
    """Pack-matching prompt: map each file using pack name + folder + filename.

    Files should be: \"filename1.mkv, filename2.mkv, ...\" (list only, no paths).
    """
    tpl = (template or "").strip() or DEFAULT_PACK_PROMPT
    prompt = (tpl.replace("{release}", _truncate(release, CAP_RELEASE))
                 .replace("{candidate}", _truncate(candidate, CAP_CANDIDATE))
                 .replace("{files}", _truncate(files_list, CAP_FILES))
                 .replace("{context}", _truncate(context, CAP_CONTEXT)))
    if folder_name:
        prompt += f"\nDownload folder name: {_truncate(folder_name, CAP_RELEASE)}"
    prompt += (
        "\nUse pack release name, folder name, AND each filename together. "
        "Strip quality/codec/uploader tags from every name before matching. "
        "Anime: prefer absolute numbers when present; accept translated titles."
    )
    if forbid_thinking:
        prompt += _NO_THINK_INSTRUCTION
    match_type_choices = "|".join(f'"{t}"' for t in PACK_MATCH_TYPES)
    prompt += f"\n{_PACK_MATCH_TYPE_DEFS}"
    if verbosity == "minimal":
        prompt += (f'\nFor each file, reply with ONLY a JSON array: '
                   f'[{{"file": "filename.mkv", "season": 1, "episode": 1, "match_type": {match_type_choices}}}]')
    else:
        reason_spec = "one short Markdown bullet or phrase"
        prompt += (f'\nFor each file, reply with ONLY a JSON array: '
                   f'[{{"file": "filename.mkv", "season": 1, "episode": 1, "match_type": {match_type_choices}, '
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


def resolve_inference(model_size: str, verbose: bool, *,
                      temperature: float = 0.0, max_tokens: int = 0,
                      timeout_seconds: int = 0) -> tuple[int, int, float]:
    """Apply optional OllamaSettings overrides on top of the model_size profile.
    max_tokens/timeout_seconds of 0 keep the profile default."""
    mt, to = _limits(model_size, verbose)
    return (max_tokens or mt, timeout_seconds or to, float(temperature))


def inference_kwargs(cfg) -> dict:
    """Pull temperature/max_tokens/timeout_seconds from an OllamaSettings-like object."""
    return {
        "temperature": getattr(cfg, "temperature", 0.0) or 0.0,
        "max_tokens": getattr(cfg, "max_tokens", 0) or 0,
        "timeout_seconds": getattr(cfg, "timeout_seconds", 0) or 0,
    }


def prompt_kwargs(cfg) -> dict:
    """Scaffold flags from OllamaSettings (forbid_thinking, etc.)."""
    return {
        "forbid_thinking": bool(getattr(cfg, "forbid_thinking", True)),
    }


async def _generate(host: str, model: str, prompt: str, api_style: str = "ollama",
                    json_format: bool = True, verbose: bool = False,
                    model_size: str = "medium", keep_alive_minutes: int = 10,
                    temperature: float = 0.0, max_tokens: int = 0,
                    timeout_seconds: int = 0) -> Optional[str]:
    """Single short completion. Returns raw text or None on any failure."""
    base = _base_url(host)
    if not base or not model:
        return None
    if breaker_open():
        logger.info("LLM assist skipped: circuit breaker is open")
        return None
    max_tokens, timeout, temperature = resolve_inference(
        model_size, verbose, temperature=temperature,
        max_tokens=max_tokens, timeout_seconds=timeout_seconds)
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            if api_style == "openai":
                body: dict[str, Any] = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                if json_format:
                    body["response_format"] = {"type": "json_object"}
                r = await client.post(f"{base}/v1/chat/completions", json=body)
                r.raise_for_status()
                out = r.json()["choices"][0]["message"]["content"]
            else:
                body = {
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": temperature, "num_predict": max_tokens},
                }
                if keep_alive_minutes and keep_alive_minutes > 0:
                    # Keeps the model loaded between sequential calls in a batch run —
                    # ollama-only; the openai style (LM Studio, llama.cpp) has no equivalent.
                    body["keep_alive"] = f"{int(keep_alive_minutes)}m"
                if json_format:
                    body["format"] = "json"
                r = await client.post(f"{base}/api/generate", json=body)
                r.raise_for_status()
                out = r.json().get("response", "")
            record_result(True, round((time.monotonic() - started) * 1000))
            return out
    except Exception as e:
        # httpx timeout exceptions often stringify empty — keep the class name.
        record_result(False, round((time.monotonic() - started) * 1000),
                      str(e) or type(e).__name__)
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
                           keep_alive_minutes: int = 10, temperature: float = 0.0,
                           max_tokens: int = 0, timeout_seconds: int = 0):
    """Streaming counterpart of _generate: an async generator of raw text chunks.
    Fail-soft like everything else — any error just ends the stream."""
    base = _base_url(host)
    if not base or not model:
        return
    if breaker_open():
        logger.info("LLM stream skipped: circuit breaker is open")
        return
    max_tokens, timeout, temperature = resolve_inference(
        model_size, verbose, temperature=temperature,
        max_tokens=max_tokens, timeout_seconds=timeout_seconds)
    started = time.monotonic()
    emitted_any = False
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            if api_style == "openai":
                body: dict[str, Any] = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature,
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
                            emitted_any = True
                            yield delta
                record_result(True, round((time.monotonic() - started) * 1000))
                return
            body = {
                "model": model,
                "prompt": prompt,
                "stream": True,
                "options": {"temperature": temperature, "num_predict": max_tokens},
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
                        emitted_any = True
                        yield obj["response"]
                    if obj.get("done"):
                        break
            record_result(True, round((time.monotonic() - started) * 1000))
    except GeneratorExit:
        # Consumer closed the stream early (hit its display cap) — the call itself
        # worked; it must never accumulate toward the breaker.
        record_result(True, round((time.monotonic() - started) * 1000))
        raise
    except Exception as e:
        # A stream that already emitted text still counts as a success for the
        # breaker — only a call that produced nothing is a real failure.
        record_result(emitted_any, round((time.monotonic() - started) * 1000),
                      str(e) or type(e).__name__)
        logger.info(f"LLM stream unavailable: {e}")


async def explain_deletion_stream(host: str, model: str, item_summary: str,
                                  api_style: str = "ollama", template: str = "",
                                  verbosity: str = "brief", model_size: str = "medium",
                                  keep_alive_minutes: int = 10,
                                  forbid_thinking: bool = True,
                                  temperature: float = 0.0, max_tokens: int = 0,
                                  timeout_seconds: int = 0):
    """Streaming deletion rationale: yields displayable (think-stripped) text
    chunks. Brief/verbose both allow a verdict line + bullets (up to limit)."""
    verbose = verbosity == "verbose"
    limit = 1500 if verbose else 600
    prompt = build_explain_prompt(template, item_summary, verbosity,
                                  forbid_thinking=forbid_thinking)
    emitted = 0
    acc = ""
    async for chunk in _generate_stream(host, model, prompt, api_style, verbose=verbose,
                                        model_size=model_size,
                                        keep_alive_minutes=keep_alive_minutes,
                                        temperature=temperature, max_tokens=max_tokens,
                                        timeout_seconds=timeout_seconds):
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
    """Parse per-file episode matches from LLM response. Expects a JSON array, but
    weaker models on larger packs sometimes collapse to a single JSON object
    (answering only the first file) despite the "for each file" instruction —
    salvage that as a one-item list rather than discarding the whole reply."""
    raw = _strip_think(raw)
    if not raw:
        return None
    try:
        result = json.loads(raw)
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "file" in result:
            return [result]
    except Exception:
        m = re.search(r"\[.*\]", raw or "", re.DOTALL)
        if m:
            try:
                result = json.loads(m.group(0))
                if isinstance(result, list):
                    return result
            except Exception:
                pass
        m = re.search(r"\{.*\}", raw or "", re.DOTALL)
        if m:
            try:
                result = json.loads(m.group(0))
                if isinstance(result, dict) and "file" in result:
                    return [result]
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
                       keep_alive_minutes: int = 10, reply_format: str = "markdown",
                       confidence_style: str = "numeric",
                       forbid_thinking: bool = True,
                       temperature: float = 0.0, max_tokens: int = 0,
                       timeout_seconds: int = 0) -> Optional[dict[str, Any]]:
    """Single structured LLM review of a deterministic match decision (one call —
    no separate match/explain prompts). Returns
    {"agrees": bool, "confidence_adjustment": float ±0.3, "rationale": str}
    or None (= no assist available). Supplements the deterministic rationale,
    never replaces it."""
    verbose = verbosity == "verbose"
    # v0.30.0: Settings no longer exposes reply_format — force rich-text JSON
    # unless an explicit "simple" override is passed for tiny models.
    eff_format = reply_format if reply_format == "simple" else REPLY_FORMAT
    prompt = build_review_prompt(template, release_title, candidate_title, context,
                                 det_summary, verbosity, eff_format, confidence_style,
                                 forbid_thinking=forbid_thinking)
    raw = await _generate(host, model, prompt, api_style,
                          json_format=eff_format != "simple", verbose=verbose,
                          model_size=model_size, keep_alive_minutes=keep_alive_minutes,
                          temperature=temperature, max_tokens=max_tokens,
                          timeout_seconds=timeout_seconds)
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


def _validate_pack_matches(parsed: list) -> list[dict]:
    """Normalize one pack-LLM reply into the closed match_type vocabulary."""
    type_by_lower = {t.lower(): t for t in PACK_MATCH_TYPES}
    validated = []
    for item in parsed:
        if isinstance(item, dict) and "file" in item and "season" in item and "episode" in item:
            try:
                raw_type = str(item.get("match_type", "")).strip().lower()
                validated.append({
                    "file": str(item["file"])[:200],
                    "season": int(item.get("season", 0)),
                    "episode": int(item.get("episode", 0)),
                    "match_type": type_by_lower.get(raw_type, "Low Confidence"),
                    "confidence": str(item.get("confidence", "medium")).lower()[:20],
                    "reason": str(item.get("reason", ""))[:300],
                })
            except (TypeError, ValueError):
                continue
    return validated


async def _review_pack_chunk(host: str, model: str, release_title: str,
                             candidate_title: str, file_names: list[str],
                             api_style: str, template: str, verbosity: str,
                             model_size: str, keep_alive_minutes: int,
                             forbid_thinking: bool, folder_name: str,
                             temperature: float, max_tokens: int,
                             timeout_seconds: int,
                             chunk_index: int = 0, chunk_total: int = 1) -> Optional[list[dict]]:
    """One LLM call for a single pack-file chunk."""
    verbose = verbosity == "verbose"
    files_str = ", ".join(file_names) if file_names else "No files listed"
    context = ("Multi-file pack. Use pack name + folder name + each filename. "
               "Match each file to its episode.")
    if chunk_total > 1:
        context += f" This is chunk {chunk_index + 1} of {chunk_total} — only map the files listed."
    prompt = build_pack_prompt(template, release_title, candidate_title, files_str,
                              context, verbosity, forbid_thinking=forbid_thinking,
                              folder_name=folder_name)
    raw = await _generate(host, model, prompt, api_style, json_format=True,
                         verbose=verbose, model_size=model_size,
                         keep_alive_minutes=keep_alive_minutes,
                         temperature=temperature, max_tokens=max_tokens,
                         timeout_seconds=timeout_seconds)
    if raw is None:
        return None
    parsed = _parse_pack_matches(raw)
    if not parsed or not isinstance(parsed, list):
        return None
    validated = _validate_pack_matches(parsed)
    return validated if validated else None


async def review_pack_files(host: str, model: str, release_title: str,
                             candidate_title: str, file_names: list[str],
                             api_style: str = "ollama", template: str = "",
                             verbosity: str = "brief", model_size: str = "medium",
                             keep_alive_minutes: int = 10,
                             forbid_thinking: bool = True,
                             folder_name: str = "",
                             temperature: float = 0.0, max_tokens: int = 0,
                             timeout_seconds: int = 0) -> Optional[list[dict]]:
    """Per-file episode matching for season/series packs. Returns list of
    [{"file": "...", "season": int, "episode": int, "match_type": one of
    PACK_MATCH_TYPES, "confidence": "high|medium|low", "reason": "..."}]
    or None if unavailable. Fails soft.

    Packs larger than PACK_CHUNK_SIZE are split into sequential chunks and merged
    by filename (first answer wins) so small models stay within output budgets.
    """
    names = list(file_names or [])[:50]
    if not names:
        return None
    chunks = [names[i:i + PACK_CHUNK_SIZE] for i in range(0, len(names), PACK_CHUNK_SIZE)]
    merged: list[dict] = []
    seen: set[str] = set()
    any_ok = False
    for idx, chunk in enumerate(chunks):
        part = await _review_pack_chunk(
            host, model, release_title, candidate_title, chunk,
            api_style, template, verbosity, model_size, keep_alive_minutes,
            forbid_thinking, folder_name, temperature, max_tokens, timeout_seconds,
            chunk_index=idx, chunk_total=len(chunks))
        if part is None:
            continue
        any_ok = True
        for row in part:
            key = row["file"].lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
    return merged if any_ok else None


async def explain_deletion(host: str, model: str, item_summary: str,
                           api_style: str = "ollama", template: str = "",
                           verbosity: str = "brief", model_size: str = "medium",
                           keep_alive_minutes: int = 10,
                           forbid_thinking: bool = True,
                           temperature: float = 0.0, max_tokens: int = 0,
                           timeout_seconds: int = 0) -> Optional[str]:
    """Deletion-candidate rationale. Returns the text or None (= no assist available).
    Minimal verbosity returns a bare KEEP/DELETE verdict."""
    verbose = verbosity == "verbose"
    prompt = build_explain_prompt(template, item_summary, verbosity,
                                  forbid_thinking=forbid_thinking)
    raw = await _generate(host, model, prompt, api_style, json_format=False, verbose=verbose,
                          model_size=model_size, keep_alive_minutes=keep_alive_minutes,
                          temperature=temperature, max_tokens=max_tokens,
                          timeout_seconds=timeout_seconds)
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
    if task == "pack":
        placeholders = "{release}, {candidate}, {files}, {context}"
        task_desc = "mapping each file in a season/series pack to its episode"
    elif task == "explain":
        placeholders = "{item}"
        task_desc = "explaining whether a media item is a good deletion candidate"
    else:
        placeholders = "{release}, {candidate}, {context}"
        task_desc = "matching download release names to library entries"
    prompt = (
        "You improve prompt templates for a media-management tool.\n"
        f"Task the template is for: {task_desc}.\n"
        f"Rewrite the rough draft below into a clear, effective prompt template. "
        f"Keep it concise. You MUST preserve these placeholders exactly: {placeholders}. "
        "Do not add a reply-format instruction (the app appends one). "
        "Reply with ONLY the improved template text.\n\n"
        f"Rough draft:\n{draft}"
    )
    raw = await _generate(host, model, prompt, api_style, json_format=False, verbose=True)
    refined = _strip_think(raw) if raw else ""
    return refined or None
