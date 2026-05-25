"""Background cache layer for the dashboard.

Runs a daemon thread that refreshes all DuckDB query results every 300 seconds
so Flask request handlers never block on parquet I/O.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .queries import DuckDBQueryService

_log = logging.getLogger(__name__)

_REFRESH_INTERVAL_S = 300
_FIRST_LOAD_TIMEOUT_S = 60


class DashboardCache:
    """Wraps a DuckDBQueryService and refreshes all panel results in a background thread."""

    def __init__(self, qs: DuckDBQueryService) -> None:
        self._qs = qs
        self._data: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._ready = threading.Event()

        t = threading.Thread(target=self._run, daemon=True, name="dashboard-cache-refresh")
        t.start()

        if not self._ready.wait(timeout=_FIRST_LOAD_TIMEOUT_S):
            _log.warning("DashboardCache: first load timed out after %ds", _FIRST_LOAD_TIMEOUT_S)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def refresh(self) -> None:
        """Force a synchronous refresh. Intended for tests."""
        self._refresh()

    def _refresh(self) -> None:
        _log.info("DashboardCache: refresh cycle starting")
        methods: dict[str, Any] = {
            "overview_counters": lambda: self._qs.overview_counters(),
            "signals_by_strategy": lambda: self._qs.signals_by_strategy(),
            "signals_per_hour_last_24h": lambda: self._qs.signals_per_hour_last_24h(),
            "top_markets_by_signal": lambda: self._qs.top_markets_by_signal(limit=10),
            "health_snapshot": lambda: self._qs.health_snapshot(),
            "no_fill_breakdown": lambda: self._qs.no_fill_breakdown(),
            "edge_distribution": lambda: self._qs.edge_distribution(),
            "limitless_open_gaps": lambda: self._qs.limitless_open_gaps(),
            "market_coverage": lambda: self._qs.market_coverage(),
            "relationship_type_breakdown": lambda: self._qs.relationship_type_breakdown(),
            "markets_with_most_relationships": lambda: self._qs.markets_with_most_relationships(),
        }
        fresh: dict[str, Any] = {}
        for key, fn in methods.items():
            try:
                fresh[key] = fn()
            except Exception:
                _log.exception("DashboardCache: error refreshing %s", key)
        with self._lock:
            self._data.update(fresh)
        _log.info("DashboardCache: refresh cycle complete (%d keys)", len(fresh))

    def _run(self) -> None:
        self._refresh()
        self._ready.set()
        while True:
            time.sleep(_REFRESH_INTERVAL_S)
            self._refresh()


__all__ = ["DashboardCache"]
