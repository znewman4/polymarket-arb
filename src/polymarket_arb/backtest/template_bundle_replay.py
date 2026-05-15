"""N-way bundle scanner for template-approved mutual exclusion outcome spaces.

RESEARCH-ONLY / DIAGNOSTIC-ONLY. Not trading advice.
All outputs are labelled auto_applied_pending_human_review.

This module groups template-approved mutual exclusion relationships by their
shared outcome_space_id and scans whether the sum of YES prices across all
observed markets in that outcome space creates an arbitrage opportunity.

Strategy logic (reuses existing category_bundle_scanner):
  buy_all_yes:  pay sum_yes, guaranteed to receive 1.0 from the winner.
                Profit when sum_yes < 1.0 - costs (underround).
  buy_all_no:   pay sum_no, guaranteed to receive (N-1) from N-1 losers.
                Profit when sum_no < (N-1) - costs.
"""

from __future__ import annotations

import csv
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..storage.parquet.context_relationship_decisions_repo import (
    ParquetContextRelationshipDecisionsRepository,
)
from ..storage.parquet.price_history_repo import ParquetPriceHistoryRepository
from ..storage.parquet.relationship_candidates_repo import ParquetRelationshipCandidatesRepository
from ..strategies.category_bundle_scanner import (
    CategoryBundleScanRow,
    CategoryPricePoint,
    row_to_dict,
    scan_category_bundle,
)
from ..strategies.category_outcome_spaces import (
    CategoryOutcomeSpace,
    group_template_mutual_exclusion_spaces,
)
from ..strategies.models import CategoryBundleBacktestConfig

_LABEL = "RESEARCH-ONLY — auto_applied_pending_human_review"
_NOTE = "Template-auto-applied bundles. Not trading advice. Requires human review before any credibility upgrade."

# ── decision filter ───────────────────────────────────────────────────────────

_REVIEWED_LANES = frozenset({"strict_context_valid", "reviewed_context_valid"})
_MUTUAL_EXCLUSION_SPACES = frozenset({
    "sports_title_competition_mutual_exclusion",
    "electoral_mutual_exclusion",
    "sports_ranking_finish_position",
    "sports_championship_conference_progression",
})


def _template_rel_ids(data_root: Path) -> set[str]:
    """Return relationship IDs that were matched by a template in a reviewed lane."""
    dec_repo = ParquetContextRelationshipDecisionsRepository(data_root)
    ids: set[str] = set()
    for d in dec_repo.iter_latest():
        if d.strategy_lane not in _REVIEWED_LANES:
            continue
        if d.context_space_id not in _MUTUAL_EXCLUSION_SPACES:
            continue
        if "template=" not in (d.decision_reason or ""):
            continue
        ids.add(d.relationship_id)
    return ids


# ── main entry points ─────────────────────────────────────────────────────────


def run_template_bundle_scan(
    data_root: Path,
    cfg: CategoryBundleBacktestConfig,
) -> dict[str, Any]:
    """Latest-price bundle scan for all template-approved mutual exclusion spaces.

    RESEARCH-ONLY.
    """
    run_id = cfg.run_id or uuid.uuid4().hex
    cfg = cfg.model_copy(update={"run_id": run_id})
    out_dir = data_root / "backtests" / run_id / "template_bundle"
    out_dir.mkdir(parents=True, exist_ok=True)

    spaces, template_ids = _load_spaces(data_root)
    price_repo = ParquetPriceHistoryRepository(data_root)

    scan_rows: list[dict[str, Any]] = []
    for space in spaces:
        yes_prices, no_prices = _latest_prices(price_repo, space)
        bundle_row = scan_category_bundle(
            space,
            yes_prices=yes_prices,
            no_prices=no_prices,
            min_net_edge=cfg.min_net_edge,
            fee_bps=cfg.fee_bps,
            slippage_bps=cfg.slippage_bps,
            allow_uncertain=True,  # always True — template-approved partial bundles
        )
        scan_rows.append(_enrich_scan_row(bundle_row, space))

    metrics = _scan_metrics(run_id, cfg, scan_rows, template_ids)
    _write_json(out_dir / "config.json", cfg.model_dump(mode="json"))
    _write_json(out_dir / "metrics.json", metrics)
    _write_csv(out_dir / "bundle_scan.csv", scan_rows)
    _write_markdown(out_dir / "bundle_scan.md", scan_rows, run_id)
    return {"run_id": run_id, "output_dir": out_dir, "metrics": metrics, "scan_rows": scan_rows}


def run_template_bundle_backtest(
    data_root: Path,
    cfg: CategoryBundleBacktestConfig,
) -> dict[str, Any]:
    """Replay template-bundle opportunities over historical prices.

    RESEARCH-ONLY. All results labelled auto_applied_pending_human_review.
    """
    run_id = cfg.run_id or uuid.uuid4().hex
    cfg = cfg.model_copy(update={"run_id": run_id})
    out_dir = data_root / "backtests" / run_id / "template_bundle"
    out_dir.mkdir(parents=True, exist_ok=True)

    spaces, template_ids = _load_spaces(data_root)
    price_repo = ParquetPriceHistoryRepository(data_root)

    # Latest-price scan for the report header
    latest_scan_rows: list[dict[str, Any]] = []
    for space in spaces:
        yes_prices, no_prices = _latest_prices(price_repo, space)
        row = scan_category_bundle(
            space, yes_prices=yes_prices, no_prices=no_prices,
            min_net_edge=cfg.min_net_edge, fee_bps=cfg.fee_bps,
            slippage_bps=cfg.slippage_bps, allow_uncertain=True,
        )
        latest_scan_rows.append(_enrich_scan_row(row, space))

    # Historical replay
    opportunities: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    open_spaces: set[str] = set()
    cash = cfg.starting_cash_usdc
    total_fees = Decimal("0")
    total_slippage = Decimal("0")
    bundle_count = 0
    funnel: dict[str, int] = {
        "spaces_loaded": len(spaces),
        "spaces_with_price_history": 0,
        "spaces_aligned": 0,
        "ticks_evaluated": 0,
        "gross_violations": 0,
        "net_violations": 0,
        "accepted": 0,
        "rejected_costs": 0,
        "rejected_already_open": 0,
    }

    for space in spaces:
        ticks = _aligned_ticks(price_repo, space, cfg)
        if ticks is None:
            continue
        funnel["spaces_with_price_history"] += 1
        if not ticks:
            continue
        funnel["spaces_aligned"] += 1

        for ts_ms, yes_prices, no_prices in ticks:
            funnel["ticks_evaluated"] += 1
            row = scan_category_bundle(
                space, yes_prices=yes_prices, no_prices=no_prices,
                min_net_edge=cfg.min_net_edge, fee_bps=cfg.fee_bps,
                slippage_bps=cfg.slippage_bps, allow_uncertain=True,
            )
            if row.gross_edge > Decimal("0"):
                funnel["gross_violations"] += 1
            if row.rejection_reason:
                opportunities.append(_opp_row(run_id, space, row, ts_ms, accepted=False))
                continue

            funnel["net_violations"] += 1
            if space.outcome_space_id in open_spaces:
                funnel["rejected_already_open"] += 1
                opp = _opp_row(run_id, space, row, ts_ms, accepted=False)
                opp["rejection_reason"] = "already_open"
                opportunities.append(opp)
                continue
            if cash < cfg.max_stake_per_bundle_usdc:
                funnel["rejected_costs"] += 1
                opp = _opp_row(run_id, space, row, ts_ms, accepted=False)
                opp["rejection_reason"] = "cash_limit"
                opportunities.append(opp)
                continue

            funnel["accepted"] += 1
            opportunities.append(_opp_row(run_id, space, row, ts_ms, accepted=True))
            leg_trades, fees, slippage = _execute(run_id, space, row, ts_ms, yes_prices, no_prices, cfg)
            trades.extend(leg_trades)
            total_fees += fees
            total_slippage += slippage
            cash -= cfg.max_stake_per_bundle_usdc + fees
            open_spaces.add(space.outcome_space_id)
            bundle_count += 1

    funnel["bundles_executed"] = bundle_count
    ending_equity = cash + _mtm_all(trades, price_repo)
    net_pnl = ending_equity - cfg.starting_cash_usdc
    metrics = _backtest_metrics(run_id, cfg, latest_scan_rows, opportunities, trades,
                                cash, ending_equity, total_fees, total_slippage, funnel, template_ids)

    _write_json(out_dir / "config.json", cfg.model_dump(mode="json"))
    _write_json(out_dir / "metrics.json", metrics)
    _write_json(out_dir / "funnel.json", funnel)
    _write_csv(out_dir / "bundle_scan.csv", latest_scan_rows)
    _write_csv(out_dir / "opportunities.csv", opportunities)
    _write_csv(out_dir / "trades.csv", trades)
    _write_markdown(out_dir / "bundle_report.md", latest_scan_rows, run_id)
    return {
        "run_id": run_id,
        "output_dir": out_dir,
        "metrics": metrics,
        "funnel": funnel,
        "scan_rows": latest_scan_rows,
    }


# ── helpers ───────────────────────────────────────────────────────────────────


def _load_spaces(data_root: Path) -> tuple[list[CategoryOutcomeSpace], set[str]]:
    """Load template-approved mutual exclusion outcome spaces from the store."""
    rel_repo = ParquetRelationshipCandidatesRepository(data_root)
    all_rels = list(rel_repo.iter_latest())
    t_ids = _template_rel_ids(data_root)
    spaces = group_template_mutual_exclusion_spaces(all_rels, template_rel_ids=t_ids)
    return spaces, t_ids


def _latest_prices(
    price_repo: ParquetPriceHistoryRepository,
    space: CategoryOutcomeSpace,
) -> tuple[dict[str, CategoryPricePoint], dict[str, CategoryPricePoint]]:
    yes: dict[str, CategoryPricePoint] = {}
    no: dict[str, CategoryPricePoint] = {}
    for candidate in space.candidates:
        for tok, store in ((candidate.yes_token_id, yes), (candidate.no_token_id, no)):
            rows = list(price_repo.iter_for_token(tok))
            if rows:
                latest = max(rows, key=lambda r: r.ts_ms)
                store[tok] = CategoryPricePoint(tok, latest.price, latest.ts_ms)
    return yes, no


def _aligned_ticks(
    price_repo: ParquetPriceHistoryRepository,
    space: CategoryOutcomeSpace,
    cfg: CategoryBundleBacktestConfig,
) -> list[tuple[int, dict[str, CategoryPricePoint], dict[str, CategoryPricePoint]]] | None:
    from bisect import bisect_right
    yes_series: dict[str, list] = {}
    for candidate in space.candidates:
        rows = [
            r for r in price_repo.iter_for_token(candidate.yes_token_id)
            if (cfg.start_ts_ms is None or r.ts_ms >= cfg.start_ts_ms)
            and (cfg.end_ts_ms is None or r.ts_ms <= cfg.end_ts_ms)
        ]
        if not rows:
            return None
        yes_series[candidate.yes_token_id] = sorted(rows, key=lambda r: r.ts_ms)

    first_ts = max(rows[0].ts_ms for rows in yes_series.values())
    last_ts = min(rows[-1].ts_ms for rows in yes_series.values())
    if first_ts > last_ts:
        return []

    aligned: list[tuple] = []
    ts_ms = first_ts
    while ts_ms <= last_ts:
        yes_pts: dict[str, CategoryPricePoint] = {}
        for tok, rows in yes_series.items():
            timestamps = [r.ts_ms for r in rows]
            idx = bisect_right(timestamps, ts_ms) - 1
            if idx < 0:
                break
            row = rows[idx]
            if ts_ms - row.ts_ms > cfg.quote_staleness_limit_ms:
                break
            yes_pts[tok] = CategoryPricePoint(tok, row.price, row.ts_ms)
        else:
            aligned.append((ts_ms, yes_pts, {}))  # no_prices not needed for YES bundle scan
        ts_ms += cfg.signal_interval_ms
    return aligned


def _execute(
    run_id: str,
    space: CategoryOutcomeSpace,
    row: CategoryBundleScanRow,
    ts_ms: int,
    yes_prices: dict[str, CategoryPricePoint],
    no_prices: dict[str, CategoryPricePoint],
    cfg: CategoryBundleBacktestConfig,
) -> tuple[list[dict[str, Any]], Decimal, Decimal]:
    trades: list[dict[str, Any]] = []
    total_fees = Decimal("0")
    total_slippage = Decimal("0")
    n = max(1, len(space.candidates))
    stake_per_leg = cfg.max_stake_per_bundle_usdc / Decimal(n)
    cost_frac = cfg.slippage_bps / Decimal("10000")
    fee_frac = cfg.fee_bps / Decimal("10000")
    basket = row.best_executable_basket

    for idx, candidate in enumerate(space.candidates):
        tok = candidate.yes_token_id if basket == "buy_all_yes" else candidate.no_token_id
        prices = yes_prices if basket == "buy_all_yes" else no_prices
        point = prices.get(tok)
        if point is None:
            continue
        fill = min(Decimal("1"), point.price * (Decimal("1") + cost_frac))
        shares = stake_per_leg / fill if fill > 0 else Decimal("0")
        fee = stake_per_leg * fee_frac
        slippage = stake_per_leg * cost_frac
        total_fees += fee
        total_slippage += slippage
        trades.append({
            "trade_id": f"{run_id}:{space.outcome_space_id}:{ts_ms}:{idx}",
            "run_id": run_id,
            "outcome_space_id": space.outcome_space_id,
            "candidate": candidate.candidate,
            "market_id": candidate.market_id,
            "token_id": tok,
            "basket": basket,
            "side": "buy",
            "fill_ts_ms": ts_ms,
            "fill_price": str(fill),
            "shares": str(shares),
            "notional_usdc": str(stake_per_leg),
            "fees_usdc": str(fee),
            "slippage_usdc": str(slippage),
            "label": _LABEL,
        })
    return trades, total_fees, total_slippage


def _mtm_all(trades: list[dict[str, Any]], price_repo: ParquetPriceHistoryRepository) -> Decimal:
    prices: dict[str, Decimal] = {}
    for t in trades:
        tok = str(t["token_id"])
        if tok not in prices:
            rows = list(price_repo.iter_for_token(tok))
            if rows:
                prices[tok] = max(rows, key=lambda r: r.ts_ms).price
    return sum(
        Decimal(str(t["shares"])) * prices.get(str(t["token_id"]), Decimal("0"))
        for t in trades
    )


def _enrich_scan_row(row: CategoryBundleScanRow, space: CategoryOutcomeSpace) -> dict[str, Any]:
    d = row_to_dict(row)
    d["candidate_count_observed"] = len(space.candidates)
    d["candidate_names"] = "; ".join(c.candidate for c in space.candidates)
    d["market_ids"] = "; ".join(c.market_id for c in space.candidates)
    d["yes_token_ids"] = "; ".join(c.yes_token_id for c in space.candidates)
    d["source_relationship_count"] = len(space.source_relationship_ids)
    d["label"] = _LABEL
    return d


def _opp_row(
    run_id: str,
    space: CategoryOutcomeSpace,
    row: CategoryBundleScanRow,
    ts_ms: int,
    *,
    accepted: bool,
) -> dict[str, Any]:
    d = _enrich_scan_row(row, space)
    d["opportunity_id"] = f"{run_id}:{space.outcome_space_id}:{ts_ms}"
    d["run_id"] = run_id
    d["signal_ts_ms"] = ts_ms
    d["accepted_for_simulation"] = accepted
    return d


def _scan_metrics(
    run_id: str,
    cfg: CategoryBundleBacktestConfig,
    scan_rows: list[dict[str, Any]],
    template_ids: set[str],
) -> dict[str, Any]:
    opportunities = sum(1 for r in scan_rows if r.get("best_executable_basket") != "none")
    return {
        "run_id": run_id,
        "strategy": "template_bundle_scan",
        "label": _LABEL,
        "note": _NOTE,
        "outcome_spaces_scanned": len(scan_rows),
        "template_relationships_loaded": len(template_ids),
        "opportunities_found": opportunities,
        "trades_executed": 0,
        "starting_cash_usdc": str(cfg.starting_cash_usdc),
        "net_pnl_usdc": "0",
        "credibility_label": "data_insufficient",
        "generated_at": _now_iso(),
    }


def _backtest_metrics(
    run_id: str,
    cfg: CategoryBundleBacktestConfig,
    scan_rows: list[dict[str, Any]],
    opportunities: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    ending_cash: Decimal,
    ending_equity: Decimal,
    total_fees: Decimal,
    total_slippage: Decimal,
    funnel: dict[str, int],
    template_ids: set[str],
) -> dict[str, Any]:
    net_pnl = ending_equity - cfg.starting_cash_usdc
    bundle_count = funnel.get("bundles_executed", 0)
    if bundle_count == 0:
        label = "data_insufficient"
        rationale = "No template bundle opportunities passed the net-edge gate."
    elif bundle_count < 30:
        label = "data_insufficient"
        rationale = f"Only {bundle_count} bundle trades; sample too small for credibility."
    elif net_pnl <= 0:
        label = "not_credible"
        rationale = "Net PnL is not positive."
    else:
        label = "inconclusive"
        rationale = "Positive PnL but auto-applied templates require human review."
    return {
        "run_id": run_id,
        "strategy": "template_bundle_backtest",
        "label": _LABEL,
        "note": _NOTE,
        "template_relationships_loaded": len(template_ids),
        "outcome_spaces_scanned": len(scan_rows),
        "spaces_with_price_history": funnel.get("spaces_with_price_history", 0),
        "ticks_evaluated": funnel.get("ticks_evaluated", 0),
        "gross_violations": funnel.get("gross_violations", 0),
        "net_violations": funnel.get("net_violations", 0),
        "bundles_executed": bundle_count,
        "starting_cash_usdc": str(cfg.starting_cash_usdc),
        "ending_cash_usdc": str(ending_cash),
        "ending_equity_usdc": str(ending_equity),
        "net_pnl_usdc": str(net_pnl),
        "total_fees_usdc": str(total_fees),
        "total_slippage_usdc": str(total_slippage),
        "credibility_label": label,
        "credibility_rationale": rationale,
        "generated_at": _now_iso(),
    }


def _write_markdown(path: Path, scan_rows: list[dict[str, Any]], run_id: str) -> None:
    ts = _now_iso()
    lines = [
        f"# Template Bundle Scan — {ts}",
        "",
        f"> {_LABEL}",
        f"> {_NOTE}",
        f"> run_id: `{run_id}`",
        "",
        "## Bundle summary",
        "",
        "| outcome_space_id | markets | sum_yes | basket | gross_edge | rejection |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in scan_rows:
        lines.append(
            f"| `{r['outcome_space_id']}` "
            f"| {r.get('candidate_count_observed', '?')} "
            f"| {r.get('sum_yes_prices', '—')} "
            f"| {r.get('best_executable_basket', '—')} "
            f"| {r.get('gross_edge', '—')} "
            f"| {r.get('rejection_reason') or 'none'} |"
        )
    lines += [
        "",
        "## Market detail per bundle",
        "",
    ]
    for r in scan_rows:
        lines += [
            f"### `{r['outcome_space_id']}`",
            f"- **Markets ({r.get('candidate_count_observed', '?')})**: {r.get('candidate_names', '—')[:200]}",
            f"- **Sum YES prices**: {r.get('sum_yes_prices', '—')}",
            f"- **Best basket**: {r.get('best_executable_basket', '—')}",
            f"- **Gross edge**: {r.get('gross_edge', '—')}",
            f"- **Net edge**: {r.get('net_edge_after_costs', '—')}",
            f"- **Rejection**: {r.get('rejection_reason') or 'none'}",
            f"- **Source relationships**: {r.get('source_relationship_count', '?')}",
            "",
        ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_json(path: Path, payload: Any) -> None:
    def _ser(v: Any) -> Any:
        if isinstance(v, Decimal):
            return str(v)
        if isinstance(v, Path):
            return str(v)
        if isinstance(v, dict):
            return {k: _ser(vv) for k, vv in v.items()}
        if isinstance(v, (list, tuple)):
            return [_ser(vv) for vv in v]
        return v
    path.write_text(json.dumps(_ser(payload), indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({k for row in rows for k in row})
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
