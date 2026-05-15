"""Optional public context fetch helpers.

The Phase 5.6 default path uses curated manual rules. This module only
supports explicit public GET snapshots for configured sources.
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

FORBIDDEN_HOST_FRAGMENTS = ("clob.polymarket.com",)


def validate_public_context_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("context fetch URL must be http or https")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("context fetch URL must include a host")
    if any(fragment in host for fragment in FORBIDDEN_HOST_FRAGMENTS):
        raise ValueError("context fetch URL points at a disallowed host")


async def fetch_public_text(url: str) -> str:
    validate_public_context_url(url)
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text
