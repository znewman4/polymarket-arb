"""Pinned PyArrow schemas for every normalised table.

Schemas are versioned. A schema bump introduces a new constant (e.g.
``MARKETS_SCHEMA_V2``); we never mutate the existing one. Old data carries
its own ``schema_version`` row so reads can branch.
"""

from __future__ import annotations

import pyarrow as pa

# 4-decimal Polymarket prices, room for sizes up to a billion shares.
PRICE_TYPE = pa.decimal128(38, 8)
SIZE_TYPE = pa.decimal128(38, 8)

LEVEL_STRUCT = pa.struct([
    pa.field("price", PRICE_TYPE),
    pa.field("size", SIZE_TYPE),
])

MARKETS_SCHEMA_V1 = pa.schema([
    pa.field("id", pa.string()),
    pa.field("condition_id", pa.string()),
    pa.field("slug", pa.string()),
    pa.field("question", pa.string()),
    pa.field("description", pa.string()),
    pa.field("end_date_ms", pa.int64()),
    pa.field("start_date_ms", pa.int64()),
    pa.field("closed_at_ms", pa.int64()),       # nullable
    pa.field("resolved_at_ms", pa.int64()),     # nullable
    pa.field("active", pa.bool_()),
    pa.field("closed", pa.bool_()),
    pa.field("archived", pa.bool_()),
    pa.field("outcomes", pa.list_(pa.string())),
    # Renamed from `outcome_prices` to make it explicit this is Gamma's
    # stale snapshot, not CLOB live prices. Phase 4 fusion must use BestQuote.
    pa.field("gamma_outcome_prices_snapshot", pa.list_(PRICE_TYPE)),
    pa.field("clob_token_ids", pa.list_(pa.string())),
    pa.field("volume", PRICE_TYPE),
    pa.field("liquidity", PRICE_TYPE),
    pa.field("event_id", pa.string()),
    pa.field("neg_risk", pa.bool_()),
    pa.field("text_hash", pa.string()),         # sha256(question + description)
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

EVENTS_SCHEMA_V1 = pa.schema([
    pa.field("id", pa.string()),
    pa.field("ticker", pa.string()),
    pa.field("slug", pa.string()),
    pa.field("title", pa.string()),
    pa.field("description", pa.string()),
    pa.field("start_date_ms", pa.int64()),
    pa.field("end_date_ms", pa.int64()),
    pa.field("market_ids", pa.list_(pa.string())),
    pa.field("tags", pa.list_(pa.string())),
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

ORDERBOOK_SNAPSHOTS_SCHEMA_V1 = pa.schema([
    pa.field("token_id", pa.string()),
    pa.field("condition_id", pa.string()),
    pa.field("market_slug", pa.string()),
    pa.field("timestamp_ms", pa.int64()),
    pa.field("bids", pa.list_(LEVEL_STRUCT)),
    pa.field("asks", pa.list_(LEVEL_STRUCT)),
    pa.field("book_hash", pa.string()),
    pa.field("source", pa.string()),
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

BEST_QUOTES_SCHEMA_V1 = pa.schema([
    pa.field("token_id", pa.string()),
    pa.field("timestamp_ms", pa.int64()),
    pa.field("best_bid", PRICE_TYPE),
    pa.field("best_bid_size", SIZE_TYPE),
    pa.field("best_ask", PRICE_TYPE),
    pa.field("best_ask_size", SIZE_TYPE),
    pa.field("midpoint", PRICE_TYPE),
    pa.field("spread", PRICE_TYPE),
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

ORDER_EVENTS_SCHEMA_V1 = pa.schema([
    pa.field("event_id", pa.string()),
    pa.field("event_ts_ms", pa.int64()),
    pa.field("order_id", pa.string()),
    pa.field("strategy_id", pa.string()),
    pa.field("kind", pa.string()),
    pa.field("token_id", pa.string()),
    pa.field("side", pa.string()),
    pa.field("price", PRICE_TYPE),
    pa.field("size", SIZE_TYPE),
    pa.field("payload_json", pa.string()),  # opaque blob, easier than nested struct
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

FILL_EVENTS_SCHEMA_V1 = pa.schema([
    pa.field("event_id", pa.string()),
    pa.field("event_ts_ms", pa.int64()),
    pa.field("order_id", pa.string()),
    pa.field("fill_id", pa.string()),
    pa.field("token_id", pa.string()),
    pa.field("side", pa.string()),
    pa.field("price", PRICE_TYPE),
    pa.field("size", SIZE_TYPE),
    pa.field("fee", PRICE_TYPE),
    pa.field("payload_json", pa.string()),
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

POSITION_SNAPSHOTS_SCHEMA_V1 = pa.schema([
    pa.field("event_id", pa.string()),
    pa.field("event_ts_ms", pa.int64()),
    pa.field("token_id", pa.string()),
    pa.field("quantity", SIZE_TYPE),
    pa.field("avg_entry_price", PRICE_TYPE),
    pa.field("realised_pnl", PRICE_TYPE),
    pa.field("unrealised_pnl", PRICE_TYPE),
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

RISK_SNAPSHOTS_SCHEMA_V1 = pa.schema([
    pa.field("event_id", pa.string()),
    pa.field("event_ts_ms", pa.int64()),
    pa.field("strategy_id", pa.string()),
    pa.field("order_id", pa.string()),
    pa.field("overall", pa.string()),
    # Each check is JSON-serialised so we don't fight pyarrow over nested unions.
    pa.field("checks_json", pa.string()),
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

# ─── Phase 1.5: AI-derived market semantics ────────────────────────────────
# We persist controlled advisory fields + hashes only. Raw model response
# text is NEVER stored (see docs/trade_gate.md and storage.md). The optional
# debug capture under data/debug/nlp_thinking/ is gitignored and is read by
# zero downstream code.
MARKET_SEMANTICS_SCHEMA_V1 = pa.schema([
    pa.field("source_market_id", pa.string()),
    pa.field("source_condition_id", pa.string()),
    pa.field("question", pa.string()),
    pa.field("canonical_question", pa.string()),
    pa.field("market_type", pa.string()),
    pa.field("subject_entities", pa.list_(pa.string())),
    pa.field("event_entities", pa.list_(pa.string())),
    pa.field("temporal_phrase", pa.string()),
    pa.field("temporal_phrase_normalized", pa.string()),
    pa.field("temporal_resolution", pa.string()),
    pa.field("exact_deadline_ms", pa.int64()),
    pa.field("date_constraints_json", pa.string()),
    pa.field("jurisdiction", pa.string()),
    pa.field("positive_resolution_condition", pa.string()),
    pa.field("negative_resolution_condition", pa.string()),
    pa.field("necessary_conditions_for_yes", pa.list_(pa.string())),
    pa.field("sufficient_conditions_for_yes", pa.list_(pa.string())),
    pa.field("necessary_conditions_for_no", pa.list_(pa.string())),
    pa.field("sufficient_conditions_for_no", pa.list_(pa.string())),
    pa.field("evidence_required", pa.list_(pa.string())),
    pa.field("ambiguity_flags", pa.list_(pa.string())),
    pa.field("ambiguity_score", pa.float64()),       # set by Phase 1.6
    pa.field("semantic_confidence", pa.float64()),
    pa.field("needs_manual_review", pa.bool_()),
    # Controlled advisory explanation fields (these REPLACE chain-of-thought).
    pa.field("explanation_summary", pa.string()),
    pa.field("flag_rationales_json", pa.string()),
    pa.field("uncertainty_notes_json", pa.string()),
    pa.field("rule_curation_notes_json", pa.string()),
    # Audit / reproducibility (hashes only; no raw text).
    pa.field("raw_response_hash", pa.string()),
    pa.field("model_name", pa.string()),
    pa.field("prompt_version", pa.string()),
    pa.field("rulebook_id", pa.string()),
    pa.field("rulebook_version", pa.int32()),
    pa.field("extraction_id", pa.string()),
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

MARKET_SEMANTICS_SCHEMA_V2 = pa.schema([
    # All v1 fields preserved exactly
    pa.field("source_market_id", pa.string()),
    pa.field("source_condition_id", pa.string()),
    pa.field("question", pa.string()),
    pa.field("canonical_question", pa.string()),
    pa.field("market_type", pa.string()),
    pa.field("subject_entities", pa.list_(pa.string())),
    pa.field("event_entities", pa.list_(pa.string())),
    pa.field("temporal_phrase", pa.string()),
    pa.field("temporal_phrase_normalized", pa.string()),
    pa.field("temporal_resolution", pa.string()),
    pa.field("exact_deadline_ms", pa.int64()),
    pa.field("date_constraints_json", pa.string()),
    pa.field("jurisdiction", pa.string()),
    pa.field("positive_resolution_condition", pa.string()),
    pa.field("negative_resolution_condition", pa.string()),
    pa.field("necessary_conditions_for_yes", pa.list_(pa.string())),
    pa.field("sufficient_conditions_for_yes", pa.list_(pa.string())),
    pa.field("necessary_conditions_for_no", pa.list_(pa.string())),
    pa.field("sufficient_conditions_for_no", pa.list_(pa.string())),
    pa.field("evidence_required", pa.list_(pa.string())),
    pa.field("ambiguity_flags", pa.list_(pa.string())),
    pa.field("ambiguity_score", pa.float64()),
    pa.field("semantic_confidence", pa.float64()),
    pa.field("needs_manual_review", pa.bool_()),
    pa.field("explanation_summary", pa.string()),
    pa.field("flag_rationales_json", pa.string()),
    pa.field("uncertainty_notes_json", pa.string()),
    pa.field("rule_curation_notes_json", pa.string()),
    pa.field("raw_response_hash", pa.string()),
    pa.field("model_name", pa.string()),
    pa.field("prompt_version", pa.string()),
    pa.field("rulebook_id", pa.string()),
    pa.field("rulebook_version", pa.int32()),
    pa.field("extraction_id", pa.string()),
    # Phase 5.5 D+ terms-aware fields (all nullable)
    pa.field("event_atoms_json", pa.string()),          # nullable
    pa.field("proposition_json", pa.string()),          # nullable
    pa.field("outcome_space_json", pa.string()),        # nullable
    pa.field("tie_rule", pa.string()),                  # nullable
    pa.field("if_event_never_occurs_rule", pa.string()),# nullable
    pa.field("resolution_source", pa.string()),         # nullable
    pa.field("timezone_or_boundary", pa.string()),      # nullable
    pa.field("terms_confidence", pa.float64()),
    pa.field("long_horizon", pa.bool_()),
    pa.field("unresolved_reference_event", pa.bool_()),
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

MARKET_EMBEDDINGS_SCHEMA_V1 = pa.schema([
    pa.field("market_id", pa.string()),
    pa.field("embedding_space", pa.string()),       # e.g. "nomic-embed-text@v1.5"
    pa.field("text_hash", pa.string()),             # sha256 of text fed in
    pa.field("dimensions", pa.int32()),
    pa.field("vector", pa.list_(pa.float32())),
    pa.field("model_name", pa.string()),
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

NLP_VALIDATION_FAILURES_SCHEMA_V1 = pa.schema([
    pa.field("failure_id", pa.string()),
    pa.field("market_id", pa.string()),
    pa.field("model_name", pa.string()),
    pa.field("prompt_version", pa.string()),
    pa.field("prompt_hash", pa.string()),
    pa.field("raw_response_hash", pa.string()),     # hash only — no raw text
    pa.field("failure_kind", pa.string()),          # invalid_json | schema_violation | thinking_filter_failed | http_error
    pa.field("validation_error_json", pa.string()),
    pa.field("attempted_ts_ms", pa.int64()),
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

MARKET_IMPLICATIONS_SCHEMA_V1 = pa.schema([
    pa.field("implication_id", pa.string()),
    pa.field("market_id", pa.string()),
    pa.field("extraction_id", pa.string()),
    pa.field("implication_type", pa.string()),
    pa.field("statement", pa.string()),
    pa.field("extracted_label", pa.string()),
    pa.field("deterministic_score", pa.float64()),
    pa.field("model_confidence", pa.float64()),
    pa.field("final_confidence", pa.float64()),
    pa.field("ambiguity_flags", pa.list_(pa.string())),
    pa.field("needs_manual_review", pa.bool_()),
    pa.field("source", pa.string()),
    pa.field("model_name", pa.string()),
    pa.field("prompt_version", pa.string()),
    pa.field("rulebook_id", pa.string()),
    pa.field("rulebook_version", pa.int32()),
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

RULEBOOK_EVALUATIONS_SCHEMA_V1 = pa.schema([
    pa.field("evaluation_id", pa.string()),
    pa.field("extraction_id", pa.string()),
    pa.field("market_id", pa.string()),
    pa.field("rulebook_id", pa.string()),
    pa.field("rulebook_version", pa.int32()),
    pa.field("rulebook_content_hash", pa.string()),
    pa.field("score", pa.float64()),
    pa.field("subscores_json", pa.string()),
    pa.field("flags", pa.list_(pa.string())),
    pa.field("evaluated_ts_ms", pa.int64()),
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

MARKET_SCORES_SCHEMA_V1 = pa.schema([
    pa.field("market_id", pa.string()),
    pa.field("model_probability_placeholder", pa.float64()),
    pa.field("market_midpoint", pa.float64()),
    pa.field("spread", pa.float64()),
    pa.field("liquidity_score", pa.float64()),
    pa.field("semantic_confidence", pa.float64()),
    pa.field("ambiguity_score", pa.float64()),
    pa.field("implication_quality_score", pa.float64()),
    pa.field("resolution_risk_score", pa.float64()),
    pa.field("evidence_quality_score", pa.float64()),
    pa.field("freshness_score", pa.float64()),
    pa.field("final_signal_score", pa.float64()),
    pa.field("recommendation", pa.string()),
    pa.field("explanation_json", pa.string()),
    pa.field("rulebook_id", pa.string()),
    pa.field("rulebook_version", pa.int32()),
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

# ─── Phase 5.5 backfill schemas ────────────────────────────────────────────

PRICE_HISTORY_SCHEMA_V1 = pa.schema([
    pa.field("market_id", pa.string()),
    pa.field("condition_id", pa.string()),        # nullable
    pa.field("token_id", pa.string()),
    pa.field("outcome", pa.string()),             # nullable
    pa.field("ts_ms", pa.int64()),
    pa.field("price", PRICE_TYPE),
    pa.field("source", pa.string()),              # "clob" | "gamma" | "recorded_book"
    pa.field("fidelity", pa.string()),            # interval string used when fetching
    pa.field("interval", pa.string()),
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

TRADE_HISTORY_SCHEMA_V1 = pa.schema([
    pa.field("market_id", pa.string()),
    pa.field("condition_id", pa.string()),
    pa.field("token_id", pa.string()),
    pa.field("outcome", pa.string()),
    pa.field("trade_ts_ms", pa.int64()),
    pa.field("price", PRICE_TYPE),
    pa.field("size", SIZE_TYPE),
    pa.field("side", pa.string()),                # nullable
    pa.field("source", pa.string()),
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

BACKFILL_COVERAGE_SCHEMA_V1 = pa.schema([
    pa.field("market_id", pa.string()),
    pa.field("condition_id", pa.string()),
    pa.field("question", pa.string()),
    pa.field("start_ts_ms", pa.int64()),
    pa.field("end_ts_ms", pa.int64()),
    pa.field("requested_days", pa.int32()),
    pa.field("has_gamma", pa.bool_()),
    pa.field("has_price_history", pa.bool_()),
    pa.field("has_trade_history", pa.bool_()),
    pa.field("has_semantics", pa.bool_()),
    pa.field("has_rulebook_score", pa.bool_()),
    pa.field("has_implications", pa.bool_()),
    pa.field("has_embeddings", pa.bool_()),
    pa.field("has_backfill_coverage", pa.bool_()),
    pa.field("price_points_count", pa.int32()),
    pa.field("trade_points_count", pa.int32()),
    pa.field("first_price_ts_ms", pa.int64()),    # nullable
    pa.field("last_price_ts_ms", pa.int64()),     # nullable
    pa.field("missing_price_gap_count", pa.int32()),
    pa.field("largest_price_gap_ms", pa.int64()),
    pa.field("price_min", PRICE_TYPE),            # nullable
    pa.field("price_max", PRICE_TYPE),            # nullable
    pa.field("price_out_of_bounds_count", pa.int32()),
    pa.field("duplicate_timestamp_count", pa.int32()),
    pa.field("coverage_score", pa.float64()),
    pa.field("recommended_for_backtest", pa.bool_()),
    pa.field("exclusion_reasons_json", pa.string()),  # JSON array
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

# ─── Phase 5.5 Chunks C+D schemas ─────────────────────────────────────────────

RELATIONSHIP_CANDIDATES_SCHEMA_V1 = pa.schema([
    pa.field("relationship_id", pa.string()),
    pa.field("market_id_a", pa.string()),
    pa.field("market_id_b", pa.string()),
    pa.field("condition_id_a", pa.string()),        # nullable
    pa.field("condition_id_b", pa.string()),        # nullable
    pa.field("token_id_a_yes", pa.string()),        # nullable
    pa.field("token_id_a_no", pa.string()),         # nullable
    pa.field("token_id_b_yes", pa.string()),        # nullable
    pa.field("token_id_b_no", pa.string()),         # nullable
    pa.field("question_a", pa.string()),
    pa.field("question_b", pa.string()),
    pa.field("relationship_type", pa.string()),
    pa.field("entity_match_score", pa.float64()),
    pa.field("time_scope_match_score", pa.float64()),
    pa.field("resolution_criteria_match_score", pa.float64()),
    pa.field("threshold_relation_json", pa.string()),
    pa.field("semantic_similarity_score", pa.float64()),   # nullable
    pa.field("deterministic_confidence", pa.float64()),
    pa.field("model_confidence", pa.float64()),
    pa.field("final_confidence", pa.float64()),
    pa.field("validation_status", pa.string()),
    pa.field("rejection_reasons_json", pa.string()),
    pa.field("rationale_summary", pa.string()),
    pa.field("evidence_json", pa.string()),
    pa.field("rulebook_id", pa.string()),
    pa.field("rulebook_version", pa.int32()),
    pa.field("rulebook_content_hash", pa.string()),
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

RELATIONSHIP_CANDIDATES_SCHEMA_V2 = pa.schema([
    # All v1 fields preserved
    pa.field("relationship_id", pa.string()),
    pa.field("market_id_a", pa.string()),
    pa.field("market_id_b", pa.string()),
    pa.field("condition_id_a", pa.string()),
    pa.field("condition_id_b", pa.string()),
    pa.field("token_id_a_yes", pa.string()),
    pa.field("token_id_a_no", pa.string()),
    pa.field("token_id_b_yes", pa.string()),
    pa.field("token_id_b_no", pa.string()),
    pa.field("question_a", pa.string()),
    pa.field("question_b", pa.string()),
    pa.field("relationship_type", pa.string()),
    pa.field("entity_match_score", pa.float64()),
    pa.field("time_scope_match_score", pa.float64()),
    pa.field("resolution_criteria_match_score", pa.float64()),
    pa.field("threshold_relation_json", pa.string()),
    pa.field("semantic_similarity_score", pa.float64()),
    pa.field("deterministic_confidence", pa.float64()),
    pa.field("model_confidence", pa.float64()),
    pa.field("final_confidence", pa.float64()),
    pa.field("validation_status", pa.string()),
    pa.field("rejection_reasons_json", pa.string()),
    pa.field("rationale_summary", pa.string()),
    pa.field("evidence_json", pa.string()),
    pa.field("rulebook_id", pa.string()),
    pa.field("rulebook_version", pa.int32()),
    pa.field("rulebook_content_hash", pa.string()),
    # Phase 5.5 D+ fields (nullable)
    pa.field("relationship_validity_status", pa.string()),
    pa.field("strategy_eligibility_status", pa.string()),
    pa.field("strategy_exclusion_reasons_json", pa.string()),
    pa.field("relationship_family", pa.string()),
    pa.field("proposition_type", pa.string()),         # nullable
    pa.field("event_atoms_json", pa.string()),         # nullable
    pa.field("terms_match_score", pa.float64()),       # nullable
    pa.field("outcome_space_match_score", pa.float64()),# nullable
    pa.field("reference_event_match_score", pa.float64()),# nullable
    pa.field("candidate_a", pa.string()),              # nullable
    pa.field("candidate_b", pa.string()),              # nullable
    pa.field("shared_event", pa.string()),             # nullable
    pa.field("reference_event", pa.string()),          # nullable
    pa.field("relationship_subtype", pa.string()),
    pa.field("outcome_space_id", pa.string()),
    pa.field("outcome_subtype_a", pa.string()),
    pa.field("outcome_subtype_b", pa.string()),
    pa.field("entity_type_a", pa.string()),
    pa.field("entity_type_b", pa.string()),
    pa.field("stage_a", pa.string()),
    pa.field("stage_b", pa.string()),
    pa.field("party_a", pa.string()),                  # nullable
    pa.field("party_b", pa.string()),                  # nullable
    pa.field("team_a", pa.string()),                   # nullable
    pa.field("team_b", pa.string()),                   # nullable
    pa.field("shared_reference_event", pa.string()),   # nullable
    pa.field("classification_reason_json", pa.string()),
    pa.field("mixed_subtype_reason", pa.string()),
    pa.field("strategy_family", pa.string()),
    pa.field("strategy_eligible_reason", pa.string()),
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

STRATEGY_CANDIDATES_SCHEMA_V1 = pa.schema([
    pa.field("candidate_id", pa.string()),
    pa.field("run_id", pa.string()),
    pa.field("relationship_id", pa.string()),
    pa.field("market_id_a", pa.string()),
    pa.field("market_id_b", pa.string()),
    pa.field("token_id_a", pa.string()),
    pa.field("token_id_b", pa.string()),
    pa.field("relationship_type", pa.string()),
    pa.field("signal_ts_ms", pa.int64()),
    pa.field("price_a", PRICE_TYPE),
    pa.field("price_b", PRICE_TYPE),
    pa.field("price_a_ts_ms", pa.int64()),
    pa.field("price_b_ts_ms", pa.int64()),
    pa.field("inequality_violated", pa.string()),
    pa.field("theoretical_edge", PRICE_TYPE),
    pa.field("gross_edge", PRICE_TYPE),
    pa.field("estimated_fee", PRICE_TYPE),
    pa.field("estimated_slippage", PRICE_TYPE),
    pa.field("net_edge_after_costs", PRICE_TYPE),
    pa.field("execution_model", pa.string()),
    pa.field("execution_model_confidence", pa.float64()),
    pa.field("accepted_for_simulation", pa.bool_()),
    pa.field("rejection_reason", pa.string()),      # nullable
    pa.field("simulated_position_json", pa.string()),
    pa.field("stake_usdc", PRICE_TYPE),
    pa.field("expected_payout_json", pa.string()),
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

SIMULATED_TRADES_SCHEMA_V1 = pa.schema([
    pa.field("trade_id", pa.string()),
    pa.field("candidate_id", pa.string()),
    pa.field("run_id", pa.string()),
    pa.field("relationship_id", pa.string()),
    pa.field("leg", pa.string()),
    pa.field("token_id", pa.string()),
    pa.field("market_id", pa.string()),
    pa.field("side", pa.string()),
    pa.field("fill_ts_ms", pa.int64()),
    pa.field("fill_price", PRICE_TYPE),
    pa.field("shares", SIZE_TYPE),
    pa.field("notional_usdc", PRICE_TYPE),
    pa.field("fees_usdc", PRICE_TYPE),
    pa.field("slippage_cost_usdc", PRICE_TYPE),
    pa.field("execution_model", pa.string()),
    pa.field("mark_to_market_ts_ms", pa.int64()),          # nullable
    pa.field("mark_to_market_value_usdc", PRICE_TYPE),     # nullable
    pa.field("resolution_ts_ms", pa.int64()),              # nullable
    pa.field("resolution_outcome", pa.string()),           # nullable
    pa.field("realised_pnl_usdc", PRICE_TYPE),             # nullable
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

BACKTEST_METRICS_SCHEMA_V1 = pa.schema([
    pa.field("run_id", pa.string()),
    pa.field("config_hash", pa.string()),
    pa.field("starting_cash_usdc", PRICE_TYPE),
    pa.field("ending_cash_usdc", PRICE_TYPE),
    pa.field("ending_equity_usdc", PRICE_TYPE),
    pa.field("total_return_pct", pa.float64()),
    pa.field("gross_pnl_usdc", PRICE_TYPE),
    pa.field("net_pnl_usdc", PRICE_TYPE),
    pa.field("total_fees_usdc", PRICE_TYPE),
    pa.field("total_slippage_usdc", PRICE_TYPE),
    pa.field("relationships_considered", pa.int32()),
    pa.field("signals_generated", pa.int32()),
    pa.field("candidates_accepted", pa.int32()),
    pa.field("candidates_rejected", pa.int32()),
    pa.field("trades_executed", pa.int32()),
    pa.field("rejection_reason_counts_json", pa.string()),
    pa.field("win_rate_when_resolved", pa.float64()),      # nullable
    pa.field("avg_gross_edge", pa.float64()),
    pa.field("avg_net_edge", pa.float64()),
    pa.field("avg_hold_time_ms", pa.float64()),            # nullable
    pa.field("max_drawdown_pct", pa.float64()),
    pa.field("sharpe_like", pa.float64()),                 # nullable
    pa.field("pnl_by_relationship_type_json", pa.string()),
    pa.field("pnl_by_execution_model_json", pa.string()),
    pa.field("pnl_by_confidence_bucket_json", pa.string()),
    pa.field("null_baseline_pnl_usdc", PRICE_TYPE),        # nullable
    pa.field("null_baseline_win_rate", pa.float64()),      # nullable
    pa.field("credibility_label", pa.string()),
    pa.field("credibility_rationale", pa.string()),
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

CONTEXT_SOURCES_SCHEMA_V1 = pa.schema([
    pa.field("context_source_id", pa.string()),
    pa.field("context_space_id", pa.string()),
    pa.field("source_type", pa.string()),
    pa.field("source_tier", pa.int32()),
    pa.field("title", pa.string()),
    pa.field("url", pa.string()),
    pa.field("domain", pa.string()),
    pa.field("publisher", pa.string()),
    pa.field("retrieved_at_ms", pa.int64()),
    pa.field("effective_start_ms", pa.int64()),
    pa.field("effective_end_ms", pa.int64()),
    pa.field("raw_path", pa.string()),
    pa.field("content_hash", pa.string()),
    pa.field("status", pa.string()),
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

CONTEXT_DOCUMENTS_SCHEMA_V1 = pa.schema([
    pa.field("context_document_id", pa.string()),
    pa.field("context_source_id", pa.string()),
    pa.field("context_space_id", pa.string()),
    pa.field("url", pa.string()),
    pa.field("title", pa.string()),
    pa.field("retrieved_at_ms", pa.int64()),
    pa.field("raw_path", pa.string()),
    pa.field("cleaned_text_path", pa.string()),
    pa.field("content_excerpt", pa.string()),
    pa.field("content_hash", pa.string()),
    pa.field("extraction_status", pa.string()),
    pa.field("error_message", pa.string()),
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

CONTEXT_RULES_SCHEMA_V1 = pa.schema([
    pa.field("context_rule_id", pa.string()),
    pa.field("context_space_id", pa.string()),
    pa.field("context_type", pa.string()),
    pa.field("rule_type", pa.string()),
    pa.field("rule_json", pa.string()),
    pa.field("source_document_ids_json", pa.string()),
    pa.field("quoted_evidence_json", pa.string()),
    pa.field("confidence", pa.float64()),
    pa.field("needs_manual_review", pa.bool_()),
    pa.field("human_review_status", pa.string()),
    pa.field("human_review_notes", pa.string()),
    pa.field("valid_from_ms", pa.int64()),
    pa.field("valid_to_ms", pa.int64()),
    pa.field("extraction_model", pa.string()),
    pa.field("extraction_prompt_version", pa.string()),
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

CONTEXT_RELATIONSHIP_DECISIONS_SCHEMA_V1 = pa.schema([
    pa.field("decision_id", pa.string()),
    pa.field("relationship_id", pa.string()),
    pa.field("context_space_id", pa.string()),
    pa.field("context_rule_ids_json", pa.string()),
    pa.field("previous_validation_status", pa.string()),
    pa.field("new_validation_status", pa.string()),
    pa.field("previous_strategy_eligibility", pa.string()),
    pa.field("new_strategy_eligibility", pa.string()),
    pa.field("strategy_lane", pa.string()),
    pa.field("decision_reason", pa.string()),
    pa.field("evidence_summary", pa.string()),
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
])

ALL_SCHEMAS = {
    "markets": MARKETS_SCHEMA_V1,
    "events": EVENTS_SCHEMA_V1,
    "orderbook_snapshots": ORDERBOOK_SNAPSHOTS_SCHEMA_V1,
    "best_quotes": BEST_QUOTES_SCHEMA_V1,
    "order_events": ORDER_EVENTS_SCHEMA_V1,
    "fill_events": FILL_EVENTS_SCHEMA_V1,
    "position_snapshots": POSITION_SNAPSHOTS_SCHEMA_V1,
    "risk_snapshots": RISK_SNAPSHOTS_SCHEMA_V1,
    "market_semantics": MARKET_SEMANTICS_SCHEMA_V1,
    "market_embeddings": MARKET_EMBEDDINGS_SCHEMA_V1,
    "nlp_validation_failures": NLP_VALIDATION_FAILURES_SCHEMA_V1,
    "market_implications": MARKET_IMPLICATIONS_SCHEMA_V1,
    "rulebook_evaluations": RULEBOOK_EVALUATIONS_SCHEMA_V1,
    "market_scores": MARKET_SCORES_SCHEMA_V1,
    "price_history": PRICE_HISTORY_SCHEMA_V1,
    "trade_history": TRADE_HISTORY_SCHEMA_V1,
    "backfill_coverage": BACKFILL_COVERAGE_SCHEMA_V1,
    "relationship_candidates": RELATIONSHIP_CANDIDATES_SCHEMA_V1,
    "strategy_candidates": STRATEGY_CANDIDATES_SCHEMA_V1,
    "simulated_trades": SIMULATED_TRADES_SCHEMA_V1,
    "backtest_metrics": BACKTEST_METRICS_SCHEMA_V1,
    "context_sources": CONTEXT_SOURCES_SCHEMA_V1,
    "context_documents": CONTEXT_DOCUMENTS_SCHEMA_V1,
    "context_rules": CONTEXT_RULES_SCHEMA_V1,
    "context_relationship_decisions": CONTEXT_RELATIONSHIP_DECISIONS_SCHEMA_V1,
    "orders_log": None,  # populated below
    "positions": None,  # populated below
}

ORDERS_LOG_SCHEMA_V1 = pa.schema([
    pa.field("intent_id", pa.string()),
    pa.field("ts_ms", pa.int64()),
    pa.field("strategy_id", pa.string()),
    pa.field("token_id", pa.string()),
    pa.field("market_id", pa.string()),
    pa.field("side", pa.string()),
    pa.field("requested_size", pa.string()),
    pa.field("filled_size", pa.string()),
    pa.field("avg_fill_price", pa.string()),  # nullable
    pa.field("notional_usdc", pa.string()),
    pa.field("fees_usdc", pa.string()),
    pa.field("status", pa.string()),
    pa.field("reason", pa.string()),
    pa.field("paper_mode", pa.bool_()),
    pa.field("kill_switch_active", pa.bool_()),
    pa.field("orders_allowed", pa.bool_()),
    pa.field("preflight_passed", pa.bool_()),
    pa.field("preflight_token_id", pa.string()),  # nullable
    pa.field("http_status", pa.int32()),  # nullable
    pa.field("source_lane", pa.string()),
    pa.field("source_relationship_id", pa.string()),
    pa.field("source_hypothesis_id", pa.string()),
    pa.field("schema_version", pa.int32()),
    pa.field("ingested_ts_ms", pa.int64()),
    pa.field("notes", pa.string()),
    pa.field("detail_json", pa.string()),
])
ALL_SCHEMAS["orders_log"] = ORDERS_LOG_SCHEMA_V1

POSITIONS_SCHEMA_V1 = pa.schema([
    pa.field("position_id", pa.string()),
    pa.field("strategy_id", pa.string()),
    pa.field("market_id", pa.string()),
    pa.field("token_id", pa.string()),
    pa.field("side", pa.string()),
    pa.field("open_ts_ms", pa.int64()),
    pa.field("entry_price", pa.string()),
    pa.field("size", pa.string()),
    pa.field("notional_usdc", pa.string()),
    pa.field("source_relationship_id", pa.string()),
    pa.field("notes", pa.string()),
    pa.field("status", pa.string()),
    pa.field("close_ts_ms", pa.int64()),  # nullable
    pa.field("close_price", pa.string()),  # nullable
    pa.field("realised_pnl", pa.string()),  # nullable
    pa.field("schema_version", pa.int32()),
])
ALL_SCHEMAS["positions"] = POSITIONS_SCHEMA_V1
