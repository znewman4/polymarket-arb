from __future__ import annotations

import pytest

from polymarket_arb.cli import limitless as limitless_cli
from polymarket_arb.limitless.models import ArbMatch, LimitlessMarketEntry, PolyMarketEntry


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
