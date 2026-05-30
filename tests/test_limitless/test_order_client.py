"""Tests for LimitlessOrderClient — EIP-712 payload, owner_id caching, error paths."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from polymarket_arb.http.client import HttpError
from polymarket_arb.limitless.models import LimitlessMarketEntry
from polymarket_arb.limitless.order_client import LimitlessOrderClient

_KILL_SWITCH = Path("/tmp/test-limitless-ks")
# Deterministic test key (safe secp256k1 scalar, never used on mainnet)
_TEST_PRIVATE_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
_TEST_WALLET = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"


def _make_client(
    *,
    paper_mode: bool = False,
    key_id: str = "kid",
    key_secret: str = "a77aUecRr6g1cwK6vmRRaLCVnxkj/cfl4STLg7Q0yBM=",
    wallet_address: str | None = _TEST_WALLET,
    private_key: str | None = _TEST_PRIVATE_KEY,
    http: MagicMock | None = None,
    collateral_approved: bool = True,
) -> LimitlessOrderClient:
    if http is None:
        http = MagicMock()
    client = LimitlessOrderClient(
        limitless_host="https://api.limitless.exchange",
        http=http,
        kill_switch_path=_KILL_SWITCH,
        paper_mode=paper_mode,
        key_id=key_id,
        key_secret=key_secret,
        wallet_address=wallet_address,
        private_key=private_key,
    )
    if collateral_approved:
        client._approved.add("0xEXCHANGE")
    return client


def _make_market(
    *,
    slug: str = "btc-65k",
    yes_price: float = 0.6,
    token_id_yes: str = "111000111",
    token_id_no: str = "222000222",
    address: str = "0xEXCHANGE",
) -> LimitlessMarketEntry:
    return LimitlessMarketEntry(
        slug=slug,
        title="Will BTC be above $65k?",
        yes_price=yes_price,
        address=address,
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
async def test_paper_fill_no_side_uses_no_price():
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
    assert "/profiles/public/0xABC" in call_args[0][1]


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


# ─── _ensure_collateral_approval ──────────────────────────────────────────────


_VALID_EXCHANGE = "0x1111111111111111111111111111111111111111"


def test_collateral_approval_uses_existing_allowance_and_caches_address():
    with patch("polymarket_arb.limitless.order_client.Web3") as mock_web3:
        mock_web3.to_checksum_address.side_effect = lambda address: address
        w3 = mock_web3.return_value
        usdc = w3.eth.contract.return_value
        usdc.functions.allowance.return_value.call.return_value = 1_000_000
        client = _make_client(collateral_approved=False)

        client._ensure_collateral_approval(_VALID_EXCHANGE, 1.0)
        client._ensure_collateral_approval(_VALID_EXCHANGE, 1.0)

    usdc.functions.allowance.assert_called_once_with(_TEST_WALLET, _VALID_EXCHANGE)
    usdc.functions.approve.assert_not_called()
    assert _VALID_EXCHANGE in client._approved


def test_collateral_approval_sends_max_approval_when_allowance_insufficient():
    with patch("polymarket_arb.limitless.order_client.Web3") as mock_web3:
        mock_web3.to_checksum_address.side_effect = lambda address: address
        mock_web3.to_hex.return_value = "0xapprove"
        w3 = mock_web3.return_value
        w3.eth.chain_id = 8453
        w3.eth.gas_price = 100
        w3.eth.get_transaction_count.return_value = 4
        w3.eth.account.sign_transaction.return_value = SimpleNamespace(raw_transaction=b"signed")
        w3.eth.send_raw_transaction.return_value = b"tx-hash"
        w3.eth.wait_for_transaction_receipt.return_value = {"status": 1}
        usdc = w3.eth.contract.return_value
        usdc.functions.allowance.return_value.call.return_value = 0
        approve_call = usdc.functions.approve.return_value
        approve_call.build_transaction.return_value = {"built": True}
        client = _make_client(collateral_approved=False)

        client._ensure_collateral_approval(_VALID_EXCHANGE, 1.0)

    usdc.functions.approve.assert_called_once_with(_VALID_EXCHANGE, (1 << 256) - 1)
    approve_call.build_transaction.assert_called_once_with({
        "from": _TEST_WALLET,
        "chainId": 8453,
        "nonce": 4,
        "gas": 100_000,
        "gasPrice": 100,
    })
    w3.eth.account.sign_transaction.assert_called_once_with(
        {"built": True},
        private_key=_TEST_PRIVATE_KEY,
    )
    w3.eth.send_raw_transaction.assert_called_once_with(b"signed")
    w3.eth.wait_for_transaction_receipt.assert_called_once_with(b"tx-hash", timeout=120)
    assert _VALID_EXCHANGE in client._approved


# ─── live submit — payload structure ─────────────────────────────────────────


_FAKE_SIGNED_ORDER = {"salt": 123, "signature": "0xdeadbeef", "tokenId": "111000111"}


@pytest.mark.asyncio
async def test_live_submit_correct_outer_payload():
    """POST /orders body must be {order, orderType, marketSlug, ownerId}."""
    http = MagicMock()
    http.request_json = AsyncMock(side_effect=[{"id": 7}, {"order": {"id": "ord-1"}}])

    with patch(
        "polymarket_arb.limitless.order_client.build_signed_order",
        return_value=_FAKE_SIGNED_ORDER,
    ):
        client = _make_client(http=http)
        result = await client.place_order(_make_market(), side="YES", size_usdc=25.0)

    assert result.status == "live_submitted"
    post_call = http.request_json.call_args_list[1]
    body = json.loads(post_call[1]["content"])

    assert body["order"] == _FAKE_SIGNED_ORDER
    assert body["orderType"] == "GTC"
    assert body["marketSlug"] == "btc-65k"
    assert body["ownerId"] == 7


@pytest.mark.asyncio
async def test_live_submit_ensures_collateral_approval_before_signing():
    http = MagicMock()
    http.request_json = AsyncMock(side_effect=[{"id": 7}, {"order": {"id": "ord-1"}}])
    client = _make_client(http=http, collateral_approved=False)
    market = _make_market(address=_VALID_EXCHANGE)

    with (
        patch.object(client, "_ensure_collateral_approval") as mock_approval,
        patch(
            "polymarket_arb.limitless.order_client.build_signed_order",
            return_value=_FAKE_SIGNED_ORDER,
        ) as mock_build,
    ):
        result = await client.place_order(market, side="YES", size_usdc=1.0)

    assert result.status == "live_submitted"
    mock_approval.assert_called_once_with(_VALID_EXCHANGE, 1.0)
    mock_build.assert_called_once()


@pytest.mark.asyncio
async def test_live_submit_sleeps_after_fresh_approval():
    http = MagicMock()
    http.request_json = AsyncMock(side_effect=[
        {"id": 7},
        {"order": {"id": "ord-1"}},
        {"order": {"id": "ord-2"}},
    ])
    client = _make_client(http=http, collateral_approved=False)
    market = _make_market(address=_VALID_EXCHANGE)

    def approve(address: str, amount: float) -> None:
        client._approved.add(address)

    with (
        patch.object(client, "_get_owner_id", AsyncMock(return_value=7)),
        patch.object(client, "_ensure_collateral_approval", side_effect=approve),
        patch("polymarket_arb.limitless.order_client.asyncio.sleep", new_callable=AsyncMock) as sleep,
        patch(
            "polymarket_arb.limitless.order_client.build_signed_order",
            return_value=_FAKE_SIGNED_ORDER,
        ),
    ):
        first = await client._live_submit(
            market=market,
            side="YES",
            price=market.yes_price,
            size_usdc=1.0,
        )
        sleep.assert_awaited_once_with(3)

        sleep.reset_mock()
        second = await client._live_submit(
            market=market,
            side="YES",
            price=market.yes_price,
            size_usdc=1.0,
        )
        sleep.assert_not_awaited()

    assert first.status == "live_submitted"
    assert second.status == "live_submitted"


@pytest.mark.asyncio
async def test_live_submit_passes_correct_args_to_build_signed_order():
    """build_signed_order is called with the right token_id, price, side."""
    http = MagicMock()
    http.request_json = AsyncMock(side_effect=[{"id": 3}, {"order": {"id": "x"}}])

    with patch(
        "polymarket_arb.limitless.order_client.build_signed_order",
        return_value=_FAKE_SIGNED_ORDER,
    ) as mock_build:
        client = _make_client(http=http)
        market = _make_market(yes_price=0.55, token_id_yes="TOK_YES_999")
        await client.place_order(market, side="YES", size_usdc=20.0)

    mock_build.assert_called_once()
    kwargs = mock_build.call_args.kwargs
    assert kwargs["token_id"] == "TOK_YES_999"
    assert abs(kwargs["price"] - 0.55) < 1e-9
    assert kwargs["size_usdc"] == 20.0
    assert kwargs["side"] == "BUY"
    assert kwargs["order_type"] == "GTC"
    assert kwargs["exchange_address"] == "0xEXCHANGE"
    assert kwargs["private_key"] == _TEST_PRIVATE_KEY
    assert kwargs["wallet_address"] == _TEST_WALLET


@pytest.mark.asyncio
async def test_live_submit_uses_no_token_for_no_side():
    http = MagicMock()
    http.request_json = AsyncMock(side_effect=[{"id": 1}, {"order": {"id": "x"}}])

    with patch(
        "polymarket_arb.limitless.order_client.build_signed_order",
        return_value=_FAKE_SIGNED_ORDER,
    ) as mock_build:
        client = _make_client(http=http)
        market = _make_market(yes_price=0.4, token_id_no="TOK_NO_456")
        result = await client.place_order(market, side="NO", size_usdc=10.0)

    assert result.status == "live_submitted"
    kwargs = mock_build.call_args.kwargs
    assert kwargs["token_id"] == "TOK_NO_456"
    assert abs(kwargs["price"] - 0.6) < 1e-9  # 1 - 0.4


@pytest.mark.asyncio
async def test_live_submit_logs_http_response_body_on_failure():
    http = MagicMock()
    response = MagicMock(text='{"error":"invalid signature"}')
    error = HttpError("401 Unauthorized", response=response)
    http.request_json = AsyncMock(side_effect=[{"id": 1}, error])

    with (
        patch(
            "polymarket_arb.limitless.order_client.build_signed_order",
            return_value=_FAKE_SIGNED_ORDER,
        ),
        patch("polymarket_arb.limitless.order_client.logger") as mock_logger,
    ):
        client = _make_client(http=http)
        result = await client.place_order(_make_market(), side="YES", size_usdc=10.0)

    assert result.status == "failed"
    mock_logger.error.assert_called_once_with(
        "limitless live submit failed for {}: {} | response: {}",
        "btc-65k",
        error,
        '{"error":"invalid signature"}',
    )


# ─── live submit — preflight failures ────────────────────────────────────────


@pytest.mark.asyncio
async def test_live_submit_fails_if_credentials_missing():
    client = _make_client(key_id=None, key_secret=None)
    result = await client.place_order(_make_market(), side="YES", size_usdc=10.0)
    assert result.status == "failed"
    assert "credentials" in result.error


@pytest.mark.asyncio
async def test_live_submit_fails_if_private_key_missing():
    client = _make_client(private_key=None)
    result = await client.place_order(_make_market(), side="YES", size_usdc=10.0)
    assert result.status == "failed"
    assert "private_key" in result.error


@pytest.mark.asyncio
async def test_live_submit_fails_if_wallet_address_missing():
    client = _make_client(wallet_address=None, private_key=_TEST_PRIVATE_KEY)
    result = await client.place_order(_make_market(), side="YES", size_usdc=10.0)
    assert result.status == "failed"
    assert "wallet_address" in result.error


@pytest.mark.asyncio
async def test_live_submit_fails_if_token_id_missing():
    with patch("polymarket_arb.limitless.order_client.build_signed_order"):
        client = _make_client()
        market = _make_market(token_id_yes="", token_id_no="")
        result = await client.place_order(market, side="YES", size_usdc=10.0)

    assert result.status == "failed"
    assert "token_id_yes" in result.error


@pytest.mark.asyncio
async def test_live_submit_fails_if_exchange_address_missing():
    with (
        patch("polymarket_arb.limitless.order_client.build_signed_order"),
        patch("polymarket_arb.limitless.order_client.logger") as mock_logger,
    ):
        client = _make_client()
        market = _make_market(address="")
        result = await client.place_order(market, side="YES", size_usdc=10.0)

    assert result.status == "failed"
    assert "exchange_address" in result.error
    mock_logger.warning.assert_called_once_with(
        "limitless live submit rejected: exchange_address missing for {}",
        market.slug,
    )


@pytest.mark.asyncio
async def test_live_submit_fails_if_owner_id_unresolvable():
    # wallet_address=None means _get_owner_id() returns None
    client = _make_client(wallet_address=None, private_key=_TEST_PRIVATE_KEY)
    result = await client.place_order(_make_market(), side="YES", size_usdc=10.0)
    assert result.status == "failed"
    assert "wallet_address" in result.error


@pytest.mark.asyncio
async def test_live_submit_fails_when_collateral_approval_fails():
    http = MagicMock()
    http.request_json = AsyncMock(return_value={"id": 1})
    client = _make_client(http=http, collateral_approved=False)
    market = _make_market(address=_VALID_EXCHANGE)

    with (
        patch.object(
            client,
            "_ensure_collateral_approval",
            side_effect=RuntimeError("transaction reverted"),
        ),
        patch("polymarket_arb.limitless.order_client.build_signed_order") as mock_build,
    ):
        result = await client.place_order(market, side="YES", size_usdc=1.0)

    assert result.status == "failed"
    assert "collateral approval failed: transaction reverted" in result.error
    mock_build.assert_not_called()
    assert http.request_json.call_count == 1
