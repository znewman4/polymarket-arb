"""HTML report generators for polymarket-arb."""

from __future__ import annotations

from .historical_dataset_report import generate_historical_dataset_report
from .semantic_quality_report import generate_semantic_quality_report

__all__ = [
    "generate_historical_dataset_report",
    "generate_semantic_quality_report",
]
