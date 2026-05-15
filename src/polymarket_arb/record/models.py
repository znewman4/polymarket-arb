"""Recording-layer models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RecordingManifest:
    run_id: str
    started_at_ms: int
    ended_at_ms: int | None
    duration_s: int
    command: str
    args: dict[str, Any]
    markets_limit: int
    quote_interval_s: int | None
    score_interval_s: int | None
    orderbook_interval_s: int | None
    tables_written: list[str] = field(default_factory=list)
    row_counts_by_table: dict[str, int] = field(default_factory=dict)
    code_version: str | None = None
    config_hash: str | None = None
    status: str = "running"
    error_summary: str | None = None
    schema_version: int = 1
