"""Named in-memory circuit breakers (v0.34.0, RES-02).

Mirrors the LLM breaker in llm_assist but keyed by integration name so a downed
Sonarr doesn't keep getting hit every scan cycle. Fail-soft: callers check
breaker_open(name) and skip/return empty rather than raising.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger("powarr")

_DEFAULT_THRESHOLD = 5
_DEFAULT_COOLDOWN_S = 600.0

_threshold = _DEFAULT_THRESHOLD
_cooldown_s = _DEFAULT_COOLDOWN_S
_stats: dict[str, dict[str, Any]] = {}


def _fresh(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "calls": 0, "successes": 0, "failures": 0, "consecutive_failures": 0,
        "last_error": None, "last_error_at": None, "last_success_at": None,
        "breaker_open_until": 0.0, "breaker_trips": 0,
    }


def set_config(threshold: int = 5, cooldown_minutes: int = 10) -> None:
    global _threshold, _cooldown_s
    _threshold = max(0, int(threshold or 0))
    _cooldown_s = max(1, int(cooldown_minutes or 0)) * 60.0


def breaker_open(name: str, now: Optional[float] = None) -> bool:
    s = _stats.get(name)
    if not s:
        return False
    return (now if now is not None else time.monotonic()) < s["breaker_open_until"]


def record_result(name: str, ok: bool, error: str = "",
                  now: Optional[float] = None) -> None:
    now = now if now is not None else time.monotonic()
    s = _stats.setdefault(name, _fresh(name))
    s["calls"] += 1
    if ok:
        s["successes"] += 1
        s["consecutive_failures"] = 0
        s["breaker_open_until"] = 0.0
        s["last_success_at"] = time.time()
        return
    s["failures"] += 1
    s["consecutive_failures"] += 1
    s["last_error"] = (error or "")[:300] or None
    s["last_error_at"] = time.time()
    if _threshold and s["consecutive_failures"] >= _threshold and not breaker_open(name, now):
        s["breaker_open_until"] = now + _cooldown_s
        s["breaker_trips"] += 1
        logger.warning(
            f"Integration circuit breaker opened for '{name}' after "
            f"{s['consecutive_failures']} consecutive failures — pausing for "
            f"{_cooldown_s / 60:.0f} min")


def reset_breaker(name: str | None = None) -> None:
    if name is None:
        for s in _stats.values():
            s["breaker_open_until"] = 0.0
            s["consecutive_failures"] = 0
        return
    s = _stats.get(name)
    if s:
        s["breaker_open_until"] = 0.0
        s["consecutive_failures"] = 0


def get_stats(name: str | None = None) -> dict[str, Any] | list[dict[str, Any]]:
    now = time.monotonic()

    def _one(s: dict[str, Any]) -> dict[str, Any]:
        open_ = now < s["breaker_open_until"]
        return {
            "name": s["name"],
            "calls": s["calls"],
            "successes": s["successes"],
            "failures": s["failures"],
            "consecutive_failures": s["consecutive_failures"],
            "last_error": s["last_error"],
            "last_error_at": s["last_error_at"],
            "last_success_at": s["last_success_at"],
            "breaker_open": open_,
            "breaker_seconds_remaining": round(s["breaker_open_until"] - now) if open_ else 0,
            "breaker_trips": s["breaker_trips"],
        }

    if name is not None:
        return _one(_stats.get(name) or _fresh(name))
    return [_one(s) for s in _stats.values()]


class BreakerOpenError(RuntimeError):
    """Raised when a call is short-circuited because the breaker is open."""

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"Circuit breaker open for '{name}'")
