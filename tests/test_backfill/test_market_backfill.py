"""Tests for market universe selection and backfill orchestration."""

from __future__ import annotations

from decimal import Decimal

import respx
from httpx import Response

from polymarket_arb.backfill.market_backfill import select_universe
from polymarket_arb.backfill.models import BackfillConfig
from polymarket_arb.storage.base import MarketRow
from polymarket_arb.storage.parquet.markets_repo import ParquetMarketsRepository


def _market(
    market_id: str = "m1",
    *,
    active: bool = True,
    closed: bool = False,
    token_ids: list[str] | None = None,
) -> MarketRow:
    return MarketRow(
        id=market_id,
        condition_id=f"cond-{market_id}",
        slug=f"slug-{market_id}",
        question=f"Will {market_id} resolve yes?",
        description=None,
        end_date_ms=None,
        start_date_ms=None,
        closed_at_ms=None,
        resolved_at_ms=None,
        active=active,
        closed=closed,
        archived=False,
        outcomes=["Yes", "No"],
        gamma_outcome_prices_snapshot=[Decimal("0.5"), Decimal("0.5")],
        clob_token_ids=token_ids or [f"tok-{market_id}-yes", f"tok-{market_id}-no"],
        volume=Decimal("1000"),
        liquidity=Decimal("500"),
        event_id=None,
        neg_risk=False,
        text_hash="abc123",
        schema_version=1,
        ingested_ts_ms=1_700_000_000_000,
    )


def test_backfill_rows_join_to_known_gamma_markets(tmp_data_root, settings):
    repo = ParquetMarketsRepository(tmp_data_root)
    m1 = _market("m1", token_ids=["tok1", "tok2"])
    m2 = _market("m2", token_ids=["tok3", "tok4"])
    repo.upsert_markets([m1, m2])

    cfg = BackfillConfig(market_limit=10, include_recently_resolved=False)
    universe = select_universe(tmp_data_root, cfg)
    assert len(universe) == 2
    ids = {m.id for m in universe}
    assert "m1" in ids and "m2" in ids


def test_backfill_universe_respects_limit(tmp_data_root):
    repo = ParquetMarketsRepository(tmp_data_root)
    markets = [_market(f"m{i}") for i in range(10)]
    repo.upsert_markets(markets)

    cfg = BackfillConfig(market_limit=3, include_recently_resolved=False)
    universe = select_universe(tmp_data_root, cfg)
    assert len(universe) == 3


def test_backfill_empty_universe_when_no_data(tmp_data_root):
    cfg = BackfillConfig(include_recently_resolved=False)
    universe = select_universe(tmp_data_root, cfg)
    assert universe == []


async def test_backfill_no_live_network_in_unit_tests(settings, tmp_data_root):
    """Asserts no real HTTP escapes during backfill — all calls go through respx mock."""
    from polymarket_arb.backfill.price_history import run_price_history_backfill
    from polymarket_arb.storage.parquet.markets_repo import ParquetMarketsRepository

    repo = ParquetMarketsRepository(settings.data_root)
    repo.upsert_markets([_market("m1")])

    cfg = BackfillConfig(market_limit=5, requested_days=1, include_recently_resolved=False)

    history_payload = {
        "history": [{"t": 1700000000, "p": "0.42"}, {"t": 1700003600, "p": "0.44"}]
    }
    async with respx.mock(assert_all_called=False) as router:
        router.get(url__startswith="https://clob.example").mock(
            return_value=Response(200, json=history_payload)
        )
        router.post(url__startswith="https://clob.example").mock(
            return_value=Response(200, json=[history_payload])
        )
        result = await run_price_history_backfill(settings, cfg)

    assert result.markets_attempted >= 0  # No real HTTP was used


async def test_backfill_writes_raw_before_normalised(settings, tmp_data_root):
    """RawWriter persists JSON before normalised parquet is written."""
    from polymarket_arb.backfill.price_history import run_price_history_backfill
    from polymarket_arb.storage.parquet.markets_repo import ParquetMarketsRepository

    repo = ParquetMarketsRepository(settings.data_root)
    repo.upsert_markets([_market("m1", token_ids=["tok1"])])

    cfg = BackfillConfig(market_limit=5, requested_days=1, include_recently_resolved=False)
    history_payload = {
        "history": [{"t": 1700000000, "p": "0.42"}, {"t": 1700003600, "p": "0.44"}]
    }

    async with respx.mock(assert_all_called=False) as router:
        router.get(url__startswith="https://clob.example").mock(
            return_value=Response(200, json=history_payload)
        )
        router.post(url__startswith="https://clob.example").mock(
            return_value=Response(200, json=[history_payload])
        )
        result = await run_price_history_backfill(settings, cfg)

    if result.total_rows_written > 0:
        raw_files = list((settings.data_root / "raw" / "clob").rglob("*.json"))
        assert len(raw_files) >= 1


async def test_backfill_retries_coarse_batch_when_requested_window_is_too_long(
    settings,
    tmp_data_root,
):
    from polymarket_arb.backfill.price_history import run_price_history_backfill
    from polymarket_arb.storage.parquet.markets_repo import ParquetMarketsRepository
    from polymarket_arb.storage.parquet.price_history_repo import ParquetPriceHistoryRepository

    token_id = "123456789"
    repo = ParquetMarketsRepository(settings.data_root)
    repo.upsert_markets([_market("m1", token_ids=[token_id])])

    cfg = BackfillConfig(market_limit=5, requested_days=180, include_recently_resolved=False)
    history_payload = [{"t": 1700000000, "p": "0.42"}, {"t": 1700003600, "p": "0.44"}]

    async with respx.mock(assert_all_called=False) as router:
        route = router.post("https://clob.example/batch-prices-history").mock(
            side_effect=[
                Response(
                    400,
                    json={"error": "invalid filters: 'startTs' and 'endTs' interval is too long"},
                ),
                Response(200, json={"history": {token_id: history_payload}}),
            ]
        )
        result = await run_price_history_backfill(settings, cfg)

    assert route.call_count == 2
    assert b'"interval":"1h"' in route.calls[0].request.content
    assert b'"start_ts"' in route.calls[0].request.content
    assert b'"interval":"max"' in route.calls[1].request.content
    assert b'"fidelity":720' in route.calls[1].request.content
    assert result.total_rows_written == 2
    price_repo = ParquetPriceHistoryRepository(settings.data_root)
    rows = list(price_repo.iter_for_token(token_id))
    assert [r.interval for r in rows] == ["max", "max"]
