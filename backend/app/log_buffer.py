"""In-memory ring buffer of recent log lines, served by /api/v1/system/logs."""
import logging
from collections import deque

_BUFFER: deque[str] = deque(maxlen=1000)
_FORMATTER = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")


class RingBufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _BUFFER.append(_FORMATTER.format(record))
        except Exception:
            pass


def install() -> None:
    root = logging.getLogger()
    if not any(isinstance(h, RingBufferHandler) for h in root.handlers):
        root.addHandler(RingBufferHandler())


def get_recent_logs(lines: int = 200) -> list[str]:
    return list(_BUFFER)[-lines:]
