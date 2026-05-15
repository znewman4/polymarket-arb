"""Tiny deterministic calibrators used by Phase 4 fusion."""

from __future__ import annotations


def freshness_score(age_ms: int | None, *, stale_after_ms: int = 30_000) -> float:
    if age_ms is None:
        return 0.0
    return max(0.0, min(1.0, 1.0 - (age_ms / stale_after_ms)))
