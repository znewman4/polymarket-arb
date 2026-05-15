"""Gamma's response is JSON whose ``outcomes`` / ``outcomePrices`` /
``clobTokenIds`` fields arrive *inconsistently*: sometimes as raw lists,
sometimes as JSON strings inside JSON. Normalise to a Python list.

The bug is documented in the Polymarket-BTC bot's ``patch_gamma_markets.py``
and confirmed in their own ``polymarket/agents`` repo's parsers.
"""

from __future__ import annotations

import json
from typing import Any


def parse_stringified_json(value: Any) -> list:
    """Return ``value`` as a Python list, regardless of which Gamma form it
    arrived in.

    - ``None`` → ``[]``
    - ``list`` → returned unchanged
    - ``str`` → ``json.loads`` and (if list) return; else ``[]``
    - other → ``[]`` (we never raise; the caller's Pydantic layer
      surfaces validation errors at a higher level)
    """

    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return []
        if isinstance(parsed, list):
            return parsed
    return []
