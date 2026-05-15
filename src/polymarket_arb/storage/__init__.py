"""Storage layer: append-only Parquet now, swappable to Postgres later."""

from .base import (
    BackfillCoverageRepository,
    BacktestMetricsRepository,
    BestQuotesRepository,
    EventsRepository,
    FillEventsRepository,
    MarketsRepository,
    OrderbookRepository,
    OrderEventsRepository,
    PositionSnapshotsRepository,
    PriceHistoryRepository,
    RelationshipCandidatesRepository,
    RiskSnapshotsRepository,
    SimulatedTradesRepository,
    StrategyCandidatesRepository,
    TradeHistoryRepository,
)
from .exceptions import SchemaMismatchError, StorageError

__all__ = [
    "BackfillCoverageRepository",
    "BacktestMetricsRepository",
    "BestQuotesRepository",
    "EventsRepository",
    "FillEventsRepository",
    "MarketsRepository",
    "OrderEventsRepository",
    "OrderbookRepository",
    "PositionSnapshotsRepository",
    "PriceHistoryRepository",
    "RelationshipCandidatesRepository",
    "RiskSnapshotsRepository",
    "SchemaMismatchError",
    "SimulatedTradesRepository",
    "StorageError",
    "StrategyCandidatesRepository",
    "TradeHistoryRepository",
]
