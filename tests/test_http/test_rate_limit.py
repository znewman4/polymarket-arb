from __future__ import annotations

import time

import pytest

from polymarket_arb.http.rate_limit import RateLimiter, TokenBucket
from polymarket_arb.settings import RateLimitSpec


@pytest.mark.asyncio
async def test_token_bucket_serialises_excess_calls():
    bucket = TokenBucket(capacity=1, refill_per_s=10)  # 1 token, 10/s refill
    started = time.monotonic()
    await bucket.acquire()  # consumes the initial token
    await bucket.acquire()  # must wait ~0.1s
    elapsed = time.monotonic() - started
    assert elapsed >= 0.05  # at least roughly the refill interval


@pytest.mark.asyncio
async def test_rate_limiter_skips_unknown_hosts():
    rl = RateLimiter({"known.example": RateLimitSpec(capacity=1, refill_per_s=1)})
    await rl.acquire("https://unknown.example/foo")  # no exception, no wait
