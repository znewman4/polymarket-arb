"""OrderClient: the single choke point between strategy and Polymarket.

When ``settings.paper_mode=True`` (the safe default) the client simulates fills
against the latest recorded orderbook snapshot via
``backtest.execution_sim.simulate_buy_from_orderbook``.  No network socket is
opened, no signing key is loaded.  Every call writes one row to ``orders_log``
so the agent's behaviour is fully auditable.

When ``paper_mode=False`` AND ``orders_allowed=True`` AND a private key is
configured, the client would route through ``signing.sign_order_intent`` and
submit to the CLOB API — but that path is a deferred stub today.

Safety order (every place_order checks these BEFORE doing anything else):
    1. kill switch — file or SIGUSR1
    2. compliance.trade_gate.raise_if_orders_disallowed — global flag
    3. preflight gate — produces a token or raises
    4. paper_mode branch:
       - True  → simulate via execution_sim + write orders_log
       - False → call signing stub (currently raises) + would_submit
"""

from __future__ import annotations

import json
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from ..backtest.execution_sim import simulate_buy_from_orderbook
from ..compliance.trade_gate import OrdersForbidden, raise_if_orders_disallowed
from ..monitoring import kill_switch
from ..risk.models import OrderIntent
from ..storage.parquet.orderbook_repo import ParquetOrderbookRepository
from ..storage.parquet.orders_log_repo import ParquetOrdersLogRepository
from .models import OrderResult, OrdersLogRow
from .signing import SigningNotConfigured, SigningStubError, sign_order_intent

if TYPE_CHECKING:
    from ..settings import Settings


def _now_ms() -> int:
    return int(time.time() * 1000)


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
        signing_key_hex: str | None = None,
    ) -> None:
        self._settings = settings
        self._orderbook_repo = orderbook_repo or ParquetOrderbookRepository(settings.data_root)
        self._orders_log_repo = orders_log_repo or ParquetOrdersLogRepository(settings.data_root)
        self._signing_key_hex = signing_key_hex

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

        # ── Live: signing path (deferred stub) ───────────────────────────────
        # We get here ONLY if paper_mode=False AND orders_allowed=True.
        # The signing stub raises by design — flipping to live trading is the
        # next phase and requires explicit opt-in.
        try:
            sign_order_intent(intent, self._signing_key_hex)
        except (SigningNotConfigured, SigningStubError) as exc:
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
                status="rejected_live_signing_not_ready",
                reason=str(exc),
                filled_size=Decimal("0"),
                avg_fill_price=None,
                notional=Decimal("0"),
                fees=Decimal("0"),
                submitted=False,
                http_status=None,
                notes=notes,
                detail={"signing_error": type(exc).__name__},
            )
        # Unreachable in this phase — signing always raises.
        raise RuntimeError("live submit path reached without a signed intent; this should be unreachable.")

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
        notes: str,
    ) -> OrderResult:
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

        if intent.side.lower() != "buy":
            # We only support buys on Polymarket; sells are a future feature.
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
                reason=f"side={intent.side!r} is not supported (buy-only)",
                filled_size=Decimal("0"),
                avg_fill_price=None,
                notional=Decimal("0"),
                fees=Decimal("0"),
                submitted=False,
                http_status=None,
                notes=notes,
                detail={},
            )

        filled, notional, fees, partial, reason = simulate_buy_from_orderbook(
            book, intent.size, limit_price=intent.limit_price, fee_bps=Decimal("0"),
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
            detail_json=json.dumps(detail, default=str, sort_keys=True),
        )
        try:
            self._orders_log_repo.append(row)
        except Exception:
            # Audit-write failure is logged but never blocks the result.
            # The caller still gets an honest OrderResult.
            from loguru import logger
            logger.exception("orders_log append failed for intent_id={}", intent.id)

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
