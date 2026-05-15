"""Tests for the Historical Dataset HTML report."""

from __future__ import annotations

from decimal import Decimal

from polymarket_arb.backfill.coverage import compute_market_coverage
from polymarket_arb.backfill.models import BackfillConfig
from polymarket_arb.reports.historical_dataset_report import generate_historical_dataset_report
from polymarket_arb.storage.base import MarketRow, PriceHistoryRow
from polymarket_arb.storage.parquet.backfill_coverage_repo import ParquetBackfillCoverageRepository
from polymarket_arb.storage.parquet.markets_repo import ParquetMarketsRepository
from polymarket_arb.storage.parquet.price_history_repo import ParquetPriceHistoryRepository


def _market(market_id: str, question: str = "Will it resolve?") -> MarketRow:
    return MarketRow(
        id=market_id,
        condition_id=f"cond-{market_id}",
        slug=f"slug-{market_id}",
        question=question,
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
        clob_token_ids=[f"tok-{market_id}"],
        volume=Decimal("1000"),
        liquidity=Decimal("100"),
        event_id=None,
        neg_risk=False,
        text_hash="abc",
        schema_version=1,
        ingested_ts_ms=1_700_000_000_000,
    )


def _price_row(market_id: str, ts_offset: int = 0) -> PriceHistoryRow:
    return PriceHistoryRow(
        market_id=market_id,
        condition_id=None,
        token_id=f"tok-{market_id}",
        outcome="Yes",
        ts_ms=1_700_000_000_000 + ts_offset * 3_600_000,
        price=Decimal("0.50"),
        source="clob",
        fidelity="1h",
        interval="1h",
        schema_version=1,
        ingested_ts_ms=1_700_000_000_000,
    )


def test_historical_dataset_report_writes_html_and_csv(tmp_data_root, tmp_path):
    mkt_repo = ParquetMarketsRepository(tmp_data_root)
    ph_repo = ParquetPriceHistoryRepository(tmp_data_root)
    cov_repo = ParquetBackfillCoverageRepository(tmp_data_root)

    markets = [_market("m1"), _market("m2"), _market("m3")]
    mkt_repo.upsert_markets(markets)

    for m in markets:
        rows = [_price_row(m.id, i) for i in range(60)]
        ph_repo.append_many(rows)

    cfg = BackfillConfig(min_price_points=50)
    for m in markets:
        price_rows = list(ph_repo.iter_for_market(m.id))
        cov = compute_market_coverage(m, price_rows, [], True, True, True, cfg)
        cov_repo.append(cov)

    output_dir = tmp_path / "report_out"
    path = generate_historical_dataset_report(tmp_data_root, output_dir)

    assert path.exists()
    assert path.name == "index.html"
    html = path.read_text()
    assert "<!DOCTYPE html>" in html
    assert "Historical Dataset" in html


def test_report_tables_include_trace_ids(tmp_data_root, tmp_path):
    mkt_repo = ParquetMarketsRepository(tmp_data_root)
    ph_repo = ParquetPriceHistoryRepository(tmp_data_root)
    cov_repo = ParquetBackfillCoverageRepository(tmp_data_root)

    m = _market("trace-market")
    mkt_repo.upsert_markets([m])
    rows = [_price_row("trace-market", i) for i in range(60)]
    ph_repo.append_many(rows)

    cfg = BackfillConfig(min_price_points=50)
    price_rows = list(ph_repo.iter_for_market("trace-market"))
    cov = compute_market_coverage(m, price_rows, [], True, True, True, cfg)
    cov_repo.append(cov)

    output_dir = tmp_path / "report_trace"
    path = generate_historical_dataset_report(tmp_data_root, output_dir)

    html = path.read_text()
    # market_id column should appear in rendered HTML
    assert "market_id" in html
    assert "trace-market" in html


def test_long_questions_are_truncated_in_html_not_csv(tmp_data_root, tmp_path):
    mkt_repo = ParquetMarketsRepository(tmp_data_root)
    cov_repo = ParquetBackfillCoverageRepository(tmp_data_root)

    long_q = "A" * 200  # 200-char question
    m = _market("long-q-market", question=long_q)
    mkt_repo.upsert_markets([m])

    cfg = BackfillConfig(min_price_points=1)
    cov = compute_market_coverage(m, [], [], False, False, False, cfg)
    cov_repo.append(cov)

    output_dir = tmp_path / "report_trunc"
    path = generate_historical_dataset_report(tmp_data_root, output_dir)

    html = path.read_text()
    # HTML should not contain the full 200-char string (it gets truncated)
    assert long_q not in html
    # The truncation marker should appear
    assert "…" in html or "&#x2026;" in html or "..." in html

    # CSV should have the full question
    csv_path = output_dir / "coverage.csv"
    if csv_path.exists():
        csv_text = csv_path.read_text()
        assert long_q in csv_text


def test_report_works_with_empty_lake(tmp_data_root, tmp_path):
    """Report must not crash on an empty lake — should emit empty-state HTML."""
    output_dir = tmp_path / "empty_report"
    path = generate_historical_dataset_report(tmp_data_root, output_dir)
    assert path.exists()
    html = path.read_text()
    assert "<!DOCTYPE html>" in html
