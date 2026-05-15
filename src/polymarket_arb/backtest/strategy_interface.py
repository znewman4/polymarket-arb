"""Research strategy interface and a toy threshold strategy."""

from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any, Literal

from .models import BacktestEvent, BacktestSignal


class ResearchStrategy(ABC):
    name: str

    @abstractmethod
    def on_event(self, event: BacktestEvent, context: dict[str, Any]) -> list[BacktestSignal]:
        ...


class ScoreThresholdStrategy(ResearchStrategy):
    name = "score-threshold"

    def __init__(self, *, threshold: float = 0.8) -> None:
        self.threshold = threshold

    def on_event(self, event: BacktestEvent, context: dict[str, Any]) -> list[BacktestSignal]:
        if event.event_type != "market_score":
            return []
        score = float(event.payload.get("final_signal_score") or 0)
        if score < self.threshold:
            return []
        market = context.get("markets", {}).get(event.market_id, {})
        tokens = market.get("clob_token_ids") or []
        token_id = str(tokens[0]) if tokens else None
        quote = context.get("latest_quotes", {}).get(token_id or "")
        market_price = _decimal_or_none((quote or {}).get("midpoint"))
        side: Literal["buy", "watch"] = "buy" if token_id else "watch"
        return [
            BacktestSignal(
                signal_id=uuid.uuid4().hex,
                ts_ms=event.ts_ms,
                market_id=event.market_id,
                token_id=token_id,
                side=side,  # research-only simulated order downstream
                target_outcome=(market.get("outcomes") or [None])[0],
                estimated_probability=None,
                market_price=market_price,
                edge=None,
                confidence=score,
                reason=f"final_signal_score {score:.3f} >= threshold {self.threshold:.3f}",
                source=self.name,
                metadata_json=json.dumps({"recommendation": event.payload.get("recommendation")}),
            )
        ]


def _decimal_or_none(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))
