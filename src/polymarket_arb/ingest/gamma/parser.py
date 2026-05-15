"""Pure parsing: raw Gamma dict → ``MarketRow`` / ``EventRow``.

No I/O. All HTTP and parquet handling happens in ``client.py`` and the
markets/events repos respectively. This separation lets fixture-replay
tests pin parser quirks deterministically.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from loguru import logger
from pydantic import ValidationError

from ...parsing.gamma_models import GammaEvent, GammaMarket
from ...storage.base import EventRow, MarketRow

_MARKET_SCHEMA_VERSION = 1
_EVENT_SCHEMA_VERSION = 1

# Match the parquet schema (decimal128(38, 8)). Gamma occasionally returns
# volume/liquidity with ~15 fractional digits — quantize explicitly so PyArrow
# doesn't reject the row with a "rescaling would lose data" error. The
# rounding is documented and lossy by design; precision past 1e-8 is noise
# at Polymarket scale.
_PRICE_QUANT = Decimal("0.00000001")


def _q(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(_PRICE_QUANT, rounding=ROUND_HALF_EVEN)


class InvalidMarket(ValueError):
    """Raised when a Gamma market record fails business validation. The
    parser logs and returns ``None`` rather than raising for normal bad
    rows; this exception is reserved for callers that want strict mode.
    """


def market_text_hash(question: str, description: str | None) -> str:
    """sha256 of question + (description or '') — used to detect text drift."""

    blob = f"{question or ''}\n{description or ''}".encode()
    return hashlib.sha256(blob).hexdigest()


def _iso_to_ms(value: str | None) -> int | None:
    """Best-effort ISO-8601 → epoch ms (UTC). ``None`` on null/unknown."""

    if not value:
        return None
    s = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(value[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


def parse_market(raw: dict[str, Any], *, ingested_ts_ms: int,
                 strict: bool = False) -> MarketRow | None:
    """Validate one Gamma market dict; return ``MarketRow`` or ``None``.

    - Pydantic validation failure → log warning, drop row (or raise in strict).
    - Business validators (missing clobTokenIds, < 2 outcomes,
      length mismatch) → drop row.
    - On success returns a fully-populated, frozen ``MarketRow``.
    """

    try:
        m = GammaMarket.model_validate(raw)
    except ValidationError as exc:
        msg = f"pydantic invalidated gamma market: {exc.errors()[:1]}"
        if strict:
            raise InvalidMarket(msg) from exc
        logger.warning("dropped market (pydantic)", id=raw.get("id"), error=str(exc)[:200])
        return None

    # Business validators
    reasons: list[str] = []
    if not m.clobTokenIds or len(m.clobTokenIds) < 2:
        reasons.append("missing or <2 clobTokenIds")
    if len(m.outcomes) < 2:
        reasons.append("<2 outcomes")
    if m.outcomePrices and len(m.outcomePrices) != len(m.outcomes):
        reasons.append("outcome/outcomePrices length mismatch")
    if m.conditionId is None:
        reasons.append("missing conditionId")
    if reasons:
        msg = f"dropped market: {', '.join(reasons)}"
        if strict:
            raise InvalidMarket(msg)
        logger.warning(msg, id=m.id, slug=m.slug)
        return None

    return MarketRow(
        id=m.id,
        condition_id=m.conditionId or "",
        slug=m.slug,
        question=m.question,
        description=m.description,
        end_date_ms=_iso_to_ms(m.endDate),
        start_date_ms=_iso_to_ms(m.startDate),
        closed_at_ms=_iso_to_ms(m.closedTime),
        resolved_at_ms=_iso_to_ms(m.resolvedTime),
        active=m.active,
        closed=m.closed,
        archived=m.archived,
        outcomes=list(m.outcomes),
        gamma_outcome_prices_snapshot=[_q(p) or Decimal("0") for p in m.outcomePrices],
        clob_token_ids=list(m.clobTokenIds),
        volume=_q(m.volume),
        liquidity=_q(m.liquidity),
        event_id=m.eventId,
        neg_risk=m.negRisk,
        text_hash=market_text_hash(m.question, m.description),
        schema_version=_MARKET_SCHEMA_VERSION,
        ingested_ts_ms=ingested_ts_ms,
    )


def parse_event(raw: dict[str, Any], *, ingested_ts_ms: int,
                strict: bool = False) -> EventRow | None:
    """Validate one Gamma event dict; return ``EventRow`` or ``None``."""

    try:
        e = GammaEvent.model_validate(raw)
    except ValidationError as exc:
        if strict:
            raise InvalidMarket(f"pydantic invalidated gamma event: {exc.errors()[:1]}") from exc
        logger.warning("dropped event (pydantic)", id=raw.get("id"), error=str(exc)[:200])
        return None

    # Markets nested under an event come either as full dicts or as id refs.
    market_ids: list[str] = []
    for item in e.markets:
        if isinstance(item, dict):
            mid = item.get("id")
            if mid is not None:
                market_ids.append(str(mid))
        elif isinstance(item, str | int):
            market_ids.append(str(item))

    return EventRow(
        id=e.id,
        ticker=e.ticker,
        slug=e.slug,
        title=e.title,
        description=e.description,
        start_date_ms=_iso_to_ms(e.startDate),
        end_date_ms=_iso_to_ms(e.endDate),
        market_ids=market_ids,
        tags=[t.label for t in e.tags if t.label],
        schema_version=_EVENT_SCHEMA_VERSION,
        ingested_ts_ms=ingested_ts_ms,
    )
