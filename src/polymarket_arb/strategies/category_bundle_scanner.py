"""Scan complete category outcome spaces for N-way basket opportunities."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Literal

from .category_outcome_spaces import CategoryOutcomeSpace


@dataclass(frozen=True)
class CategoryPricePoint:
    token_id: str
    price: Decimal
    ts_ms: int


@dataclass(frozen=True)
class CategoryBundleScanRow:
    outcome_space_id: str
    display_name: str
    candidate_count: int
    known_total_candidates: int | None
    completeness_score: float
    exhaustiveness_confidence: float
    completeness_status: Literal["complete", "probably_incomplete", "unknown"]
    missing_candidate_warning: str
    strategy_allowed: bool
    sum_yes_prices: Decimal | None
    sum_no_prices: Decimal | None
    best_executable_basket: str
    gross_edge: Decimal
    net_edge_after_costs: Decimal
    rejection_reason: str | None
    registry_status: str = "unconfigured"
    configured_expected_total: int | None = None
    observed_total: int = 0
    completeness_policy: str = "incomplete_unknown"
    allow_bundle_backtest: bool = False
    completeness_reason: str = ""


def scan_category_bundle(
    space: CategoryOutcomeSpace,
    *,
    yes_prices: dict[str, CategoryPricePoint],
    no_prices: dict[str, CategoryPricePoint],
    min_net_edge: Decimal,
    fee_bps: Decimal,
    slippage_bps: Decimal,
    allow_uncertain: bool = False,
) -> CategoryBundleScanRow:
    candidate_count = len(space.candidates)
    known_total = space.known_total_candidates
    completeness_score = (
        min(1.0, candidate_count / known_total) if known_total and known_total > 0 else 0.0
    )
    is_complete = _registry_complete(space, candidate_count)
    completeness_status: Literal["complete", "probably_incomplete", "unknown"]
    if is_complete:
        completeness_status = "complete"
    elif known_total and candidate_count > known_total:
        completeness_status = "unknown"
    elif known_total:
        completeness_status = "probably_incomplete"
    else:
        completeness_status = "unknown"
    exhaustiveness_confidence = 1.0 if is_complete else (0.5 if known_total else 0.0)
    missing_warning = ""
    if known_total and candidate_count > known_total:
        missing_warning = f"observed {candidate_count}>{known_total} known candidates; registry total is stale or wrong"
    elif known_total and candidate_count < known_total:
        missing_warning = f"observed {candidate_count}/{known_total} known candidates"
    elif known_total is None:
        missing_warning = "known_total_candidates is not configured"

    yes_points = [yes_prices[c.yes_token_id] for c in space.candidates if c.yes_token_id in yes_prices]
    no_points = [no_prices[c.no_token_id] for c in space.candidates if c.no_token_id in no_prices]
    sum_yes = sum((p.price for p in yes_points), Decimal("0")) if len(yes_points) == candidate_count else None
    sum_no = sum((p.price for p in no_points), Decimal("0")) if len(no_points) == candidate_count else None

    strategy_allowed = (space.allow_bundle_backtest and is_complete) or allow_uncertain
    best_basket = "none"
    gross_edge = Decimal("0")
    net_edge = Decimal("0")
    rejection_reason: str | None = None

    cost_fraction = (fee_bps + slippage_bps) / Decimal("10000")
    if sum_yes is None:
        rejection_reason = "missing_price_history"
    elif not strategy_allowed:
        rejection_reason = "incomplete_or_unknown_outcome_space"
    else:
        yes_edge = Decimal("1") - sum_yes
        yes_net = yes_edge - cost_fraction * Decimal(candidate_count)
        # In a complete mutually exclusive outcome space, buying all NO tokens
        # pays out N-1 USDC regardless of which one candidate wins.
        no_edge = (Decimal(candidate_count - 1) - sum_no) if sum_no is not None else Decimal("0")
        no_net = no_edge - cost_fraction * Decimal(candidate_count)
        if yes_net >= min_net_edge and yes_net >= no_net:
            best_basket = "buy_all_yes"
            gross_edge = yes_edge
            net_edge = yes_net
        elif no_net >= min_net_edge:
            best_basket = "buy_all_no"
            gross_edge = no_edge
            net_edge = no_net
        else:
            gross_edge = max(yes_edge, no_edge)
            net_edge = max(yes_net, no_net)
            rejection_reason = "net_edge_below_threshold"

    return CategoryBundleScanRow(
        outcome_space_id=space.outcome_space_id,
        display_name=space.display_name,
        candidate_count=candidate_count,
        known_total_candidates=known_total,
        completeness_score=round(completeness_score, 4),
        exhaustiveness_confidence=exhaustiveness_confidence,
        completeness_status=completeness_status,
        missing_candidate_warning=missing_warning,
        strategy_allowed=strategy_allowed,
        sum_yes_prices=sum_yes,
        sum_no_prices=sum_no,
        best_executable_basket=best_basket,
        gross_edge=gross_edge,
        net_edge_after_costs=net_edge,
        rejection_reason=rejection_reason,
        registry_status=space.registry_status,
        configured_expected_total=space.configured_expected_total,
        observed_total=candidate_count,
        completeness_policy=space.completeness_policy,
        allow_bundle_backtest=space.allow_bundle_backtest,
        completeness_reason=space.completeness_reason,
    )


def row_to_dict(row: CategoryBundleScanRow) -> dict:
    d = asdict(row)
    for key in ("sum_yes_prices", "sum_no_prices", "gross_edge", "net_edge_after_costs"):
        if d[key] is not None:
            d[key] = str(d[key])
    return d


def _registry_complete(space: CategoryOutcomeSpace, candidate_count: int) -> bool:
    if not space.allow_bundle_backtest:
        return False
    policy = space.completeness_policy
    if policy == "complete_if_all_expected_present":
        return bool(space.known_total_candidates and candidate_count == space.known_total_candidates)
    if policy == "complete_if_required_options_present":
        return "satisfied" in space.completeness_reason
    return False
