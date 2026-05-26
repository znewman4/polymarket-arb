"""DuckDB view definitions over the Parquet lake.

Each entry is a CREATE-OR-REPLACE-VIEW SQL string with one ``{root}``
placeholder for the absolute data root. The engine registers views lazily on
first connect; views over tables that have no data yet are silently skipped.
"""

from __future__ import annotations

VIEW_DEFINITIONS = {
    # --- read tables expose every part-file ----------------------------------
    "markets_all": (
        "CREATE OR REPLACE VIEW markets_all AS "
        "SELECT * FROM read_parquet('{root}/normalised/markets/dt=*/*.parquet', "
        "hive_partitioning=true)"
    ),
    "events_all": (
        "CREATE OR REPLACE VIEW events_all AS "
        "SELECT * FROM read_parquet('{root}/normalised/events/dt=*/*.parquet', "
        "hive_partitioning=true)"
    ),
    "orderbook_snapshots_all": (
        "CREATE OR REPLACE VIEW orderbook_snapshots_all AS "
        "SELECT * FROM read_parquet('{root}/normalised/orderbook_snapshots/dt=*/*.parquet', "
        "hive_partitioning=true)"
    ),
    "best_quotes_all": (
        "CREATE OR REPLACE VIEW best_quotes_all AS "
        "SELECT * FROM read_parquet('{root}/normalised/best_quotes/dt=*/*.parquet', "
        "hive_partitioning=true)"
    ),
    "order_events_all": (
        "CREATE OR REPLACE VIEW order_events_all AS "
        "SELECT * FROM read_parquet('{root}/normalised/order_events/dt=*/*.parquet', "
        "hive_partitioning=true)"
    ),
    "fill_events_all": (
        "CREATE OR REPLACE VIEW fill_events_all AS "
        "SELECT * FROM read_parquet('{root}/normalised/fill_events/dt=*/*.parquet', "
        "hive_partitioning=true)"
    ),
    "position_snapshots_all": (
        "CREATE OR REPLACE VIEW position_snapshots_all AS "
        "SELECT * FROM read_parquet('{root}/normalised/position_snapshots/dt=*/*.parquet', "
        "hive_partitioning=true)"
    ),
    "risk_snapshots_all": (
        "CREATE OR REPLACE VIEW risk_snapshots_all AS "
        "SELECT * FROM read_parquet('{root}/normalised/risk_snapshots/dt=*/*.parquet', "
        "hive_partitioning=true)"
    ),
    # --- "latest state" views over append-only tables ------------------------
    "markets_latest": (
        "CREATE OR REPLACE VIEW markets_latest AS "
        "SELECT * EXCLUDE rn FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY id ORDER BY ingested_ts_ms DESC) AS rn"
        "  FROM read_parquet('{root}/normalised/markets/dt=*/*.parquet', hive_partitioning=true)"
        ") WHERE rn = 1"
    ),
    "events_latest": (
        "CREATE OR REPLACE VIEW events_latest AS "
        "SELECT * EXCLUDE rn FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY id ORDER BY ingested_ts_ms DESC) AS rn"
        "  FROM read_parquet('{root}/normalised/events/dt=*/*.parquet', hive_partitioning=true)"
        ") WHERE rn = 1"
    ),
    "positions_latest": (
        "CREATE OR REPLACE VIEW positions_latest AS "
        "SELECT * EXCLUDE rn FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY token_id ORDER BY event_ts_ms DESC) AS rn"
        "  FROM read_parquet('{root}/normalised/position_snapshots/dt=*/*.parquet', "
        "  hive_partitioning=true)"
        ") WHERE rn = 1"
    ),
    # Derived: only markets whose flags AND end-date say they are currently active.
    "active_markets_latest": (
        "CREATE OR REPLACE VIEW active_markets_latest AS "
        "SELECT * FROM markets_latest "
        "WHERE active = true AND closed = false "
        "  AND (end_date_ms IS NULL OR end_date_ms > epoch_ms(now()))"
    ),
    # ─── Phase 1.5 NLP views ──────────────────────────────────────────────
    "market_semantics_all": (
        "CREATE OR REPLACE VIEW market_semantics_all AS "
        "SELECT * FROM read_parquet('{root}/normalised/market_semantics/dt=*/*.parquet', "
        "hive_partitioning=true)"
    ),
    "market_semantics_latest": (
        "CREATE OR REPLACE VIEW market_semantics_latest AS "
        "SELECT * EXCLUDE rn FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY source_market_id "
        "    ORDER BY ingested_ts_ms DESC) AS rn"
        "  FROM read_parquet('{root}/normalised/market_semantics/dt=*/*.parquet', "
        "  hive_partitioning=true, union_by_name=true)"
        ") WHERE rn = 1"
    ),
    "market_embeddings_all": (
        "CREATE OR REPLACE VIEW market_embeddings_all AS "
        "SELECT * FROM read_parquet('{root}/normalised/market_embeddings/dt=*/*.parquet', "
        "hive_partitioning=true)"
    ),
    "market_embeddings_latest": (
        "CREATE OR REPLACE VIEW market_embeddings_latest AS "
        "SELECT * EXCLUDE rn FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY market_id, embedding_space "
        "    ORDER BY ingested_ts_ms DESC) AS rn"
        "  FROM read_parquet('{root}/normalised/market_embeddings/dt=*/*.parquet', "
        "  hive_partitioning=true)"
        ") WHERE rn = 1"
    ),
    "nlp_validation_failures_all": (
        "CREATE OR REPLACE VIEW nlp_validation_failures_all AS "
        "SELECT * FROM read_parquet("
        "  '{root}/normalised/nlp_validation_failures/dt=*/*.parquet', hive_partitioning=true)"
    ),
    "market_implications_all": (
        "CREATE OR REPLACE VIEW market_implications_all AS "
        "SELECT * FROM read_parquet('{root}/normalised/market_implications/dt=*/*.parquet', "
        "hive_partitioning=true)"
    ),
    "rulebook_evaluations_all": (
        "CREATE OR REPLACE VIEW rulebook_evaluations_all AS "
        "SELECT * FROM read_parquet('{root}/normalised/rulebook_evaluations/dt=*/*.parquet', "
        "hive_partitioning=true)"
    ),
    "rulebook_evaluations_latest": (
        "CREATE OR REPLACE VIEW rulebook_evaluations_latest AS "
        "SELECT * EXCLUDE rn FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY extraction_id, rulebook_id "
        "    ORDER BY evaluated_ts_ms DESC, ingested_ts_ms DESC) AS rn"
        "  FROM read_parquet('{root}/normalised/rulebook_evaluations/dt=*/*.parquet', "
        "  hive_partitioning=true)"
        ") WHERE rn = 1"
    ),
    "market_scores_all": (
        "CREATE OR REPLACE VIEW market_scores_all AS "
        "SELECT * FROM read_parquet('{root}/normalised/market_scores/dt=*/*.parquet', "
        "hive_partitioning=true)"
    ),
    "market_scores_latest": (
        "CREATE OR REPLACE VIEW market_scores_latest AS "
        "SELECT * EXCLUDE rn FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY market_id "
        "    ORDER BY ingested_ts_ms DESC) AS rn"
        "  FROM read_parquet('{root}/normalised/market_scores/dt=*/*.parquet', "
        "  hive_partitioning=true)"
        ") WHERE rn = 1"
    ),
    # ─── Phase 5.5 backfill views ──────────────────────────────────────────
    "price_history_all": (
        "CREATE OR REPLACE VIEW price_history_all AS "
        "SELECT * FROM read_parquet('{root}/normalised/price_history/dt=*/*.parquet', "
        "hive_partitioning=true)"
    ),
    "price_history_latest": (
        "CREATE OR REPLACE VIEW price_history_latest AS "
        "SELECT * EXCLUDE rn FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY token_id, ts_ms "
        "    ORDER BY ingested_ts_ms DESC) AS rn"
        "  FROM read_parquet('{root}/normalised/price_history/dt=*/*.parquet', "
        "  hive_partitioning=true)"
        ") WHERE rn = 1"
    ),
    "trade_history_all": (
        "CREATE OR REPLACE VIEW trade_history_all AS "
        "SELECT * FROM read_parquet('{root}/normalised/trade_history/dt=*/*.parquet', "
        "hive_partitioning=true)"
    ),
    "backfill_coverage_all": (
        "CREATE OR REPLACE VIEW backfill_coverage_all AS "
        "SELECT * FROM read_parquet('{root}/normalised/backfill_coverage/dt=*/*.parquet', "
        "hive_partitioning=true)"
    ),
    "backfill_coverage_latest": (
        "CREATE OR REPLACE VIEW backfill_coverage_latest AS "
        "SELECT * EXCLUDE rn FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY market_id "
        "    ORDER BY ingested_ts_ms DESC) AS rn"
        "  FROM read_parquet('{root}/normalised/backfill_coverage/dt=*/*.parquet', "
        "  hive_partitioning=true)"
        ") WHERE rn = 1"
    ),
    # ─── Phase 5.5 Chunks C+D views ───────────────────────────────────────────
    "relationship_candidates_all": (
        "CREATE OR REPLACE VIEW relationship_candidates_all AS "
        "SELECT * FROM read_parquet("
        "  '{root}/normalised/relationship_candidates/dt=*/*.parquet', hive_partitioning=true)"
    ),
    "relationship_candidates_latest": (
        "CREATE OR REPLACE VIEW relationship_candidates_latest AS "
        "SELECT * EXCLUDE rn FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY relationship_id "
        "    ORDER BY ingested_ts_ms DESC) AS rn"
        "  FROM read_parquet("
        "    '{root}/normalised/relationship_candidates/dt=*/*.parquet', hive_partitioning=true)"
        ") WHERE rn = 1"
    ),
    "accepted_relationship_candidates_latest": (
        "CREATE OR REPLACE VIEW accepted_relationship_candidates_latest AS "
        "SELECT * EXCLUDE rn FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY relationship_id "
        "    ORDER BY ingested_ts_ms DESC) AS rn"
        "  FROM read_parquet("
        "    '{root}/normalised/relationship_candidates/dt=*/*.parquet', hive_partitioning=true)"
        ") WHERE rn = 1 AND validation_status = 'accepted'"
    ),
    "strategy_candidates_all": (
        "CREATE OR REPLACE VIEW strategy_candidates_all AS "
        "SELECT * FROM read_parquet("
        "  '{root}/normalised/strategy_candidates/dt=*/*.parquet', hive_partitioning=true)"
    ),
    "accepted_strategy_candidates_latest": (
        "CREATE OR REPLACE VIEW accepted_strategy_candidates_latest AS "
        "SELECT * EXCLUDE rn FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY candidate_id "
        "    ORDER BY ingested_ts_ms DESC) AS rn"
        "  FROM read_parquet("
        "    '{root}/normalised/strategy_candidates/dt=*/*.parquet', hive_partitioning=true)"
        ") WHERE rn = 1 AND accepted_for_simulation = true"
    ),
    "simulated_trades_all": (
        "CREATE OR REPLACE VIEW simulated_trades_all AS "
        "SELECT * FROM read_parquet("
        "  '{root}/normalised/simulated_trades/dt=*/*.parquet', hive_partitioning=true)"
    ),
    "orders_log_all": (
        "CREATE OR REPLACE VIEW orders_log_all AS "
        "SELECT * FROM read_parquet("
        "  '{root}/normalised/orders_log/dt=*/*.parquet', hive_partitioning=true)"
    ),
    "orders_log_recent": (
        "CREATE OR REPLACE VIEW orders_log_recent AS "
        "SELECT * FROM read_parquet("
        "  '{root}/normalised/orders_log/dt=*/*.parquet', hive_partitioning=true) "
        "ORDER BY ts_ms DESC LIMIT 1000"
    ),
    "positions_all": (
        "CREATE OR REPLACE VIEW positions_all AS "
        "SELECT * FROM read_parquet("
        "  '{root}/normalised/positions/dt=*/*.parquet', hive_partitioning=true)"
    ),
    "open_positions_latest": (
        "CREATE OR REPLACE VIEW open_positions_latest AS "
        "SELECT * EXCLUDE rn FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY position_id "
        "    ORDER BY COALESCE(ingested_ts_ms, open_ts_ms) DESC, open_ts_ms DESC) AS rn "
        "  FROM read_parquet('{root}/normalised/positions/dt=*/*.parquet', "
        "  hive_partitioning=true, union_by_name=true)"
        ") WHERE rn = 1 AND status = 'open'"
    ),
    "backtest_metrics_all": (
        "CREATE OR REPLACE VIEW backtest_metrics_all AS "
        "SELECT * FROM read_parquet("
        "  '{root}/normalised/backtest_metrics/dt=*/*.parquet', hive_partitioning=true)"
    ),
    "backtest_metrics_latest": (
        "CREATE OR REPLACE VIEW backtest_metrics_latest AS "
        "SELECT * EXCLUDE rn FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY run_id "
        "    ORDER BY ingested_ts_ms DESC) AS rn"
        "  FROM read_parquet("
        "    '{root}/normalised/backtest_metrics/dt=*/*.parquet', hive_partitioning=true)"
        ") WHERE rn = 1"
    ),
    # ─── Phase 5.6 context views ─────────────────────────────────────────────
    "context_sources_all": (
        "CREATE OR REPLACE VIEW context_sources_all AS "
        "SELECT * FROM read_parquet("
        "  '{root}/normalised/context_sources/dt=*/*.parquet', "
        "  hive_partitioning=true, union_by_name=true)"
    ),
    "context_sources_latest": (
        "CREATE OR REPLACE VIEW context_sources_latest AS "
        "SELECT * EXCLUDE rn FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY context_source_id "
        "    ORDER BY ingested_ts_ms DESC) AS rn"
        "  FROM read_parquet('{root}/normalised/context_sources/dt=*/*.parquet', "
        "  hive_partitioning=true, union_by_name=true)"
        ") WHERE rn = 1"
    ),
    "context_documents_all": (
        "CREATE OR REPLACE VIEW context_documents_all AS "
        "SELECT * FROM read_parquet("
        "  '{root}/normalised/context_documents/dt=*/*.parquet', "
        "  hive_partitioning=true, union_by_name=true)"
    ),
    "context_documents_latest": (
        "CREATE OR REPLACE VIEW context_documents_latest AS "
        "SELECT * EXCLUDE rn FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY context_document_id "
        "    ORDER BY ingested_ts_ms DESC) AS rn"
        "  FROM read_parquet('{root}/normalised/context_documents/dt=*/*.parquet', "
        "  hive_partitioning=true, union_by_name=true)"
        ") WHERE rn = 1"
    ),
    "context_rules_all": (
        "CREATE OR REPLACE VIEW context_rules_all AS "
        "SELECT * FROM read_parquet("
        "  '{root}/normalised/context_rules/dt=*/*.parquet', "
        "  hive_partitioning=true, union_by_name=true)"
    ),
    "context_rules_latest": (
        "CREATE OR REPLACE VIEW context_rules_latest AS "
        "SELECT * EXCLUDE rn FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY context_rule_id "
        "    ORDER BY ingested_ts_ms DESC) AS rn"
        "  FROM read_parquet('{root}/normalised/context_rules/dt=*/*.parquet', "
        "  hive_partitioning=true, union_by_name=true)"
        ") WHERE rn = 1"
    ),
    "context_relationship_decisions_all": (
        "CREATE OR REPLACE VIEW context_relationship_decisions_all AS "
        "SELECT * FROM read_parquet("
        "  '{root}/normalised/context_relationship_decisions/dt=*/*.parquet', "
        "  hive_partitioning=true, union_by_name=true)"
    ),
    "context_relationship_decisions_latest": (
        "CREATE OR REPLACE VIEW context_relationship_decisions_latest AS "
        "SELECT * EXCLUDE rn FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY relationship_id "
        "    ORDER BY ingested_ts_ms DESC) AS rn"
        "  FROM read_parquet("
        "    '{root}/normalised/context_relationship_decisions/dt=*/*.parquet', "
        "    hive_partitioning=true, union_by_name=true)"
        ") WHERE rn = 1"
    ),
    "context_rule_audit_latest": (
        "CREATE OR REPLACE VIEW context_rule_audit_latest AS "
        "SELECT r.*, d.title AS document_title, d.url AS document_url "
        "FROM context_rules_latest r "
        "LEFT JOIN context_documents_latest d "
        "ON contains(r.source_document_ids_json, d.context_document_id)"
    ),
    "relationship_context_decisions_audit_latest": (
        "CREATE OR REPLACE VIEW relationship_context_decisions_audit_latest AS "
        "SELECT d.*, r.market_id_a, r.market_id_b, r.question_a, r.question_b, "
        "r.relationship_type, r.relationship_subtype, r.relationship_family "
        "FROM context_relationship_decisions_latest d "
        "LEFT JOIN relationship_candidates_latest r USING (relationship_id)"
    ),
}
