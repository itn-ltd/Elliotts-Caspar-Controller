"""In-memory event log shown on the dashboard."""

import threading
import time

MAX_LOG = 200
_log: list = []
_log_lock = threading.Lock()


def log_event(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    with _log_lock:
        _log.append(f"[{ts}] {msg}")
        if len(_log) > MAX_LOG:
            _log.pop(0)


def entries() -> list:
    with _log_lock:
        return list(_log)
