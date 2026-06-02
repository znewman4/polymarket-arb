"""Paper round-trip PnL proofs for Limitless x Polymarket exits."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from polymarket_arb.limitless.arb_scanner import _exit_both_legs
from polymarket_arb.limitless.models import LimitlessArbPosition, LimitlessOrderResult


class _CollectingLogRepo:
    def __init__(self) -> None:
        self.rows: list = []

    def append(self, row) -> None:
        self.rows.append(row)


class _PaperLimClient:
    _paper_mode = True
    paper_mode = True

    async def sell_yes(self, market, size_usdc: float, price: float):
        return LimitlessOrderResult(
            status="paper_filled",
            order_id="lim-exit",
            side="SELL_YES",
            price=price,
            size_usdc=size_usdc,
            market_slug=market.slug,
            error=None,
        )


class _PaperPolyClient:
    def __init__(self) -> None:
        self.intent = None
        self.kwargs = None

    def place_order(self, intent, **kwargs):
        self.intent = intent
        self.kwargs = kwargs
        return SimpleNamespace(status="paper_filled")


def _position(
    *,
    lim_entry_price: float,
    poly_yes_entry: float,
    arb_gap: float,
    stake_usdc: float = 1.0,
) -> LimitlessArbPosition:
    return LimitlessArbPosition(
        position_id="pos-1",
        limitless_slug="test-slug",
        poly_condition_id="0xCOND",
        poly_token_id_no="tok_no",
        lim_entry_price=lim_entry_price,
        poly_yes_entry=poly_yes_entry,
        arb_gap=arb_gap,
        stake_usdc=stake_usdc,
        open_ts_ms=1_000_000,
    )


def _note_float(notes: str, key: str) -> float:
    matches = dict(re.findall(r"(\w+)=(-?\d+(?:\.\d+)?)", notes))
    return float(matches[key])


@pytest.mark.asyncio
async def test_paper_arb_round_trip_is_profitable():
    """Prove a full entry + convergence exit cycle produces positive realised PnL.

    Entry: Lim YES at 0.35, Poly YES at 0.40, stake = $1.
    At convergence: both YES prices move to 0.48.
    """
    repo = _CollectingLogRepo()
    poly_client = _PaperPolyClient()

    exited = await _exit_both_legs(
        _position(lim_entry_price=0.35, poly_yes_entry=0.40, arb_gap=0.25),
        current_lim_yes=0.48,
        current_poly_yes=0.48,
        lim_client=_PaperLimClient(),
        poly_client=poly_client,
        orders_log_repo=repo,
    )

    assert exited is True
    assert repo.rows[0].status == "paper_filled"
    assert poly_client.intent.side == "sell"
    realised_profit = _note_float(repo.rows[0].notes, "realised_profit")
    assert realised_profit > 0


@pytest.mark.asyncio
async def test_paper_arb_round_trip_fees_reduce_profit():
    """Fees eat into profit at thin arb gaps.

    Entry gap is 0.03. The gross convergence move is positive, but 200 bps
    entry fees plus 200 bps exit fees make the round trip negative.
    """
    repo = _CollectingLogRepo()

    await _exit_both_legs(
        _position(lim_entry_price=0.47, poly_yes_entry=0.50, arb_gap=0.03),
        current_lim_yes=0.50,
        current_poly_yes=0.50,
        lim_client=_PaperLimClient(),
        poly_client=_PaperPolyClient(),
        orders_log_repo=repo,
    )

    gross_profit = _note_float(repo.rows[0].notes, "gross_profit")
    realised_profit = _note_float(repo.rows[0].notes, "realised_profit")
    fees_usdc = _note_float(repo.rows[0].notes, "fees_usdc")

    assert gross_profit == pytest.approx(0.03)
    assert fees_usdc > 0
    assert realised_profit < gross_profit
    assert realised_profit < 0
