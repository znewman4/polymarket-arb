"""Tests for LimitlessOrderClient — body format, owner_id caching, error paths."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from polymarket_arb.limitless.models import LimitlessMarketEntry
from polymarket_arb.limitless.order_client import LimitlessOrderClient

_KILL_SWITCH = Path("/tmp/test-limitless-ks")


def _make_client(
    *,
    paper_mode: bool = False,
    key_id: str = "kid",
    key_secret: str = "a77aUecRr6g1cwK6vmRRaLCVnxkj/cfl4STLg7Q0yBM=",
    wallet_address: str | None = "0xWALLET",
    http: MagicMock | None = None,
) -> LimitlessOrderClient:
    if http is None:
        http = MagicMock()
    return LimitlessOrderClient(
        limitless_host="https://api.limitless.exchange",
        http=http,
        kill_switch_path=_KILL_SWITCH,
        paper_mode=paper_mode,
        key_id=key_id,
        key_secret=key_secret,
        wallet_address=wallet_address,
    )


def _make_market(
    *,
    slug: str = "btc-65k",
    yes_price: float = 0.6,
    token_id_yes: str = "111000111",
    token_id_no: str = "222000222",
) -> LimitlessMarketEntry:
    return LimitlessMarketEntry(
        slug=slug,
        title="Will BTC be above $65k?",
        yes_price=yes_price,
        address="0xADDR",
        token_id_yes=token_id_yes,
        token_id_no=token_id_no,
    )


# ─── paper mode ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_paper_fill_returns_paper_filled_status():
    client = _make_client(paper_mode=True)
    result = await client.place_order(_make_market(), side="YES", size_usdc=10.0)
    assert result.status == "paper_filled"
    assert result.side == "YES"
    assert result.size_usdc == 10.0
    assert result.order_id is not None


@pytest.mark.asyncio
async def test_paper_fill_no_side_yes_price():
    client = _make_client(paper_mode=True)
    market = _make_market(yes_price=0.4)
    result = await client.place_order(market, side="NO", size_usdc=5.0)
    assert result.status == "paper_filled"
    assert abs(result.price - 0.6) < 1e-9  # 1 - 0.4


@pytest.mark.asyncio
async def test_kill_switch_blocks_order():
    _KILL_SWITCH.touch()
    try:
        client = _make_client(paper_mode=False)
        result = await client.place_order(_make_market(), side="YES", size_usdc=10.0)
        assert result.status == "rejected_kill_switch"
    finally:
        _KILL_SWITCH.unlink(missing_ok=True)


# ─── _get_owner_id ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_owner_id_fetches_from_profile_endpoint():
    http = MagicMock()
    http.request_json = AsyncMock(return_value={"id": 42, "username": "trader"})
    client = _make_client(http=http, wallet_address="0xABC")

    owner_id = await client._get_owner_id()

    assert owner_id == 42
    http.request_json.assert_called_once()
    call_args = http.request_json.call_args
    assert call_args[0][0] == "GET"
    assert "/profiles/0xABC" in call_args[0][1]


@pytest.mark.asyncio
async def test_get_owner_id_cached_after_first_fetch():
    http = MagicMock()
    http.request_json = AsyncMock(return_value={"id": 99})
    client = _make_client(http=http)

    id1 = await client._get_owner_id()
    id2 = await client._get_owner_id()

    assert id1 == id2 == 99
    assert http.request_json.call_count == 1  # only fetched once


@pytest.mark.asyncio
async def test_get_owner_id_returns_none_if_no_wallet():
    client = _make_client(wallet_address=None)
    assert await client._get_owner_id() is None


@pytest.mark.asyncio
async def test_get_owner_id_returns_none_on_http_error():
    from polymarket_arb.http.client import HttpError

    http = MagicMock()
    http.request_json = AsyncMock(side_effect=HttpError(401, "Unauthorized"))
    client = _make_client(http=http)

    assert await client._get_owner_id() is None


@pytest.mark.asyncio
async def test_get_owner_id_returns_none_if_id_missing_in_response():
    http = MagicMock()
    http.request_json = AsyncMock(return_value={"username": "no-id-here"})
    client = _make_client(http=http)

    assert await client._get_owner_id() is None


# ─── live submit body format ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_live_submit_sends_correct_body_fields():
    http = MagicMock()
    profile_resp = {"id": 7}
    order_resp = {"order": {"id": "ord-abc"}}
    http.request_json = AsyncMock(side_effect=[profile_resp, order_resp])

    client = _make_client(http=http)
    market = _make_market(yes_price=0.55, token_id_yes="TOK_YES_123")
    result = await client.place_order(market, side="YES", size_usdc=25.0)

    assert result.status == "live_submitted"
    # Second call is the POST /orders
    post_call = http.request_json.call_args_list[1]
    body = json.loads(post_call[1]["content"])

    assert body["tokenId"] == "TOK_YES_123"
    assert body["price"] == "0.55"
    assert body["size"] == "25.0"
    assert body["side"] == "BUY"
    assert body["orderType"] == "GTC"
    assert body["marketSlug"] == "btc-65k"
    assert body["ownerId"] == 7


@pytest.mark.asyncio
async def test_live_submit_uses_no_token_for_no_side():
    http = MagicMock()
    http.request_json = AsyncMock(side_effect=[{"id": 1}, {"order": {"id": "x"}}])

    client = _make_client(http=http)
    market = _make_market(yes_price=0.4, token_id_no="TOK_NO_456")
    result = await client.place_order(market, side="NO", size_usdc=10.0)

    assert result.status == "live_submitted"
    post_call = http.request_json.call_args_list[1]
    body = json.loads(post_call[1]["content"])
    assert body["tokenId"] == "TOK_NO_456"
    assert body["price"] == "0.6"  # 1 - 0.4


@pytest.mark.asyncio
async def test_live_submit_fails_if_token_id_missing():
    http = MagicMock()
    http.request_json = AsyncMock(return_value={"id": 1})

    client = _make_client(http=http)
    market = _make_market(token_id_yes="", token_id_no="")
    result = await client.place_order(market, side="YES", size_usdc=10.0)

    assert result.status == "failed"
    assert "token_id_yes" in result.error


@pytest.mark.asyncio
async def test_live_submit_fails_if_credentials_missing():
    client = _make_client(key_id=None, key_secret=None)
    result = await client.place_order(_make_market(), side="YES", size_usdc=10.0)
    assert result.status == "failed"
    assert "credentials" in result.error


@pytest.mark.asyncio
async def test_live_submit_fails_if_owner_id_unresolvable():
    client = _make_client(wallet_address=None)
    result = await client.place_order(_make_market(), side="YES", size_usdc=10.0)
    assert result.status == "failed"
    assert "owner_id" in result.error
