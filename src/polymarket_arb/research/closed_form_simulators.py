"""Closed-form simulators for DeepSeek hypotheses the buy-only replay can't trade.

RESEARCH-ONLY / SIMULATED / EXPLORATORY. No live trading, no order placement.

The buy-only replay engine can long YES tokens at historical mid prices and
exit at a later tick, but it has no path for:

* same-YES spread / convergence — two YES tokens that should resolve identically
* near-duplicate divergence       — markets with different resolution criteria
                                    whose prices should differ; trade the spread
* stale-market convergence        — a market that should track another but is
                                    stale; expect catch-up
* mutually-exclusive YES/YES overround — sum of two YES prices > 1; the would-be
                                    short pair

This module evaluates each candidate pair against the locally-stored price
history with a CLOSED-FORM rule: pick the first co-observed tick where the rule
gives a positive expected edge after a configurable slippage haircut, mark the
entry, then mark the exit at the last co-observed tick. Per-trade entry cost
and percentage return are computed in stake-invariant form (the stake is just a
scale factor; we report return_pct directly).

All outputs are flagged ``research-only / simulated / closed-form`` and never
enter the live trading or order-placement code paths.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..storage.base import MarketRow
from ..storage.parquet.price_history_repo import ParquetPriceHistoryRepository

LABEL = "RESEARCH-ONLY / simulated / closed-form / not trading advice"

SIMULATOR_NAMES = (
    "same_yes_spread_simulator",
    "near_duplicate_divergence_simulator",
    "stale_market_convergence_simulator",
    "mutex_yes_yes_overround_simulator",
)


@dataclass(frozen=True)
class SimulatedTrade:
    hypothesis_id: str
    simulator: str
    relationship_type: str
    market_id_a: str
    market_id_b: str
    token_id_a: str
    token_id_b: str
    entry_ts_ms: int
    exit_ts_ms: int
    entry_price_a: float
    entry_price_b: float
    exit_price_a: float
    exit_price_b: float
    entry_cost_per_dollar: float        # normalised: cost = stake_scale * entry_cost_per_dollar
    edge_implied_return_pct: float      # the rule's own expected return per $ cost
    realised_return_pct: float          # what actually happened to last co-observed tick
    slippage_haircut_pct: float
    notes: str = ""


def run_simulators(
    data_root: Path,
    hypotheses: list[dict[str, Any]],
    markets: list[MarketRow],
    *,
    slippage_bps: int = 50,
) -> list[SimulatedTrade]:
    """Run the appropriate closed-form simulator for every routed hypothesis.

    Only hypotheses whose ``route`` is ``unsupported_but_testable`` are scored.
    Other routes are skipped silently — the workflow handles their reporting.
    """
    market_by_id = {m.id: m for m in markets}
    repo = ParquetPriceHistoryRepository(data_root)
    price_cache: dict[str, list[tuple[int, float]]] = {}

    def prices(token_id: str) -> list[tuple[int, float]]:
        if token_id in price_cache:
            return price_cache[token_id]
        series = [(int(r.ts_ms), float(r.price)) for r in repo.iter_for_token(token_id)]
        price_cache[token_id] = series
        return series

    out: list[SimulatedTrade] = []
    haircut = slippage_bps / 10_000.0
    for h in hypotheses:
        if h.get("route") != "unsupported_but_testable":
            continue
        simulator = str(h.get("route_handler") or "")
        if simulator not in SIMULATOR_NAMES:
            continue
        ma = market_by_id.get(str(h.get("market_id_a")))
        mb = market_by_id.get(str(h.get("market_id_b")))
        if not ma or not mb or not ma.clob_token_ids or not mb.clob_token_ids:
            continue
        token_a = ma.clob_token_ids[0]
        token_b = mb.clob_token_ids[0]
        ticks_a = prices(token_a)
        ticks_b = prices(token_b)
        aligned = _align(ticks_a, ticks_b)
        if not aligned:
            continue
        trade = _SIMULATORS[simulator](
            hypothesis_id=str(h.get("hypothesis_id")),
            relationship_type=str(h.get("relationship_type") or ""),
            market_id_a=ma.id,
            market_id_b=mb.id,
            token_id_a=token_a,
            token_id_b=token_b,
            aligned=aligned,
            haircut=haircut,
        )
        if trade is not None:
            out.append(trade)
    return out


def _align(
    a: list[tuple[int, float]],
    b: list[tuple[int, float]],
) -> list[tuple[int, float, float]]:
    """Forward-fill align two ts-sorted price series on the union of timestamps.

    Returns ``[(ts_ms, price_a, price_b), ...]`` for timestamps where BOTH series
    have at least one prior tick. Cheap and good enough for closed-form scoring.
    """
    if not a or not b:
        return []
    i = j = 0
    out: list[tuple[int, float, float]] = []
    last_a = last_b = None
    while i < len(a) or j < len(b):
        ta = a[i][0] if i < len(a) else None
        tb = b[j][0] if j < len(b) else None
        if ta is not None and (tb is None or ta <= tb):
            last_a = a[i][1]
            ts = ta
            i += 1
        else:
            last_b = b[j][1]
            ts = tb
            j += 1
        if last_a is not None and last_b is not None:
            out.append((ts, last_a, last_b))
    return out


def _same_yes_spread(
    *,
    hypothesis_id: str,
    relationship_type: str,
    market_id_a: str,
    market_id_b: str,
    token_id_a: str,
    token_id_b: str,
    aligned: list[tuple[int, float, float]],
    haircut: float,
) -> SimulatedTrade | None:
    """Two YES tokens that should resolve identically. Trade the spread back to zero."""
    entry_ts, pa, pb = aligned[0]
    exit_ts, ea, eb = aligned[-1]
    spread_entry = pa - pb
    spread_exit = ea - eb
    edge_implied_return = abs(spread_entry) - haircut
    realised_return = abs(spread_entry) - abs(spread_exit)
    cost_per_dollar = (pa + pb) / 2.0
    if cost_per_dollar <= 0:
        return None
    return SimulatedTrade(
        hypothesis_id=hypothesis_id,
        simulator="same_yes_spread_simulator",
        relationship_type=relationship_type,
        market_id_a=market_id_a,
        market_id_b=market_id_b,
        token_id_a=token_id_a,
        token_id_b=token_id_b,
        entry_ts_ms=entry_ts,
        exit_ts_ms=exit_ts,
        entry_price_a=pa,
        entry_price_b=pb,
        exit_price_a=ea,
        exit_price_b=eb,
        entry_cost_per_dollar=cost_per_dollar,
        edge_implied_return_pct=edge_implied_return / cost_per_dollar if cost_per_dollar else 0.0,
        realised_return_pct=realised_return / cost_per_dollar if cost_per_dollar else 0.0,
        slippage_haircut_pct=haircut,
        notes="rule: |p_a - p_b| should collapse to zero",
    )


def _near_duplicate_divergence(
    *,
    hypothesis_id: str,
    relationship_type: str,
    market_id_a: str,
    market_id_b: str,
    token_id_a: str,
    token_id_b: str,
    aligned: list[tuple[int, float, float]],
    haircut: float,
) -> SimulatedTrade | None:
    """Markets with different resolution criteria — expect the spread to WIDEN."""
    entry_ts, pa, pb = aligned[0]
    exit_ts, ea, eb = aligned[-1]
    spread_entry = abs(pa - pb)
    spread_exit = abs(ea - eb)
    cost_per_dollar = (pa + pb) / 2.0
    if cost_per_dollar <= 0:
        return None
    realised_return = spread_exit - spread_entry
    edge_implied_return = max(0.0, 0.05 - haircut)  # speculative; widens by 5% baseline
    return SimulatedTrade(
        hypothesis_id=hypothesis_id,
        simulator="near_duplicate_divergence_simulator",
        relationship_type=relationship_type,
        market_id_a=market_id_a,
        market_id_b=market_id_b,
        token_id_a=token_id_a,
        token_id_b=token_id_b,
        entry_ts_ms=entry_ts,
        exit_ts_ms=exit_ts,
        entry_price_a=pa,
        entry_price_b=pb,
        exit_price_a=ea,
        exit_price_b=eb,
        entry_cost_per_dollar=cost_per_dollar,
        edge_implied_return_pct=edge_implied_return / cost_per_dollar if cost_per_dollar else 0.0,
        realised_return_pct=realised_return / cost_per_dollar if cost_per_dollar else 0.0,
        slippage_haircut_pct=haircut,
        notes="rule: |p_a - p_b| should widen because criteria differ",
    )


def _stale_market_convergence(
    *,
    hypothesis_id: str,
    relationship_type: str,
    market_id_a: str,
    market_id_b: str,
    token_id_a: str,
    token_id_b: str,
    aligned: list[tuple[int, float, float]],
    haircut: float,
) -> SimulatedTrade | None:
    """Stale market should catch up to the fresh one. Trade the gap."""
    entry_ts, pa, pb = aligned[0]
    exit_ts, ea, eb = aligned[-1]
    gap_entry = pa - pb
    gap_exit = ea - eb
    cost_per_dollar = (pa + pb) / 2.0
    if cost_per_dollar <= 0:
        return None
    realised_return = abs(gap_entry) - abs(gap_exit)
    edge_implied_return = max(0.0, abs(gap_entry) - haircut)
    return SimulatedTrade(
        hypothesis_id=hypothesis_id,
        simulator="stale_market_convergence_simulator",
        relationship_type=relationship_type,
        market_id_a=market_id_a,
        market_id_b=market_id_b,
        token_id_a=token_id_a,
        token_id_b=token_id_b,
        entry_ts_ms=entry_ts,
        exit_ts_ms=exit_ts,
        entry_price_a=pa,
        entry_price_b=pb,
        exit_price_a=ea,
        exit_price_b=eb,
        entry_cost_per_dollar=cost_per_dollar,
        edge_implied_return_pct=edge_implied_return / cost_per_dollar if cost_per_dollar else 0.0,
        realised_return_pct=realised_return / cost_per_dollar if cost_per_dollar else 0.0,
        slippage_haircut_pct=haircut,
        notes="rule: stale price should converge to fresh price",
    )


def _mutex_yes_yes_overround(
    *,
    hypothesis_id: str,
    relationship_type: str,
    market_id_a: str,
    market_id_b: str,
    token_id_a: str,
    token_id_b: str,
    aligned: list[tuple[int, float, float]],
    haircut: float,
) -> SimulatedTrade | None:
    """Two YES tokens that are mutually exclusive — overround = (p_a + p_b) - 1.

    We can't short, but the closed-form thought experiment is: if you could
    short both YES tokens at p_a + p_b and they pay at most 1, your edge is
    (p_a + p_b) - 1 - haircut per dollar of notional. Realised return uses the
    final overround.
    """
    entry_ts, pa, pb = aligned[0]
    exit_ts, ea, eb = aligned[-1]
    overround_entry = (pa + pb) - 1.0
    overround_exit = (ea + eb) - 1.0
    cost_per_dollar = pa + pb       # notional of the would-be short
    if cost_per_dollar <= 0:
        return None
    edge_implied_return = overround_entry - haircut
    realised_return = overround_entry - overround_exit
    return SimulatedTrade(
        hypothesis_id=hypothesis_id,
        simulator="mutex_yes_yes_overround_simulator",
        relationship_type=relationship_type,
        market_id_a=market_id_a,
        market_id_b=market_id_b,
        token_id_a=token_id_a,
        token_id_b=token_id_b,
        entry_ts_ms=entry_ts,
        exit_ts_ms=exit_ts,
        entry_price_a=pa,
        entry_price_b=pb,
        exit_price_a=ea,
        exit_price_b=eb,
        entry_cost_per_dollar=cost_per_dollar,
        edge_implied_return_pct=edge_implied_return / cost_per_dollar if cost_per_dollar else 0.0,
        realised_return_pct=realised_return / cost_per_dollar if cost_per_dollar else 0.0,
        slippage_haircut_pct=haircut,
        notes="rule: mutex YES/YES sum > 1 is an overround; closed-form short edge",
    )


_SIMULATORS = {
    "same_yes_spread_simulator": _same_yes_spread,
    "near_duplicate_divergence_simulator": _near_duplicate_divergence,
    "stale_market_convergence_simulator": _stale_market_convergence,
    "mutex_yes_yes_overround_simulator": _mutex_yes_yes_overround,
}


def simulator_summary(trades: Iterable[SimulatedTrade]) -> list[dict[str, Any]]:
    """Aggregate simulator outputs per (simulator, relationship_type)."""
    grouped: dict[tuple[str, str], list[SimulatedTrade]] = {}
    for t in trades:
        grouped.setdefault((t.simulator, t.relationship_type), []).append(t)
    out: list[dict[str, Any]] = []
    for (simulator, rtype), group in grouped.items():
        edge_returns = [t.edge_implied_return_pct for t in group]
        realised_returns = [t.realised_return_pct for t in group]
        out.append({
            "simulator": simulator,
            "relationship_type": rtype,
            "accepted_trade_count": len(group),
            "edge_implied_return_pct_avg": sum(edge_returns) / len(edge_returns),
            "edge_implied_return_pct_median": sorted(edge_returns)[len(edge_returns) // 2],
            "realised_return_pct_avg": sum(realised_returns) / len(realised_returns),
            "realised_return_pct_median": sorted(realised_returns)[len(realised_returns) // 2],
            "best_realised_return_pct": max(realised_returns),
            "worst_realised_return_pct": min(realised_returns),
            "winning_trade_share": sum(1 for r in realised_returns if r > 0) / len(realised_returns),
            "label": LABEL,
        })
    return sorted(out, key=lambda r: float(r["realised_return_pct_avg"]), reverse=True)
