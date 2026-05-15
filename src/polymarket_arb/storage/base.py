"""Storage Protocols.

Strategies and ingest code depend on these Protocols, never on concrete
classes. Adding a Postgres backend later means writing a sibling module that
implements the same Protocols and changing one DI registration in cli.py.

Phase 0: data models defined here, only ``RiskSnapshotsRepository`` has a
working impl (used by every CLI command via the preflight gate). Other repos
are wired up in their respective phases.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

# ─── Domain dataclasses (storage-layer DTOs, not API or strategy types) ────


@dataclass(frozen=True)
class MarketRow:
    id: str
    condition_id: str
    slug: str
    question: str
    description: str | None
    end_date_ms: int | None
    start_date_ms: int | None
    closed_at_ms: int | None
    resolved_at_ms: int | None
    active: bool
    closed: bool
    archived: bool
    outcomes: list[str]
    # Gamma's snapshot of outcome prices at ingestion time. NEVER use as a
    # live price — Phase 4 fusion must use CLOB BestQuote.
    gamma_outcome_prices_snapshot: list[Decimal]
    clob_token_ids: list[str]
    volume: Decimal | None
    liquidity: Decimal | None
    event_id: str | None
    neg_risk: bool
    text_hash: str
    schema_version: int
    ingested_ts_ms: int


@dataclass(frozen=True)
class EventRow:
    id: str
    ticker: str | None
    slug: str
    title: str
    description: str | None
    start_date_ms: int | None
    end_date_ms: int | None
    market_ids: list[str]
    tags: list[str]
    schema_version: int
    ingested_ts_ms: int


@dataclass(frozen=True)
class OrderbookLevel:
    price: Decimal
    size: Decimal


@dataclass(frozen=True)
class OrderbookSnapshot:
    token_id: str
    condition_id: str | None
    market_slug: str | None
    timestamp_ms: int
    bids: list[OrderbookLevel]
    asks: list[OrderbookLevel]
    book_hash: str | None
    source: str  # "rest" | "ws"
    schema_version: int
    ingested_ts_ms: int


@dataclass(frozen=True)
class BestQuote:
    token_id: str
    timestamp_ms: int
    best_bid: Decimal | None
    best_bid_size: Decimal | None
    best_ask: Decimal | None
    best_ask_size: Decimal | None
    midpoint: Decimal | None
    spread: Decimal | None
    schema_version: int
    ingested_ts_ms: int


# ─── Append-only event-table DTOs ───────────────────────────────────────────


@dataclass(frozen=True)
class OrderEvent:
    event_id: str
    event_ts_ms: int
    order_id: str
    strategy_id: str
    kind: str  # "create"|"submit"|"ack"|"reject"|"cancel"|"expire"|"replace"
    token_id: str
    side: str  # "buy"|"sell"
    price: Decimal
    size: Decimal
    payload: dict
    schema_version: int
    ingested_ts_ms: int


@dataclass(frozen=True)
class FillEvent:
    event_id: str
    event_ts_ms: int
    order_id: str
    fill_id: str
    token_id: str
    side: str
    price: Decimal
    size: Decimal
    fee: Decimal
    payload: dict
    schema_version: int
    ingested_ts_ms: int


@dataclass(frozen=True)
class PositionSnapshot:
    event_id: str
    event_ts_ms: int
    token_id: str
    quantity: Decimal
    avg_entry_price: Decimal
    realised_pnl: Decimal
    unrealised_pnl: Decimal
    schema_version: int
    ingested_ts_ms: int


@dataclass(frozen=True)
class RiskSnapshot:
    event_id: str
    event_ts_ms: int
    strategy_id: str | None
    order_id: str | None
    overall: str  # "PASS"|"FAIL"
    checks: list[dict] = field(default_factory=list)  # [{name, status, reason}]
    schema_version: int = 1
    ingested_ts_ms: int = 0


# ─── Phase 1.5 NLP DTOs ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class MarketSemanticsRow:
    """Validated structured semantic interpretation of one market.

    Raw model response text is NOT stored — only ``raw_response_hash`` plus
    the controlled advisory fields (``explanation_summary``,
    ``flag_rationales_json``, etc.).

    Phase 5.5 D+ adds terms-aware fields (all nullable for back-compat).
    """

    source_market_id: str
    source_condition_id: str | None
    question: str
    canonical_question: str
    market_type: str
    subject_entities: list[str]
    event_entities: list[str]
    temporal_phrase: str | None
    temporal_phrase_normalized: str | None
    temporal_resolution: str
    exact_deadline_ms: int | None
    date_constraints_json: str
    jurisdiction: str | None
    positive_resolution_condition: str
    negative_resolution_condition: str
    necessary_conditions_for_yes: list[str]
    sufficient_conditions_for_yes: list[str]
    necessary_conditions_for_no: list[str]
    sufficient_conditions_for_no: list[str]
    evidence_required: list[str]
    ambiguity_flags: list[str]
    ambiguity_score: float | None
    semantic_confidence: float
    needs_manual_review: bool
    explanation_summary: str | None
    flag_rationales_json: str | None
    uncertainty_notes_json: str | None
    rule_curation_notes_json: str | None
    raw_response_hash: str
    model_name: str
    prompt_version: str
    rulebook_id: str | None
    rulebook_version: int | None
    extraction_id: str
    # Phase 5.5 D+ terms-aware fields (nullable; None for v1 rows)
    event_atoms_json: str | None = None          # JSON list of EventAtom dicts
    proposition_json: str | None = None          # JSON Proposition dict
    outcome_space_json: str | None = None        # JSON OutcomeSpace dict
    tie_rule: str | None = None
    if_event_never_occurs_rule: str | None = None
    resolution_source: str | None = None
    timezone_or_boundary: str | None = None
    terms_confidence: float = 0.0
    long_horizon: bool = False
    unresolved_reference_event: bool = False
    schema_version: int = 1
    ingested_ts_ms: int = 0


@dataclass(frozen=True)
class MarketEmbeddingRow:
    market_id: str
    embedding_space: str
    text_hash: str
    dimensions: int
    vector: list[float]
    model_name: str
    schema_version: int
    ingested_ts_ms: int


@dataclass(frozen=True)
class NlpValidationFailureRow:
    failure_id: str
    market_id: str
    model_name: str
    prompt_version: str
    prompt_hash: str
    raw_response_hash: str
    failure_kind: str       # invalid_json | schema_violation | thinking_filter_failed | http_error
    validation_error_json: str
    attempted_ts_ms: int
    schema_version: int
    ingested_ts_ms: int


@dataclass(frozen=True)
class MarketImplicationRow:
    implication_id: str
    market_id: str
    extraction_id: str
    implication_type: str
    statement: str
    extracted_label: str
    deterministic_score: float
    model_confidence: float
    final_confidence: float
    ambiguity_flags: list[str]
    needs_manual_review: bool
    source: str
    model_name: str
    prompt_version: str
    rulebook_id: str
    rulebook_version: int
    schema_version: int
    ingested_ts_ms: int


@dataclass(frozen=True)
class RulebookEvaluationRow:
    evaluation_id: str
    extraction_id: str
    market_id: str
    rulebook_id: str
    rulebook_version: int
    rulebook_content_hash: str
    score: float
    subscores_json: str
    flags: list[str]
    evaluated_ts_ms: int
    schema_version: int
    ingested_ts_ms: int


@dataclass(frozen=True)
class MarketScoreRow:
    market_id: str
    model_probability_placeholder: float | None
    market_midpoint: float | None
    spread: float | None
    liquidity_score: float
    semantic_confidence: float
    ambiguity_score: float
    implication_quality_score: float
    resolution_risk_score: float
    evidence_quality_score: float
    freshness_score: float
    final_signal_score: float
    recommendation: str
    explanation_json: str
    rulebook_id: str
    rulebook_version: int
    schema_version: int
    ingested_ts_ms: int


# ─── Protocols ──────────────────────────────────────────────────────────────


class MarketsRepository(Protocol):
    def upsert_markets(self, rows: Iterable[MarketRow]) -> int: ...
    def get_market(self, market_id: str) -> MarketRow | None: ...
    def iter_active_markets(self) -> Iterator[MarketRow]: ...
    def search(self, query: str, limit: int = 20) -> list[MarketRow]: ...


class EventsRepository(Protocol):
    def upsert_events(self, rows: Iterable[EventRow]) -> int: ...
    def get_event(self, event_id: str) -> EventRow | None: ...
    def iter_events(self) -> Iterator[EventRow]: ...


class OrderbookRepository(Protocol):
    def append_snapshot(self, snap: OrderbookSnapshot) -> None: ...
    def append_snapshots(self, snaps: Iterable[OrderbookSnapshot]) -> int: ...
    def latest_book(self, token_id: str) -> OrderbookSnapshot | None: ...


class BestQuotesRepository(Protocol):
    def append(self, quote: BestQuote) -> None: ...
    def append_many(self, quotes: Iterable[BestQuote]) -> int: ...
    def latest(self, token_id: str) -> BestQuote | None: ...


class OrderEventsRepository(Protocol):
    def append(self, event: OrderEvent) -> None: ...
    def append_many(self, events: Iterable[OrderEvent]) -> int: ...
    def for_order(self, order_id: str) -> list[OrderEvent]: ...


class FillEventsRepository(Protocol):
    def append(self, event: FillEvent) -> None: ...
    def append_many(self, events: Iterable[FillEvent]) -> int: ...
    def for_order(self, order_id: str) -> list[FillEvent]: ...


class PositionSnapshotsRepository(Protocol):
    def append(self, snap: PositionSnapshot) -> None: ...
    def append_many(self, snaps: Iterable[PositionSnapshot]) -> int: ...
    def latest_for(self, token_id: str) -> PositionSnapshot | None: ...


class RiskSnapshotsRepository(Protocol):
    def append(self, snap: RiskSnapshot) -> None: ...
    def append_many(self, snaps: Iterable[RiskSnapshot]) -> int: ...
    def recent(self, limit: int = 50) -> list[RiskSnapshot]: ...


class MarketSemanticsRepository(Protocol):
    def upsert(self, row: MarketSemanticsRow) -> None: ...
    def upsert_many(self, rows: Iterable[MarketSemanticsRow]) -> int: ...
    def get_latest(self, market_id: str) -> MarketSemanticsRow | None: ...
    def needs_review(self, limit: int = 50) -> list[MarketSemanticsRow]: ...


class MarketEmbeddingsRepository(Protocol):
    def upsert(self, row: MarketEmbeddingRow) -> None: ...
    def upsert_many(self, rows: Iterable[MarketEmbeddingRow]) -> int: ...
    def get_latest(self, market_id: str, embedding_space: str) -> MarketEmbeddingRow | None: ...
    def known_text_hashes(self, embedding_space: str) -> set[str]: ...


class NlpValidationFailuresRepository(Protocol):
    def append(self, row: NlpValidationFailureRow) -> None: ...
    def append_many(self, rows: Iterable[NlpValidationFailureRow]) -> int: ...
    def recent(self, limit: int = 50) -> list[NlpValidationFailureRow]: ...


# ─── Phase 5.5 backfill DTOs ─────────────────────────────────────────────────


@dataclass(frozen=True)
class PriceHistoryRow:
    """One candlestick/tick from CLOB /prices-history."""

    market_id: str
    condition_id: str | None
    token_id: str
    outcome: str | None
    ts_ms: int
    price: Decimal
    source: str  # "clob" | "gamma" | "recorded_book"
    fidelity: str  # interval string used when fetching
    interval: str
    schema_version: int
    ingested_ts_ms: int


@dataclass(frozen=True)
class TradeHistoryRow:
    """One public trade event (best-effort; endpoint may be unavailable)."""

    market_id: str
    condition_id: str
    token_id: str
    outcome: str
    trade_ts_ms: int
    price: Decimal
    size: Decimal
    side: str | None  # "buy" | "sell" | None if unknown
    source: str
    schema_version: int
    ingested_ts_ms: int


@dataclass(frozen=True)
class BackfillCoverageRow:
    """Per-market coverage metadata written after each backfill run."""

    market_id: str
    condition_id: str
    question: str
    start_ts_ms: int
    end_ts_ms: int
    requested_days: int
    has_gamma: bool
    has_price_history: bool
    has_trade_history: bool
    has_semantics: bool
    has_rulebook_score: bool
    has_implications: bool
    has_embeddings: bool
    has_backfill_coverage: bool  # whether this row itself is up-to-date
    price_points_count: int
    trade_points_count: int
    first_price_ts_ms: int | None
    last_price_ts_ms: int | None
    missing_price_gap_count: int
    largest_price_gap_ms: int
    price_min: Decimal | None
    price_max: Decimal | None
    price_out_of_bounds_count: int
    duplicate_timestamp_count: int
    coverage_score: float  # 0..1
    recommended_for_backtest: bool
    exclusion_reasons_json: str  # JSON array of reason strings
    schema_version: int
    ingested_ts_ms: int


# ─── Phase 5.5 Protocols ──────────────────────────────────────────────────────


class PriceHistoryRepository(Protocol):
    def append(self, row: PriceHistoryRow) -> None: ...
    def append_many(self, rows: Iterable[PriceHistoryRow]) -> int: ...
    def iter_for_token(self, token_id: str) -> Iterator[PriceHistoryRow]: ...
    def count_for_token(self, token_id: str) -> int: ...


class TradeHistoryRepository(Protocol):
    def append(self, row: TradeHistoryRow) -> None: ...
    def append_many(self, rows: Iterable[TradeHistoryRow]) -> int: ...
    def iter_for_token(self, token_id: str) -> Iterator[TradeHistoryRow]: ...


class BackfillCoverageRepository(Protocol):
    def append(self, row: BackfillCoverageRow) -> None: ...
    def append_many(self, rows: Iterable[BackfillCoverageRow]) -> int: ...
    def latest_for_market(self, market_id: str) -> BackfillCoverageRow | None: ...
    def iter_latest(self) -> Iterator[BackfillCoverageRow]: ...


# ─── Phase 5.5 Chunks C+D DTOs ───────────────────────────────────────────────


@dataclass(frozen=True)
class RelationshipCandidateRow:
    """One candidate cross-market relationship (validated or rejected).

    Phase 5.5 D+ adds type-specific scores and strategy-eligibility split.
    New fields have defaults for backward compat with v1 parquet rows.
    """

    relationship_id: str
    market_id_a: str
    market_id_b: str
    condition_id_a: str | None
    condition_id_b: str | None
    token_id_a_yes: str | None
    token_id_a_no: str | None
    token_id_b_yes: str | None
    token_id_b_no: str | None
    question_a: str
    question_b: str
    relationship_type: str
    entity_match_score: float
    time_scope_match_score: float
    resolution_criteria_match_score: float
    threshold_relation_json: str
    semantic_similarity_score: float | None
    deterministic_confidence: float
    model_confidence: float
    final_confidence: float
    validation_status: str   # "accepted" | "rejected" | "needs_manual_review"
    rejection_reasons_json: str
    rationale_summary: str
    evidence_json: str
    rulebook_id: str
    rulebook_version: int
    rulebook_content_hash: str
    # Phase 5.5 D+ fields (nullable; default values for v1 back-compat)
    relationship_validity_status: str = ""          # mirrors validation_status
    strategy_eligibility_status: str = "unknown"   # "eligible"|"ineligible"|"needs_manual_review"
    strategy_exclusion_reasons_json: str = "[]"    # JSON list of reason dicts
    relationship_family: str = ""                  # "category"|"temporal"|"nesting"|"contradiction"
    proposition_type: str | None = None
    event_atoms_json: str | None = None
    terms_match_score: float | None = None
    outcome_space_match_score: float | None = None
    reference_event_match_score: float | None = None
    candidate_a: str | None = None
    candidate_b: str | None = None
    shared_event: str | None = None
    reference_event: str | None = None
    relationship_subtype: str = ""
    outcome_space_id: str = ""
    outcome_subtype_a: str = ""
    outcome_subtype_b: str = ""
    entity_type_a: str = ""
    entity_type_b: str = ""
    stage_a: str = ""
    stage_b: str = ""
    party_a: str | None = None
    party_b: str | None = None
    team_a: str | None = None
    team_b: str | None = None
    shared_reference_event: str | None = None
    classification_reason_json: str = "{}"
    mixed_subtype_reason: str = ""
    strategy_family: str = ""
    strategy_eligible_reason: str = ""
    schema_version: int = 1
    ingested_ts_ms: int = 0


@dataclass(frozen=True)
class StrategyCandidateRow:
    """One trade candidate generated from a relationship violation."""

    candidate_id: str
    run_id: str
    relationship_id: str
    market_id_a: str
    market_id_b: str
    token_id_a: str
    token_id_b: str
    relationship_type: str
    signal_ts_ms: int
    price_a: Decimal
    price_b: Decimal
    price_a_ts_ms: int
    price_b_ts_ms: int
    inequality_violated: str
    theoretical_edge: Decimal
    gross_edge: Decimal
    estimated_fee: Decimal
    estimated_slippage: Decimal
    net_edge_after_costs: Decimal
    execution_model: str
    execution_model_confidence: float
    accepted_for_simulation: bool
    rejection_reason: str | None
    simulated_position_json: str
    stake_usdc: Decimal
    expected_payout_json: str
    schema_version: int
    ingested_ts_ms: int


@dataclass(frozen=True)
class SimulatedTradeRow:
    """One simulated fill leg of a strategy candidate."""

    trade_id: str
    candidate_id: str
    run_id: str
    relationship_id: str
    leg: str   # "a" | "b"
    token_id: str
    market_id: str
    side: str  # always "buy"
    fill_ts_ms: int
    fill_price: Decimal
    shares: Decimal
    notional_usdc: Decimal
    fees_usdc: Decimal
    slippage_cost_usdc: Decimal
    execution_model: str
    mark_to_market_ts_ms: int | None
    mark_to_market_value_usdc: Decimal | None
    resolution_ts_ms: int | None
    resolution_outcome: str | None   # "yes" | "no" | "unresolved"
    realised_pnl_usdc: Decimal | None
    schema_version: int
    ingested_ts_ms: int


@dataclass(frozen=True)
class BacktestEquityPointRow:
    """One point on the backtest equity curve."""

    run_id: str
    ts_ms: int
    cash_usdc: Decimal
    open_positions_count: int
    gross_position_value_usdc: Decimal
    equity_usdc: Decimal
    drawdown_pct: float
    schema_version: int
    ingested_ts_ms: int


@dataclass(frozen=True)
class BacktestMetricsRow:
    """Aggregate metrics for one backtest run."""

    run_id: str
    config_hash: str
    starting_cash_usdc: Decimal
    ending_cash_usdc: Decimal
    ending_equity_usdc: Decimal
    total_return_pct: float
    gross_pnl_usdc: Decimal
    net_pnl_usdc: Decimal
    total_fees_usdc: Decimal
    total_slippage_usdc: Decimal
    relationships_considered: int
    signals_generated: int
    candidates_accepted: int
    candidates_rejected: int
    trades_executed: int
    rejection_reason_counts_json: str
    win_rate_when_resolved: float | None
    avg_gross_edge: float
    avg_net_edge: float
    avg_hold_time_ms: float | None
    max_drawdown_pct: float
    sharpe_like: float | None
    pnl_by_relationship_type_json: str
    pnl_by_execution_model_json: str
    pnl_by_confidence_bucket_json: str
    null_baseline_pnl_usdc: Decimal | None
    null_baseline_win_rate: float | None
    credibility_label: str
    credibility_rationale: str
    schema_version: int
    ingested_ts_ms: int


# ─── Phase 5.5 Chunks C+D Protocols ──────────────────────────────────────────


class RelationshipCandidatesRepository(Protocol):
    def append(self, row: RelationshipCandidateRow) -> None: ...
    def append_many(self, rows: Iterable[RelationshipCandidateRow]) -> int: ...
    def get_latest(self, relationship_id: str) -> RelationshipCandidateRow | None: ...
    def iter_latest(self) -> Iterator[RelationshipCandidateRow]: ...
    def iter_accepted(self) -> Iterator[RelationshipCandidateRow]: ...


class StrategyCandidatesRepository(Protocol):
    def append(self, row: StrategyCandidateRow) -> None: ...
    def append_many(self, rows: Iterable[StrategyCandidateRow]) -> int: ...
    def iter_for_run(self, run_id: str) -> Iterator[StrategyCandidateRow]: ...


class SimulatedTradesRepository(Protocol):
    def append(self, row: SimulatedTradeRow) -> None: ...
    def append_many(self, rows: Iterable[SimulatedTradeRow]) -> int: ...
    def iter_for_run(self, run_id: str) -> Iterator[SimulatedTradeRow]: ...


class BacktestMetricsRepository(Protocol):
    def append(self, row: BacktestMetricsRow) -> None: ...
    def append_many(self, rows: Iterable[BacktestMetricsRow]) -> int: ...
    def get_latest_for_run(self, run_id: str) -> BacktestMetricsRow | None: ...


# ─── Phase 5.6 context DTOs ─────────────────────────────────────────────────


@dataclass(frozen=True)
class ContextSourceRow:
    """Registered evidence source metadata for context rules."""

    context_source_id: str
    context_space_id: str
    source_type: str
    source_tier: int
    title: str
    url: str
    domain: str
    publisher: str
    retrieved_at_ms: int
    effective_start_ms: int
    effective_end_ms: int | None
    raw_path: str
    content_hash: str
    status: str
    schema_version: int
    ingested_ts_ms: int


@dataclass(frozen=True)
class ContextDocumentRow:
    """Raw or cleaned local source snapshot used for context extraction."""

    context_document_id: str
    context_source_id: str
    context_space_id: str
    url: str
    title: str
    retrieved_at_ms: int
    raw_path: str
    cleaned_text_path: str | None
    content_excerpt: str
    content_hash: str
    extraction_status: str
    error_message: str | None
    schema_version: int
    ingested_ts_ms: int


@dataclass(frozen=True)
class ContextRuleRow:
    """Structured context rule consumed by deterministic validators."""

    context_rule_id: str
    context_space_id: str
    context_type: str
    rule_type: str
    rule_json: str
    source_document_ids_json: str
    quoted_evidence_json: str
    confidence: float
    needs_manual_review: bool
    human_review_status: str
    human_review_notes: str
    valid_from_ms: int | None
    valid_to_ms: int | None
    extraction_model: str
    extraction_prompt_version: str
    schema_version: int
    ingested_ts_ms: int


@dataclass(frozen=True)
class ContextRelationshipDecisionRow:
    """Audit row for a relationship decision changed or explained by context."""

    decision_id: str
    relationship_id: str
    context_space_id: str
    context_rule_ids_json: str
    previous_validation_status: str
    new_validation_status: str
    previous_strategy_eligibility: str
    new_strategy_eligibility: str
    strategy_lane: str
    decision_reason: str
    evidence_summary: str
    schema_version: int
    ingested_ts_ms: int


class ContextSourcesRepository(Protocol):
    def append(self, row: ContextSourceRow) -> None: ...
    def append_many(self, rows: Iterable[ContextSourceRow]) -> int: ...
    def get_latest(self, context_source_id: str) -> ContextSourceRow | None: ...
    def iter_latest(self) -> Iterator[ContextSourceRow]: ...


class ContextDocumentsRepository(Protocol):
    def append(self, row: ContextDocumentRow) -> None: ...
    def append_many(self, rows: Iterable[ContextDocumentRow]) -> int: ...
    def get_latest(self, context_document_id: str) -> ContextDocumentRow | None: ...
    def iter_latest(self) -> Iterator[ContextDocumentRow]: ...


class ContextRulesRepository(Protocol):
    def append(self, row: ContextRuleRow) -> None: ...
    def append_many(self, rows: Iterable[ContextRuleRow]) -> int: ...
    def get_latest(self, context_rule_id: str) -> ContextRuleRow | None: ...
    def iter_latest(self) -> Iterator[ContextRuleRow]: ...
    def iter_for_space(self, context_space_id: str) -> Iterator[ContextRuleRow]: ...


class ContextRelationshipDecisionsRepository(Protocol):
    def append(self, row: ContextRelationshipDecisionRow) -> None: ...
    def append_many(self, rows: Iterable[ContextRelationshipDecisionRow]) -> int: ...
    def iter_latest(self) -> Iterator[ContextRelationshipDecisionRow]: ...
    def iter_for_relationship(self, relationship_id: str) -> Iterator[ContextRelationshipDecisionRow]: ...
