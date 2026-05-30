from __future__ import annotations

import time

import pytest

from polymarket_arb.cli import limitless as limitless_cli
from polymarket_arb.limitless.models import ArbMatch, LimitlessMarketEntry, PolyMarketEntry
from polymarket_arb.live.models import OrdersLogRow
from polymarket_arb.storage.parquet.orders_log_repo import ParquetOrdersLogRepository


def _match(slug: str = "pacifica-token") -> ArbMatch:
    return ArbMatch(
        limitless=LimitlessMarketEntry(
            slug=slug,
            title="Will Pacifica launch a token?",
            yes_price=0.40,
            address="0xabc",
        ),
        poly=PolyMarketEntry(
            condition_id="cond",
            token_id_yes="yes",
            token_id_no="no",
            question="Will Pacifica launch a token?",
            yes_price=0.50,
        ),
        similarity=0.95,
        arb_gap=0.10,
        status="ARB_OPPORTUNITY",
    )


def _orders_row(*, notes: str, ts_ms: int, status: str = "paper_filled") -> OrdersLogRow:
    return OrdersLogRow(
        intent_id=f"intent-{ts_ms}",
        ts_ms=ts_ms,
        strategy_id="limitless_arb",
        token_id="0xabc",
        market_id="pacifica-token",
        side="YES",
        requested_size="1",
        filled_size="1",
        avg_fill_price="0.4",
        notional_usdc="1",
        fees_usdc="0",
        status=status,
        reason="",
        paper_mode=True,
        kill_switch_active=False,
        orders_allowed=True,
        preflight_passed=True,
        preflight_token_id=None,
        http_status=None,
        source_lane="limitless_arb",
        source_relationship_id="rel",
        notes=notes,
    )


@pytest.mark.asyncio
async def test_run_execute_aborts_live_limitless_with_paper_poly(settings, monkeypatch) -> None:
    settings = settings.model_copy(update={"limitless_paper_mode": False, "paper_mode": True})
    called = False

    def fail_if_called():
        nonlocal called
        called = True
        raise AssertionError("credentials should not be loaded after safety abort")

    errors: list[str] = []
    monkeypatch.setattr(limitless_cli, "_load_limitless_creds", fail_if_called)
    monkeypatch.setattr(limitless_cli.logger, "error", lambda message: errors.append(message))

    results = [_match()]
    returned = await limitless_cli._run_execute(
        settings=settings,
        results=results,
        lim_client_obj=None,
        stake_usdc=1.0,
        min_net_edge=0.02,
    )

    assert returned is results
    assert called is False
    assert errors
    assert "SAFETY ABORT: Limitless leg is LIVE but Polymarket leg is PAPER." in errors[0]


def test_get_active_slugs_reads_recent_limitless_orders(settings, tmp_data_root) -> None:
    now_ms = int(time.time() * 1000)
    repo = ParquetOrdersLogRepository(tmp_data_root)
    repo.append_many([
        _orders_row(notes="arb_gap=0.1 slug=pacifica-token lim_entry=0.4", ts_ms=now_ms),
        _orders_row(
            notes="arb_gap=0.1 slug=old-market lim_entry=0.4",
            ts_ms=now_ms - 8 * 24 * 60 * 60 * 1000,
        ),
        _orders_row(
            notes="arb_gap=0.1 slug=rejected-market lim_entry=0.4",
            ts_ms=now_ms,
            status="rejected_kill_switch",
        ),
    ])

    assert limitless_cli._get_active_slugs(settings, paper_mode=True) == {"pacifica-token"}
