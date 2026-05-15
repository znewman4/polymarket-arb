"""Conservative N-way category bundle scan/backtest.

This module is research-only. It writes explainable file artifacts and never
places orders or touches authenticated trading APIs.
"""

from __future__ import annotations

import csv
import json
import uuid
from bisect import bisect_right
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..storage.base import PriceHistoryRow
from ..storage.parquet.price_history_repo import ParquetPriceHistoryRepository
from ..storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)
from ..strategies.category_bundle_scanner import (
    CategoryBundleScanRow,
    CategoryPricePoint,
    row_to_dict,
    scan_category_bundle,
)
from ..strategies.category_outcome_spaces import (
    CategoryOutcomeSpace,
    group_category_outcome_spaces,
    load_outcome_space_metadata,
)
from ..strategies.models import CategoryBundleBacktestConfig

_FUNNEL_COUNT_KEYS = (
    "accepted_relationships_loaded",
    "outcome_spaces_loaded",
    "complete_outcome_spaces",
    "analysis_only_outcome_spaces",
    "outcome_spaces_with_price_history",
    "outcome_spaces_with_aligned_price_series",
    "ticks_evaluated",
    "gross_violations_found",
    "net_violations_after_costs",
    "candidates_accepted_for_simulation",
    "trades_executed",
)

_FUNNEL_REJECTION_KEYS = (
    "missing_price_history",
    "alignment_failed",
    "incomplete_or_unknown_outcome_space",
    "net_edge_below_threshold",
    "unsupported_trade_structure",
    "cash_limit",
)


def run_category_bundle_scan(
    data_root: Path,
    cfg: CategoryBundleBacktestConfig,
) -> dict[str, Any]:
    """Write a latest-price bundle scan without simulating trades."""
    run_id = cfg.run_id or uuid.uuid4().hex
    cfg = cfg.model_copy(update={"run_id": run_id})
    out_dir = data_root / "backtests" / run_id / "category_bundle"
    out_dir.mkdir(parents=True, exist_ok=True)

    rel_repo = ParquetRelationshipCandidatesRepository(data_root)
    relationships = list(rel_repo.iter_accepted())
    metadata = load_outcome_space_metadata(Path(cfg.metadata_path))
    spaces = group_category_outcome_spaces(relationships, metadata=metadata)
    price_repo = ParquetPriceHistoryRepository(data_root)

    scan_rows: list[dict[str, Any]] = []
    for space in spaces:
        yes_prices, no_prices = _latest_prices_for_space(price_repo, space)
        row = scan_category_bundle(
            space,
            yes_prices=yes_prices,
            no_prices=no_prices,
            min_net_edge=cfg.min_net_edge,
            fee_bps=cfg.fee_bps,
            slippage_bps=cfg.slippage_bps,
            allow_uncertain=cfg.allow_uncertain_bundles,
        )
        scan_rows.append(_scan_row_with_context(row, space))

    _write_json(out_dir / "config.json", _jsonify(cfg.model_dump()))
    _write_csv(out_dir / "bundle_scan.csv", scan_rows, _bundle_scan_fields())
    metrics = _scan_metrics(run_id, cfg, scan_rows)
    _write_json(out_dir / "metrics.json", metrics)
    return {"run_id": run_id, "output_dir": out_dir, "metrics": metrics, "scan_rows": scan_rows}


def run_category_bundle_backtest(
    data_root: Path,
    cfg: CategoryBundleBacktestConfig,
) -> dict[str, Any]:
    """Replay complete category bundles over historical prices."""
    run_id = cfg.run_id or uuid.uuid4().hex
    cfg = cfg.model_copy(update={"run_id": run_id})
    out_dir = data_root / "backtests" / run_id / "category_bundle"
    out_dir.mkdir(parents=True, exist_ok=True)

    funnel = _new_funnel_audit(run_id)
    rel_repo = ParquetRelationshipCandidatesRepository(data_root)
    relationships = list(rel_repo.iter_accepted())
    funnel["counts"]["accepted_relationships_loaded"] = len(relationships)

    metadata = load_outcome_space_metadata(Path(cfg.metadata_path))
    spaces = group_category_outcome_spaces(relationships, metadata=metadata)
    funnel["counts"]["outcome_spaces_loaded"] = len(spaces)

    price_repo = ParquetPriceHistoryRepository(data_root)
    latest_scan_rows: list[dict[str, Any]] = []
    opportunities: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    open_spaces: set[str] = set()

    cash = cfg.starting_cash_usdc
    total_fees = Decimal("0")
    total_slippage = Decimal("0")
    bundle_trade_count = 0

    for space in spaces:
        latest_yes, latest_no = _latest_prices_for_space(price_repo, space)
        latest_row = scan_category_bundle(
            space,
            yes_prices=latest_yes,
            no_prices=latest_no,
            min_net_edge=cfg.min_net_edge,
            fee_bps=cfg.fee_bps,
            slippage_bps=cfg.slippage_bps,
            allow_uncertain=cfg.allow_uncertain_bundles,
        )
        latest_scan_rows.append(_scan_row_with_context(latest_row, space))

        if _is_complete(space):
            _inc(funnel, "complete_outcome_spaces")
        else:
            _inc(funnel, "analysis_only_outcome_spaces")

        aligned = _aligned_ticks_for_space(price_repo, space, cfg)
        if aligned is None:
            _inc(funnel, "missing_price_history")
            continue
        if not aligned:
            _inc(funnel, "alignment_failed")
            continue
        _inc(funnel, "outcome_spaces_with_price_history")
        _inc(funnel, "outcome_spaces_with_aligned_price_series")

        for ts_ms, yes_prices, no_prices in aligned:
            _inc(funnel, "ticks_evaluated")
            row = scan_category_bundle(
                space,
                yes_prices=yes_prices,
                no_prices=no_prices,
                min_net_edge=cfg.min_net_edge,
                fee_bps=cfg.fee_bps,
                slippage_bps=cfg.slippage_bps,
                allow_uncertain=cfg.allow_uncertain_bundles,
            )
            if row.rejection_reason:
                _inc(funnel, row.rejection_reason)
                if row.gross_edge > Decimal("0"):
                    _inc(funnel, "gross_violations_found")
                    opportunities.append(
                        _opportunity_row(run_id, space, row, ts_ms, accepted=False)
                    )
                continue

            _inc(funnel, "gross_violations_found")
            _inc(funnel, "net_violations_after_costs")
            opportunity = _opportunity_row(run_id, space, row, ts_ms, accepted=True)

            if row.best_executable_basket not in {"buy_all_yes", "buy_all_no"}:
                _inc(funnel, "unsupported_trade_structure")
                opportunity["accepted_for_simulation"] = False
                opportunity["rejection_reason"] = "unsupported_trade_structure"
                opportunities.append(opportunity)
                continue
            if space.outcome_space_id in open_spaces:
                opportunity["accepted_for_simulation"] = False
                opportunity["rejection_reason"] = "already_open_outcome_space"
                opportunities.append(opportunity)
                continue

            required_cash = cfg.max_stake_per_bundle_usdc
            if cash < required_cash:
                _inc(funnel, "cash_limit")
                opportunity["accepted_for_simulation"] = False
                opportunity["rejection_reason"] = "cash_limit"
                opportunities.append(opportunity)
                continue

            _inc(funnel, "candidates_accepted_for_simulation")
            opportunities.append(opportunity)
            bundle_trades, fees, slippage = _execute_bundle(
                run_id,
                opportunity["opportunity_id"],
                space,
                row.best_executable_basket,
                ts_ms,
                yes_prices,
                no_prices,
                cfg,
            )
            trades.extend(bundle_trades)
            total_fees += fees
            total_slippage += slippage
            cash -= cfg.max_stake_per_bundle_usdc + fees
            open_spaces.add(space.outcome_space_id)
            bundle_trade_count += 1
            equity_curve.append({
                "ts_ms": ts_ms,
                "cash_usdc": str(cash),
                "equity_usdc": str(cash + _mark_to_market(trades, latest_yes, latest_no)),
            })

    funnel["counts"]["trades_executed"] = bundle_trade_count
    ending_equity = cash + _mark_to_market_all(trades, price_repo)
    metrics = _backtest_metrics(
        run_id,
        cfg,
        spaces,
        latest_scan_rows,
        opportunities,
        trades,
        cash,
        ending_equity,
        total_fees,
        total_slippage,
        funnel,
    )

    _write_json(out_dir / "config.json", _jsonify(cfg.model_dump()))
    _write_json(out_dir / "metrics.json", metrics)
    _write_json(out_dir / "funnel_audit.json", funnel)
    _write_csv(out_dir / "bundle_scan.csv", latest_scan_rows, _bundle_scan_fields())
    _write_csv(out_dir / "bundle_opportunities.csv", opportunities, _opportunity_fields())
    _write_csv(out_dir / "trades.csv", trades, _trade_fields())
    _write_csv(out_dir / "equity_curve.csv", equity_curve, ["ts_ms", "cash_usdc", "equity_usdc"])

    return {
        "run_id": run_id,
        "output_dir": out_dir,
        "metrics": metrics,
        "funnel_audit": funnel,
    }


def _latest_prices_for_space(
    price_repo: ParquetPriceHistoryRepository,
    space: CategoryOutcomeSpace,
) -> tuple[dict[str, CategoryPricePoint], dict[str, CategoryPricePoint]]:
    yes: dict[str, CategoryPricePoint] = {}
    no: dict[str, CategoryPricePoint] = {}
    for candidate in space.candidates:
        yes_row = _latest_row(price_repo.iter_for_token(candidate.yes_token_id))
        no_row = _latest_row(price_repo.iter_for_token(candidate.no_token_id))
        if yes_row is not None:
            yes[candidate.yes_token_id] = CategoryPricePoint(candidate.yes_token_id, yes_row.price, yes_row.ts_ms)
        if no_row is not None:
            no[candidate.no_token_id] = CategoryPricePoint(candidate.no_token_id, no_row.price, no_row.ts_ms)
    return yes, no


def _aligned_ticks_for_space(
    price_repo: ParquetPriceHistoryRepository,
    space: CategoryOutcomeSpace,
    cfg: CategoryBundleBacktestConfig,
) -> list[tuple[int, dict[str, CategoryPricePoint], dict[str, CategoryPricePoint]]] | None:
    yes_series: dict[str, list[PriceHistoryRow]] = {}
    no_series: dict[str, list[PriceHistoryRow]] = {}
    for candidate in space.candidates:
        yes_rows = _filter_rows(price_repo.iter_for_token(candidate.yes_token_id), cfg)
        if not yes_rows:
            return None
        yes_series[candidate.yes_token_id] = yes_rows
        no_rows = _filter_rows(price_repo.iter_for_token(candidate.no_token_id), cfg)
        if no_rows:
            no_series[candidate.no_token_id] = no_rows

    first_ts = max(rows[0].ts_ms for rows in yes_series.values())
    last_ts = min(rows[-1].ts_ms for rows in yes_series.values())
    if first_ts > last_ts:
        return []

    aligned: list[tuple[int, dict[str, CategoryPricePoint], dict[str, CategoryPricePoint]]] = []
    ts_ms = first_ts
    while ts_ms <= last_ts:
        yes_points: dict[str, CategoryPricePoint] = {}
        no_points: dict[str, CategoryPricePoint] = {}
        for token_id, rows in yes_series.items():
            row = _row_at_or_before(rows, ts_ms)
            if row is None or ts_ms - row.ts_ms > cfg.quote_staleness_limit_ms:
                break
            yes_points[token_id] = CategoryPricePoint(token_id, row.price, row.ts_ms)
        else:
            for token_id, rows in no_series.items():
                row = _row_at_or_before(rows, ts_ms)
                if row is not None and ts_ms - row.ts_ms <= cfg.quote_staleness_limit_ms:
                    no_points[token_id] = CategoryPricePoint(token_id, row.price, row.ts_ms)
            aligned.append((ts_ms, yes_points, no_points))
        ts_ms += cfg.signal_interval_ms
    return aligned


def _filter_rows(
    rows: Any,
    cfg: CategoryBundleBacktestConfig,
) -> list[PriceHistoryRow]:
    result = []
    for row in rows:
        if cfg.start_ts_ms is not None and row.ts_ms < cfg.start_ts_ms:
            continue
        if cfg.end_ts_ms is not None and row.ts_ms > cfg.end_ts_ms:
            continue
        result.append(row)
    return result


def _row_at_or_before(rows: list[PriceHistoryRow], ts_ms: int) -> PriceHistoryRow | None:
    timestamps = [r.ts_ms for r in rows]
    idx = bisect_right(timestamps, ts_ms) - 1
    if idx < 0:
        return None
    return rows[idx]


def _latest_row(rows: Any) -> PriceHistoryRow | None:
    latest: PriceHistoryRow | None = None
    for row in rows:
        latest = row
    return latest


def _execute_bundle(
    run_id: str,
    opportunity_id: str,
    space: CategoryOutcomeSpace,
    basket: str,
    ts_ms: int,
    yes_prices: dict[str, CategoryPricePoint],
    no_prices: dict[str, CategoryPricePoint],
    cfg: CategoryBundleBacktestConfig,
) -> tuple[list[dict[str, Any]], Decimal, Decimal]:
    trades: list[dict[str, Any]] = []
    total_fees = Decimal("0")
    total_slippage = Decimal("0")
    stake_per_leg = cfg.max_stake_per_bundle_usdc / Decimal(max(1, len(space.candidates)))
    cost_fraction = cfg.slippage_bps / Decimal("10000")
    fee_fraction = cfg.fee_bps / Decimal("10000")

    for idx, candidate in enumerate(space.candidates):
        point = yes_prices.get(candidate.yes_token_id) if basket == "buy_all_yes" else no_prices.get(candidate.no_token_id)
        if point is None:
            continue
        fill_price = min(Decimal("1"), point.price * (Decimal("1") + cost_fraction))
        shares = stake_per_leg / fill_price if fill_price > 0 else Decimal("0")
        fee = stake_per_leg * fee_fraction
        slippage = stake_per_leg * cost_fraction
        total_fees += fee
        total_slippage += slippage
        trades.append({
            "trade_id": f"{opportunity_id}:{idx}",
            "run_id": run_id,
            "opportunity_id": opportunity_id,
            "outcome_space_id": space.outcome_space_id,
            "candidate": candidate.candidate,
            "market_id": candidate.market_id,
            "token_id": point.token_id,
            "basket": basket,
            "side": "buy",
            "fill_ts_ms": ts_ms,
            "fill_price": str(fill_price),
            "shares": str(shares),
            "notional_usdc": str(stake_per_leg),
            "fees_usdc": str(fee),
            "slippage_cost_usdc": str(slippage),
            "resolution_outcome": "unresolved",
            "realised_pnl_usdc": "",
        })
    return trades, total_fees, total_slippage


def _mark_to_market_all(
    trades: list[dict[str, Any]],
    price_repo: ParquetPriceHistoryRepository,
) -> Decimal:
    prices: dict[str, Decimal] = {}
    for trade in trades:
        token_id = str(trade["token_id"])
        if token_id not in prices:
            latest = _latest_row(price_repo.iter_for_token(token_id))
            if latest is not None:
                prices[token_id] = latest.price
    return sum(
        (
            Decimal(str(t["shares"])) * prices.get(str(t["token_id"]), Decimal("0"))
            for t in trades
        ),
        Decimal("0"),
    )


def _mark_to_market(
    trades: list[dict[str, Any]],
    latest_yes: dict[str, CategoryPricePoint],
    latest_no: dict[str, CategoryPricePoint],
) -> Decimal:
    prices = {k: v.price for k, v in latest_yes.items()}
    prices.update({k: v.price for k, v in latest_no.items()})
    return sum(
        (
            Decimal(str(t["shares"])) * prices.get(str(t["token_id"]), Decimal("0"))
            for t in trades
        ),
        Decimal("0"),
    )


def _opportunity_row(
    run_id: str,
    space: CategoryOutcomeSpace,
    row: CategoryBundleScanRow,
    ts_ms: int,
    *,
    accepted: bool,
) -> dict[str, Any]:
    opportunity_id = f"{run_id}:{space.outcome_space_id}:{ts_ms}:{row.best_executable_basket}"
    d = _scan_row_with_context(row, space)
    d.update({
        "opportunity_id": opportunity_id,
        "run_id": run_id,
        "signal_ts_ms": ts_ms,
        "accepted_for_simulation": accepted,
    })
    return d


def _scan_row_with_context(
    row: CategoryBundleScanRow,
    space: CategoryOutcomeSpace,
) -> dict[str, Any]:
    d = row_to_dict(row)
    d["candidate_names"] = "; ".join(c.candidate for c in space.candidates)
    d["market_ids"] = "; ".join(c.market_id for c in space.candidates)
    d["source_relationship_ids"] = "; ".join(space.source_relationship_ids)
    return d


def _scan_metrics(
    run_id: str,
    cfg: CategoryBundleBacktestConfig,
    scan_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    complete = sum(1 for r in scan_rows if r.get("completeness_status") == "complete")
    allowed = sum(1 for r in scan_rows if r.get("strategy_allowed") is True)
    return {
        "run_id": run_id,
        "strategy_name": cfg.strategy_name,
        "outcome_spaces_scanned": len(scan_rows),
        "complete_outcome_spaces": complete,
        "strategy_allowed_outcome_spaces": allowed,
        "opportunities_found": sum(1 for r in scan_rows if r.get("best_executable_basket") != "none"),
        "trades_executed": 0,
        "starting_cash_usdc": str(cfg.starting_cash_usdc),
        "ending_cash_usdc": str(cfg.starting_cash_usdc),
        "ending_equity_usdc": str(cfg.starting_cash_usdc),
        "net_pnl_usdc": "0",
        "credibility_label": "data_insufficient",
        "credibility_rationale": "Scan-only run; no simulated trades were executed.",
        "generated_at": _now_iso(),
    }


def _backtest_metrics(
    run_id: str,
    cfg: CategoryBundleBacktestConfig,
    spaces: list[CategoryOutcomeSpace],
    scan_rows: list[dict[str, Any]],
    opportunities: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    ending_cash: Decimal,
    ending_equity: Decimal,
    total_fees: Decimal,
    total_slippage: Decimal,
    funnel: dict[str, Any],
) -> dict[str, Any]:
    bundle_trades = int(funnel["counts"]["trades_executed"])
    net_pnl = ending_equity - cfg.starting_cash_usdc
    if not spaces:
        label = "data_insufficient"
        rationale = "No accepted mutually-exclusive category outcome spaces were available."
    elif bundle_trades == 0:
        label = "data_insufficient"
        rationale = "No complete category bundle opportunities passed price, completeness, and net-edge gates."
    elif bundle_trades < 30:
        label = "data_insufficient"
        rationale = "Fewer than 30 bundle trades were simulated; sample is too small for credibility."
    elif net_pnl <= 0:
        label = "not_credible"
        rationale = "The simulated category bundle backtest did not produce positive net PnL."
    else:
        label = "inconclusive"
        rationale = "Positive simulated PnL exists, but additional baselines and robustness checks are required."
    return {
        "run_id": run_id,
        "strategy_name": cfg.strategy_name,
        "outcome_spaces_scanned": len(scan_rows),
        "complete_outcome_spaces": sum(1 for r in scan_rows if r.get("completeness_status") == "complete"),
        "analysis_only_outcome_spaces": sum(1 for r in scan_rows if r.get("completeness_status") != "complete"),
        "opportunities_found": len(opportunities),
        "candidates_accepted": int(funnel["counts"]["candidates_accepted_for_simulation"]),
        "trades_executed": bundle_trades,
        "leg_trades_executed": len(trades),
        "starting_cash_usdc": str(cfg.starting_cash_usdc),
        "ending_cash_usdc": str(ending_cash),
        "ending_equity_usdc": str(ending_equity),
        "total_return_pct": float((net_pnl / cfg.starting_cash_usdc) * Decimal("100")) if cfg.starting_cash_usdc else 0.0,
        "gross_pnl_usdc": str(net_pnl + total_fees + total_slippage),
        "net_pnl_usdc": str(net_pnl),
        "total_fees_usdc": str(total_fees),
        "total_slippage_usdc": str(total_slippage),
        "credibility_label": label,
        "credibility_rationale": rationale,
        "generated_at": _now_iso(),
    }


def _is_complete(space: CategoryOutcomeSpace) -> bool:
    if not space.allow_bundle_backtest:
        return False
    if space.completeness_policy == "complete_if_all_expected_present":
        return bool(space.known_total_candidates and len(space.candidates) >= space.known_total_candidates)
    if space.completeness_policy == "complete_if_required_options_present":
        return "satisfied" in space.completeness_reason
    return False


def _new_funnel_audit(run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "strategy": "category_bundle",
        "counts": {key: 0 for key in _FUNNEL_COUNT_KEYS},
        "rejections": {key: 0 for key in _FUNNEL_REJECTION_KEYS},
    }


def _inc(funnel: dict[str, Any], key: str, amount: int = 1) -> None:
    if key in funnel["counts"]:
        funnel["counts"][key] += amount
    elif key in funnel["rejections"]:
        funnel["rejections"][key] += amount


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(_jsonify(payload), indent=2, sort_keys=True), encoding="utf-8")


def _jsonify(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonify(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonify(v) for v in value]
    return value


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _bundle_scan_fields() -> list[str]:
    return [
        "outcome_space_id",
        "display_name",
        "candidate_count",
        "known_total_candidates",
        "registry_status",
        "configured_expected_total",
        "observed_total",
        "completeness_policy",
        "allow_bundle_backtest",
        "completeness_reason",
        "completeness_score",
        "exhaustiveness_confidence",
        "completeness_status",
        "missing_candidate_warning",
        "strategy_allowed",
        "sum_yes_prices",
        "sum_no_prices",
        "best_executable_basket",
        "gross_edge",
        "net_edge_after_costs",
        "rejection_reason",
        "candidate_names",
        "market_ids",
        "source_relationship_ids",
    ]


def _opportunity_fields() -> list[str]:
    return [
        "opportunity_id",
        "run_id",
        "signal_ts_ms",
        "accepted_for_simulation",
        *_bundle_scan_fields(),
    ]


def _trade_fields() -> list[str]:
    return [
        "trade_id",
        "run_id",
        "opportunity_id",
        "outcome_space_id",
        "candidate",
        "market_id",
        "token_id",
        "basket",
        "side",
        "fill_ts_ms",
        "fill_price",
        "shares",
        "notional_usdc",
        "fees_usdc",
        "slippage_cost_usdc",
        "resolution_outcome",
        "realised_pnl_usdc",
    ]


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
