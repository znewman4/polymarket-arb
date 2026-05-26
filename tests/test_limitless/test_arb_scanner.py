"""Unit tests for the Limitless x Polymarket arb scanner logic."""

from __future__ import annotations

import pytest

from polymarket_arb.limitless.arb_scanner import _arb_status, compute_arb, match_markets
from polymarket_arb.limitless.models import LimitlessMarketEntry


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


# ─── compute_arb ─────────────────────────────────────────────────────────────


def test_compute_arb_reclassifies_with_new_tolerance():
    lim = _lim("x", yes_price=0.48)
    poly_raw = [_poly("x", yes_price=0.48)]
    matches = match_markets([lim], poly_raw, threshold=0.0)
    assert matches[0].status == ""
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


def test_poly_missing_tokens_still_matches():
    lim = _lim("test", yes_price=0.40)
    raw = {
        "question": "test",
        "conditionId": "0xNOTOK",
        "outcomes": '["Yes","No"]',
        "outcomePrices": '["0.45","0.55"]',
    }
    matches = match_markets([lim], [raw], threshold=0.0)
    assert len(matches) == 1
    assert matches[0].poly.token_id_yes == ""
    assert matches[0].poly.token_id_no == ""
