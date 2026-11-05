"""In-memory logs: the dashboard's event log and each instance's console output."""

import threading
import time
from collections import deque

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


class Console:
    """The last lines one CasparCG printed, for the console panel."""

    def __init__(self, size: int = 5000):
        self._lines: deque = deque(maxlen=size)
        self.count = 0  # lines ever added, so a reader can ask for what's new
        self._lock = threading.Lock()

    def add(self, line: str) -> None:
        with self._lock:
            self._lines.append(line)
            self.count += 1

    def since(self, seen: int) -> list:
        """Lines added after the first ``seen``, as many as are still held."""
        with self._lock:
            new = min(self.count - seen, len(self._lines))
            return list(self._lines)[-new:] if new > 0 else []
