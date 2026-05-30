"""Unit tests for the Limitless x Polymarket arb scanner logic."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from polymarket_arb.limitless.arb_scanner import (
    _arb_status,
    _fetch_live_poly_best_ask,
    _poly_from_raw,
    compute_arb,
    execute_arb,
    match_markets,
)
from polymarket_arb.limitless.models import ArbMatch, LimitlessMarketEntry, LimitlessOrderResult


def _lim(slug: str, yes_price: float) -> LimitlessMarketEntry:
    return LimitlessMarketEntry(
        slug=slug,
        title=f"Will {slug} happen?",
        yes_price=yes_price,
        address="0xTEST",
    )


def _poly(question: str, yes_price: float, condition_id: str = "0xPOLY") -> dict:
    return {
        "question": question,
        "conditionId": condition_id,
        "outcomes": '["Yes","No"]',
        "outcomePrices": f'["{yes_price}","{round(1 - yes_price, 6)}"]',
        "tokens": [
            {"token_id": "tok_yes", "outcome": "Yes"},
            {"token_id": "tok_no", "outcome": "No"},
        ],
    }


def _match() -> ArbMatch:
    matched = match_markets(
        [_lim("btc-above-65k", yes_price=0.40)],
        [_poly("Will btc-above-65k happen?", yes_price=0.45)],
        threshold=0.0,
    )[0]
    return ArbMatch(
        limitless=matched.limitless,
        poly=matched.poly,
        similarity=1.0,
        arb_gap=0.15,
        status="ARB_OPPORTUNITY",
    )


class _LimOrderClient:
    async def place_order(self, market, *, side: str, size_usdc: float):
        return LimitlessOrderResult(
            status="paper_filled",
            order_id="lim-order",
            side=side,
            price=market.yes_price,
            size_usdc=size_usdc,
            market_slug=market.slug,
            error=None,
        )


class _PolyOrderClient:
    def __init__(self) -> None:
        self.intent = None
        self.kwargs = None

    def place_order(self, intent, **kwargs):
        self.intent = intent
        self.kwargs = kwargs
        return SimpleNamespace(status="paper_filled")


# ─── _arb_status ─────────────────────────────────────────────────────────────


def test_arb_status_opportunity():
    assert _arb_status(0.10, tolerance=0.02) == "ARB_OPPORTUNITY"


def test_arb_status_efficient():
    assert _arb_status(0.01, tolerance=0.02) == "EFFICIENT"
    assert _arb_status(-0.01, tolerance=0.02) == "EFFICIENT"


def test_arb_status_over_round():
    assert _arb_status(-0.10, tolerance=0.02) == "OVER_ROUND"


def test_arb_status_at_boundary():
    assert _arb_status(0.02, tolerance=0.02) == "EFFICIENT"
    assert _arb_status(0.021, tolerance=0.02) == "ARB_OPPORTUNITY"


# ─── arb_gap calculation ─────────────────────────────────────────────────────


def test_arb_gap_positive():
    lim = _lim("btc-above-65k", yes_price=0.40)
    poly_raw = [_poly("Will BTC be above 65k?", yes_price=0.45)]
    matches = match_markets([lim], poly_raw, threshold=0.0)
    assert len(matches) == 1
    expected_gap = round(1.0 - (0.40 + 0.45), 6)
    assert abs(matches[0].arb_gap - expected_gap) < 1e-9


def test_arb_gap_efficient():
    lim = _lim("btc-65k", yes_price=0.50)
    poly_raw = [_poly("BTC above 65k", yes_price=0.50)]
    matches = match_markets([lim], poly_raw, threshold=0.0)
    assert matches[0].arb_gap == pytest.approx(0.0, abs=1e-9)


def test_arb_gap_negative_over_round():
    lim = _lim("btc-65k", yes_price=0.55)
    poly_raw = [_poly("BTC above 65k", yes_price=0.55)]
    matches = match_markets([lim], poly_raw, threshold=0.0)
    assert matches[0].arb_gap < 0


# ─── match_markets threshold ─────────────────────────────────────────────────


def test_threshold_filters_low_similarity():
    lim = _lim("will-xyz-happen", yes_price=0.50)
    poly_raw = [_poly("Completely unrelated question about weather", yes_price=0.50)]
    matches = match_markets([lim], poly_raw, threshold=0.82)
    assert len(matches) == 0


def test_threshold_zero_matches_everything():
    lim = _lim("something", yes_price=0.50)
    poly_raw = [_poly("Anything at all", yes_price=0.50)]
    matches = match_markets([lim], poly_raw, threshold=0.0)
    assert len(matches) == 1


def test_close_questions_match_above_threshold():
    lim = LimitlessMarketEntry(
        slug="btc-above-65000",
        title="Will Bitcoin be above $65,000 on Sep 1?",
        yes_price=0.42,
        address="0x0",
    )
    poly_raw = [_poly("Will Bitcoin be above $65,000 on September 1?", yes_price=0.45)]
    matches = match_markets([lim], poly_raw, threshold=0.75)
    assert len(matches) == 1


def test_best_match_selected_not_first():
    lim = LimitlessMarketEntry(
        slug="btc-65k",
        title="Will BTC be above 65k?",
        yes_price=0.40,
        address="0x0",
    )
    poly_raw = [
        _poly("Random noise question XYZ", yes_price=0.55, condition_id="0xWRONG"),
        _poly("Will BTC be above 65k?", yes_price=0.45, condition_id="0xRIGHT"),
    ]
    matches = match_markets([lim], poly_raw, threshold=0.0)
    assert matches[0].poly.condition_id == "0xRIGHT"


def test_named_token_mismatch_rejects_template_false_positive():
    lim = LimitlessMarketEntry(
        slug="puffpaw-launch",
        title="Will Puffpaw launch a token this year?",
        yes_price=0.40,
        address="0x0",
    )
    poly_raw = [_poly("Will Pacifica launch a token this year?", yes_price=0.45)]

    matches = match_markets([lim], poly_raw, threshold=0.0)

    assert matches == []


def test_named_token_filter_still_selects_valid_lower_similarity_match():
    lim = LimitlessMarketEntry(
        slug="puffpaw-market-cap",
        title="Will Puffpaw reach a $500M market cap this year?",
        yes_price=0.40,
        address="0x0",
    )
    poly_raw = [
        _poly(
            "Will Pacifica reach a $500M market cap this year?",
            yes_price=0.45,
            condition_id="0xWRONG",
        ),
        _poly(
            "Will Puffpaw exceed a $500M valuation this year?",
            yes_price=0.45,
            condition_id="0xRIGHT",
        ),
    ]

    matches = match_markets([lim], poly_raw, threshold=0.0)

    assert matches[0].poly.condition_id == "0xRIGHT"


# ─── compute_arb ─────────────────────────────────────────────────────────────


def test_compute_arb_reclassifies_with_new_tolerance():
    lim = _lim("x", yes_price=0.48)
    poly_raw = [_poly("x", yes_price=0.48)]
    matches = match_markets([lim], poly_raw, threshold=0.0)
    assert matches[0].status == "PENDING"
    # With tolerance=0.02, gap=0.04 becomes an opportunity.
    results = compute_arb(matches, tolerance=0.02)
    assert results[0].status == "ARB_OPPORTUNITY"
    # With tolerance=0.05, gap=0.04 remains efficient.
    results = compute_arb(matches, tolerance=0.05)
    assert results[0].status == "EFFICIENT"


def test_compute_arb_returns_new_list():
    lim = _lim("y", yes_price=0.50)
    poly_raw = [_poly("y", yes_price=0.50)]
    matches = match_markets([lim], poly_raw, threshold=0.0)
    results = compute_arb(matches, tolerance=0.02)
    assert results is not matches


# ─── poly token extraction ────────────────────────────────────────────────────


def test_poly_token_ids_extracted():
    lim = _lim("test", yes_price=0.40)
    poly_raw = [_poly("test", yes_price=0.45)]
    matches = match_markets([lim], poly_raw, threshold=0.0)
    assert matches[0].poly.token_id_yes == "tok_yes"
    assert matches[0].poly.token_id_no == "tok_no"


def test_poly_token_ids_extracted_from_nested_object():
    raw = _poly("test", yes_price=0.45)
    raw["tokens"] = {"yes": "nested_yes", "no": "nested_no"}

    entry = _poly_from_raw(raw)

    assert entry is not None
    assert entry.token_id_yes == "nested_yes"
    assert entry.token_id_no == "nested_no"


def test_poly_token_ids_extracted_from_gamma_clob_token_ids():
    raw = _poly("test", yes_price=0.45)
    raw.pop("tokens")
    raw["clobTokenIds"] = '["gamma_yes", "gamma_no"]'

    entry = _poly_from_raw(raw)

    assert entry is not None
    assert entry.token_id_yes == "gamma_yes"
    assert entry.token_id_no == "gamma_no"


def test_poly_missing_tokens_still_matches_and_warns(monkeypatch):
    lim = _lim("test", yes_price=0.40)
    raw = {
        "question": "test",
        "conditionId": "0xNOTOK",
        "outcomes": '["Yes","No"]',
        "outcomePrices": '["0.45","0.55"]',
    }
    warnings = []
    monkeypatch.setattr(
        "polymarket_arb.limitless.arb_scanner.logger.warning",
        lambda *args: warnings.append(args),
    )
    matches = match_markets([lim], [raw], threshold=0.0)
    assert len(matches) == 1
    assert matches[0].poly.token_id_yes == ""
    assert matches[0].poly.token_id_no == ""
    assert warnings == [(
        "poly parser: no token IDs for {}; "
        "tokens type={} sample={}; clobTokenIds type={} sample={}",
        "0xNOTOK",
        "NoneType",
        "None",
        "NoneType",
        "None",
    )]


def test_poly_missing_no_token_debug_logs_raw_tokens(monkeypatch):
    raw = _poly("test", yes_price=0.45)
    raw["tokens"] = {"yes": "nested_yes"}
    debug_logs = []
    monkeypatch.setattr(
        "polymarket_arb.limitless.arb_scanner.logger.debug",
        lambda *args: debug_logs.append(args),
    )

    entry = _poly_from_raw(raw)

    assert entry is not None
    assert entry.token_id_yes == "nested_yes"
    assert entry.token_id_no == ""
    assert debug_logs == [(
        "poly parser: NO token ID missing for {}; "
        "tokens type={} sample={}; clobTokenIds type={} sample={}",
        "0xPOLY",
        "dict",
        repr({"yes": "nested_yes"}),
        "NoneType",
        "None",
    )]


# ─── live execution quote ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_live_poly_best_ask_returns_float_when_api_has_asks(monkeypatch):
    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"asks": [{"price": "0.57", "size": "100"}]}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url: str, *, params: dict):
            assert url == "https://clob.polymarket.com/book"
            assert params == {"token_id": "tok_no"}
            return _Response()

    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: _Client())

    assert await _fetch_live_poly_best_ask("tok_no") == 0.57


@pytest.mark.asyncio
async def test_execute_arb_uses_live_price_when_book_available(monkeypatch):
    async def _live_ask(token_id: str) -> float:
        assert token_id == "tok_no"
        return 0.57

    monkeypatch.setattr(
        "polymarket_arb.limitless.arb_scanner._fetch_live_poly_best_ask",
        _live_ask,
    )
    poly_client = _PolyOrderClient()

    await execute_arb(
        _match(),
        lim_client=_LimOrderClient(),
        poly_client=poly_client,
        stake_usdc=1.0,
        min_net_edge=0.02,
    )

    assert poly_client.intent.price == Decimal("0.57")
    assert poly_client.intent.market_id == "0xPOLY"
    assert poly_client.kwargs["preflight_book"] == {"best_ask": 0.57}


@pytest.mark.asyncio
async def test_execute_arb_falls_back_when_live_book_unavailable(monkeypatch):
    async def _no_live_ask(token_id: str) -> None:
        assert token_id == "tok_no"
        return None

    monkeypatch.setattr(
        "polymarket_arb.limitless.arb_scanner._fetch_live_poly_best_ask",
        _no_live_ask,
    )
    poly_client = _PolyOrderClient()

    await execute_arb(
        _match(),
        lim_client=_LimOrderClient(),
        poly_client=poly_client,
        stake_usdc=1.0,
        min_net_edge=0.02,
    )

    assert poly_client.intent.price == Decimal("0.55")
    assert poly_client.kwargs["preflight_book"] is None


# ─── Task 2: tokenID capital-D + clobTokenIds plain list ─────────────────────


def test_poly_token_ids_extracted_from_tokenID_capital_D():
    raw = _poly("test", yes_price=0.45)
    raw["tokens"] = [
        {"tokenID": "capD_yes", "outcome": "Yes"},
        {"tokenID": "capD_no", "outcome": "No"},
    ]

    entry = _poly_from_raw(raw)

    assert entry is not None
    assert entry.token_id_yes == "capD_yes"
    assert entry.token_id_no == "capD_no"


def test_poly_token_ids_extracted_from_clob_token_ids_plain_list():
    raw = _poly("test", yes_price=0.45)
    raw.pop("tokens")
    # Plain Python list (not JSON-encoded string).
    raw["clobTokenIds"] = ["plain_yes", "plain_no"]

    entry = _poly_from_raw(raw)

    assert entry is not None
    assert entry.token_id_yes == "plain_yes"
    assert entry.token_id_no == "plain_no"


# ─── Task 6: enriched orders_log notes on Poly leg ───────────────────────────


@pytest.mark.asyncio
async def test_execute_arb_notes_contain_all_keys(monkeypatch):
    async def _live_ask(token_id: str) -> float:
        return 0.57

    monkeypatch.setattr(
        "polymarket_arb.limitless.arb_scanner._fetch_live_poly_best_ask",
        _live_ask,
    )
    poly_client = _PolyOrderClient()

    await execute_arb(
        _match(),
        lim_client=_LimOrderClient(),
        poly_client=poly_client,
        stake_usdc=1.0,
        min_net_edge=0.02,
    )

    notes = poly_client.kwargs["notes"]
    for key in ("arb_gap", "slug", "lim_entry", "poly_yes_entry", "similarity"):
        assert f"{key}=" in notes, f"missing key {key!r} in notes: {notes!r}"


# ─── Task 4: convergence-based early exit ────────────────────────────────────


def _make_position(arb_gap: float = 0.10):
    from polymarket_arb.limitless.models import LimitlessArbPosition
    return LimitlessArbPosition(
        position_id="pos-1",
        limitless_slug="test-slug",
        poly_condition_id="0xCOND",
        poly_token_id_no="tok_no",
        lim_entry_price=0.40,
        poly_yes_entry=0.50,
        arb_gap=arb_gap,
        stake_usdc=10.0,
        open_ts_ms=1_000_000,
    )


class _CollectingLogRepo:
    def __init__(self) -> None:
        self.rows: list = []

    def append(self, row) -> None:
        self.rows.append(row)


class _PaperLimOrderClient:
    _paper_mode = True


@pytest.mark.asyncio
async def test_scan_and_exit_positions_exits_when_converged(monkeypatch):
    from polymarket_arb.limitless.arb_scanner import scan_and_exit_positions

    # lim moved 0.40 -> 0.55 (delta=0.15); poly_yes moved 0.50 -> 0.40 (delta=0.10)
    # arb_gap=0.10, threshold=0.5 => required move=0.05; both exceed.
    async def _lim_price(slug, limitless_host="x"):
        return 0.55
    # _fetch_live_poly_best_ask returns NO ask; current_poly_yes = 1 - 0.60 = 0.40
    async def _poly_ask(token_id):
        return 0.60

    monkeypatch.setattr(
        "polymarket_arb.limitless.arb_scanner._fetch_limitless_current_price",
        _lim_price,
    )
    monkeypatch.setattr(
        "polymarket_arb.limitless.arb_scanner._fetch_live_poly_best_ask",
        _poly_ask,
    )

    repo = _CollectingLogRepo()
    exited = await scan_and_exit_positions(
        [_make_position()],
        lim_client=_PaperLimOrderClient(),
        orders_log_repo=repo,
        convergence_threshold=0.5,
    )
    assert exited == 1
    # Two rows logged: limitless exit + polymarket exit placeholder.
    assert len(repo.rows) == 2
    statuses = {r.status for r in repo.rows}
    assert "paper_exit_filled" in statuses
    assert "exit_not_implemented" in statuses


@pytest.mark.asyncio
async def test_scan_and_exit_positions_holds_when_movement_below_threshold(monkeypatch):
    from polymarket_arb.limitless.arb_scanner import scan_and_exit_positions

    # Tiny movements — far below arb_gap * threshold.
    async def _lim_price(slug, limitless_host="x"):
        return 0.41  # delta=0.01
    async def _poly_ask(token_id):
        return 0.51  # poly_yes_current = 0.49, delta=0.01

    monkeypatch.setattr(
        "polymarket_arb.limitless.arb_scanner._fetch_limitless_current_price",
        _lim_price,
    )
    monkeypatch.setattr(
        "polymarket_arb.limitless.arb_scanner._fetch_live_poly_best_ask",
        _poly_ask,
    )

    repo = _CollectingLogRepo()
    exited = await scan_and_exit_positions(
        [_make_position()],
        lim_client=_PaperLimOrderClient(),
        orders_log_repo=repo,
        convergence_threshold=0.5,
    )
    assert exited == 0
    assert repo.rows == []


@pytest.mark.asyncio
async def test_scan_and_exit_positions_skips_when_price_fetch_returns_none(monkeypatch):
    from polymarket_arb.limitless.arb_scanner import scan_and_exit_positions

    async def _lim_price(slug, limitless_host="x"):
        return None
    async def _poly_ask(token_id):
        return 0.55

    monkeypatch.setattr(
        "polymarket_arb.limitless.arb_scanner._fetch_limitless_current_price",
        _lim_price,
    )
    monkeypatch.setattr(
        "polymarket_arb.limitless.arb_scanner._fetch_live_poly_best_ask",
        _poly_ask,
    )

    repo = _CollectingLogRepo()
    exited = await scan_and_exit_positions(
        [_make_position()],
        lim_client=_PaperLimOrderClient(),
        orders_log_repo=repo,
        convergence_threshold=0.5,
    )
    assert exited == 0
    assert repo.rows == []
