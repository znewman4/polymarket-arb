"""LimitlessOrderClient — place orders on Limitless Exchange.

Mirrors the safety structure of live/order_client.py:
  1. Kill switch check
  2. paper_mode branch → simulate fill, no network call
  3. Live: resolve owner_id, sign + POST /orders

In paper mode, no credentials are needed and no network is touched.
In live mode, key_id + key_secret + wallet_address must be provided (loaded
from AWS Secrets Manager by the CLI).

Order body format (per Limitless TypeScript SDK, verified 2026-05):
  tokenId     — YES or NO token ID from market.token_id_yes / token_id_no
  price       — decimal string
  size        — decimal string (USDC notional)
  side        — "BUY" (always buying a position in our arb strategy)
  orderType   — "GTC" (Good Till Cancelled)
  marketSlug  — market slug
  ownerId     — numeric profile ID, fetched once from GET /profiles/{wallet}

Reference: https://docs.limitless.exchange/developers/authentication
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from ..http.client import AsyncHttpClient, HttpError
from ..monitoring import kill_switch
from .models import LimitlessMarketEntry, LimitlessOrderResult
from .signing import sign_request

_ORDERS_PATH = "/orders"
_PROFILES_PATH = "/profiles"


class LimitlessOrderClient:
    """Place orders on Limitless Exchange.

    Paper mode simulates a fill at the current market mid-price with no
    network contact.  Live mode POSTs a signed order to the Limitless API.
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
    ) -> None:
        self._host = limitless_host.rstrip("/")
        self._http = http
        self._ks_path = kill_switch_path
        self._paper_mode = paper_mode
        self._key_id = key_id
        self._key_secret = key_secret
        self._wallet_address = wallet_address
        self._owner_id: int | None = None  # cached after first fetch

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

        Calls GET /profiles/{wallet_address} with HMAC auth and caches the
        result.  Returns None if wallet_address is not configured or the fetch
        fails, which will cause the order to be rejected with a clear error.
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

    async def _live_submit(
        self,
        *,
        market: LimitlessMarketEntry,
        side: str,
        price: float,
        size_usdc: float,
    ) -> LimitlessOrderResult:
        if not self._key_id or not self._key_secret:
            return LimitlessOrderResult(
                status="failed",
                order_id=None,
                side=side,
                price=price,
                size_usdc=size_usdc,
                market_slug=market.slug,
                error="limitless credentials not configured",
            )

        owner_id = await self._get_owner_id()
        if owner_id is None:
            return LimitlessOrderResult(
                status="failed",
                order_id=None,
                side=side,
                price=price,
                size_usdc=size_usdc,
                market_slug=market.slug,
                error="could not resolve owner_id (wallet_address not configured or profile fetch failed)",
            )

        token_id = market.token_id_yes if side == "YES" else market.token_id_no
        if not token_id:
            return LimitlessOrderResult(
                status="failed",
                order_id=None,
                side=side,
                price=price,
                size_usdc=size_usdc,
                market_slug=market.slug,
                error=f"token_id_{side.lower()} not populated on market {market.slug!r}",
            )

        body_dict: dict[str, Any] = {
            "tokenId": token_id,
            "price": str(round(price, 6)),
            "size": str(round(size_usdc, 6)),
            "side": "BUY",
            "orderType": "GTC",
            "marketSlug": market.slug,
            "ownerId": owner_id,
        }
        body_str = json.dumps(body_dict, separators=(",", ":"), sort_keys=True)
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
                order = resp.get("order") or {}
                order_id = (
                    order.get("id")
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
            logger.error("limitless live submit failed for {}: {}", market.slug, exc)
            return LimitlessOrderResult(
                status="failed",
                order_id=None,
                side=side,
                price=price,
                size_usdc=size_usdc,
                market_slug=market.slug,
                error=str(exc),
            )
