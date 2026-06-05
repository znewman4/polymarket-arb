"""Limitless x Polymarket cross-market arbitrage scanner and executor.

Arb mechanic:
  Both markets cover the same event.  If poly_yes > lim_yes, a risk-free
  arb exists: buy YES on Limitless (cheaper) and buy NO on Polymarket (cheaper).
  Edge = poly_yes - lim_yes = guaranteed profit per share before fees.

  Example: lim_yes=0.40, poly_yes=0.55
    Buy YES on Limitless at $0.40 + Buy NO on Polymarket at $0.45 = $0.85 cost
    Guaranteed payout: $1.00 — profit = $0.15 = poly_yes - lim_yes

Direction assumption:
  We assume matched markets are directionally aligned (both ask the same yes/no
  question).  The fuzzy-match step enforces this to the degree that similar
  question text implies the same event direction.  Verify manually before
  flipping --execute on a new pair.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

from loguru import logger
from rapidfuzz import fuzz

from ..live.models import OrdersLogRow, PositionRow
from ..live.order_client import OrderClient
from ..risk.models import OrderIntent
from .models import (
    ArbMatch,
    LimitlessArbPosition,
    LimitlessMarketEntry,
    LimitlessOrderResult,
    PolyMarketEntry,
)

if TYPE_CHECKING:
    from .order_client import LimitlessOrderClient


_PUNCT = re.compile(r"[^\w\s]")
_CAPITALISED_WORD = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:['-][A-Za-z0-9]+)*\b")
_TITLE_PREFIX_WORDS = frozenset({
    "a",
    "an",
    "are",
    "can",
    "does",
    "how",
    "is",
    "new",
    "the",
    "what",
    "when",
    "where",
    "which",
    "who",
    "will",
})
_EXIT_FEE_BPS = Decimal("200")


def _normalise(text: str) -> str:
    text = text.lower()
    text = _PUNCT.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _first_named_token(text: str) -> str | None:
    """Return the first capitalised token that is not question boilerplate."""
    for match in _CAPITALISED_WORD.finditer(text):
        token = match.group(0)
        if token.casefold() not in _TITLE_PREFIX_WORDS:
            return token.casefold()
    return None


def _poly_from_raw(raw: dict) -> PolyMarketEntry | None:
    """Parse a raw Gamma market dict into a PolyMarketEntry."""
    question = raw.get("question", "")
    condition_id = raw.get("conditionId", raw.get("condition_id", ""))

    outcomes_raw = raw.get("outcomes", "[]")
    prices_raw = raw.get("outcomePrices", "[]")
    if isinstance(outcomes_raw, str):
        try:
            outcomes = json.loads(outcomes_raw)
            prices = json.loads(prices_raw)
        except Exception:
            return None
    else:
        outcomes = list(outcomes_raw)
        prices = list(prices_raw)

    if not outcomes or not prices or len(outcomes) != len(prices):
        return None

    yes_idx = next((i for i, o in enumerate(outcomes) if str(o).lower() == "yes"), None)
    if yes_idx is None:
        return None

    try:
        yes_price = float(prices[yes_idx])
    except (ValueError, TypeError):
        return None

    if yes_price <= 0.0 or yes_price >= 1.0:
        return None

    # Extract CLOB token IDs from supported raw payload shapes. Gamma currently
    # exposes stringified clobTokenIds aligned with outcomes.
    tokens = raw.get("tokens")
    token_id_yes = ""
    token_id_no = ""
    if isinstance(tokens, list):
        for t in tokens:
            if isinstance(t, dict):
                outcome = str(t.get("outcome", "")).lower()
                tid = t.get("token_id", t.get("tokenId", t.get("tokenID", "")))
                if outcome == "yes":
                    token_id_yes = str(tid)
                elif outcome == "no":
                    token_id_no = str(tid)
    elif isinstance(tokens, dict):
        for outcome, tid in tokens.items():
            if str(outcome).lower() == "yes":
                token_id_yes = str(tid)
            elif str(outcome).lower() == "no":
                token_id_no = str(tid)

    clob_token_ids_raw = raw.get("clobTokenIds", raw.get("clob_token_ids"))
    if isinstance(clob_token_ids_raw, str):
        try:
            clob_token_ids = json.loads(clob_token_ids_raw)
        except Exception:
            clob_token_ids = []
    else:
        clob_token_ids = clob_token_ids_raw or []
    if isinstance(clob_token_ids, list):
        for outcome, tid in zip(outcomes, clob_token_ids, strict=False):
            if str(outcome).lower() == "yes" and not token_id_yes:
                token_id_yes = str(tid)
            elif str(outcome).lower() == "no" and not token_id_no:
                token_id_no = str(tid)

    if not token_id_no:
        tokens_type = type(tokens).__name__
        clob_type = type(clob_token_ids_raw).__name__
        tokens_sample = repr(tokens)[:200] if tokens is not None else "None"
        clob_sample = repr(clob_token_ids_raw)[:200] if clob_token_ids_raw is not None else "None"
        if not token_id_yes:
            logger.warning(
                "poly parser: no token IDs for {}; "
                "tokens type={} sample={}; clobTokenIds type={} sample={}",
                condition_id, tokens_type, tokens_sample, clob_type, clob_sample,
            )
        else:
            logger.debug(
                "poly parser: NO token ID missing for {}; "
                "tokens type={} sample={}; clobTokenIds type={} sample={}",
                condition_id, tokens_type, tokens_sample, clob_type, clob_sample,
            )

    return PolyMarketEntry(
        condition_id=condition_id,
        token_id_yes=token_id_yes,
        token_id_no=token_id_no,
        question=question,
        yes_price=yes_price,
    )


def match_markets(
    limitless: list[LimitlessMarketEntry],
    poly_raw: list[dict],
    threshold: float = 0.82,
) -> list[ArbMatch]:
    """Fuzzy-match Limitless markets to Polymarket markets by question text.

    Uses rapidfuzz token_sort_ratio so word-order differences don't penalise
    the score. Candidates with different first meaningful capitalised tokens
    are rejected before selecting the best fuzzy match. Returns one ArbMatch
    per Limitless market where the best remaining match exceeds the threshold.
    Computes the raw arb gap only; callers must pass the matches through
    compute_arb with their configured tolerance before displaying or executing.
    """
    poly_entries = []
    for raw in poly_raw:
        entry = _poly_from_raw(raw)
        if entry is not None:
            poly_entries.append((_normalise(entry.question), entry))

    matches: list[ArbMatch] = []
    for lim in limitless:
        lim_norm = _normalise(lim.title)
        lim_name = _first_named_token(lim.title)
        best_score = 0.0
        best_poly: PolyMarketEntry | None = None
        for p_norm, p in poly_entries:
            score = fuzz.token_sort_ratio(lim_norm, p_norm) / 100.0
            poly_name = _first_named_token(p.question)
            if lim_name is not None and poly_name is not None and lim_name != poly_name:
                continue
            if score > best_score:
                best_score = score
                best_poly = p
        if best_poly is not None and best_score >= threshold:
            arb_gap = round(best_poly.yes_price - lim.yes_price, 6)
            if arb_gap > 0.30:
                continue  # almost certainly a false positive (price mismatch, not real arb)
            matches.append(ArbMatch(
                limitless=lim,
                poly=best_poly,
                similarity=round(best_score, 4),
                arb_gap=arb_gap,
                status="PENDING",
            ))

    logger.info("arb_scanner: {} matched pairs (threshold={})", len(matches), threshold)
    return matches


def compute_arb(
    matches: list[ArbMatch],
    tolerance: float = 0.02,
) -> list[ArbMatch]:
    """Re-compute arb status with a specific tolerance and return updated matches."""
    return [
        ArbMatch(
            limitless=m.limitless,
            poly=m.poly,
            similarity=m.similarity,
            arb_gap=m.arb_gap,
            status=_arb_status(m.arb_gap, tolerance),
        )
        for m in matches
    ]


def _arb_status(arb_gap: float, tolerance: float) -> str:
    if arb_gap > tolerance:
        return "ARB_OPPORTUNITY"
    if arb_gap < -tolerance:
        return "OVER_ROUND"
    return "EFFICIENT"


async def _fetch_live_poly_best_ask(token_id: str) -> float | None:
    """Fetch best ask price from Polymarket CLOB API directly."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                "https://clob.polymarket.com/book",
                params={"token_id": token_id},
            )
            response.raise_for_status()
            data = response.json()
            asks = data.get("asks", [])
            if asks:
                return float(asks[0]["price"])
    except Exception as exc:
        logger.warning("failed to fetch live poly book for {}: {}", token_id, exc)
    return None


async def execute_arb(
    match: ArbMatch,
    *,
    lim_client: LimitlessOrderClient,
    poly_client: OrderClient,
    stake_usdc: float,
    min_net_edge: float,
    orders_log_repo=None,
    positions_repo=None,
) -> tuple[LimitlessOrderResult, object]:
    """Execute both legs of an arb simultaneously.

    Buys YES on Limitless and NO on Polymarket.  Checks min_net_edge before
    submitting.  Both results are logged to orders_log if a repo is supplied.

    Returns:
        (lim_result, poly_result) tuple.
    """
    if match.arb_gap <= min_net_edge:
        logger.info(
            "arb_scanner: skipping {} — gap {:.4f} <= min_net_edge {:.4f}",
            match.limitless.slug, match.arb_gap, min_net_edge,
        )
        dummy_lim = LimitlessOrderResult(
            status="skipped_below_min_edge",
            order_id=None,
            side="YES",
            price=match.limitless.yes_price,
            size_usdc=stake_usdc,
            market_slug=match.limitless.slug,
            error=f"arb_gap {match.arb_gap:.4f} <= min_net_edge {min_net_edge}",
        )
        return dummy_lim, None

    # The paginated Limitless endpoint can omit venue.exchange for CLOB markets;
    # fetch the detail payload before handing the market to live order placement.
    if not match.limitless.address and (
        match.limitless.token_id_yes or match.limitless.token_id_no
    ):
        fetched_address = await _fetch_limitless_market_address(
            match.limitless.slug,
            limitless_host="https://api.limitless.exchange",
        )
        if fetched_address:
            from dataclasses import replace as _replace
            match = _replace(
                match,
                limitless=_replace(match.limitless, address=fetched_address),
            )
        else:
            logger.warning(
                "execute_arb: skipping {} — could not resolve exchange address",
                match.limitless.slug,
            )
            dummy_lim = LimitlessOrderResult(
                status="failed",
                order_id=None,
                side="YES",
                price=match.limitless.yes_price,
                size_usdc=stake_usdc,
                market_slug=match.limitless.slug,
                error="exchange_address could not be resolved from detail endpoint",
            )
            return dummy_lim, None

    if not getattr(lim_client, "paper_mode", True):
        try:
            lim_balance = await asyncio.to_thread(lim_client.collateral_balance_usdc)
        except Exception as exc:
            logger.warning(
                "execute_arb: skipping {} — could not check Limitless collateral balance: {}",
                match.limitless.slug,
                exc,
            )
            dummy_lim = LimitlessOrderResult(
                status="failed",
                order_id=None,
                side="YES",
                price=match.limitless.yes_price,
                size_usdc=stake_usdc,
                market_slug=match.limitless.slug,
                error=f"could not check Limitless collateral balance: {exc}",
            )
            return dummy_lim, None
        required = Decimal(str(stake_usdc))
        if lim_balance < required:
            logger.warning(
                "execute_arb: skipping {} — Limitless collateral balance {} < stake {}",
                match.limitless.slug,
                lim_balance,
                required,
            )
            dummy_lim = LimitlessOrderResult(
                status="failed",
                order_id=None,
                side="YES",
                price=match.limitless.yes_price,
                size_usdc=stake_usdc,
                market_slug=match.limitless.slug,
                error=(
                    "insufficient Limitless collateral balance "
                    f"({lim_balance} USDC < {required} USDC)"
                ),
            )
            return dummy_lim, None

    # Refresh Polymarket token IDs from CLOB — Gamma clobTokenIds can be stale.
    clob_tokens = await _fetch_poly_token_ids(match.poly.condition_id)
    if clob_tokens:
        token_id_yes, token_id_no = clob_tokens
        from dataclasses import replace as _replace
        match = _replace(
            match,
            poly=_replace(
                match.poly,
                token_id_yes=token_id_yes,
                token_id_no=token_id_no,
            ),
        )
    else:
        logger.warning(
            "execute_arb: could not refresh CLOB token IDs for {}, using Gamma values",
            match.poly.condition_id,
        )

    live_ask = await _fetch_live_poly_best_ask(match.poly.token_id_no)
    if live_ask is None:
        logger.warning("execute_arb: no live poly book for {}", match.limitless.slug)
        live_price = Decimal(str(round(1.0 - match.poly.yes_price, 6)))
        preflight_book = None
    else:
        live_price = Decimal(str(round(live_ask, 6)))
        preflight_book = {"best_ask": float(live_price)}

    intent = OrderIntent(
        id=uuid.uuid4().hex,
        strategy_id="limitless_arb",
        token_id=match.poly.token_id_no,
        market_id=match.poly.condition_id,
        side="buy",
        price=live_price,
        size=Decimal(str(round(stake_usdc, 6))),
    )

    lim_result, poly_result = await asyncio.gather(
        lim_client.place_order(match.limitless, side="YES", size_usdc=stake_usdc),
        asyncio.to_thread(
            poly_client.place_order,
            intent,
            strategy_id="limitless_arb",
            market_id=match.poly.condition_id,
            preflight_book=preflight_book,
            notes=(
                f"arb_gap={match.arb_gap:.4f} slug={match.limitless.slug} "
                f"lim_entry={match.limitless.yes_price:.4f} "
                f"poly_yes_entry={match.poly.yes_price:.4f} "
                f"similarity={match.similarity:.3f}"
            ),
        ),
    )

    logger.info(
        "execute_arb: lim={} poly={} slug={}",
        lim_result.status, poly_result.status, match.limitless.slug,
    )

    position_id = hashlib.sha256(
        f"{match.limitless.slug}|{match.poly.condition_id}|{int(time.time() * 1000)}".encode()
    ).hexdigest()[:16]
    if positions_repo is not None:
        _write_entry_positions(
            positions_repo=positions_repo,
            position_id=position_id,
            match=match,
            stake_usdc=stake_usdc,
            lim_result=lim_result,
            poly_result=poly_result,
        )

    if orders_log_repo is not None:
        _log_limitless_leg(lim_result, match, orders_log_repo)

    return lim_result, poly_result


def _entry_notes(match: ArbMatch) -> str:
    return (
        f"arb_gap={match.arb_gap:.4f} slug={match.limitless.slug} "
        f"lim_entry={match.limitless.yes_price:.4f} "
        f"poly_yes_entry={match.poly.yes_price:.4f} "
        f"similarity={match.similarity:.3f}"
    )


def _write_entry_positions(
    *,
    positions_repo,
    position_id: str,
    match: ArbMatch,
    stake_usdc: float,
    lim_result: LimitlessOrderResult,
    poly_result,
) -> None:
    ts = int(time.time() * 1000)
    notes = _entry_notes(match)
    if lim_result.status in ("paper_filled", "live_submitted"):
        try:
            positions_repo.append(PositionRow(
                position_id=f"{position_id}_lim",
                strategy_id="limitless_arb",
                market_id=match.limitless.slug,
                token_id=match.limitless.address,
                side="buy",
                open_ts_ms=ts,
                entry_price=str(round(match.limitless.yes_price, 6)),
                size=str(round(stake_usdc, 6)),
                notional_usdc=str(round(stake_usdc, 6)),
                gross_edge=str(round(match.arb_gap, 6)),
                relationship_id=position_id,
                relationship_type="limitless_poly_arb",
                notes=notes,
                status="open",
                schema_version=1,
                ingested_ts_ms=ts,
            ))
        except Exception:
            logger.exception("positions append failed for limitless leg {}", match.limitless.slug)

    if poly_result is not None and poly_result.status in ("paper_filled", "live_submitted"):
        try:
            positions_repo.append(PositionRow(
                position_id=f"{position_id}_poly",
                strategy_id="limitless_arb",
                market_id=match.poly.condition_id,
                token_id=match.poly.token_id_no,
                side="buy",
                open_ts_ms=ts,
                entry_price=str(round(1.0 - match.poly.yes_price, 6)),
                size=str(round(stake_usdc, 6)),
                notional_usdc=str(round(stake_usdc, 6)),
                gross_edge=str(round(match.arb_gap, 6)),
                relationship_id=position_id,
                relationship_type="limitless_poly_arb",
                notes=notes,
                status="open",
                schema_version=1,
                ingested_ts_ms=ts,
            ))
        except Exception:
            logger.exception("positions append failed for poly leg {}", match.limitless.slug)


def _log_limitless_leg(
    result: LimitlessOrderResult,
    match: ArbMatch,
    orders_log_repo,
) -> None:
    import time as _time
    row = OrdersLogRow(
        intent_id=result.order_id or uuid.uuid4().hex,
        ts_ms=int(_time.time() * 1000),
        strategy_id="limitless_arb",
        token_id=match.limitless.address,
        market_id=match.limitless.slug,
        side=result.side,
        requested_size=str(round(result.size_usdc, 6)),
        filled_size=str(round(result.size_usdc, 6)) if result.status == "paper_filled" else "0",
        avg_fill_price=str(round(result.price, 6)),
        notional_usdc=str(round(result.size_usdc, 6)) if result.status == "paper_filled" else "0",
        fees_usdc="0",
        status=result.status,
        reason=result.error or "",
        paper_mode=(result.status == "paper_filled"),
        kill_switch_active=(result.status == "rejected_kill_switch"),
        orders_allowed=True,
        preflight_passed=True,
        preflight_token_id=None,
        http_status=None,
        source_lane="limitless_arb",
        source_relationship_id=f"{match.limitless.slug}|{match.poly.condition_id}",
        notes=(
            f"arb_gap={match.arb_gap:.4f} slug={match.limitless.slug} "
            f"lim_entry={match.limitless.yes_price:.4f} "
            f"poly_yes_entry={match.poly.yes_price:.4f} "
            f"similarity={match.similarity:.3f}"
        ),
    )
    try:
        orders_log_repo.append(row)
    except Exception:
        logger.exception("orders_log append failed for limitless leg {}", match.limitless.slug)


# ─── Convergence-based early exit ────────────────────────────────────────────


async def _fetch_limitless_current_price(
    slug: str,
    limitless_host: str = "https://api.limitless.exchange",
) -> float | None:
    """Fetch the current Limitless YES bid for a given market slug.

    Returns the best YES price in 0.0-1.0, or None on any failure (HTTP error,
    parse failure, unrecognised price total, market closed).
    """
    import httpx

    url = f"{limitless_host.rstrip('/')}/markets/{slug}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.warning("convergence: limitless fetch failed for {}: {}", slug, exc)
        return None

    prices = data.get("prices")
    if not isinstance(prices, list) or len(prices) < 1:
        return None
    try:
        yes_raw = float(prices[0])
        no_raw = float(prices[1]) if len(prices) >= 2 else (1.0 - yes_raw)
    except (TypeError, ValueError):
        return None

    total = yes_raw + no_raw
    if 0.9 <= total <= 1.1:
        yes_price = yes_raw
    elif 90.0 <= total <= 110.0:
        yes_price = yes_raw / 100.0
    else:
        return None
    if not (0.0 < yes_price < 1.0):
        return None
    return yes_price


async def _fetch_limitless_market_entry(
    slug: str,
    limitless_host: str = "https://api.limitless.exchange",
) -> LimitlessMarketEntry | None:
    """Fetch and parse the current Limitless market detail by slug."""
    import httpx

    from ..ingest.limitless.parser import parse_limitless_market

    url = f"{limitless_host.rstrip('/')}/markets/{slug}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.warning("limitless detail fetch failed for {}: {}", slug, exc)
        return None

    return parse_limitless_market(data)


async def _fetch_limitless_market_address(
    slug: str,
    limitless_host: str = "https://api.limitless.exchange",
) -> str:
    """Fetch the exchange address for a Limitless market by slug.

    The paginated /markets/active endpoint returns venue={} for CLOB markets,
    but the individual /markets/{slug} endpoint returns the full venue.exchange.
    """
    import httpx

    url = f"{limitless_host.rstrip('/')}/markets/{slug}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            venue = data.get("venue") or {}
            address = (
                venue.get("exchange", "")
                or venue.get("address", "")
                or data.get("address", "")
                or data.get("contractAddress", "")
            )
            if address:
                logger.debug("limitless detail fetch: address={} for {}", address, slug)
            else:
                logger.warning("limitless detail fetch: no address found for {}", slug)
            return address
    except Exception as exc:
        logger.warning("limitless detail fetch failed for {}: {}", slug, exc)
        return ""


async def _fetch_poly_token_ids(
    condition_id: str,
    clob_host: str = "https://clob.polymarket.com",
) -> tuple[str, str] | None:
    """Fetch YES and NO token IDs from the Polymarket CLOB API.

    The Gamma API sometimes returns stale or incorrect clobTokenIds.
    The CLOB API is authoritative.

    Returns (token_id_yes, token_id_no) or None if lookup fails.
    """
    import httpx

    url = f"{clob_host.rstrip('/')}/markets/{condition_id}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
            tokens = data.get("tokens", [])
            token_id_yes = ""
            token_id_no = ""
            for t in tokens:
                outcome = str(t.get("outcome", "")).lower()
                tid = str(t.get("token_id", ""))
                if outcome == "yes":
                    token_id_yes = tid
                elif outcome == "no":
                    token_id_no = tid
            if token_id_yes and token_id_no:
                logger.debug(
                    "clob token lookup: {} YES={} NO={}",
                    condition_id, token_id_yes[:8], token_id_no[:8],
                )
                return token_id_yes, token_id_no
            logger.warning("clob token lookup: missing tokens for {}", condition_id)
            return None
    except Exception as exc:
        logger.warning("clob token lookup failed for {}: {}", condition_id, exc)
        return None


async def _exit_both_legs(
    position: LimitlessArbPosition,
    *,
    current_lim_yes: float,
    current_poly_yes: float,
    lim_client: LimitlessOrderClient,
    orders_log_repo,
    poly_client=None,
    positions_repo=None,
    limitless_host: str = "https://api.limitless.exchange",
) -> bool:
    """Exit both legs of an open arb position.

    Both legs go through their order clients so paper/live behavior and audit
    logging stay centralised.
    """
    stake = Decimal(str(position.stake_usdc))
    lim_entry = Decimal(str(position.lim_entry_price))
    lim_exit = Decimal(str(current_lim_yes))
    poly_yes_entry = Decimal(str(position.poly_yes_entry))
    poly_yes_exit = Decimal(str(current_poly_yes))
    fee_rate = _EXIT_FEE_BPS / Decimal("10000")

    realised_lim_proceeds_dec = lim_exit * stake
    entry_cost_lim_dec = lim_entry * stake
    realised_poly_proceeds_dec = (Decimal("1") - poly_yes_exit) * stake
    entry_cost_poly_dec = (Decimal("1") - poly_yes_entry) * stake
    # Valid entries require lim_entry < poly_yes_entry for a positive YES+NO edge.
    gross_profit_dec = (
        (realised_lim_proceeds_dec - entry_cost_lim_dec)
        + (realised_poly_proceeds_dec - entry_cost_poly_dec)
    )
    entry_fees_dec = (entry_cost_lim_dec + entry_cost_poly_dec) * fee_rate
    exit_fees_dec = (realised_lim_proceeds_dec + realised_poly_proceeds_dec) * fee_rate
    total_fees_dec = entry_fees_dec + exit_fees_dec
    realised_profit_dec = gross_profit_dec - total_fees_dec

    realised_lim_proceeds = float(realised_lim_proceeds_dec)
    realised_poly_proceeds = float(realised_poly_proceeds_dec)
    realised_profit = float(realised_profit_dec)
    lim_exit_fee = float(realised_lim_proceeds_dec * fee_rate)
    exit_ts = int(time.time() * 1000)
    exit_notes_lim = (
        f"exit_leg=limitless position_id={position.position_id} "
        f"lim_entry={position.lim_entry_price:.4f} "
        f"lim_exit={current_lim_yes:.4f} "
        f"gross_profit={float(gross_profit_dec):.4f} "
        f"fees_usdc={float(total_fees_dec):.4f} "
        f"realised_profit={realised_profit:.4f}"
    )
    exit_notes_poly = (
        f"exit_leg=polymarket position_id={position.position_id} "
        f"poly_entry={position.poly_yes_entry:.4f} "
        f"poly_yes_current={current_poly_yes:.4f} "
        f"gross_profit={float(gross_profit_dec):.4f} "
        f"fees_usdc={float(total_fees_dec):.4f} "
        f"realised_profit={realised_profit:.4f}"
    )

    lim_paper_mode = bool(getattr(lim_client, "paper_mode", getattr(lim_client, "_paper_mode", True)))
    lim_market = LimitlessMarketEntry(
        slug=position.limitless_slug,
        title=position.limitless_slug,
        yes_price=current_lim_yes,
        address="",
        token_id_yes="",
        token_id_no="",
    )
    if not lim_paper_mode:
        fetched_market = await _fetch_limitless_market_entry(
            position.limitless_slug,
            limitless_host=limitless_host,
        )
        if fetched_market is not None:
            lim_market = replace(fetched_market, yes_price=current_lim_yes)

    lim_exit_result = await lim_client.sell_yes(
        lim_market,
        size_usdc=position.stake_usdc,
        price=current_lim_yes,
    )
    lim_exit_success = lim_exit_result.status in {"paper_filled", "live_submitted"}

    lim_row = OrdersLogRow(
        intent_id=lim_exit_result.order_id or uuid.uuid4().hex,
        ts_ms=exit_ts,
        strategy_id="limitless_arb_exit",
        token_id=lim_market.token_id_yes,
        market_id=position.limitless_slug,
        side="SELL_YES",
        requested_size=str(round(position.stake_usdc, 6)),
        filled_size=(
            str(round(lim_exit_result.size_usdc, 6))
            if lim_exit_success
            else "0"
        ),
        avg_fill_price=str(round(lim_exit_result.price, 6)),
        notional_usdc=(
            str(round(lim_exit_result.price * lim_exit_result.size_usdc, 6))
            if lim_exit_success
            else "0"
        ),
        fees_usdc=str(round(lim_exit_fee, 6)) if lim_exit_success else "0",
        status=lim_exit_result.status,
        reason=lim_exit_result.error or "",
        paper_mode=lim_paper_mode,
        kill_switch_active=(lim_exit_result.status == "rejected_kill_switch"),
        orders_allowed=True,
        preflight_passed=True,
        preflight_token_id=None,
        http_status=None,
        source_lane="limitless_arb_exit",
        source_relationship_id=position.position_id,
        notes=exit_notes_lim,
    )

    poly_exit_status = "exit_not_implemented"
    if poly_client is not None and position.poly_token_id_no:
        sell_intent = OrderIntent(
            id=uuid.uuid4().hex,
            strategy_id="limitless_arb_exit",
            token_id=position.poly_token_id_no,
            market_id=position.poly_condition_id,
            side="sell",
            price=Decimal(str(round(1.0 - current_poly_yes, 6))),
            size=Decimal(str(round(position.stake_usdc, 6))),
        )
        poly_exit_result = poly_client.place_order(
            sell_intent,
            strategy_id="limitless_arb_exit",
            market_id=position.poly_condition_id,
            source_lane="limitless_arb_exit",
            source_relationship_id=position.position_id,
            notes=exit_notes_poly,
        )
        poly_exit_status = poly_exit_result.status
    else:
        poly_placeholder = OrdersLogRow(
            intent_id=uuid.uuid4().hex,
            ts_ms=exit_ts,
            strategy_id="limitless_arb_exit",
            token_id=position.poly_token_id_no,
            market_id=position.poly_condition_id,
            side="SELL_NO",
            requested_size=str(round(position.stake_usdc, 6)),
            filled_size="0",
            avg_fill_price=str(round(1.0 - current_poly_yes, 6)),
            notional_usdc="0",
            fees_usdc="0",
            status="exit_not_implemented",
            reason="poly_client not passed to _exit_both_legs",
            paper_mode=True,
            kill_switch_active=False,
            orders_allowed=True,
            preflight_passed=True,
            preflight_token_id=None,
            http_status=None,
            source_lane="limitless_arb_exit",
            source_relationship_id=position.position_id,
            notes=exit_notes_poly,
        )
        try:
            orders_log_repo.append(poly_placeholder)
        except Exception:
            logger.exception("orders_log append failed for poly placeholder {}", position.limitless_slug)

    try:
        orders_log_repo.append(lim_row)
    except Exception:
        logger.exception("orders_log append failed for limitless exit {}", position.limitless_slug)
        return False

    if positions_repo is not None:
        for suffix in ("_lim", "_poly"):
            try:
                is_lim = suffix == "_lim"
                positions_repo.append(PositionRow(
                    position_id=f"{position.position_id}{suffix}",
                    strategy_id="limitless_arb",
                    market_id=position.limitless_slug if is_lim else position.poly_condition_id,
                    token_id="" if is_lim else position.poly_token_id_no,
                    side="sell",
                    open_ts_ms=position.open_ts_ms,
                    entry_price=str(round(
                        position.lim_entry_price if is_lim else (1.0 - position.poly_yes_entry),
                        6,
                    )),
                    size=str(round(position.stake_usdc, 6)),
                    notional_usdc=str(round(
                        realised_lim_proceeds if is_lim else realised_poly_proceeds,
                        6,
                    )),
                    gross_edge=str(round(realised_profit, 6)),
                    relationship_id=position.position_id,
                    relationship_type="limitless_poly_arb",
                    notes=exit_notes_lim if is_lim else exit_notes_poly,
                    status="closed",
                    schema_version=1,
                    ingested_ts_ms=exit_ts,
                ))
            except Exception:
                logger.exception(
                    "positions close write failed for {} {}",
                    position.limitless_slug,
                    suffix,
                )

    logger.info(
        "convergence: exited {} — profit={:.4f} lim={:.4f}->{:.4f} poly_yes={:.4f}->{:.4f} poly_exit={}",
        position.limitless_slug,
        realised_profit,
        position.lim_entry_price,
        current_lim_yes,
        position.poly_yes_entry,
        current_poly_yes,
        poly_exit_status,
    )
    return True


async def scan_and_exit_positions(
    positions: list[LimitlessArbPosition],
    *,
    lim_client: LimitlessOrderClient,
    orders_log_repo,
    convergence_threshold: float = 0.5,
    min_realised_profit_usdc: float = 0.0,
    limitless_host: str = "https://api.limitless.exchange",
    poly_client=None,
    positions_repo=None,
) -> int:
    """Scan open arb positions and exit any that have converged enough.

    For each position, fetches current Limitless and Polymarket YES prices and
    checks whether *both* legs have moved more than ``arb_gap *
    convergence_threshold`` since entry. If both have, computes realised net
    profit at current prices and exits via ``_exit_both_legs`` when profit
    exceeds ``min_realised_profit_usdc``. Returns the number of positions exited.
    """
    if not positions:
        return 0

    exited = 0
    for position in positions:
        if position.lim_entry_price >= position.poly_yes_entry:
            logger.warning(
                "position {} was entered with inverted prices "
                "(lim_entry >= poly_yes_entry) — skipping convergence exit",
                position.limitless_slug,
            )
            continue

        current_lim = await _fetch_limitless_current_price(
            position.limitless_slug, limitless_host=limitless_host,
        )
        if current_lim is None:
            logger.debug(
                "convergence: skipping {} — no live limitless price",
                position.limitless_slug,
            )
            continue
        current_poly = await _fetch_live_poly_best_ask(position.poly_token_id_no)
        if current_poly is None:
            logger.debug(
                "convergence: skipping {} — no live poly book",
                position.limitless_slug,
            )
            continue
        # _fetch_live_poly_best_ask returns the best ask for the NO token, so
        # current YES price on Polymarket is approximately 1 - ask_for_no.
        current_poly_yes = max(0.0, min(1.0, 1.0 - current_poly))

        if positions_repo is not None:
            snap_ts = int(time.time() * 1000)
            unrealised = (
                (current_lim - position.lim_entry_price)
                + (position.poly_yes_entry - current_poly_yes)
            ) * position.stake_usdc
            snap_notes = (
                f"snap lim_now={current_lim:.4f} poly_yes_now={current_poly_yes:.4f} "
                f"unrealised={unrealised:.4f}"
            )
            try:
                positions_repo.append(PositionRow(
                    position_id=position.position_id,
                    strategy_id="limitless_arb",
                    market_id=position.limitless_slug,
                    token_id=position.poly_token_id_no,
                    side="snapshot",
                    open_ts_ms=position.open_ts_ms,
                    entry_price=str(round(position.lim_entry_price, 6)),
                    size=str(round(position.stake_usdc, 6)),
                    notional_usdc=str(round(position.stake_usdc, 6)),
                    gross_edge=str(round(unrealised, 6)),
                    relationship_id=position.position_id,
                    relationship_type="limitless_poly_arb",
                    notes=snap_notes,
                    status="open",
                    schema_version=1,
                    ingested_ts_ms=snap_ts,
                ))
            except Exception:
                logger.exception("snapshot write failed for {}", position.limitless_slug)

        threshold = position.arb_gap * convergence_threshold
        delta_lim = abs(current_lim - position.lim_entry_price)
        delta_poly = abs(current_poly_yes - position.poly_yes_entry)
        if delta_lim < threshold or delta_poly < threshold:
            logger.debug(
                "convergence: {} held — moves ({:.4f},{:.4f}) below threshold {:.4f}",
                position.limitless_slug, delta_lim, delta_poly, threshold,
            )
            continue

        realised_profit = (
            (current_lim - position.lim_entry_price)
            + (position.poly_yes_entry - current_poly_yes)
        ) * position.stake_usdc
        if realised_profit <= min_realised_profit_usdc:
            logger.debug(
                "convergence: {} held — realised profit {:.4f} <= min {:.4f}",
                position.limitless_slug, realised_profit, min_realised_profit_usdc,
            )
            continue

        if await _exit_both_legs(
            position,
            current_lim_yes=current_lim,
            current_poly_yes=current_poly_yes,
            lim_client=lim_client,
            orders_log_repo=orders_log_repo,
            poly_client=poly_client,
            positions_repo=positions_repo,
            limitless_host=limitless_host,
        ):
            exited += 1

    return exited
