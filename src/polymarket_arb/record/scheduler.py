"""Small deterministic interval helpers for recording commands."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smh]?)\s*$", re.IGNORECASE)


def parse_duration_s(value: str | int | float) -> int:
    if isinstance(value, int | float):
        return max(0, int(value))
    match = _DURATION_RE.match(value)
    if not match:
        raise ValueError(f"invalid duration {value!r}; expected e.g. 10s, 1m, 1h")
    amount = int(match.group(1))
    unit = (match.group(2) or "s").lower()
    factor = {"s": 1, "m": 60, "h": 3600}[unit]
    return amount * factor


async def run_interval_loop(
    action: Callable[[], Awaitable[dict[str, int]]],
    *,
    interval_s: int,
    duration_s: int,
) -> dict[str, int]:
    """Run ``action`` at least once, then repeat until duration elapses."""

    started = time.monotonic()
    totals: dict[str, int] = {}
    first = True
    while first or (time.monotonic() - started) < duration_s:
        first = False
        result = await action()
        for key, value in result.items():
            totals[key] = totals.get(key, 0) + int(value)
        remaining = duration_s - (time.monotonic() - started)
        if remaining <= 0:
            break
        await asyncio.sleep(min(max(interval_s, 0), remaining))
    return totals
