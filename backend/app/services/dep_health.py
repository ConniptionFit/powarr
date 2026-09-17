"""Cached per-integration dependency health (v0.34.0, TEL-01).

Updated by scan failures and optional cold probes. Docker /system/health stays
DB-only; clients read /system/dependencies for the richer picture.
"""
from __future__ import annotations

import time
from typing import Any, Optional

_status: dict[str, dict[str, Any]] = {}


def record(name: str, ok: bool, message: str = "", *, source: str = "probe") -> None:
    _status[name] = {
        "name": name,
        "ok": ok,
        "message": (message or "")[:300],
        "source": source,
        "checked_at": time.time(),
    }


def snapshot(names: list[str] | None = None) -> list[dict[str, Any]]:
    from app.services import circuit_breaker
    keys = names or sorted(_status.keys())
    out = []
    for name in keys:
        row = dict(_status.get(name) or {
            "name": name, "ok": None, "message": "not checked yet",
            "source": None, "checked_at": None,
        })
        br = circuit_breaker.get_stats(name)
        row["breaker_open"] = br["breaker_open"]
        row["breaker_seconds_remaining"] = br["breaker_seconds_remaining"]
        row["last_error"] = br["last_error"]
        out.append(row)
    return out
