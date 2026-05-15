"""Parse CLOB /prices-history payloads into PriceHistoryRow DTOs.

Amendment: parse_prices_history returns a PriceHistoryParseResult object
(rows + quality counters) rather than a bare list, so callers can surface
deduplication and out-of-bounds counts in coverage metadata.

Invalid price points are dropped and counted (strict=False default), not
raised, so a few bad points never crash an entire token backfill.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from ...storage.base import PriceHistoryRow


class PriceHistoryParseError(ValueError):
    """Raised only for completely unreadable payloads (wrong type, missing key)."""


@dataclass(frozen=True)
class PriceHistoryParseResult:
    rows: list[PriceHistoryRow]
    duplicate_timestamp_count: int
    price_out_of_bounds_count: int
    malformed_point_count: int
    warnings: list[str]


def _now_ms() -> int:
    return int(time.time() * 1000)


_ZERO = Decimal("0")
_ONE = Decimal("1")


def parse_prices_history(
    payload: dict[str, Any],
    *,
    token_id: str,
    interval: str,
    market_id: str,
    condition_id: str | None,
    outcome: str | None,
    source: str = "clob",
    strict: bool = False,
) -> PriceHistoryParseResult:
    """Parse a /prices-history JSON payload into ``PriceHistoryRow`` DTOs.

    Parameters
    ----------
    payload:
        Raw JSON from the API.  Expected shape:
        ``{"history": [{"t": <unix_seconds_int>, "p": <str|float>}, ...]}``
    token_id:
        CLOB asset/token ID (NOT Gamma market_id).
    interval:
        The interval string that was requested (e.g. ``"1h"``).
    market_id:
        Gamma market ID for joining.
    condition_id:
        Optional Gamma condition ID.
    outcome:
        Optional outcome label (e.g. ``"Yes"``).
    source:
        Data source tag — defaults to ``"clob"``.
    strict:
        If True, raise ``PriceHistoryParseError`` on the first out-of-bounds
        or malformed point instead of dropping-and-counting.
    """
    if not isinstance(payload, dict):
        raise PriceHistoryParseError("prices-history payload must be a dict")

    raw_points: list[Any] = payload.get("history") or []
    if not isinstance(raw_points, list):
        raise PriceHistoryParseError("'history' field must be a list")

    malformed_count = 0
    out_of_bounds_count = 0
    warnings: list[str] = []
    ingested_ts_ms = _now_ms()
    fidelity_str = str(interval)

    # First pass: parse valid points, count errors.
    parsed: list[tuple[int, Decimal]] = []  # (ts_ms, price)
    for raw in raw_points:
        if not isinstance(raw, dict):
            malformed_count += 1
            if strict:
                raise PriceHistoryParseError(f"malformed point (not a dict): {raw!r}")
            continue

        t_val = raw.get("t")
        p_val = raw.get("p")

        # Parse timestamp (unix seconds → ms).
        try:
            t_s = int(t_val)  # type: ignore[arg-type]
            ts_ms = t_s * 1000 if t_s < 10_000_000_000 else t_s
        except (TypeError, ValueError) as exc:
            malformed_count += 1
            if strict:
                raise PriceHistoryParseError(f"malformed timestamp: {t_val!r}") from exc
            continue

        # Parse price.
        try:
            price = Decimal(str(p_val))
        except (InvalidOperation, TypeError) as exc:
            malformed_count += 1
            if strict:
                raise PriceHistoryParseError(f"malformed price: {p_val!r}") from exc
            continue

        if price < _ZERO or price > _ONE:
            out_of_bounds_count += 1
            if strict:
                raise PriceHistoryParseError(f"price out of bounds [0,1]: {price}")
            warnings.append(f"price out of bounds at ts={ts_ms}: {price}")
            continue

        parsed.append((ts_ms, price))

    # Count duplicates before deduplication.
    seen_ts: dict[int, int] = {}
    for ts_ms, _ in parsed:
        seen_ts[ts_ms] = seen_ts.get(ts_ms, 0) + 1
    duplicate_count = sum(v - 1 for v in seen_ts.values() if v > 1)
    if duplicate_count > 0:
        warnings.append(f"{duplicate_count} duplicate timestamp(s) found and deduped")

    # Dedupe: keep the last occurrence for each timestamp (stable).
    dedupe: dict[int, Decimal] = {}
    for ts_ms, price in parsed:
        dedupe[ts_ms] = price

    rows: list[PriceHistoryRow] = [
        PriceHistoryRow(
            market_id=market_id,
            condition_id=condition_id,
            token_id=token_id,
            outcome=outcome,
            ts_ms=ts_ms,
            price=price,
            source=source,
            fidelity=fidelity_str,
            interval=interval,
            schema_version=1,
            ingested_ts_ms=ingested_ts_ms,
        )
        for ts_ms, price in sorted(dedupe.items())
    ]

    return PriceHistoryParseResult(
        rows=rows,
        duplicate_timestamp_count=duplicate_count,
        price_out_of_bounds_count=out_of_bounds_count,
        malformed_point_count=malformed_count,
        warnings=warnings,
    )
