"""Depth-aware execution post-pass for the standardised backtest.

The standardised replay engines currently fill at ``mid_price + flat_bps`` (the
``price_history_only`` execution model).  This module re-fills each trade leg
against any recorded orderbook depth available within a tolerance window of the
entry timestamp, updating ``entry_price``, ``slippage_cost_usdc`` and
``entry_cost_usdc`` accordingly.

When recorded depth is unavailable (the common case today — the orderbook lake
has very few historical snapshots), the leg is kept as-is and labelled
``execution_model_used="price_history_only_fallback"`` so the coverage gap is
explicit on every row.

NO live order placement.  This is post-processing of the standardised trade log.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from ...storage.base import OrderbookLevel, OrderbookSnapshot
from ..cost_model import _EXECUTION_MODEL_CONFIDENCE
from ..execution_sim import simulate_buy_from_orderbook
from .contract import StandardisedTradeRow

_ORDERBOOK_GLOB = "normalised/orderbook_snapshots/dt=*/*.parquet"


def build_orderbook_lookup(
    data_root: Path,
    token_ids: set[str] | None = None,
) -> dict[str, list[OrderbookSnapshot]]:
    """Pre-load orderbook snapshots for the given tokens, sorted by ts ascending.

    Returns ``{token_id: [snapshot, ...]}``.  Tokens with no snapshots are absent.
    Empty dict if the lake has no orderbook data at all.
    """
    glob = str(data_root / _ORDERBOOK_GLOB)
    paths = list((data_root / "normalised" / "orderbook_snapshots").glob("dt=*/*.parquet"))
    if not paths:
        return {}
    con = duckdb.connect()
    try:
        if token_ids:
            placeholders = ",".join(["?"] * len(token_ids))
            cur = con.execute(
                f"SELECT * FROM read_parquet('{glob}', hive_partitioning=true) "
                f"WHERE token_id IN ({placeholders}) "
                "ORDER BY token_id, timestamp_ms",
                list(token_ids),
            )
        else:
            cur = con.execute(
                f"SELECT * FROM read_parquet('{glob}', hive_partitioning=true) "
                "ORDER BY token_id, timestamp_ms"
            )
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()
    finally:
        con.close()

    out: dict[str, list[OrderbookSnapshot]] = {}
    for raw in rows:
        d = dict(zip(cols, raw, strict=False))
        d.pop("dt", None)
        snap = OrderbookSnapshot(
            token_id=d["token_id"],
            condition_id=d.get("condition_id"),
            market_slug=d.get("market_slug"),
            timestamp_ms=int(d["timestamp_ms"]),
            bids=[_level(x) for x in (d.get("bids") or [])],
            asks=[_level(x) for x in (d.get("asks") or [])],
            book_hash=d.get("book_hash"),
            source=d.get("source", ""),
            schema_version=int(d.get("schema_version", 1)),
            ingested_ts_ms=int(d.get("ingested_ts_ms", 0)),
        )
        out.setdefault(snap.token_id, []).append(snap)
    return out


def apply_depth_aware_execution(
    trades: list[StandardisedTradeRow],
    *,
    orderbook_lookup: dict[str, list[OrderbookSnapshot]],
    max_snapshot_age_ms: int = 24 * 60 * 60 * 1000,
    fee_bps: Decimal = Decimal("0"),
) -> dict[str, Any]:
    """For each trade leg, attempt to re-fill against recorded orderbook depth.

    Mutates the trades in place.  Returns a coverage summary:
        {"legs_total", "legs_filled_from_depth",
         "legs_fallback_price_history_only", "per_lane": {lane: {filled, fallback}}}

    Rules:
    - If the leg has no entry_ts_ms or no token_id, skip with no change.
    - If no snapshots exist for the token, mark as fallback.
    - If snapshots exist but the nearest is older than ``max_snapshot_age_ms``,
      mark as fallback and record ``depth_lookup_nearest_age_ms``.
    - Otherwise, walk the nearest snapshot's asks for the trade's shares.
      Update ``entry_price``, ``slippage_cost_usdc``, ``entry_cost_usdc``,
      ``execution_model_used="recorded_depth"`` and confidence=1.0.
      If the book is too thin to fully fill, mark as partial fallback and keep
      the original flat-bps fill.
    """
    per_lane: dict[str, dict[str, int]] = {}
    filled = 0
    fallback = 0

    def _bump(lane: str, key: str) -> None:
        d = per_lane.setdefault(lane, {"filled": 0, "fallback": 0, "no_snapshots": 0, "too_far": 0, "thin_book": 0})
        d[key] = d.get(key, 0) + 1

    for t in trades:
        # Closed-form simulator trades and control trades use a different model;
        # don't re-fill them.
        if t.trade_kind in ("closed_form", "control"):
            t.execution_model_used = t.execution_model_used or t.execution_model
            t.execution_model_confidence = (
                t.execution_model_confidence
                if t.execution_model_confidence is not None
                else _EXECUTION_MODEL_CONFIDENCE.get(t.execution_model_used, 0.4)
            )
            continue
        if not t.token_id or t.entry_ts_ms is None or t.shares <= 0:
            t.execution_model_used = "price_history_only_fallback"
            t.execution_model_confidence = _EXECUTION_MODEL_CONFIDENCE["price_history_only"]
            fallback += 1
            _bump(t.source_lane, "fallback")
            continue

        snapshots = orderbook_lookup.get(t.token_id) or []
        if not snapshots:
            t.execution_model_used = "price_history_only_fallback"
            t.execution_model_confidence = _EXECUTION_MODEL_CONFIDENCE["price_history_only"]
            t.depth_lookup_nearest_age_ms = None
            fallback += 1
            _bump(t.source_lane, "no_snapshots")
            _bump(t.source_lane, "fallback")
            continue

        nearest = min(snapshots, key=lambda s: abs(s.timestamp_ms - t.entry_ts_ms))
        age = abs(nearest.timestamp_ms - t.entry_ts_ms)
        if age > max_snapshot_age_ms:
            t.execution_model_used = "price_history_only_fallback"
            t.execution_model_confidence = _EXECUTION_MODEL_CONFIDENCE["price_history_only"]
            t.depth_lookup_nearest_age_ms = int(age)
            fallback += 1
            _bump(t.source_lane, "too_far")
            _bump(t.source_lane, "fallback")
            continue

        # Walk the asks for a buy.
        shares_dec = Decimal(str(t.shares))
        filled_qty, notional_dec, _fees_dec, partial, _reason = simulate_buy_from_orderbook(
            nearest, shares_dec, fee_bps=fee_bps,
        )
        if filled_qty <= 0:
            # Book exists but has no depth on the buy side at any price.
            t.execution_model_used = "price_history_only_fallback"
            t.execution_model_confidence = _EXECUTION_MODEL_CONFIDENCE["price_history_only"]
            t.depth_lookup_nearest_age_ms = int(age)
            fallback += 1
            _bump(t.source_lane, "thin_book")
            _bump(t.source_lane, "fallback")
            continue

        # Real depth-aware fill.  Record the actual fill price/cost.
        avg_fill = notional_dec / filled_qty
        # Slippage = (avg_fill - original_entry_price) * shares (when original known).
        if t.entry_price is not None:
            slippage = max(0.0, (float(avg_fill) - float(t.entry_price)) * float(filled_qty))
        else:
            slippage = 0.0
        t.entry_price = float(avg_fill)
        t.shares = float(filled_qty)
        t.notional_usdc = float(notional_dec)
        t.slippage_cost_usdc = slippage
        t.entry_cost_usdc = t.notional_usdc + t.fees_usdc
        t.execution_model_used = "recorded_depth_partial" if partial else "recorded_depth"
        t.execution_model_confidence = _EXECUTION_MODEL_CONFIDENCE["recorded_depth"]
        t.depth_lookup_nearest_age_ms = int(age)
        if "fallback" in t.observed_pattern_tag:
            t.observed_pattern_tag = ""
        filled += 1
        _bump(t.source_lane, "filled")

    return {
        "rule": "post_pass_re_fill_using_recorded_depth_with_fallback",
        "max_snapshot_age_ms": max_snapshot_age_ms,
        "legs_total": len(trades),
        "legs_filled_from_depth": filled,
        "legs_fallback_price_history_only": fallback,
        "depth_coverage_pct": (
            100.0 * filled / max(1, filled + fallback)
        ),
        "per_lane": per_lane,
    }


def _level(x: Any) -> OrderbookLevel:
    if isinstance(x, dict):
        return OrderbookLevel(price=Decimal(str(x["price"])), size=Decimal(str(x["size"])))
    # DuckDB returns LIST(STRUCT(...)) as a list of dicts; sometimes as objects with attributes.
    return OrderbookLevel(price=Decimal(str(getattr(x, "price", 0))), size=Decimal(str(getattr(x, "size", 0))))
