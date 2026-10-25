"""In-memory ring buffer of recent log lines, served by /api/v1/system/logs.

v0.34.0 (TEL-02): optional request_id from contextvars is injected into each
formatted line when a request is in flight.
"""
import logging
import threading
from collections import deque
from contextvars import ContextVar
from typing import Optional

_BUFFER: deque[str] = deque(maxlen=1000)
# Total lines ever appended; lets /system/logs?since=N return only what is new
# instead of re-sending the whole buffer on every poll.
_TOTAL = 0
_LOCK = threading.Lock()
_request_id: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


def set_request_id(rid: Optional[str]) -> None:
    _request_id.set(rid)


def get_request_id() -> Optional[str]:
    return _request_id.get()


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get() or "-"
        return True


_FORMATTER = logging.Formatter(
    "%(asctime)s %(levelname)s %(name)s [%(request_id)s]: %(message)s",
    "%Y-%m-%d %H:%M:%S",
)


class RingBufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            if not hasattr(record, "request_id"):
                record.request_id = _request_id.get() or "-"
            line = _FORMATTER.format(record)
            global _TOTAL
            with _LOCK:
                _BUFFER.append(line)
                _TOTAL += 1
        except Exception:
            pass


def install() -> None:
    root = logging.getLogger()
    filt = _RequestIdFilter()
    root.addFilter(filt)
    for h in root.handlers:
        h.addFilter(filt)
        # Keep console format in sync when possible
        if isinstance(h, logging.StreamHandler) and not isinstance(h, RingBufferHandler):
            try:
                h.setFormatter(_FORMATTER)
            except Exception:
                pass
    if not any(isinstance(h, RingBufferHandler) for h in root.handlers):
        h = RingBufferHandler()
        h.addFilter(filt)
        root.addHandler(h)


def get_recent_logs(lines: int = 200) -> list[str]:
    return list(_BUFFER)[-lines:]


def get_logs_since(since: int, lines: int = 1000) -> tuple[list[str], int, bool]:
    """(new_lines, next_cursor, reset). `since` is a cursor from a previous
    call. reset=True means the cursor was unusable (server restarted, so the
    counter went backwards) and the lines returned are a fresh tail, not a
    continuation. Lines that rolled off the ring before the caller came back
    are simply gone."""
    with _LOCK:
        total = _TOTAL
        buf = list(_BUFFER)
    reset = since < 0 or since > total
    n = lines if reset else min(total - since, len(buf), lines)
    return (buf[-n:] if n > 0 else []), total, reset
