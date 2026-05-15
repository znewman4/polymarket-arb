"""Pure validation functions for the backfill dataset.

All functions are IO-free and return ``ValidationResult`` instances.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from ..storage.base import (
    BackfillCoverageRow,
    MarketRow,
    PriceHistoryRow,
    TradeHistoryRow,
)


@dataclass(frozen=True)
class ValidationResult:
    name: str
    status: Literal["PASS", "WARN", "FAIL"]
    details: dict[str, Any]


_ZERO = Decimal("0")
_ONE = Decimal("1")


def validate_markets_table(rows: list[MarketRow]) -> ValidationResult:
    if not rows:
        return ValidationResult(
            name="markets_table",
            status="WARN",
            details={"message": "no markets found — run gamma fetch-markets first"},
        )
    missing_tokens = [m.id for m in rows if not m.clob_token_ids]
    return ValidationResult(
        name="markets_table",
        status="WARN" if missing_tokens else "PASS",
        details={
            "total": len(rows),
            "missing_token_ids": len(missing_tokens),
        },
    )


def validate_price_history(rows: list[PriceHistoryRow]) -> ValidationResult:
    if not rows:
        return ValidationResult(
            name="price_history",
            status="WARN",
            details={"message": "no price history — run backfill prices first"},
        )
    out_of_bounds = [r for r in rows if r.price < _ZERO or r.price > _ONE]
    seen_ts: dict[tuple, int] = {}
    for r in rows:
        key = (r.token_id, r.ts_ms)
        seen_ts[key] = seen_ts.get(key, 0) + 1
    duplicates = sum(v - 1 for v in seen_ts.values() if v > 1)

    status: Literal["PASS", "WARN", "FAIL"] = "PASS"
    if out_of_bounds:
        status = "FAIL"
    elif duplicates:
        status = "WARN"

    return ValidationResult(
        name="price_history",
        status=status,
        details={
            "total": len(rows),
            "out_of_bounds": len(out_of_bounds),
            "duplicate_timestamps": duplicates,
        },
    )


def validate_trade_history(rows: list[TradeHistoryRow]) -> ValidationResult:
    if not rows:
        return ValidationResult(
            name="trade_history",
            status="WARN",
            details={"message": "no trade history (endpoint may be unavailable)"},
        )
    out_of_bounds = [r for r in rows if r.price < _ZERO or r.price > _ONE or r.size < _ZERO]
    seen: dict[tuple, int] = {}
    for r in rows:
        key = (r.token_id, r.trade_ts_ms, str(r.price), str(r.size))
        seen[key] = seen.get(key, 0) + 1
    duplicates = sum(v - 1 for v in seen.values() if v > 1)

    status: Literal["PASS", "WARN", "FAIL"] = "PASS"
    if out_of_bounds:
        status = "FAIL"
    elif duplicates:
        status = "WARN"

    return ValidationResult(
        name="trade_history",
        status=status,
        details={
            "total": len(rows),
            "out_of_bounds": len(out_of_bounds),
            "duplicate_trades": duplicates,
        },
    )


def validate_semantics_coverage(
    market_ids: list[str],
    sem_ids: set[str],
    score_ids: set[str],
    impl_ids: set[str],
) -> ValidationResult:
    if not market_ids:
        return ValidationResult(
            name="semantics_coverage",
            status="WARN",
            details={"message": "no markets to check"},
        )
    missing_sem = [m for m in market_ids if m not in sem_ids]
    missing_score = [m for m in market_ids if m not in score_ids]
    missing_impl = [m for m in market_ids if m not in impl_ids]
    pct_sem = len(sem_ids.intersection(market_ids)) / len(market_ids)
    status: Literal["PASS", "WARN", "FAIL"] = "PASS"
    if pct_sem < 0.5:
        status = "FAIL"
    elif missing_sem or missing_score or missing_impl:
        status = "WARN"
    return ValidationResult(
        name="semantics_coverage",
        status=status,
        details={
            "total_markets": len(market_ids),
            "missing_semantics": len(missing_sem),
            "missing_scores": len(missing_score),
            "missing_implications": len(missing_impl),
            "semantics_pct": round(pct_sem, 3),
        },
    )


def validate_no_thinking(rows: list[Any], fields_to_check: list[str]) -> ValidationResult:
    """Scan string fields for <think> markers (chain-of-thought leakage)."""
    violations: list[str] = []
    for row in rows:
        for field in fields_to_check:
            val = getattr(row, field, None)
            if isinstance(val, str) and "<think>" in val.lower():
                violations.append(f"{getattr(row, 'source_market_id', getattr(row, 'market_id', '?'))}.{field}")
    status: Literal["PASS", "WARN", "FAIL"] = "FAIL" if violations else "PASS"
    return ValidationResult(
        name="no_thinking_content",
        status=status,
        details={"violations": violations, "checked_fields": fields_to_check},
    )


def validate_coverage_rows(rows: list[BackfillCoverageRow]) -> ValidationResult:
    if not rows:
        return ValidationResult(
            name="backfill_coverage",
            status="WARN",
            details={"message": "no coverage rows — run backfill coverage first"},
        )
    recommended = sum(1 for r in rows if r.recommended_for_backtest)
    low_coverage = [r.market_id for r in rows if r.coverage_score < 0.3]
    return ValidationResult(
        name="backfill_coverage",
        status="PASS",
        details={
            "total": len(rows),
            "recommended_for_backtest": recommended,
            "low_coverage_markets": len(low_coverage),
        },
    )
