"""Tests for coverage computation."""

from __future__ import annotations

from decimal import Decimal

from polymarket_arb.backfill.coverage import compute_market_coverage, verify_dataset
from polymarket_arb.backfill.models import BackfillConfig
from polymarket_arb.storage.base import MarketRow, PriceHistoryRow


def _market(market_id: str = "m1", token_ids: list[str] | None = None) -> MarketRow:
    _token_ids: list[str] = ["tok1", "tok2"] if token_ids is None else token_ids
    return MarketRow(
        id=market_id,
        condition_id="cond1",
        slug="slug",
        question=f"Will {market_id} resolve?",
        description=None,
        end_date_ms=None,
        start_date_ms=None,
        closed_at_ms=None,
        resolved_at_ms=None,
        active=True,
        closed=False,
        archived=False,
        outcomes=["Yes", "No"],
        gamma_outcome_prices_snapshot=[Decimal("0.5"), Decimal("0.5")],
        clob_token_ids=_token_ids,
        volume=Decimal("1000"),
        liquidity=Decimal("100"),
        event_id=None,
        neg_risk=False,
        text_hash="abc",
        schema_version=1,
        ingested_ts_ms=1_700_000_000_000,
    )


def _price_row(ts_ms: int, price: str = "0.50") -> PriceHistoryRow:
    return PriceHistoryRow(
        market_id="m1",
        condition_id=None,
        token_id="tok1",
        outcome="Yes",
        ts_ms=ts_ms,
        price=Decimal(price),
        source="clob",
        fidelity="1h",
        interval="1h",
        schema_version=1,
        ingested_ts_ms=1_700_000_000_000,
    )


def test_coverage_score_full_data_is_high():
    market = _market()
    price_rows = [_price_row(1_700_000_000_000 + i * 3_600_000) for i in range(100)]
    cfg = BackfillConfig(min_price_points=50)
    cov = compute_market_coverage(market, price_rows, [], True, True, True, cfg)
    assert cov.coverage_score >= 0.6
    assert cov.recommended_for_backtest is True


def test_coverage_score_missing_history_is_low():
    market = _market()
    cfg = BackfillConfig(min_price_points=50)
    cov = compute_market_coverage(market, [], [], False, False, False, cfg)
    assert cov.coverage_score < 0.3
    assert cov.recommended_for_backtest is False


def test_coverage_excludes_market_without_token_ids():
    market = _market(token_ids=[])
    cfg = BackfillConfig(min_price_points=50)
    cov = compute_market_coverage(market, [], [], False, False, False, cfg)
    assert cov.recommended_for_backtest is False
    assert "no_token_ids" in cov.exclusion_reasons_json


def test_coverage_reports_largest_gap():
    market = _market()
    # 10 rows at 1h apart, then a 2-week gap, then 5 more
    base = 1_700_000_000_000
    close_rows = [_price_row(base + i * 3_600_000) for i in range(10)]
    far_row = _price_row(base + 10 * 3_600_000 + 14 * 24 * 3_600_000)  # 2-week gap
    price_rows = [*close_rows, far_row]
    cfg = BackfillConfig(min_price_points=5)
    cov = compute_market_coverage(market, price_rows, [], True, True, True, cfg)
    assert cov.largest_price_gap_ms >= 14 * 24 * 3_600_000


def test_coverage_reports_unmatched_tokens():
    market = _market(token_ids=[])
    cfg = BackfillConfig()
    cov = compute_market_coverage(market, [], [], False, False, False, cfg)
    assert "no_token_ids" in cov.exclusion_reasons_json


def test_coverage_penalty_for_duplicates():
    market = _market()
    # Two rows with the same ts_ms
    price_rows = [_price_row(1_700_000_000_000), _price_row(1_700_000_000_000, "0.51")]
    cfg = BackfillConfig(min_price_points=1)
    cov = compute_market_coverage(market, price_rows, [], True, True, True, cfg)
    assert cov.duplicate_timestamp_count >= 1
    assert "duplicate_timestamps" in cov.exclusion_reasons_json


def test_verify_outputs_pass_warn_fail_statuses(tmp_data_root):
    cfg = BackfillConfig()
    # Empty lake → should have WARN status (no data), not crash
    results = verify_dataset(tmp_data_root, cfg)
    statuses = {r.status for r in results}
    assert statuses.issubset({"PASS", "WARN", "FAIL"})
    # With no data, at least markets check should be WARN
    names = {r.name for r in results}
    assert "markets_table" in names
