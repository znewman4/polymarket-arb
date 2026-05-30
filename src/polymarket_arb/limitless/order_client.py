"""LimitlessOrderClient — place orders on Limitless Exchange.

Mirrors the safety structure of live/order_client.py:
  1. Kill switch check
  2. paper_mode branch → simulate fill, no network call
  3. Live: resolve owner_id → EIP-712 sign order → POST /orders

In paper mode, no credentials are needed and no network is touched.
In live mode, key_id, key_secret, wallet_address, and private_key must all be
provided (loaded from AWS Secrets Manager by the CLI).

POST /orders payload (per Limitless TypeScript SDK + API, verified 2026-05):
  {
    "order":       <eip712_signed_order>,   # from eip712.build_signed_order()
    "orderType":   "GTC",
    "marketSlug":  "<slug>",
    "ownerId":     <int>,
  }

The EIP-712 signed order fields are documented in limitless/eip712.py.

References:
  https://docs.limitless.exchange/developers/authentication
  https://github.com/guzus/dr-manhattan/blob/main/dr_manhattan/exchanges/limitless.py
"""

from __future__ import annotations

import asyncio
import json
import uuid
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

from loguru import logger
from web3 import Web3

from ..http.client import AsyncHttpClient, HttpError
from ..monitoring import kill_switch
from .eip712 import build_signed_order
from .models import LimitlessMarketEntry, LimitlessOrderResult
from .signing import sign_request

_ORDERS_PATH = "/orders"
_PROFILES_PATH = "/profiles"
_BASE_MAINNET_RPC_URL = "https://mainnet.base.org"
_BASE_USDC_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
_USDC_SCALE = Decimal("1000000")
_MAX_UINT256 = (1 << 256) - 1
_ERC20_APPROVAL_ABI = [
    {
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


class LimitlessOrderClient:
    """Place orders on Limitless Exchange.

    Paper mode simulates a fill at the current market mid-price with no
    network contact.  Live mode builds an EIP-712 signed order and POSTs it.
    """

    def __init__(
        self,
        *,
        limitless_host: str,
        http: AsyncHttpClient,
        kill_switch_path: Path,
        paper_mode: bool = True,
        key_id: str | None = None,
        key_secret: str | None = None,
        wallet_address: str | None = None,
        private_key: str | None = None,
    ) -> None:
        self._host = limitless_host.rstrip("/")
        self._http = http
        self._ks_path = kill_switch_path
        self._paper_mode = paper_mode
        self._key_id = key_id
        self._key_secret = key_secret
        self._wallet_address = wallet_address
        self._private_key = private_key
        self._owner_id: int | None = None  # cached after first fetch
        self._approved: set[str] = set()

    async def place_order(
        self,
        market: LimitlessMarketEntry,
        *,
        side: str,
        size_usdc: float,
    ) -> LimitlessOrderResult:
        """Place a YES or NO order on a Limitless market.

        Args:
            market:    The target market.
            side:      "YES" or "NO".
            size_usdc: Order size in USDC.

        Returns:
            LimitlessOrderResult with status and fill details.
        """
        side = side.upper()
        price = market.yes_price if side == "YES" else (1.0 - market.yes_price)

        if kill_switch.is_active(self._ks_path):
            logger.warning("limitless order rejected: kill switch active ({})", market.slug)
            return LimitlessOrderResult(
                status="rejected_kill_switch",
                order_id=None,
                side=side,
                price=price,
                size_usdc=size_usdc,
                market_slug=market.slug,
                error="kill switch active",
            )

        if self._paper_mode:
            return self._paper_fill(market=market, side=side, price=price, size_usdc=size_usdc)

        return await self._live_submit(market=market, side=side, price=price, size_usdc=size_usdc)

    def _paper_fill(
        self,
        *,
        market: LimitlessMarketEntry,
        side: str,
        price: float,
        size_usdc: float,
    ) -> LimitlessOrderResult:
        logger.info(
            "limitless paper fill: {} {} @ {:.4f} ({})",
            side, size_usdc, price, market.slug,
        )
        return LimitlessOrderResult(
            status="paper_filled",
            order_id=uuid.uuid4().hex,
            side=side,
            price=price,
            size_usdc=size_usdc,
            market_slug=market.slug,
            error=None,
        )

    async def _get_owner_id(self) -> int | None:
        """Return the numeric Limitless profile ID for our wallet, fetching once.

        Calls GET /profiles/public/{wallet_address} with HMAC auth and caches
        the result.  Returns None if wallet_address is not configured or the
        fetch fails, which will cause the order to be rejected with a clear
        error.
        """
        if self._owner_id is not None:
            return self._owner_id

        if not self._wallet_address:
            logger.warning("limitless: wallet_address not configured; cannot resolve owner_id")
            return None

        if not self._key_id or not self._key_secret:
            logger.warning("limitless: credentials not configured; cannot resolve owner_id")
            return None

        path = f"{_PROFILES_PATH}/public/{self._wallet_address}"
        auth_headers = sign_request(
            key_id=self._key_id,
            key_secret=self._key_secret,
            method="GET",
            path=path,
            body="",
        )
        try:
            resp = await self._http.request_json(
                "GET",
                f"{self._host}{path}",
                headers=auth_headers,
            )
            if isinstance(resp, dict) and "id" in resp:
                self._owner_id = int(resp["id"])
                logger.info("limitless: resolved owner_id={}", self._owner_id)
                return self._owner_id
            logger.error("limitless: profile response missing 'id' field: {!r}", resp)
            return None
        except HttpError as exc:
            logger.error("limitless: failed to fetch owner_id from {}: {}", path, exc)
            return None

    def _ensure_collateral_approval(self, exchange_address: str, amount_usdc: float) -> None:
        """Ensure the exchange may spend sufficient Base USDC for this order."""
        if exchange_address in self._approved:
            return
        if not self._wallet_address or not self._private_key:
            raise RuntimeError("wallet credentials not configured for collateral approval")

        owner = Web3.to_checksum_address(self._wallet_address)
        spender = Web3.to_checksum_address(exchange_address)
        if spender in self._approved:
            return

        required_allowance = int(
            (Decimal(str(amount_usdc)) * _USDC_SCALE).to_integral_value(rounding=ROUND_CEILING)
        )
        w3 = Web3(Web3.HTTPProvider(_BASE_MAINNET_RPC_URL, request_kwargs={"timeout": 10}))
        usdc = w3.eth.contract(
            address=Web3.to_checksum_address(_BASE_USDC_ADDRESS),
            abi=_ERC20_APPROVAL_ABI,
        )
        allowance = int(usdc.functions.allowance(owner, spender).call())

        if allowance < required_allowance:
            transaction = usdc.functions.approve(spender, _MAX_UINT256).build_transaction({
                "from": owner,
                "chainId": w3.eth.chain_id,
                "nonce": w3.eth.get_transaction_count(owner, "pending"),
                "gas": 100_000,
                "gasPrice": w3.eth.gas_price,
            })
            signed = w3.eth.account.sign_transaction(transaction, private_key=self._private_key)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            if int(receipt.get("status", 0)) != 1:
                raise RuntimeError(f"USDC approve transaction reverted: {w3.to_hex(tx_hash)}")
            logger.info(
                "limitless: Base USDC approval confirmed for exchange={} tx_hash={}",
                spender,
                w3.to_hex(tx_hash),
            )

        self._approved.add(spender)

    async def _live_submit(
        self,
        *,
        market: LimitlessMarketEntry,
        side: str,
        price: float,
        size_usdc: float,
    ) -> LimitlessOrderResult:
        # --- preflight checks ---
        if not self._key_id or not self._key_secret:
            return LimitlessOrderResult(
                status="failed", order_id=None, side=side, price=price,
                size_usdc=size_usdc, market_slug=market.slug,
                error="limitless credentials not configured",
            )

        if not self._private_key:
            return LimitlessOrderResult(
                status="failed", order_id=None, side=side, price=price,
                size_usdc=size_usdc, market_slug=market.slug,
                error="private_key not configured for EIP-712 signing",
            )

        if not self._wallet_address:
            return LimitlessOrderResult(
                status="failed", order_id=None, side=side, price=price,
                size_usdc=size_usdc, market_slug=market.slug,
                error="wallet_address not configured",
            )

        token_id = market.token_id_yes if side == "YES" else market.token_id_no
        if not token_id:
            return LimitlessOrderResult(
                status="failed", order_id=None, side=side, price=price,
                size_usdc=size_usdc, market_slug=market.slug,
                error=f"token_id_{side.lower()} not populated on market {market.slug!r}",
            )

        if not market.address:
            logger.warning(
                "limitless live submit rejected: exchange_address missing for {}",
                market.slug,
            )
            return LimitlessOrderResult(
                status="failed", order_id=None, side=side, price=price,
                size_usdc=size_usdc, market_slug=market.slug,
                error=f"exchange_address (market.address) not populated for {market.slug!r}",
            )

        owner_id = await self._get_owner_id()
        if owner_id is None:
            return LimitlessOrderResult(
                status="failed", order_id=None, side=side, price=price,
                size_usdc=size_usdc, market_slug=market.slug,
                error="could not resolve owner_id (wallet_address not configured or profile fetch failed)",
            )

        was_already_approved = market.address in self._approved
        try:
            await asyncio.to_thread(
                self._ensure_collateral_approval,
                market.address,
                size_usdc,
            )
        except Exception as exc:
            logger.exception("limitless collateral approval failed for {}", market.slug)
            return LimitlessOrderResult(
                status="failed", order_id=None, side=side, price=price,
                size_usdc=size_usdc, market_slug=market.slug,
                error=f"collateral approval failed: {exc}",
            )

        if not was_already_approved:
            logger.info("limitless: new approval confirmed, waiting 3s before order submission")
            await asyncio.sleep(3)

        # --- build EIP-712 signed order ---
        signed_order = build_signed_order(
            token_id=token_id,
            price=price,
            size_usdc=size_usdc,
            side="BUY",
            order_type="GTC",
            exchange_address=market.address,
            private_key=self._private_key,
            wallet_address=self._wallet_address,
        )

        payload = {
            "order":       signed_order,
            "orderType":   "GTC",
            "marketSlug":  market.slug,
            "ownerId":     owner_id,
        }
        body_str = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        auth_headers = sign_request(
            key_id=self._key_id,
            key_secret=self._key_secret,
            method="POST",
            path=_ORDERS_PATH,
            body=body_str,
        )

        try:
            resp = await self._http.request_json(
                "POST",
                f"{self._host}{_ORDERS_PATH}",
                content=body_str,
                headers={**auth_headers, "Content-Type": "application/json"},
            )
            order_id = None
            if isinstance(resp, dict):
                order_data = resp.get("order") or {}
                order_id = (
                    order_data.get("id")
                    or resp.get("orderId")
                    or resp.get("id")
                    or resp.get("order_id")
                )
            logger.info(
                "limitless live submit: {} {} @ {:.4f} ({}) order_id={}",
                side, size_usdc, price, market.slug, order_id,
            )
            return LimitlessOrderResult(
                status="live_submitted",
                order_id=str(order_id) if order_id else None,
                side=side,
                price=price,
                size_usdc=size_usdc,
                market_slug=market.slug,
                error=None,
            )
        except HttpError as exc:
            response_body = getattr(getattr(exc, "response", None), "text", "")
            logger.error(
                "limitless live submit failed for {}: {} | response: {}",
                market.slug,
                exc,
                response_body,
            )
            return LimitlessOrderResult(
                status="failed",
                order_id=None,
                side=side,
                price=price,
                size_usdc=size_usdc,
                market_slug=market.slug,
                error=str(exc),
            )
