"""Risk-layer dataclasses: order intent, gate context, check results, token."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    reason: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status is CheckStatus.PASS


@dataclass(frozen=True)
class PreflightReport:
    overall: CheckStatus
    checks: list[CheckResult]
    strategy_id: str | None
    order_id: str | None

    @property
    def passed(self) -> bool:
        return self.overall is CheckStatus.PASS


class PreflightFailure(RuntimeError):
    """Raised when the preflight gate refuses to issue a token."""

    def __init__(self, report: PreflightReport) -> None:
        self.report = report
        first_fail = next((c for c in report.checks if not c.passed), None)
        msg = "preflight failed"
        if first_fail is not None:
            msg += f": {first_fail.name} → {first_fail.reason}"
        super().__init__(msg)


@dataclass(frozen=True)
class RiskLimits:
    """Per-strategy + global risk limits. Phase 0 only stores them; Phase 4+
    enforces."""

    max_stake_usdc_per_market: Decimal = Decimal("1.0")
    max_stake_usdc_per_event: Decimal = Decimal("5.0")
    max_total_unresolved_usdc: Decimal = Decimal("20.0")
    max_open_orders: int = 5
    max_markets_per_strategy: int = 10
    min_edge_after_fees_pct: Decimal = Decimal("0.005")
    min_liquidity_usdc: Decimal = Decimal("50.0")
    max_spread_pct: Decimal = Decimal("0.05")
    max_quote_age_ms: int = 5_000


@dataclass(frozen=True)
class OrderIntent:
    """A proposed order. Constructing one is free; getting it past the gate
    is not. The Phase 10 OrderClient will consume an OrderIntent + a
    PreflightToken keyed to the same intent.id."""

    id: str
    strategy_id: str
    token_id: str
    side: str  # "buy" | "sell"
    price: Decimal
    size: Decimal
    limit_price: Decimal | None = None
    quote_age_ms: int | None = None
    spread_pct: Decimal | None = None
    available_balance_usdc: Decimal | None = None


@dataclass(frozen=True)
class PreflightContext:
    """Inputs to every check. Lets checks remain side-effect-free + testable."""

    strategy_id: str | None
    order: OrderIntent | None
    paper_mode: bool
    limits: RiskLimits


@dataclass(frozen=True)
class PreflightToken:
    """Opaque token issued by the gate. Cannot be constructed outside this
    module reliably (we discourage it; Phase 10 OrderClient checks it).

    The token carries the order id it was issued for; reusing a token for a
    different order is a contract violation that callers must not attempt.
    """

    token_id: str
    issued_for_order_id: str | None
    issued_at_ms: int
    report: PreflightReport

    @staticmethod
    def _new(order_id: str | None, report: PreflightReport, now_ms: int) -> PreflightToken:
        return PreflightToken(
            token_id=uuid.uuid4().hex,
            issued_for_order_id=order_id,
            issued_at_ms=now_ms,
            report=report,
        )
