"""Parse raw Limitless market dicts into LimitlessMarketEntry objects.

The Limitless /markets/active endpoint returns:
  {
    "data": [
      {
        "slug": "btc-above-65000",
        "title": "Will BTC be above $65,000?",
        "address": "0x...",
        "prices": [42.8, 57.2],   # [YES%, NO%] — percentages, always sum ≈ 100
        "marketType": "single",    # "single" = binary YES/NO; "group" = multi-outcome
        "tradeType": "amm",        # "amm" | "clob"
        ...
      }
    ],
    "totalMarketsCount": 150
  }
prices[0] is the YES probability as a percentage (divide by 100 to get 0.0-1.0).
Only "single" marketType markets are binary YES/NO.
"""

from __future__ import annotations

from loguru import logger

from ...limitless.models import LimitlessMarketEntry


def parse_limitless_market(raw: dict) -> LimitlessMarketEntry | None:
    """Parse one raw Limitless market dict. Returns None if invalid or non-binary."""
    slug = raw.get("slug", "")
    title = raw.get("title", "") or raw.get("question", "")
    address = raw.get("address", "")

    if raw.get("marketType", "single") != "single":
        return None

    prices = raw.get("prices")
    if not isinstance(prices, list) or len(prices) != 2:
        logger.debug("limitless parser: skipping market {!r} — invalid prices field", slug)
        return None

    try:
        yes_pct = float(prices[0])
        no_pct = float(prices[1])
    except (TypeError, ValueError):
        logger.debug("limitless parser: skipping market {!r} — non-numeric prices", slug)
        return None

    if not (0.0 < yes_pct < 100.0) or not (0.0 < no_pct < 100.0):
        return None

    if abs(yes_pct + no_pct - 100.0) > 2.0:
        logger.debug(
            "limitless parser: skipping market {!r} — prices don't sum to 100 ({} + {})",
            slug, yes_pct, no_pct,
        )
        return None

    return LimitlessMarketEntry(
        slug=slug,
        title=title,
        yes_price=yes_pct / 100.0,
        address=address,
    )
