"""LimitlessOrderClient — place orders on Limitless Exchange.

Mirrors the safety structure of live/order_client.py:
  1. Kill switch check
  2. paper_mode branch → simulate fill, no network call
  3. Live: sign + POST /orders

In paper mode, no credentials are needed and no network is touched.
In live mode, key_id + key_secret must be provided (loaded from AWS Secrets
Manager by the CLI).

NOTE: The live order request format (field names, endpoint path) is based on
Limitless API docs as of 2026-05.  Verify against the latest docs at
https://docs.limitless.exchange/ before flipping paper_mode off.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from ..http.client import AsyncHttpClient, HttpError
from ..monitoring import kill_switch
from .models import LimitlessMarketEntry, LimitlessOrderResult
from .signing import sign_request

_ORDERS_PATH = "/orders"


def _now_ms() -> int:
    return int(time.time() * 1000)


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
    ) -> None:
        self._host = limitless_host.rstrip("/")
        self._http = http
        self._ks_path = kill_switch_path
        self._paper_mode = paper_mode
        self._key_id = key_id
        self._key_secret = key_secret

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

        body_dict: dict[str, Any] = {
            "marketAddress": market.address,
            "side": "BUY",
            "outcome": side,
            "amount": str(round(size_usdc, 6)),
            "price": str(round(price, 6)),
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
                order_id = resp.get("orderId") or resp.get("id") or resp.get("order_id")
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
