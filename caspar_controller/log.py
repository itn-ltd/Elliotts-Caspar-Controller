"""Logs: the dashboard's event log, each instance's console output, and the log files."""

import logging
import logging.handlers
import os
import sys
import threading
import time
from collections import deque
from itertools import islice

MAX_LOG = 200
FILE_BYTES = 1_000_000  # each log file rotates at about 1 MB...
FILE_COUNT = 5  # ...keeping this many old ones
EVENTS_FILE = "events.log"
AGENT_FILE = "agent.log"
_log: list = []
_log_lock = threading.Lock()
# The event log's copy on disk. No handler until write_files(), so tests stay in memory.
_events = logging.getLogger("caspar_controller.events")
_events.propagate = False
_events.setLevel(logging.INFO)


def log_event(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    with _log_lock:
        _log.append(f"[{ts}] {msg}")
        if len(_log) > MAX_LOG:
            _log.pop(0)
    _events.info(msg)


def entries() -> list:
    with _log_lock:
        return list(_log)


def _rotating(path: str, fmt: logging.Formatter) -> logging.Handler:
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=FILE_BYTES, backupCount=FILE_COUNT, encoding="utf-8", delay=True
    )
    handler.setFormatter(fmt)
    return handler


def write_files(folder: str) -> None:
    """Keep the app's log and the event log in ``folder``, and reload the last events."""
    os.makedirs(folder, exist_ok=True)
    events = os.path.join(folder, EVENTS_FILE)
    try:
        with open(events, encoding="utf-8", errors="replace") as f:
            earlier = [line.rstrip("\n") for line in deque(f, MAX_LOG)]
    except FileNotFoundError:
        earlier = []
    with _log_lock:
        _log[:0] = earlier  # dated, so they read as before this start
        del _log[: max(0, len(_log) - MAX_LOG)]
    _events.addHandler(
        _rotating(events, logging.Formatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
    )
    root = logging.getLogger()  # the app's own loggers and uvicorn's
    root.addHandler(
        _rotating(
            os.path.join(folder, AGENT_FILE),
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"),
        )
    )
    root.setLevel(logging.INFO)
    # A crash in any thread goes in the file, not the frozen exe's devnull.
    sys.excepthook = lambda *exc: logging.critical("Uncaught error", exc_info=exc)
    threading.excepthook = lambda a: logging.error(
        "Uncaught error in %s",
        a.thread.name if a.thread else "a thread",
        exc_info=(a.exc_type, a.exc_value, a.exc_traceback),
    )


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

    def since(self, seen: int) -> tuple[list, int]:
        """(lines added after the first ``seen``, as many as are still held; the new count)."""
        with self._lock:
            new = min(self.count - seen, len(self._lines))
            return list(islice(self._lines, len(self._lines) - max(new, 0), None)), self.count
