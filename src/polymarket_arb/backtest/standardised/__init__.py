"""Standardised backtest contract.

Phase: evidence-capture / auditability.

Every accepted trade from every source (rulebook deterministic, aggressive
deterministic, embedding-only, DeepSeek free-reign, closed-form simulators,
controls, strict validation, diagnostic) must flow through this contract before
it lands in any report.  This is the single source of truth used to compare
lanes side-by-side and to audit individual trades.

NO automatic promotion / demotion / kill decisions are made here.  Review fields
are neutral (``review_status``, ``review_notes``, ``observed_failure_mode``,
``observed_pattern_tag``, ``needs_manual_review``).  Promotion logic will be
designed later, after enough standardised logs have been inspected.
"""

from .contract import (
    SCHEMA_VERSION,
    SOURCE_LANES,
    DeepSeekPreTradeJustification,
    StandardisedRunManifest,
    StandardisedTradeRow,
)

__all__ = [
    "SCHEMA_VERSION",
    "SOURCE_LANES",
    "DeepSeekPreTradeJustification",
    "StandardisedRunManifest",
    "StandardisedTradeRow",
]
