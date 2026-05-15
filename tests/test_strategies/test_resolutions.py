"""Tests for resolution inference."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from polymarket_arb.backtest.resolutions import infer_resolutions
from polymarket_arb.storage.base import MarketRow, PriceHistoryRow

_TS = int(datetime.now(timezone.utc).timestamp() * 1000)


def _market(market_id: str, closed: bool = False, resolved_at: int | None = None) -> MarketRow:
    return MarketRow(
        id=market_id,
        condition_id=f"cond_{market_id}",
        slug=market_id,
        question=f"Question for {market_id}",
        description=None,
        end_date_ms=_TS - 1000 if closed else _TS + 30 * 24 * 3600 * 1000,
        start_date_ms=_TS - 90 * 24 * 3600 * 1000,
        closed_at_ms=_TS - 1000 if closed else None,
        resolved_at_ms=resolved_at,
        active=not closed,
        closed=closed,
        archived=False,
        outcomes=["Yes", "No"],
        gamma_outcome_prices_snapshot=[Decimal("1") if closed else Decimal("0.5"), Decimal("0") if closed else Decimal("0.5")],
        clob_token_ids=[f"tok_{market_id}_yes", f"tok_{market_id}_no"],
        volume=None,
        liquidity=None,
        event_id=None,
        neg_risk=False,
        text_hash="abc",
        schema_version=1,
        ingested_ts_ms=_TS,
    )


def _price_row(token_id: str, ts_ms: int, price: float) -> PriceHistoryRow:
    return PriceHistoryRow(
        market_id="mkt",
        condition_id=None,
        token_id=token_id,
        outcome=None,
        ts_ms=ts_ms,
        price=Decimal(str(price)),
        source="clob",
        fidelity="1h",
        interval="1h",
        schema_version=1,
        ingested_ts_ms=_TS,
    )


def test_resolved_yes_from_price_convergence():
    """Closed market with YES token → 1.0 → resolves YES."""
    market = _market("mkt", closed=True, resolved_at=_TS - 1000)
    prices = {
        "tok_mkt_yes": [
            _price_row("tok_mkt_yes", _TS - 48 * 3600 * 1000, 0.5),
            _price_row("tok_mkt_yes", _TS - 24 * 3600 * 1000, 0.99),
            _price_row("tok_mkt_yes", _TS - 1 * 3600 * 1000, 0.999),
        ]
    }
    result = infer_resolutions([market], prices, epsilon=0.05)
    assert "mkt" in result
    r = result["mkt"]
    assert r.yes_outcome == "yes"
    assert r.inference_method == "price_convergence"


def test_resolved_no_from_price_convergence():
    """Closed market with YES token → 0.0 → resolves NO."""
    market = _market("mkt", closed=True)
    prices = {
        "tok_mkt_yes": [
            _price_row("tok_mkt_yes", _TS - 24 * 3600 * 1000, 0.05),
            _price_row("tok_mkt_yes", _TS - 1 * 3600 * 1000, 0.01),
        ]
    }
    result = infer_resolutions([market], prices, epsilon=0.05)
    r = result["mkt"]
    assert r.yes_outcome == "no"


def test_open_market_unresolved():
    """Open market → unresolved."""
    market = _market("mkt", closed=False)
    prices = {"tok_mkt_yes": [_price_row("tok_mkt_yes", _TS, 0.5)]}
    result = infer_resolutions([market], prices)
    r = result["mkt"]
    assert r.yes_outcome == "unresolved"


def test_missing_price_data_unresolved():
    """Market with no price data → unresolved."""
    market = _market("mkt", closed=True)
    result = infer_resolutions([market], {})
    r = result["mkt"]
    assert r.yes_outcome == "unresolved"
    assert r.inference_method == "missing"


def test_closed_ambiguous_prices_unresolved():
    """Closed market but price stays near 0.5 → unresolved."""
    market = _market("mkt", closed=True)
    prices = {
        "tok_mkt_yes": [
            _price_row("tok_mkt_yes", _TS - 10 * 3600 * 1000, 0.48),
            _price_row("tok_mkt_yes", _TS - 5 * 3600 * 1000, 0.52),
        ]
    }
    result = infer_resolutions([market], prices)
    r = result["mkt"]
    assert r.yes_outcome == "unresolved"
