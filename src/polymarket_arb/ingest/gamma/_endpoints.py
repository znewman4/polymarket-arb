"""Gamma URL paths. Pagination uses ``limit`` + ``offset`` (NOT cursor)."""

from __future__ import annotations

MARKETS_PATH = "/markets"
EVENTS_PATH = "/events"
SINGLE_MARKET_PATH = "/markets/{market_id}"
SINGLE_EVENT_PATH = "/events/{event_id}"
