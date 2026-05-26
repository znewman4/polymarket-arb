"""Limitless x Polymarket cross-market arbitrage scanner and executor.

Arb mechanic:
  Both markets cover the same event.  If lim_yes + poly_yes < 1.0, a risk-free
  arb exists: buy YES on Limitless and buy NO on Polymarket (or vice versa).
  The arb gap = 1.0 - (lim_yes + poly_yes) represents the guaranteed profit per
  dollar wagered, before fees and slippage.

  Example: lim_yes=0.40, poly_yes=0.45 → gap=0.15
    Buy YES on Limitless at $0.40 + Buy NO on Polymarket at $0.55 = $0.95 cost
    Guaranteed payout: $1.00 (whichever outcome resolves)

Direction assumption:
  We assume matched markets are directionally aligned (both ask the same yes/no
  question).  The fuzzy-match step enforces this to the degree that similar
  question text implies the same event direction.  Verify manually before
  flipping --execute on a new pair.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from loguru import logger
from rapidfuzz import fuzz

from ..live.models import OrdersLogRow
from ..live.order_client import OrderClient
from ..risk.models import OrderIntent
from .models import ArbMatch, LimitlessMarketEntry, LimitlessOrderResult, PolyMarketEntry

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

    # Extract CLOB token IDs from the "tokens" array.
    tokens = raw.get("tokens", [])
    token_id_yes = ""
    token_id_no = ""
    if isinstance(tokens, list):
        for t in tokens:
            if isinstance(t, dict):
                outcome = str(t.get("outcome", "")).lower()
                tid = t.get("token_id", t.get("tokenId", ""))
                if outcome == "yes":
                    token_id_yes = str(tid)
                elif outcome == "no":
                    token_id_no = str(tid)

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
            arb_gap = round(1.0 - (lim.yes_price + best_poly.yes_price), 6)
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


async def execute_arb(
    match: ArbMatch,
    *,
    lim_client: LimitlessOrderClient,
    poly_client: OrderClient,
    stake_usdc: float,
    min_net_edge: float,
    orders_log_repo=None,
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

    intent = OrderIntent(
        id=uuid.uuid4().hex,
        strategy_id="limitless_arb",
        token_id=match.poly.token_id_no,
        side="buy",
        price=Decimal(str(round(1.0 - match.poly.yes_price, 6))),
        size=Decimal(str(round(stake_usdc, 6))),
    )

    lim_result, poly_result = await asyncio.gather(
        lim_client.place_order(match.limitless, side="YES", size_usdc=stake_usdc),
        asyncio.to_thread(
            poly_client.place_order,
            intent,
            strategy_id="limitless_arb",
            market_id=match.poly.condition_id,
            notes=f"limitless_arb pair: {match.limitless.slug} | arb_gap={match.arb_gap:.4f}",
        ),
    )

    logger.info(
        "execute_arb: lim={} poly={} slug={}",
        lim_result.status, poly_result.status, match.limitless.slug,
    )

    if orders_log_repo is not None:
        _log_limitless_leg(lim_result, match, orders_log_repo)

    return lim_result, poly_result


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
        notes=f"arb_gap={match.arb_gap:.4f} similarity={match.similarity:.3f}",
    )
    try:
        orders_log_repo.append(row)
    except Exception:
        logger.exception("orders_log append failed for limitless leg {}", match.limitless.slug)
