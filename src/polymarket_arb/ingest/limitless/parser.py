"""Parse raw Limitless market dicts into LimitlessMarketEntry objects.

The Limitless /markets/active endpoint returns:
  {
    "data": [
      {
        "slug": "btc-above-65000",
        "title": "Will BTC be above $65,000?",
        "address": "0x...",
        "prices": [0.428, 0.572], # [YES, NO] decimals, sum approximately 1
        "marketType": "single",    # "single" = binary YES/NO; "group" = multi-outcome
        "tradeType": "amm",        # "amm" | "clob"
        ...
      }
    ],
    "totalMarketsCount": 150
  }
The API has returned both decimal prices summing approximately to 1 and older
percentage prices summing approximately to 100. Both forms are normalized to
0.0-1.0 before building an entry.
Only "single" marketType markets are binary YES/NO.
"""

from __future__ import annotations

from loguru import logger

from ...limitless.models import LimitlessMarketEntry


def parse_limitless_market(raw: dict) -> LimitlessMarketEntry | None:
    """Parse a binary market, accepting decimal or percentage price payloads."""
    slug = raw.get("slug", "")
    title = raw.get("title", "") or raw.get("question", "")
    # For CLOB markets the top-level "address" field is absent; fall back through
    # several known shapes that have appeared across Limitless API revisions.
    venue = raw.get("venue") or {}
    address = (
        raw.get("address", "")
        or venue.get("exchange", "")
        or venue.get("address", "")
        or raw.get("contractAddress", "")
        or raw.get("contract_address", "")
    )
    if not address:
        logger.warning(
            "limitless parser: address missing for {!r}; top-level keys={}; venue keys={}",
            slug,
            sorted(raw.keys()),
            sorted(venue.keys()),
        )

    if raw.get("marketType", "single") != "single":
        return None

    prices = raw.get("prices")
    if not isinstance(prices, list) or len(prices) != 2:
        logger.debug("limitless parser: skipping market {!r} — invalid prices field", slug)
        return None

    try:
        yes_raw = float(prices[0])
        no_raw = float(prices[1])
    except (TypeError, ValueError):
        logger.debug("limitless parser: skipping market {!r} — non-numeric prices", slug)
        return None

    if not (0.0 < yes_raw < 110.0) or not (0.0 < no_raw < 110.0):
        return None

    total = yes_raw + no_raw
    if 0.9 <= total <= 1.1:
        yes_price = yes_raw
    elif 90.0 <= total <= 110.0:
        yes_price = yes_raw / 100.0
    else:
        logger.debug(
            "limitless parser: skipping market {!r} — unrecognised price total ({} + {})",
            slug, yes_raw, no_raw,
        )
        return None

    if not (0.0 < yes_price < 1.0):
        return None

    tokens = raw.get("tokens") or {}
    token_id_yes = str(tokens.get("yes", ""))
    token_id_no = str(tokens.get("no", ""))

    return LimitlessMarketEntry(
        slug=slug,
        title=title,
        yes_price=yes_price,
        address=address,
        token_id_yes=token_id_yes,
        token_id_no=token_id_no,
    )
