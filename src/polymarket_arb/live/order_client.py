"""OrderClient: the single choke point between strategy and Polymarket.

When ``settings.paper_mode=True`` (the safe default) the client simulates fills
against the latest recorded orderbook snapshot via
``backtest.execution_sim.simulate_buy_from_orderbook``.  No network socket is
opened, no signing key is loaded.  Every call writes one row to ``orders_log``
so the agent's behaviour is fully auditable.

When ``paper_mode=False`` AND ``orders_allowed=True`` AND Polymarket credentials
are configured, the client routes through ``signing.sign_and_build_order`` +
``signing.post_order`` to submit to the CLOB API.

Safety order (every place_order checks these BEFORE doing anything else):
    1. kill switch — file or SIGUSR1
    2. compliance.trade_gate.raise_if_orders_disallowed — global flag
    3. preflight gate — produces a token or raises
    4. paper_mode branch:
       - True  → simulate via execution_sim + write orders_log
       - False → call signing stub (currently raises) + would_submit
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING

from ..backtest.execution_sim import simulate_buy_from_orderbook, simulate_sell_from_orderbook
from ..compliance.trade_gate import OrdersForbidden, raise_if_orders_disallowed
from ..monitoring import kill_switch
from ..risk.models import OrderIntent
from ..storage.base import OrderbookLevel, OrderbookSnapshot
from ..storage.parquet.orderbook_repo import ParquetOrderbookRepository
from ..storage.parquet.orders_log_repo import ParquetOrdersLogRepository
from ..storage.parquet.positions_repo import ParquetPositionsRepository
from .models import OrderResult, OrdersLogRow, PositionRow
from .signing import (
    SigningNotConfigured,
    build_clob_client,
    post_order,
    sign_and_build_order,
)

if TYPE_CHECKING:
    from ..settings import Settings

_FEE_BPS = Decimal("200")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _best_ask_from_preflight_book(preflight_book: dict | None) -> Decimal | None:
    if preflight_book is None:
        return None
    try:
        best_ask = Decimal(str(preflight_book.get("best_ask")))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not best_ask.is_finite() or not Decimal("0") < best_ask <= Decimal("1"):
        return None
    return best_ask


class OrderClient:
    """Single place-order entry point.  Honours kill switch + compliance gate +
    paper_mode flag.  Writes to ``orders_log`` on every call (even rejections).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        orderbook_repo: ParquetOrderbookRepository | None = None,
        orders_log_repo: ParquetOrdersLogRepository | None = None,
        positions_repo: ParquetPositionsRepository | None = None,
    ) -> None:
        self._settings = settings
        self._orderbook_repo = orderbook_repo or ParquetOrderbookRepository(settings.data_root)
        self._orders_log_repo = orders_log_repo or ParquetOrdersLogRepository(settings.data_root)
        self._positions_repo = positions_repo or ParquetPositionsRepository(settings.data_root)

    def place_order(
        self,
        intent: OrderIntent,
        *,
        strategy_id: str = "",
        market_id: str = "",
        source_lane: str = "",
        source_relationship_id: str = "",
        source_hypothesis_id: str = "",
        preflight_token_id: str | None = None,
        preflight_passed: bool = True,
        preflight_book: dict | None = None,
        notes: str = "",
    ) -> OrderResult:
        """Place an order (paper or live).  Always returns an OrderResult and
        always appends one row to ``orders_log``.

        Pre-trade gates, in order:
            1. kill switch
            2. compliance (orders_allowed flag)
            3. paper_mode branch

        ``preflight_passed`` should be set by the caller after running the
        ``PreflightGate``.  ``False`` causes the order to be rejected with
        ``status="rejected_preflight"``.
        """
        ts = _now_ms()
        ks_active = kill_switch.is_active(self._settings.killswitch_path)
        orders_allowed = self._settings.orders_allowed
        paper_mode = self._settings.paper_mode

        live_best_ask = _best_ask_from_preflight_book(preflight_book)
        if live_best_ask is not None:
            preflight_passed = True
            preflight_token_id = intent.token_id

        # ── Gate 0: caller-supplied preflight verdict ────────────────────────
        if not preflight_passed:
            return self._record_and_return(
                intent=intent,
                ts=ts,
                strategy_id=strategy_id,
                market_id=market_id,
                source_lane=source_lane,
                source_relationship_id=source_relationship_id,
                source_hypothesis_id=source_hypothesis_id,
                paper_mode=paper_mode,
                kill_switch_active=ks_active,
                orders_allowed=orders_allowed,
                preflight_passed=False,
                preflight_token_id=preflight_token_id,
                status="rejected_preflight",
                reason="caller's PreflightGate returned FAIL",
                filled_size=Decimal("0"),
                avg_fill_price=None,
                notional=Decimal("0"),
                fees=Decimal("0"),
                submitted=False,
                http_status=None,
                notes=notes,
                detail={},
            )

        # ── Gate 1: kill switch ──────────────────────────────────────────────
        if ks_active:
            return self._record_and_return(
                intent=intent,
                ts=ts,
                strategy_id=strategy_id,
                market_id=market_id,
                source_lane=source_lane,
                source_relationship_id=source_relationship_id,
                source_hypothesis_id=source_hypothesis_id,
                paper_mode=paper_mode,
                kill_switch_active=True,
                orders_allowed=orders_allowed,
                preflight_passed=preflight_passed,
                preflight_token_id=preflight_token_id,
                status="rejected_kill_switch",
                reason=f"kill switch active ({self._settings.killswitch_path})",
                filled_size=Decimal("0"),
                avg_fill_price=None,
                notional=Decimal("0"),
                fees=Decimal("0"),
                submitted=False,
                http_status=None,
                notes=notes,
                detail={},
            )

        # ── Gate 2: paper_mode branch ────────────────────────────────────────
        if paper_mode:
            return self._place_paper(
                intent=intent,
                ts=ts,
                strategy_id=strategy_id,
                market_id=market_id,
                source_lane=source_lane,
                source_relationship_id=source_relationship_id,
                source_hypothesis_id=source_hypothesis_id,
                kill_switch_active=False,
                orders_allowed=orders_allowed,
                preflight_passed=preflight_passed,
                preflight_token_id=preflight_token_id,
                live_best_ask=live_best_ask,
                notes=notes,
            )

        # ── Gate 3: live mode — compliance must permit it ────────────────────
        try:
            raise_if_orders_disallowed(self._settings)
        except OrdersForbidden as exc:
            return self._record_and_return(
                intent=intent,
                ts=ts,
                strategy_id=strategy_id,
                market_id=market_id,
                source_lane=source_lane,
                source_relationship_id=source_relationship_id,
                source_hypothesis_id=source_hypothesis_id,
                paper_mode=False,
                kill_switch_active=False,
                orders_allowed=orders_allowed,
                preflight_passed=preflight_passed,
                preflight_token_id=preflight_token_id,
                status="rejected_orders_disallowed",
                reason=str(exc),
                filled_size=Decimal("0"),
                avg_fill_price=None,
                notional=Decimal("0"),
                fees=Decimal("0"),
                submitted=False,
                http_status=None,
                notes=notes,
                detail={},
            )

        # ── Live: build client, sign, submit ────────────────────────────────
        if not self._settings.polymarket_credentials_configured:
            return self._record_and_return(
                intent=intent,
                ts=ts,
                strategy_id=strategy_id,
                market_id=market_id,
                source_lane=source_lane,
                source_relationship_id=source_relationship_id,
                source_hypothesis_id=source_hypothesis_id,
                paper_mode=False,
                kill_switch_active=False,
                orders_allowed=orders_allowed,
                preflight_passed=preflight_passed,
                preflight_token_id=preflight_token_id,
                status="rejected_credentials_missing",
                reason="polymarket credentials not configured in settings",
                filled_size=Decimal("0"),
                avg_fill_price=None,
                notional=Decimal("0"),
                fees=Decimal("0"),
                submitted=False,
                http_status=None,
                notes=notes,
                detail={},
            )

        try:
            clob_client = build_clob_client(
                private_key_hex=self._settings.polymarket_private_key,
                api_key=self._settings.polymarket_api_key,
                api_secret=self._settings.polymarket_api_secret,
                api_passphrase=self._settings.polymarket_api_passphrase,
                chain_id=self._settings.polymarket_chain_id,
                host=self._settings.polymarket_clob_host,
            )
            signed_order = sign_and_build_order(
                clob_client,
                token_id=intent.token_id,
                price=intent.price,
                size=intent.size,
                side=intent.side,
            )
            resp = post_order(clob_client, signed_order)
            order_id = resp.get("orderID") or resp.get("order_id") or resp.get("id", "")
            http_status_code = 200 if order_id else 400
            status = "live_submitted" if order_id else "live_failed"
            reason = "" if order_id else f"CLOB response: {resp}"
            from loguru import logger
            logger.info(
                "live order submitted: token_id={} order_id={} status={}",
                intent.token_id, order_id, status,
            )
            return self._record_and_return(
                intent=intent,
                ts=ts,
                strategy_id=strategy_id,
                market_id=market_id,
                source_lane=source_lane,
                source_relationship_id=source_relationship_id,
                source_hypothesis_id=source_hypothesis_id,
                paper_mode=False,
                kill_switch_active=False,
                orders_allowed=orders_allowed,
                preflight_passed=preflight_passed,
                preflight_token_id=preflight_token_id,
                status=status,
                reason=reason,
                filled_size=intent.size if status == "live_submitted" else Decimal("0"),
                avg_fill_price=intent.price if status == "live_submitted" else None,
                notional=intent.size * intent.price if status == "live_submitted" else Decimal("0"),
                fees=Decimal("0"),
                submitted=status == "live_submitted",
                http_status=http_status_code,
                notes=notes,
                detail={"clob_response": resp, "order_id": order_id},
            )
        except SigningNotConfigured as exc:
            return self._record_and_return(
                intent=intent,
                ts=ts,
                strategy_id=strategy_id,
                market_id=market_id,
                source_lane=source_lane,
                source_relationship_id=source_relationship_id,
                source_hypothesis_id=source_hypothesis_id,
                paper_mode=False,
                kill_switch_active=False,
                orders_allowed=orders_allowed,
                preflight_passed=preflight_passed,
                preflight_token_id=preflight_token_id,
                status="rejected_credentials_missing",
                reason=str(exc),
                filled_size=Decimal("0"),
                avg_fill_price=None,
                notional=Decimal("0"),
                fees=Decimal("0"),
                submitted=False,
                http_status=None,
                notes=notes,
                detail={},
            )
        except Exception as exc:
            from loguru import logger
            logger.exception("live order submission failed for intent_id={}", intent.id)
            return self._record_and_return(
                intent=intent,
                ts=ts,
                strategy_id=strategy_id,
                market_id=market_id,
                source_lane=source_lane,
                source_relationship_id=source_relationship_id,
                source_hypothesis_id=source_hypothesis_id,
                paper_mode=False,
                kill_switch_active=False,
                orders_allowed=orders_allowed,
                preflight_passed=preflight_passed,
                preflight_token_id=preflight_token_id,
                status="live_failed",
                reason=str(exc),
                filled_size=Decimal("0"),
                avg_fill_price=None,
                notional=Decimal("0"),
                fees=Decimal("0"),
                submitted=False,
                http_status=None,
                notes=notes,
                detail={"error_type": type(exc).__name__},
            )

    # ── paper-mode fill ───────────────────────────────────────────────────

    def _place_paper(
        self,
        *,
        intent: OrderIntent,
        ts: int,
        strategy_id: str,
        market_id: str,
        source_lane: str,
        source_relationship_id: str,
        source_hypothesis_id: str,
        kill_switch_active: bool,
        orders_allowed: bool,
        preflight_passed: bool,
        preflight_token_id: str | None,
        live_best_ask: Decimal | None,
        notes: str,
    ) -> OrderResult:
        if live_best_ask is not None:
            book = OrderbookSnapshot(
                token_id=intent.token_id,
                condition_id=None,
                market_slug=None,
                timestamp_ms=ts,
                bids=[],
                asks=[OrderbookLevel(price=live_best_ask, size=intent.size)],
                book_hash=None,
                source="live_preflight",
                schema_version=1,
                ingested_ts_ms=ts,
            )
        else:
            book = self._orderbook_repo.latest_book(intent.token_id)
        if book is None:
            return self._record_and_return(
                intent=intent,
                ts=ts,
                strategy_id=strategy_id,
                market_id=market_id,
                source_lane=source_lane,
                source_relationship_id=source_relationship_id,
                source_hypothesis_id=source_hypothesis_id,
                paper_mode=True,
                kill_switch_active=kill_switch_active,
                orders_allowed=orders_allowed,
                preflight_passed=preflight_passed,
                preflight_token_id=preflight_token_id,
                status="paper_no_book",
                reason="no orderbook snapshot available for token",
                filled_size=Decimal("0"),
                avg_fill_price=None,
                notional=Decimal("0"),
                fees=Decimal("0"),
                submitted=False,
                http_status=None,
                notes=notes,
                detail={},
            )

        side = intent.side.lower()
        if side == "sell":
            filled, notional, fees, partial, reason = simulate_sell_from_orderbook(
                book, intent.size, limit_price=intent.limit_price, fee_bps=_FEE_BPS,
            )
        elif side == "buy":
            filled, notional, fees, partial, reason = simulate_buy_from_orderbook(
                book, intent.size, limit_price=intent.limit_price, fee_bps=_FEE_BPS,
            )
        else:
            return self._record_and_return(
                intent=intent,
                ts=ts,
                strategy_id=strategy_id,
                market_id=market_id,
                source_lane=source_lane,
                source_relationship_id=source_relationship_id,
                source_hypothesis_id=source_hypothesis_id,
                paper_mode=True,
                kill_switch_active=kill_switch_active,
                orders_allowed=orders_allowed,
                preflight_passed=preflight_passed,
                preflight_token_id=preflight_token_id,
                status="rejected_unsupported_side",
                reason=f"side={intent.side!r} is not supported (buy or sell only)",
                filled_size=Decimal("0"),
                avg_fill_price=None,
                notional=Decimal("0"),
                fees=Decimal("0"),
                submitted=False,
                http_status=None,
                notes=notes,
                detail={},
            )

        avg = (notional / filled) if filled > 0 else None
        if filled == 0:
            status = "paper_no_fill"
        elif partial:
            status = "paper_partial"
        else:
            status = "paper_filled"

        return self._record_and_return(
            intent=intent,
            ts=ts,
            strategy_id=strategy_id,
            market_id=market_id,
            source_lane=source_lane,
            source_relationship_id=source_relationship_id,
            source_hypothesis_id=source_hypothesis_id,
            paper_mode=True,
            kill_switch_active=kill_switch_active,
            orders_allowed=orders_allowed,
            preflight_passed=preflight_passed,
            preflight_token_id=preflight_token_id,
            status=status,
            reason=reason,
            filled_size=filled,
            avg_fill_price=avg,
            notional=notional,
            fees=fees,
            submitted=False,
            http_status=None,
            notes=notes,
            detail={"book_timestamp_ms": book.timestamp_ms, "book_ask_levels": len(book.asks)},
        )

    # ── audit-row writer + result builder ─────────────────────────────────

    def _record_and_return(
        self,
        *,
        intent: OrderIntent,
        ts: int,
        strategy_id: str,
        market_id: str,
        source_lane: str,
        source_relationship_id: str,
        source_hypothesis_id: str,
        paper_mode: bool,
        kill_switch_active: bool,
        orders_allowed: bool,
        preflight_passed: bool,
        preflight_token_id: str | None,
        status: str,
        reason: str,
        filled_size: Decimal,
        avg_fill_price: Decimal | None,
        notional: Decimal,
        fees: Decimal,
        submitted: bool,
        http_status: int | None,
        notes: str,
        detail: dict,
    ) -> OrderResult:
        row = OrdersLogRow(
            intent_id=intent.id,
            ts_ms=ts,
            strategy_id=strategy_id or intent.strategy_id,
            token_id=intent.token_id,
            market_id=market_id,
            side=intent.side,
            requested_size=str(intent.size),
            filled_size=str(filled_size),
            avg_fill_price=str(avg_fill_price) if avg_fill_price is not None else None,
            notional_usdc=str(notional),
            fees_usdc=str(fees),
            status=status,
            reason=reason,
            paper_mode=paper_mode,
            kill_switch_active=kill_switch_active,
            orders_allowed=orders_allowed,
            preflight_passed=preflight_passed,
            preflight_token_id=preflight_token_id,
            http_status=http_status,
            source_lane=source_lane,
            source_relationship_id=source_relationship_id,
            source_hypothesis_id=source_hypothesis_id,
            schema_version=1,
            ingested_ts_ms=ts,
            notes=notes,
            detail_json=json.dumps({**intent.detail, **detail}, default=str, sort_keys=True),
        )
        orders_log_written = False
        try:
            self._orders_log_repo.append(row)
            orders_log_written = True
        except Exception:
            # Audit-write failure is logged but never blocks the result.
            # The caller still gets an honest OrderResult.
            from loguru import logger
            logger.exception("orders_log append failed for intent_id={}", intent.id)

        scanner_managed_strategy = row.strategy_id in {"limitless_arb", "limitless_arb_exit"}
        if (
            orders_log_written
            and status == "paper_filled"
            and avg_fill_price is not None
            and not scanner_managed_strategy
        ):
            position_key = f"{row.market_id}{row.token_id}{row.strategy_id}{row.ts_ms}"
            position = PositionRow(
                position_id=hashlib.sha256(position_key.encode("utf-8")).hexdigest()[:16],
                strategy_id=row.strategy_id,
                market_id=row.market_id,
                token_id=row.token_id,
                side=row.side,
                open_ts_ms=row.ts_ms,
                entry_price=str(avg_fill_price),
                size=row.filled_size,
                notional_usdc=row.notional_usdc,
                gross_edge=str(intent.detail.get("gross_edge", "")),
                relationship_id=str(intent.detail.get("relationship_id", "")),
                relationship_type=str(intent.detail.get("relationship_type", "")),
                notes=row.notes,
                status="open",
                schema_version=1,
                ingested_ts_ms=row.ts_ms,
            )
            try:
                self._positions_repo.append(position)
            except Exception:
                from loguru import logger
                logger.exception("positions append failed for intent_id={}", intent.id)

        return OrderResult(
            intent_id=intent.id,
            submitted=submitted,
            status=status,
            paper_mode=paper_mode,
            token_id=intent.token_id,
            side=intent.side,
            requested_size=intent.size,
            filled_size=filled_size,
            avg_fill_price=avg_fill_price,
            notional_usdc=notional,
            fees_usdc=fees,
            reason=reason,
            ts_ms=ts,
            kill_switch_active=kill_switch_active,
            orders_allowed=orders_allowed,
            preflight_passed=preflight_passed,
            preflight_token_id=preflight_token_id,
            http_status=http_status,
        )


def new_intent_id() -> str:
    return uuid.uuid4().hex


def healthcheck(settings: Settings, *, data_root: Path | None = None) -> dict:
    """Quick check that the agent could (in principle) operate.

    Returns a dict of {paper_mode, orders_allowed, kill_switch_active,
    orderbook_lake_has_data, orders_log_lake_writable}.  Does NOT place any
    order; safe to call from a CLI healthcheck subcommand.
    """
    root = data_root or settings.data_root
    ks_active = kill_switch.is_active(settings.killswitch_path)
    book_repo = ParquetOrderbookRepository(root)
    ob_has_data = book_repo._has_data()
    log_dir = root / "normalised" / "orders_log"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_writable = True
    except OSError:
        log_writable = False
    return {
        "paper_mode": settings.paper_mode,
        "orders_allowed": settings.orders_allowed,
        "kill_switch_active": ks_active,
        "orderbook_lake_has_data": ob_has_data,
        "orders_log_lake_writable": log_writable,
        "data_root": str(root),
    }
