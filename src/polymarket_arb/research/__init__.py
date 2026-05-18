"""Space-centric research pipeline.

This module is the home of the Space Research and Optimisation reporting
pipeline:

  row_contracts     — typed Pydantic row models with hard validation rules
  space_sweep       — aggregator + A/B/C/D/E/F grade classifier
  space_optimisation — per-space parameter grid runner

The pipeline never mixes diagnostic-only rows (e.g. ``same_topic_no_trade``)
into trade or PnL totals.  Strategy-family attribution is only granted to
strategy-eligible relationships.

RESEARCH-ONLY — no live trading, wallets, signing, or order placement.
"""

from .row_contracts import (  # noqa: F401
    AcceptedSimulatedTradeRow,
    BlockedOpportunityRow,
    BundleAcceptedTradeRow,
    BundleDiagnosticRow,
    DiagnosticOnlyRelationshipRow,
    GrossOpportunityRow,
    NetOpportunityRow,
    RelationshipAuditRow,
    SpaceOptimisationRow,
    SpaceSummaryRow,
    StrategyEligibleRelationshipRow,
    ViolationWindowRow,
    is_diagnostic_only_subtype,
    space_id_for,
)
