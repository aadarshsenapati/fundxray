"""Token-bucket throttle. SmartAPI enforces rate limits; exceeding them gets you blocked."""
from __future__ import annotations

import threading
import time


class TokenBucket:
    def __init__(self, rate_per_sec: float, capacity: float | None = None):
        self.rate = float(rate_per_sec)
        self.capacity = float(capacity if capacity is not None else max(1.0, rate_per_sec))
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1.0) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                deficit = tokens - self._tokens
            time.sleep(deficit / self.rate)
