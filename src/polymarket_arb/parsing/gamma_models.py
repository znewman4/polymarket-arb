"""Pydantic v2 models for Gamma ``/markets`` and ``/events`` responses.

These models accept Gamma's quirks (stringified-JSON arrays, missing dates,
mixed bool/int types) and produce strictly-typed objects. They are NOT the
storage DTOs — those live in ``storage.base`` (``MarketRow`` / ``EventRow``)
and are produced by ``ingest.gamma.parser``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .outcome_normaliser import parse_stringified_json


class Tag(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    id: str | None = None
    label: str | None = None
    slug: str | None = None

    @field_validator("id", mode="before")
    @classmethod
    def _coerce_id(cls, v: Any) -> str | None:
        return None if v is None else str(v)


class ClobReward(BaseModel):
    """Subset of fields we care about. Gamma sends more we ignore."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    id: str | None = None
    rewardsAmount: Decimal | None = None

    @field_validator("id", mode="before")
    @classmethod
    def _coerce_id(cls, v: Any) -> str | None:
        return None if v is None else str(v)


class GammaMarket(BaseModel):
    """One element of a Gamma ``/markets`` response."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str
    conditionId: str | None = None
    slug: str
    question: str
    description: str | None = None

    endDate: str | None = None
    startDate: str | None = None
    closedTime: str | None = None
    # Gamma occasionally uses ``resolutionDate``/``resolvedTime``; we handle
    # both via a pre-validator below.
    resolvedTime: str | None = None

    active: bool = False
    closed: bool = False
    archived: bool = False

    outcomes: list[str] = Field(default_factory=list)
    outcomePrices: list[Decimal] = Field(default_factory=list)
    clobTokenIds: list[str] = Field(default_factory=list)

    volume: Decimal | None = None
    liquidity: Decimal | None = None

    eventId: str | None = None
    negRisk: bool = False

    @field_validator("id", "conditionId", "eventId", mode="before")
    @classmethod
    def _coerce_id(cls, v: Any) -> str | None:
        if v is None:
            return None
        return str(v)

    @field_validator("outcomes", "clobTokenIds", mode="before")
    @classmethod
    def _normalise_str_list(cls, v: Any) -> list:
        return [str(x) for x in parse_stringified_json(v)]

    @field_validator("outcomePrices", mode="before")
    @classmethod
    def _normalise_decimal_list(cls, v: Any) -> list:
        return [Decimal(str(x)) for x in parse_stringified_json(v)]


class GammaEvent(BaseModel):
    """One element of a Gamma ``/events`` response (or nested inside a
    market). We keep this shallow: Phase 1 stores ``market_ids`` only and
    does not recursively parse nested markets."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str
    ticker: str | None = None
    slug: str
    title: str
    description: str | None = None

    startDate: str | None = None
    endDate: str | None = None

    markets: list[Any] = Field(default_factory=list)
    tags: list[Tag] = Field(default_factory=list)

    @field_validator("id", mode="before")
    @classmethod
    def _coerce_id(cls, v: Any) -> str:
        return str(v)
