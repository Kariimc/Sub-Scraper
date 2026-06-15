"""Reusable resilience primitives: jittered backoff + a circuit breaker.

Extracted from the download manager so the same battle-tested logic backs both
the subprocess engine and the async HTTP streamer, and so it can be unit-tested
in isolation.
"""

from __future__ import annotations

import random
import threading
import time


class CircuitOpen(Exception):
    """Raised when a source's circuit breaker is tripped (cooling down)."""


def backoff_delay(
    attempt: int,
    *,
    base: float = 1.0,
    cap: float = 30.0,
    jitter: bool = True,
) -> float:
    """Exponential backoff with *equal jitter*.

    ``attempt`` is 1-based. The raw delay doubles each attempt (``base`` * 2^n),
    capped at ``cap``. Equal jitter (``raw/2 + random(0, raw/2)``) keeps a sane
    floor so a flurry of retries never collapses to ~0s, while still spreading
    load to avoid the thundering-herd problem on a recovering host.
    """
    raw = min(cap, base * (2 ** max(0, attempt - 1)))
    if not jitter:
        return raw
    half = raw / 2.0
    return half + random.uniform(0.0, half)


class CircuitBreaker:
    """Trips after ``threshold`` consecutive failures for one origin, then
    refuses work for ``cooldown`` seconds so we stop hammering a dead or
    rate-limited host (graceful degradation).

    Thread-safe: a single breaker instance is shared across all workers hitting
    the same origin.
    """

    def __init__(self, threshold: int = 6, cooldown: float = 30.0, *, name: str = "") -> None:
        self._threshold = max(1, threshold)
        self._cooldown = max(0.0, cooldown)
        self.name = name
        self._fails = 0
        self._open_until = 0.0
        self._lock = threading.Lock()

    def before(self) -> None:
        """Fail fast if the breaker is currently open."""
        with self._lock:
            remaining = self._open_until - time.monotonic()
            if remaining > 0:
                raise CircuitOpen(
                    f"{self.name or 'origin'} circuit open; cooling down "
                    f"{remaining:.0f}s after repeated failures"
                )

    def record(self, ok: bool) -> bool:
        """Record an outcome. Returns ``True`` iff this call tripped the breaker."""
        with self._lock:
            if ok:
                self._fails = 0
                return False
            self._fails += 1
            if self._fails >= self._threshold:
                self._open_until = time.monotonic() + self._cooldown
                self._fails = 0
                return True
            return False

    @property
    def is_open(self) -> bool:
        with self._lock:
            return time.monotonic() < self._open_until

    def reset(self) -> None:
        with self._lock:
            self._fails = 0
            self._open_until = 0.0
