"""Offline replay engine over local recorded snapshots."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ..storage.base import OrderbookLevel, OrderbookSnapshot
from . import datasets
from .event_stream import build_event_stream
from .execution_sim import simulate_signal_execution
from .metrics import compute_metrics
from .models import BacktestPosition, BacktestRunConfig, SimulatedFill, SimulatedOrder
from .strategy_interface import ScoreThresholdStrategy


def run_backtest(data_root: Path, config: BacktestRunConfig) -> dict[str, Any]:
    markets = {str(m["id"]): m for m in datasets.load_markets_latest(data_root)}
    events = build_event_stream(
        data_root,
        start_ts_ms=config.start_ts_ms,
        end_ts_ms=config.end_ts_ms,
    )
    if not events:
        raise datasets.DatasetMissingError("no local replay events found; run record quotes/snapshots first")
    strategy = _strategy(config)
    latest_quotes: dict[str, dict] = {}
    latest_books: dict[str, OrderbookSnapshot] = {}
    signals = []
    orders: list[SimulatedOrder] = []
    fills: list[SimulatedFill] = []
    positions: dict[str, BacktestPosition] = {}
    cash = config.initial_cash

    for event in events:
        if event.event_type == "best_quote":
            latest_quotes[str(event.payload.get("token_id"))] = event.payload
        elif event.event_type == "orderbook_snapshot":
            book = _book(event.payload)
            latest_books[book.token_id] = book
        context = {"markets": markets, "latest_quotes": latest_quotes, "latest_books": latest_books}
        for signal in strategy.on_event(event, context):
            signals.append(signal)
            if not config.execute_signals:
                continue
            order, fill = simulate_signal_execution(signal, latest_books.get(signal.token_id or ""), config)
            if order:
                orders.append(order)
            if fill:
                fills.append(fill)
                if fill.filled_shares > 0:
                    cash -= fill.notional + fill.fees if fill.side == "buy" else -(fill.notional - fill.fees)
                    _apply_position(positions, fill)

    metrics = compute_metrics(
        total_events=len(events),
        total_signals=len(signals),
        orders=orders,
        fills=fills,
        final_cash=cash,
        open_positions_count=sum(1 for p in positions.values() if p.shares != 0),
    )
    out_dir = data_root / ".." / "backtests" / config.run_id
    # Keep backtests sibling to data_root if data_root is tmp/data; for normal
    # settings this resolves to data/backtests.
    out_dir = data_root / "backtests" / config.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "config.json", asdict(config))
    _write_rows(out_dir / "signals.parquet", [asdict(s) for s in signals])
    _write_rows(out_dir / "simulated_orders.parquet", [asdict(o) for o in orders])
    _write_rows(out_dir / "fills.parquet", [asdict(f) for f in fills])
    _write_rows(out_dir / "positions.parquet", [asdict(p) for p in positions.values()])
    _write_rows(out_dir / "equity_curve.parquet", [{"ts_ms": e.ts_ms, "cash": cash} for e in events[-1:]])
    _write_json(out_dir / "metrics.json", asdict(metrics))
    return {"run_id": config.run_id, "output_dir": out_dir, "metrics": metrics}


def default_config(*, strategy_name: str, threshold: float, start_ts_ms=None, end_ts_ms=None) -> BacktestRunConfig:
    return BacktestRunConfig(
        run_id=uuid.uuid4().hex,
        dataset_path=None,
        strategy_name=strategy_name,
        initial_cash=Decimal("1000"),
        max_position_per_market=Decimal("10"),
        latency_ms=0,
        fee_bps=Decimal("0"),
        slippage_bps=None,
        quote_staleness_limit_ms=60_000,
        start_ts_ms=start_ts_ms,
        end_ts_ms=end_ts_ms,
        threshold=threshold,
        execute_signals=True,
    )


def _strategy(config: BacktestRunConfig):
    if config.strategy_name != "score-threshold":
        raise ValueError(f"unknown strategy {config.strategy_name!r}")
    return ScoreThresholdStrategy(threshold=config.threshold)


def _book(row: dict) -> OrderbookSnapshot:
    return OrderbookSnapshot(
        token_id=str(row["token_id"]),
        condition_id=row.get("condition_id"),
        market_slug=row.get("market_slug"),
        timestamp_ms=int(row.get("timestamp_ms") or 0),
        bids=[OrderbookLevel(Decimal(str(x["price"])), Decimal(str(x["size"]))) for x in (row.get("bids") or [])],
        asks=[OrderbookLevel(Decimal(str(x["price"])), Decimal(str(x["size"]))) for x in (row.get("asks") or [])],
        book_hash=row.get("book_hash"),
        source=str(row.get("source") or "rest"),
        schema_version=int(row.get("schema_version") or 1),
        ingested_ts_ms=int(row.get("ingested_ts_ms") or 0),
    )


def _apply_position(positions: dict[str, BacktestPosition], fill: SimulatedFill) -> None:
    key = fill.token_id
    pos = positions.setdefault(key, BacktestPosition(market_id=fill.market_id, token_id=fill.token_id))
    if fill.side == "buy":
        total_cost = pos.avg_entry_price * pos.shares + fill.notional
        pos.shares += fill.filled_shares
        pos.avg_entry_price = Decimal("0") if pos.shares == 0 else total_cost / pos.shares
    else:
        pos.shares -= fill.filled_shares


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, default=str, indent=2, sort_keys=True), encoding="utf-8")


def _write_rows(path: Path, rows: list[dict]) -> None:
    if not rows:
        rows = [{"empty": True}]
    cleaned = [{k: _serialise(v) for k, v in row.items()} for row in rows]
    tmp = path.with_suffix(path.suffix + ".tmp")
    pq.write_table(pa.Table.from_pylist(cleaned), tmp)
    os.replace(tmp, path)


def _serialise(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return json.dumps(value, default=str, sort_keys=True)
    return value
