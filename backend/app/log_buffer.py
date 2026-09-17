"""In-memory ring buffer of recent log lines, served by /api/v1/system/logs.

v0.34.0 (TEL-02): optional request_id from contextvars is injected into each
formatted line when a request is in flight.
"""
import logging
from collections import deque
from contextvars import ContextVar
from typing import Optional

_BUFFER: deque[str] = deque(maxlen=1000)
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
            _BUFFER.append(_FORMATTER.format(record))
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
