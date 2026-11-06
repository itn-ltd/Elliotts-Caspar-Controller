"""Slow down guessing: refuse an address for a while after too many failures."""

import math
import threading
import time


class Throttle:
    """After ``limit`` failures from one address within ``window`` seconds, refuse it for ``block``."""

    def __init__(self, limit: int = 5, window: float = 60, block: float = 60):
        self.limit, self.window, self.block = limit, window, block
        self._failures: dict[str, list] = {}  # address -> times of recent failures
        self._blocked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def blocked(self, address: str) -> int:
        """Seconds until ``address`` may try again; 0 if it may now."""
        with self._lock:
            left = self._blocked_until.get(address, 0) - time.monotonic()
            if left <= 0:
                self._blocked_until.pop(address, None)
                return 0
            return math.ceil(left)

    def failed(self, address: str) -> bool:
        """Count a failure. True if it starts a block, so the caller logs it once, not every try."""
        now = time.monotonic()
        with self._lock:
            recent = [t for t in self._failures.get(address, []) if t > now - self.window] + [now]
            self._prune(now)
            if len(recent) < self.limit:
                self._failures[address] = recent
                return False
            self._failures.pop(address, None)
            self._blocked_until[address] = now + self.block
            return True

    def _prune(self, now: float) -> None:
        """Forget addresses whose failures and blocks have all run out."""
        for address in [a for a, times in self._failures.items() if times[-1] <= now - self.window]:
            del self._failures[address]
        for address in [a for a, until in self._blocked_until.items() if until <= now]:
            del self._blocked_until[address]

    def succeeded(self, address: str) -> None:
        with self._lock:
            self._failures.pop(address, None)
