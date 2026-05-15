"""Models returned by local inspection reports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TableStatus:
    name: str
    backing_path: Path
    file_count: int
    row_count: int | None
    latest_ingested_ts_ms: int | None


@dataclass(frozen=True)
class AuditCheck:
    status: str  # PASS | WARN | FAIL
    name: str
    detail: str


@dataclass(frozen=True)
class PipelineStage:
    name: str
    present: bool
    latest_ts_ms: int | None
    summary: dict[str, Any]
    next_command: str | None = None
