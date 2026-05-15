"""Per-host token-bucket rate limiter, asyncio-friendly."""

from __future__ import annotations

import asyncio
import contextlib
import time
from urllib.parse import urlparse

from ..settings import RateLimitSpec


class TokenBucket:
    """Simple monotonic-clock token bucket. Not thread-safe; intended for
    single-event-loop async use."""

    def __init__(self, capacity: float, refill_per_s: float) -> None:
        if capacity <= 0 or refill_per_s <= 0:
            raise ValueError("capacity and refill_per_s must be positive")
        self.capacity = float(capacity)
        self.refill_per_s = float(refill_per_s)
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._cv = asyncio.Condition()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_per_s)
            self._last = now

    async def acquire(self, n: float = 1.0) -> None:
        if n > self.capacity:
            raise ValueError(f"requested {n} tokens but bucket capacity is {self.capacity}")
        async with self._cv:
            while True:
                self._refill()
                if self._tokens >= n:
                    self._tokens -= n
                    return
                # wait long enough to accumulate the deficit, then loop
                deficit = n - self._tokens
                wait = deficit / self.refill_per_s
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._cv.wait(), timeout=wait)


class RateLimiter:
    """Pool of `TokenBucket` keyed by hostname."""

    def __init__(self, specs: dict[str, RateLimitSpec]) -> None:
        self._buckets: dict[str, TokenBucket] = {
            host: TokenBucket(spec.capacity, spec.refill_per_s) for host, spec in specs.items()
        }

    @staticmethod
    def host_of(url: str) -> str:
        return urlparse(url).hostname or ""

    async def acquire(self, url: str) -> None:
        host = self.host_of(url)
        bucket = self._buckets.get(host)
        if bucket is None:
            return
        await bucket.acquire()
