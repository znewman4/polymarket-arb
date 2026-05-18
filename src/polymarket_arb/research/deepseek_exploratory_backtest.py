"""Massive exploratory DeepSeek backtest workflow.

RESEARCH-ONLY / SIMULATED / BACKTESTED / EXPLORATORY.
No live trading, no wallets, no signing, no authenticated endpoints, and no
trading advice.  This module uses local historical data plus local Ollama
models only.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from contextlib import suppress
from dataclasses import asdict, dataclass
from dataclasses import asdict as _dc_asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..backtest.context_aware_replay import run_context_aware_backtest
from ..backtest.research_replay import run_research_backtest
from ..nlp.hypothesis_engine import Hypothesis, generate_hypotheses, write_hypotheses_jsonl
from ..research_presets import ResearchPreset, apply_preset, load_preset
from ..settings import Settings
from ..storage.base import ContextRelationshipDecisionRow, MarketRow, RelationshipCandidateRow
from ..storage.parquet.context_relationship_decisions_repo import (
    ParquetContextRelationshipDecisionsRepository,
)
from ..storage.parquet.markets_repo import ParquetMarketsRepository
from ..storage.parquet.price_history_repo import ParquetPriceHistoryRepository
from ..storage.parquet.relationship_candidates_repo import ParquetRelationshipCandidatesRepository
from ..strategies.models import ContextAwareBacktestConfig
from .closed_form_simulators import run_simulators, simulator_summary

PROMPT_VERSION = "deepseek_relationship_hypothesis_v2_both_sides"
REPORT_LABEL = "RESEARCH-ONLY / simulated / backtested / exploratory / not trading advice"
DEFAULT_OLLAMA_BASE = "http://127.0.0.1:11434"
DEFAULT_DEEPSEEK_MODEL = "deepseek-r1:latest"
DEFAULT_EMBED_MODEL = "nomic-embed-text:latest"

# Normalisation contract: every source group runs the engine at the SAME per-leg
# stake with effectively unlimited cash so that percentage metrics (edge return,
# mark-to-market return, slippage as a fraction of notional) are directly
# comparable across sources. Raw USD PnL is *not* a comparison metric — see the
# main report's signal-quality vs position-sizing vs portfolio-growth split.
NORMALISED_STAKE_USDC_PER_LEG = 50.0      # → 100 USDC per accepted trade-pair
NORMALISED_STARTING_CASH_USDC = 10_000_000.0   # effectively infinite for the sample sizes used here


@dataclass(frozen=True)
class WorkflowConfig:
    run_id: str
    ollama_base: str = DEFAULT_OLLAMA_BASE
    deepseek_model: str = DEFAULT_DEEPSEEK_MODEL
    embedding_model: str = DEFAULT_EMBED_MODEL
    market_limit: int = 500
    embedding_pair_cap: int = 240
    deepseek_pair_limit: int = 160
    deepseek_seed_pair_limit: int = 80
    sim_threshold: float = 0.74
    max_pairs_per_market: int = 6


def run_workflow(settings: Settings, cfg: WorkflowConfig) -> Path:
    """Run the full exploratory comparison and write the requested report pack."""
    data_root = settings.data_root
    report_dir = data_root / "reports" / "deepseek_exploratory_backtest" / cfg.run_id
    report_dir.mkdir(parents=True, exist_ok=True)

    provenance = _model_provenance(cfg)
    (report_dir / "model_provenance_audit.md").write_text(
        _model_provenance_markdown(provenance),
        encoding="utf-8",
    )

    old_audit = audit_v9_accounting(data_root)
    _write_csv(report_dir / "pnl_reconciliation.csv", old_audit["rows"])
    (report_dir / "pnl_accounting_audit.md").write_text(
        _pnl_audit_markdown(old_audit),
        encoding="utf-8",
    )

    baseline = _run_named_backtest(data_root, f"{cfg.run_id}_baseline", "strict_research")
    aggressive = _run_named_backtest(
        data_root,
        f"{cfg.run_id}_aggressive_deterministic",
        "aggressive_deterministic_surface",
    )

    markets = _candidate_market_sample(data_root, cfg.market_limit)
    existing_pairs = _existing_pair_keys(data_root)
    embedding_candidates, embedding_meta = generate_hypotheses(
        markets,
        existing_pair_keys=existing_pairs,
        sim_threshold=cfg.sim_threshold,
        max_pairs_per_market=cfg.max_pairs_per_market,
        overall_pair_cap=cfg.embedding_pair_cap,
        ollama_base=cfg.ollama_base,
    )
    write_hypotheses_jsonl(
        report_dir / "embedding_candidates.jsonl",
        embedding_candidates,
        embedding_meta,
    )

    embedding_promoted = promote_embedding_hypotheses(
        data_root,
        embedding_candidates,
        markets,
        cfg.run_id,
    )

    seed_candidates = seed_deepseek_candidates(data_root, cfg.deepseek_seed_pair_limit)
    embedding_deepseek_limit = max(0, cfg.deepseek_pair_limit - len(seed_candidates))
    deepseek_candidates = [
        *embedding_candidates[:embedding_deepseek_limit],
        *seed_candidates,
    ][: cfg.deepseek_pair_limit]
    deepseek_markets = _markets_for_candidates(data_root, markets, deepseek_candidates)
    write_hypotheses_jsonl(
        report_dir / "deepseek_candidate_mix.jsonl",
        deepseek_candidates,
        {
            "schema_version": 1,
            "embedding_retrieval_candidates": min(len(embedding_candidates), embedding_deepseek_limit),
            "deterministic_seed_candidates": len(seed_candidates),
            "label": REPORT_LABEL,
        },
    )

    deepseek_hypotheses = reason_with_deepseek(
        deepseek_candidates,
        deepseek_markets,
        cfg,
        report_dir,
    )
    route_counts = promote_deepseek_hypotheses(
        data_root,
        deepseek_hypotheses,
        deepseek_markets,
        cfg.run_id,
    )
    deepseek_promoted = int(route_counts.get("promoted_to_replay_store", 0))

    # Closed-form simulators score the "unsupported_but_testable" route — DeepSeek
    # hypotheses that the buy-only replay engine cannot trade get scored here
    # instead of being silently rejected.
    simulator_trades = run_simulators(
        data_root,
        deepseek_hypotheses,
        deepseek_markets,
        slippage_bps=50,
    )
    simulator_rows = [_dc_asdict(t) | {"label": REPORT_LABEL} for t in simulator_trades]
    simulator_perf = simulator_summary(simulator_trades)

    embedding_run = _run_named_backtest(
        data_root,
        f"{cfg.run_id}_embedding_only",
        "embedding_hypothesis_surface",
    )
    deepseek_run = _run_named_backtest(
        data_root,
        f"{cfg.run_id}_deepseek_generative",
        "deepseek_hypothesis_surface",
    )
    ultra_run = _run_named_backtest(
        data_root,
        f"{cfg.run_id}_ultra_loose_diagnostic",
        "ultra_loose_diagnostic_surface",
    )
    strict_validation_run = _run_strict_validation(data_root, f"{cfg.run_id}_strict_validation")

    run_outputs = {
        "baseline_deterministic": baseline,
        "aggressive_deterministic": aggressive,
        "embedding_only": embedding_run,
        "deepseek_generative": deepseek_run,
        "ultra_loose_diagnostic": ultra_run,
        "strict_validation": strict_validation_run,
    }
    rel_lookup = _relationship_lookup(data_root)
    source_summaries, trade_rows = _summarise_runs(data_root, run_outputs, rel_lookup)
    family_rows = _group_performance(trade_rows, ("source_group", "relationship_family_label"))
    context_rows = _group_performance(trade_rows, ("source_group", "context_space_id"))
    losing_rows = sorted(
        trade_rows,
        key=lambda r: _float(r.get("mark_to_market_pnl_usdc")),
    )[:50]

    deepseek_results = _hypothesis_results(deepseek_hypotheses, trade_rows, "deepseek_generative")
    embedding_results = _embedding_results(embedding_candidates, trade_rows)
    strict_rows = _strict_validation_rows(strict_validation_run, trade_rows)
    promotion_rows = _promotion_candidates(deepseek_results, family_rows)
    kill_rows = _kill_or_tighten_candidates(deepseek_results, family_rows)
    bottlenecks = _bottlenecks(
        embedding_candidates=embedding_candidates,
        embedding_promoted=embedding_promoted,
        deepseek_hypotheses=deepseek_hypotheses,
        deepseek_promoted=deepseek_promoted,
        deepseek_results=deepseek_results,
        route_counts=route_counts,
        simulator_trade_count=len(simulator_trades),
    )

    _write_csv(report_dir / "baseline_results.csv", [source_summaries["baseline_deterministic"]])
    _write_csv(report_dir / "embedding_only_results.csv", embedding_results)
    _write_csv(report_dir / "deepseek_hypothesis_results.csv", deepseek_results)
    _write_csv(report_dir / "relationship_family_performance.csv", family_rows)
    _write_csv(report_dir / "context_space_performance.csv", context_rows)
    _write_csv(report_dir / "losing_trade_analysis.csv", losing_rows)
    _write_csv(report_dir / "strict_validation_results.csv", strict_rows)
    _write_csv(report_dir / "rulebook_promotion_candidates.csv", promotion_rows)
    _write_csv(report_dir / "kill_or_tighten_candidates.csv", kill_rows)
    _write_csv(report_dir / "bottlenecks.csv", bottlenecks)
    _write_csv(report_dir / "deepseek_route_breakdown.csv", _route_breakdown_rows(route_counts))
    _write_csv(report_dir / "closed_form_simulator_trades.csv", simulator_rows)
    _write_csv(report_dir / "closed_form_simulator_performance.csv", simulator_perf)
    _write_csv(
        report_dir / "hypothesis_origin_breakdown.csv",
        _origin_breakdown(
            embedding_candidates,
            embedding_promoted,
            deepseek_hypotheses,
            deepseek_promoted,
            trade_rows,
        ),
    )
    _write_jsonl(report_dir / "deepseek_hypotheses.jsonl", deepseek_results)

    summary = {
        "label": REPORT_LABEL,
        "run_id": cfg.run_id,
        "config": asdict(cfg),
        "normalised_stake_usdc_per_leg": NORMALISED_STAKE_USDC_PER_LEG,
        "normalised_starting_cash_usdc": NORMALISED_STARTING_CASH_USDC,
        "model_provenance": provenance,
        "old_pnl_audit": old_audit["summary"],
        "source_summaries": source_summaries,
        "embedding_candidates": len(embedding_candidates),
        "deepseek_deterministic_seed_candidates": len(seed_candidates),
        "embedding_promoted_to_replay": embedding_promoted,
        "deepseek_hypotheses": len(deepseek_hypotheses),
        "deepseek_route_counts": route_counts,
        "deepseek_promoted_to_replay": deepseek_promoted,
        "deepseek_traded_hypotheses": sum(1 for r in deepseek_results if _int(r.get("accepted_trade_count")) > 0),
        "deepseek_outside_rulebook": sum(
            1 for h in deepseek_hypotheses if bool(h.get("outside_existing_deterministic_rulebook"))
        ),
        "closed_form_simulator_trades": len(simulator_trades),
        "closed_form_simulator_summaries": simulator_perf,
        "report_dir": str(report_dir),
        "run_outputs": run_outputs,
    }
    # Write the three split reports: signal quality, position sizing, portfolio growth.
    (report_dir / "signal_quality_report.md").write_text(
        _signal_quality_report(cfg, source_summaries, family_rows, simulator_perf, route_counts),
        encoding="utf-8",
    )
    (report_dir / "position_sizing_report.md").write_text(
        _position_sizing_report(cfg, source_summaries),
        encoding="utf-8",
    )
    (report_dir / "portfolio_growth_report.md").write_text(
        _portfolio_growth_report(cfg, source_summaries, trade_rows),
        encoding="utf-8",
    )
    (report_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    (report_dir / "main_deepseek_exploratory_report.md").write_text(
        _main_report(
            cfg,
            old_audit,
            provenance,
            source_summaries,
            deepseek_results,
            family_rows,
            losing_rows,
            promotion_rows,
            kill_rows,
            strict_rows,
            bottlenecks,
            report_dir,
        ),
        encoding="utf-8",
    )
    return report_dir


def audit_v9_accounting(data_root: Path) -> dict[str, Any]:
    runs = {
        "v9_baseline": (
            data_root / "backtests" / "aggressive_baseline_v9" / "research" / "exploratory_trade_surface",
            "baseline deterministic/exploratory",
        ),
        "v9_aggressive_deterministic": (
            data_root / "backtests" / "aggressive_deterministic_v9" / "research" / "aggressive_learning_surface",
            "aggressive deterministic",
        ),
        "v9_embedding_legacy_ollama": (
            data_root / "backtests" / "aggressive_ollama_v9" / "research" / "ollama_hypothesis_surface",
            "legacy embedding-only Ollama-labelled",
        ),
        "v9_ultra_loose": (
            data_root / "backtests" / "aggressive_ultra_loose_v9" / "research" / "ultra_loose_diagnostic_surface",
            "ultra-loose diagnostic",
        ),
    }
    rel_lookup = _relationship_lookup(data_root)
    rows: list[dict[str, Any]] = []
    legacy_llm_rows: list[dict[str, Any]] = []
    for run_name, (run_dir, label) in runs.items():
        metrics = _read_json(run_dir / "metrics.json")
        trades = _read_csv(run_dir / "trades.csv")
        enriched = [_enrich_trade_row(r, rel_lookup) for r in trades]
        summary = _accounting_summary(
            run_name=run_name,
            run_label=label,
            metrics=metrics,
            trades=enriched,
            data_root=data_root,
            source_filter=None,
        )
        rows.append(summary)
        llm_summary = _accounting_summary(
            run_name=f"{run_name}:legacy_llm_context_spaces",
            run_label="legacy LLM/embedding context-space subset",
            metrics={},
            trades=enriched,
            data_root=data_root,
            source_filter=lambda r: str(r.get("context_space_id", "")).startswith("llm_hyp_"),
        )
        rows.append(llm_summary)
        legacy_llm_rows.extend(
            r for r in enriched if str(r.get("context_space_id", "")).startswith("llm_hyp_")
        )

    legacy_llm_total = _accounting_summary(
        run_name="v9_legacy_llm_context_spaces_all_runs",
        run_label="legacy LLM/embedding context-space subset across all v9 runs",
        metrics={},
        trades=legacy_llm_rows,
        data_root=data_root,
        source_filter=None,
    )
    rows.append(legacy_llm_total)
    summary = {
        "old_plus_10_edge_implied_correct_for_legacy_llm_subset": abs(
            _float(legacy_llm_total.get("edge_implied_pnl_usdc")) - 10.203
        ) < 0.01,
        "legacy_llm_subset_entry_cost_usdc": legacy_llm_total["entry_cost_usdc"],
        "legacy_llm_subset_edge_implied_pnl_usdc": legacy_llm_total["edge_implied_pnl_usdc"],
        "legacy_llm_subset_trade_pairs": legacy_llm_total["accepted_trade_count"],
        "diagnosis": (
            "Headline net_pnl_usdc is mark-to-market ending-equity PnL. The small +10.20 "
            "number is edge-implied PnL for legacy llm_hyp_* context spaces only, not all "
            "aggressive trades. The denominator is 906 USDC for that subset; 520 USDC "
            "is the baseline sports_championship_conference_progression space alone."
        ),
    }
    return {"rows": rows, "summary": summary}


def promote_embedding_hypotheses(
    data_root: Path,
    hypotheses: list[Hypothesis],
    markets: list[MarketRow],
    run_id: str,
) -> int:
    market_by_id = {m.id: m for m in markets}
    rows: list[RelationshipCandidateRow] = []
    decisions: list[ContextRelationshipDecisionRow] = []
    for h in hypotheses:
        rel_type = _embedding_replay_type(h.hypothesis_type)
        if not rel_type:
            continue
        ma = market_by_id.get(h.market_id_a)
        mb = market_by_id.get(h.market_id_b)
        if not ma or not mb or len(ma.clob_token_ids) < 2 or len(mb.clob_token_ids) < 2:
            continue
        rel_id = _stable_id("embedding", run_id, h.hypothesis_id, h.hypothesis_type)
        subtype = f"embedding_hypothesis_{h.hypothesis_type}"
        space_id = f"embedding_hyp_{ma.id[-6:]}_{mb.id[-6:]}_{h.hypothesis_type}"[:180]
        evidence = {
            "hypothesis_source": "embedding_only",
            "source_candidate_method": h.hypothesis_engine,
            "model_type": "embedding",
            "model_name": DEFAULT_EMBED_MODEL,
            "hypothesis_id": h.hypothesis_id,
            "hypothesis_relationship_type": h.hypothesis_type,
            "confidence": h.confidence,
            "similarity": h.similarity,
            "prompt_version": "",
            "reasoning_summary": "Embedding-only candidate; no generative reasoning was used.",
            "expected_failure_modes": h.expected_failure_modes,
            "uncertainty_flags": h.uncertainty_flags,
        }
        row = _relationship_row(
            relationship_id=rel_id,
            market_a=ma,
            market_b=mb,
            relationship_type=rel_type,
            relationship_subtype=subtype,
            outcome_space_id=space_id,
            confidence=h.confidence,
            semantic_similarity=h.similarity,
            rationale=h.explanation,
            evidence=evidence,
            rulebook_id="embedding_hypothesis_v1",
        )
        rows.append(row)
        decisions.append(_decision_row(row, "embedding-only research hypothesis"))
    if rows:
        ParquetRelationshipCandidatesRepository(data_root).append_many(rows)
        ParquetContextRelationshipDecisionsRepository(data_root).append_many(decisions)
    return len(rows)


def reason_with_deepseek(
    candidates: list[Hypothesis],
    markets: list[MarketRow],
    cfg: WorkflowConfig,
    report_dir: Path,
) -> list[dict[str, Any]]:
    market_by_id = {m.id: m for m in markets}
    out: list[dict[str, Any]] = []
    raw_path = report_dir / "deepseek_raw_responses.jsonl"
    for idx, cand in enumerate(candidates, start=1):
        ma = market_by_id.get(cand.market_id_a)
        mb = market_by_id.get(cand.market_id_b)
        if ma is None or mb is None:
            continue
        prompt = _deepseek_prompt(cand, ma, mb)
        started = _now_ms()
        response_text, duration_ms = _ollama_generate(
            cfg.ollama_base,
            cfg.deepseek_model,
            prompt,
            timeout_s=240,
        )
        parsed, parse_error = _parse_json_response(response_text)
        row = _normalise_deepseek_response(
            parsed,
            parse_error=parse_error,
            candidate=cand,
            market_a=ma,
            market_b=mb,
            model=cfg.deepseek_model,
            duration_ms=duration_ms,
            request_ts_ms=started,
        )
        out.append(row)
        with raw_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "idx": idx,
                "candidate_id": cand.hypothesis_id,
                "response": response_text,
                "parsed": row,
            }, sort_keys=True) + "\n")
    return out


def promote_deepseek_hypotheses(
    data_root: Path,
    hypotheses: list[dict[str, Any]],
    markets: list[MarketRow],
    run_id: str,
) -> dict[str, int]:
    """Attach a route/sub-handler to every DeepSeek hypothesis in-place.

    Only ``directly_replayable`` candidates are written to the relationship store
    (so the buy-only replay engine can pick them up). The other four routes are
    handled downstream: ``unsupported_but_testable`` flows into closed-form
    simulators, ``diagnostic_only`` is recorded but never traded, ``human_review``
    is queued, and ``invalid`` is excluded.

    Returns the route-count breakdown for the bottleneck/origin reports.
    """
    market_by_id = {m.id: m for m in markets}
    rows: list[RelationshipCandidateRow] = []
    decisions: list[ContextRelationshipDecisionRow] = []
    counts: Counter[str] = Counter()
    for h in hypotheses:
        route, handler = _route_deepseek_hypothesis(h)
        h["route"] = route
        h["route_handler"] = handler
        counts[route] += 1
        if route != "directly_replayable":
            continue
        if _float(h.get("confidence")) < 0.20:
            counts["invalid"] += 1
            counts["directly_replayable"] -= 1
            h["route"] = "invalid"
            h["route_handler"] = "confidence_below_0.20"
            continue
        ma = market_by_id.get(str(h.get("market_id_a")))
        mb = market_by_id.get(str(h.get("market_id_b")))
        if not ma or not mb or len(ma.clob_token_ids) < 2 or len(mb.clob_token_ids) < 2:
            counts["invalid"] += 1
            counts["directly_replayable"] -= 1
            h["route"] = "invalid"
            h["route_handler"] = "market_not_found_or_missing_tokens"
            continue
        hyp_type = str(h.get("relationship_type") or "uncertain")
        replay_type = handler  # for directly_replayable, handler IS the replay type
        rel_id = _stable_id("deepseek", run_id, str(h.get("hypothesis_id")), hyp_type, replay_type)
        subtype = f"deepseek_hypothesis_{_slug(hyp_type)}"
        space_id = f"deepseek_hyp_{ma.id[-6:]}_{mb.id[-6:]}_{_slug(hyp_type)}"[:180]
        evidence = {
            **h,
            "hypothesis_source": "deepseek_generative",
            "source_candidate_method": h.get("source_candidate_method"),
            "model_type": "generative",
            "model_name": h.get("model_name"),
            "prompt_version": PROMPT_VERSION,
            "hypothesis_relationship_type": hyp_type,
        }
        row = _relationship_row(
            relationship_id=rel_id,
            market_a=ma,
            market_b=mb,
            relationship_type=replay_type,
            relationship_subtype=subtype,
            outcome_space_id=space_id,
            confidence=_float(h.get("confidence"), 0.5),
            semantic_similarity=_float(h.get("candidate_similarity")),
            rationale=str(h.get("reasoning_summary") or "")[:500],
            evidence=evidence,
            rulebook_id="deepseek_hypothesis_v1",
        )
        rows.append(row)
        decisions.append(_decision_row(row, "DeepSeek generative research hypothesis"))
    if rows:
        ParquetRelationshipCandidatesRepository(data_root).append_many(rows)
        ParquetContextRelationshipDecisionsRepository(data_root).append_many(decisions)
    counts["promoted_to_replay_store"] = len(rows)
    return dict(counts)


def _run_named_backtest(data_root: Path, run_id: str, preset_name: str) -> dict[str, Any]:
    preset = load_preset(preset_name)
    # Normalise stake + cash across every comparison run so metrics are stake-invariant
    # and we never have to compare 250-USDC and 1-USDC fills as if they were the same.
    preset = preset.model_copy(update={"stake_size_usdc": NORMALISED_STAKE_USDC_PER_LEG})
    base_cfg = ContextAwareBacktestConfig(
        run_id=run_id,
        starting_cash_usdc=Decimal(str(NORMALISED_STARTING_CASH_USDC)),
    )
    cfg = apply_preset(preset, base_cfg)
    if preset_name == "strict_research":
        result = run_context_aware_backtest(data_root, cfg)
    else:
        result = run_research_backtest(data_root, cfg, preset)
    return {
        "run_id": result["run_id"],
        "preset_name": preset_name,
        "output_dir": str(result["output_dir"]),
        "metrics": result["metrics"],
    }


def _run_strict_validation(data_root: Path, run_id: str) -> dict[str, Any]:
    preset = ResearchPreset(
        preset_name="deepseek_strict_validation_surface",
        label="DEEPSEEK_STRICT_VALIDATION",
        lane="exploratory_context_unreviewed",
        relationship_universe="all_with_context_decisions",
        include_auto_approved=True,
        min_relationship_confidence=0.65,
        min_combined_prob=0.20,
        min_single_prob=0.02,
        min_gross_edge=0.02,
        min_net_edge=0.01,
        slippage_bps=50,
        alignment_mode="forward_fill_max_age",
        max_staleness_minutes=360,
        cooldown_minutes=0,
        max_trades_per_relationship=3,
        entry_policy="first_violation_only",
        exit_policy="hold_to_resolution",
        sizing_policy="flat_small",
        stake_size_usdc=2,
        include_relationship_subtype_prefixes=["deepseek_hypothesis_"],
        label_all_outputs_exploratory=True,
        record_before_costs=True,
        execute_trades=True,
    )
    preset = preset.model_copy(update={"stake_size_usdc": NORMALISED_STAKE_USDC_PER_LEG})
    base_cfg = ContextAwareBacktestConfig(
        run_id=run_id,
        starting_cash_usdc=Decimal(str(NORMALISED_STARTING_CASH_USDC)),
    )
    cfg = apply_preset(preset, base_cfg)
    result = run_research_backtest(data_root, cfg, preset)
    return {
        "run_id": result["run_id"],
        "preset_name": preset.preset_name,
        "output_dir": str(result["output_dir"]),
        "metrics": result["metrics"],
    }


def _candidate_market_sample(data_root: Path, limit: int) -> list[MarketRow]:
    markets_repo = ParquetMarketsRepository(data_root)
    price_repo = ParquetPriceHistoryRepository(data_root)
    try:
        priced_tokens = price_repo.distinct_token_ids()
    except AttributeError:
        priced_tokens = set()
    markets = []
    for market in markets_repo.iter_all_markets():
        if len(market.clob_token_ids) < 2:
            continue
        if priced_tokens and not any(tok in priced_tokens for tok in market.clob_token_ids[:2]):
            continue
        markets.append(market)
    markets.sort(key=lambda m: float(m.volume or Decimal("0")), reverse=True)
    return markets[:limit]


def _existing_pair_keys(data_root: Path) -> set[frozenset[str]]:
    pairs: set[frozenset[str]] = set()
    for rel in ParquetRelationshipCandidatesRepository(data_root).iter_latest():
        if rel.market_id_a and rel.market_id_b:
            pairs.add(frozenset({rel.market_id_a, rel.market_id_b}))
    return pairs


def seed_deepseek_candidates(data_root: Path, limit: int) -> list[Hypothesis]:
    """Seed DeepSeek with replay-supported relationship pairs as controls.

    These are not counted as outside-rulebook discoveries. They let the report
    measure whether DeepSeek can correctly decide known tradeable relationship
    classes, while embedding-only retrieval continues to measure discovery noise.
    """
    if limit <= 0:
        return []
    supported = {
        "nested_a_implies_b",
        "nested_b_implies_a",
        "contradiction",
        "mutually_exclusive",
        "same_entity_exclusive",
        "mutually_exclusive_category",
        "inverse",
        "inverse_temporal_order",
    }
    rels = [
        rel for rel in ParquetRelationshipCandidatesRepository(data_root).iter_latest()
        if rel.relationship_type in supported
        and rel.validation_status != "rejected"
        and rel.final_confidence >= 0.45
        and not (rel.relationship_subtype or "").startswith((
            "llm_hypothesis_",
            "embedding_hypothesis_",
            "deepseek_hypothesis_",
        ))
    ]
    rels.sort(key=lambda r: (r.final_confidence, r.relationship_id), reverse=True)
    out = []
    for rel in rels[:limit]:
        htype = rel.relationship_subtype or rel.relationship_type
        out.append(Hypothesis(
            hypothesis_id=_stable_id("deterministic_seed", rel.relationship_id),
            market_id_a=rel.market_id_a,
            market_id_b=rel.market_id_b,
            question_a=rel.question_a,
            question_b=rel.question_b,
            similarity=float(rel.semantic_similarity_score or rel.final_confidence),
            hypothesis_type=f"deterministic_seed_{htype}",
            explanation=(
                "Deterministic seed/control candidate for DeepSeek generative "
                f"classification: {rel.relationship_type}/{htype}"
            ),
            confidence=float(rel.final_confidence),
            sources_used=["relationship_candidates", "deterministic_seed_control"],
            hypothesis_engine="deterministic_seed_relationship",
            outside_current_relationship_space=False,
            uncertainty_flags=[],
            proposed_trade_logic=(
                "DeepSeek must independently classify this existing replay-supported "
                "relationship; research-only simulated comparison."
            ),
            human_review_required=True,
            expected_failure_modes=["model_misclassification", "over-trusting_seed"],
        ))
    return out


def _markets_for_candidates(
    data_root: Path,
    base_markets: list[MarketRow],
    candidates: list[Hypothesis],
) -> list[MarketRow]:
    by_id = {m.id: m for m in base_markets}
    missing = {
        mid
        for h in candidates
        for mid in (h.market_id_a, h.market_id_b)
        if mid not in by_id
    }
    if missing:
        for market in ParquetMarketsRepository(data_root).iter_all_markets():
            if market.id in missing:
                by_id[market.id] = market
    return list(by_id.values())


def _relationship_lookup(data_root: Path) -> dict[str, RelationshipCandidateRow]:
    return {
        rel.relationship_id: rel
        for rel in ParquetRelationshipCandidatesRepository(data_root).iter_latest()
    }


def _embedding_replay_type(hypothesis_type: str) -> str:
    if hypothesis_type in {"likely_duplicate_market", "temporal_ordering_pair"}:
        return "contradiction"
    if hypothesis_type == "primary_race_pairwise":
        return "mutually_exclusive_category"
    return ""


DEEPSEEK_ROUTES: tuple[str, ...] = (
    "directly_replayable",
    "unsupported_but_testable",
    "diagnostic_only",
    "human_review",
    "invalid",
)


def _route_deepseek_hypothesis(h: dict[str, Any]) -> tuple[str, str]:
    """Classify a DeepSeek hypothesis into one of five routes + a sub-handler.

    A hypothesis is NOT rejected for being unsupported by the buy-only engine.
    Instead it is routed to the appropriate simulator or queue. The sub-handler
    string names the closed-form simulator (when route is unsupported_but_testable)
    or the rationale string for the other routes.
    """
    relation = str(h.get("relationship_type") or "").lower()
    if h.get("parse_error"):
        return "invalid", "parse_error"
    if relation == "invalid_same_topic_only":
        return "invalid", "deepseek_marked_same_topic_only"
    if relation == "uncertain_human_review_required":
        return "human_review", "deepseek_low_confidence_or_ambiguous"
    if _deepseek_replay_type(h):
        return "directly_replayable", _deepseek_replay_type(h)
    if relation == "near_duplicate_different_criteria":
        return "unsupported_but_testable", "near_duplicate_divergence_simulator"
    if relation == "stale_related_market":
        return "unsupported_but_testable", "stale_market_convergence_simulator"
    if relation == "mutually_exclusive":
        # Mutex pairs we couldn't lift to a buy-only contradiction route — score them
        # as "would-be-short" YES/YES overround instead.
        return "unsupported_but_testable", "mutex_yes_yes_overround_simulator"
    if relation in {"same_event_duplicate", "duplicate"}:
        return "unsupported_but_testable", "same_yes_spread_simulator"
    if not relation:
        return "invalid", "missing_relationship_type"
    return "diagnostic_only", f"no_handler_for_{relation}"


def _deepseek_replay_type(h: dict[str, Any]) -> str:
    relation = str(h.get("relationship_type") or "").lower()
    direction = str(h.get("relationship_direction") or h.get("direction") or "").lower()
    if relation == "mutually_exclusive" or direction == "mutually_exclusive":
        return "mutually_exclusive_category"
    if direction == "a_implies_b":
        return "nested_a_implies_b"
    if direction == "b_implies_a":
        return "nested_b_implies_a"
    if direction == "inverse_yes_no":
        return "inverse"
    if relation in {
        "parent_child_implication",
        "threshold_ladder",
        "date_deadline_ladder",
        "sports_progression",
        "party_candidate_consistency",
    }:
        if direction in {"a_to_b", "a_implies_b"}:
            return "nested_a_implies_b"
        if direction in {"b_to_a", "b_implies_a"}:
            return "nested_b_implies_a"
    return ""


def _relationship_row(
    *,
    relationship_id: str,
    market_a: MarketRow,
    market_b: MarketRow,
    relationship_type: str,
    relationship_subtype: str,
    outcome_space_id: str,
    confidence: float,
    semantic_similarity: float,
    rationale: str,
    evidence: dict[str, Any],
    rulebook_id: str,
) -> RelationshipCandidateRow:
    return RelationshipCandidateRow(
        relationship_id=relationship_id,
        market_id_a=market_a.id,
        market_id_b=market_b.id,
        condition_id_a=market_a.condition_id,
        condition_id_b=market_b.condition_id,
        token_id_a_yes=market_a.clob_token_ids[0] if market_a.clob_token_ids else None,
        token_id_a_no=market_a.clob_token_ids[1] if len(market_a.clob_token_ids) > 1 else None,
        token_id_b_yes=market_b.clob_token_ids[0] if market_b.clob_token_ids else None,
        token_id_b_no=market_b.clob_token_ids[1] if len(market_b.clob_token_ids) > 1 else None,
        question_a=market_a.question or "",
        question_b=market_b.question or "",
        relationship_type=relationship_type,
        entity_match_score=0.0,
        time_scope_match_score=0.0,
        resolution_criteria_match_score=0.0,
        threshold_relation_json="{}",
        semantic_similarity_score=semantic_similarity,
        deterministic_confidence=0.0,
        model_confidence=confidence,
        final_confidence=confidence,
        validation_status="needs_manual_review",
        rejection_reasons_json="[]",
        rationale_summary=rationale,
        evidence_json=json.dumps(evidence, sort_keys=True),
        rulebook_id=rulebook_id,
        rulebook_version=1,
        rulebook_content_hash="",
        relationship_validity_status="needs_manual_review",
        strategy_eligibility_status="eligible",
        strategy_exclusion_reasons_json="[]",
        relationship_family="hypothesis",
        relationship_subtype=relationship_subtype,
        outcome_space_id=outcome_space_id,
        shared_event=outcome_space_id,
        classification_reason_json=json.dumps({"source": rulebook_id}, sort_keys=True),
        strategy_family="hypothesis_research",
        strategy_eligible_reason=f"{rulebook_id} research-only replay",
        schema_version=1,
        ingested_ts_ms=_now_ms(),
    )


def _decision_row(rel: RelationshipCandidateRow, reason: str) -> ContextRelationshipDecisionRow:
    raw = json.dumps([
        rel.relationship_id,
        rel.outcome_space_id,
        "exploratory_context_unreviewed",
        reason,
    ], sort_keys=True)
    return ContextRelationshipDecisionRow(
        decision_id=hashlib.sha256(raw.encode()).hexdigest()[:32],
        relationship_id=rel.relationship_id,
        context_space_id=rel.outcome_space_id,
        context_rule_ids_json="[]",
        previous_validation_status=rel.validation_status,
        new_validation_status="needs_manual_review",
        previous_strategy_eligibility=rel.strategy_eligibility_status,
        new_strategy_eligibility="eligible",
        strategy_lane="exploratory_context_unreviewed",
        decision_reason=f"{reason}; {REPORT_LABEL}",
        evidence_summary="local model hypothesis; human review required",
        schema_version=1,
        ingested_ts_ms=_now_ms(),
    )


def _deepseek_prompt(candidate: Hypothesis, market_a: MarketRow, market_b: MarketRow) -> str:
    return f"""/no_think
You are auditing Polymarket market relationships for a research-only local
backtest. No live trading is allowed. Return ONLY valid JSON.

Classify the relationship between Market A and Market B and ARGUE BOTH SIDES:
you must say why the relationship might be tradeable AND why it might be invalid.
Do not just say "yes, related". A hypothesis without an explicit invalidity
argument and a falsification test is rejected by downstream filters.

Allowed relationship_type values:
duplicate_same_yes, near_duplicate_different_criteria, mutually_exclusive,
parent_child_implication, threshold_ladder, date_deadline_ladder,
party_candidate_consistency, sports_progression, stale_related_market,
invalid_same_topic_only, uncertain_human_review_required

Allowed relationship_direction values:
a_implies_b, b_implies_a, mutually_exclusive, inverse_yes_no, same_yes, none, unknown

Allowed proposed_simulator values:
stale_market_convergence_v2, near_duplicate_convergence_simulator,
near_duplicate_divergence_simulator, near_duplicate_no_trade_diagnostic,
same_yes_spread_simulator, mutex_yes_yes_overround_simulator,
threshold_ladder_implication_simulator, date_ladder_implication_simulator,
party_candidate_consistency_simulator, sports_progression_simulator,
diagnostic_false_positive_baseline, not_applicable

Allowed routing_decision values:
replay, simulate, diagnose_only, human_review

JSON schema:
{{
  "relationship_type": "...",
  "relationship_direction": "...",
  "confidence": 0.0,
  "outside_existing_deterministic_rulebook": true,
  "uncertainty_flags": ["..."],
  "reasoning_summary": "concise audit summary, not chain-of-thought",
  "evidence_summary": "specific text evidence",
  "resolution_criteria_comparison": "same/different criteria and why",
  "why_relationship_may_be_tradeable": "specific tradeable mechanism",
  "why_relationship_may_be_invalid": "specific failure mode — required",
  "falsification_test": "what observation would falsify this hypothesis",
  "proposed_simulator": "one of the allowed proposed_simulator values",
  "control_recommendation": "what control group should be compared against",
  "covered_by_deterministic_rulebook": false,
  "implied_new_rule_family": "one short phrase or empty",
  "routing_decision": "one of replay, simulate, diagnose_only, human_review",
  "proposed_trade_logic": "buy-only simulated logic if supported, otherwise unsupported",
  "should_promote_to_replay": false,
  "human_review_required": true,
  "failure_modes": ["..."]
}}

Promote to replay only if the relation is buy-only representable as:
mutually exclusive, inverse yes/no, or a clear implication/ladder direction.
Do not promote same-YES duplicates unless one YES is the other's NO.

Important classification rules:
- If two markets ask whether DIFFERENT teams/candidates/entities win the SAME
  single-winner championship, election, nomination, race, or award, classify
  `relationship_type="mutually_exclusive"`,
  `relationship_direction="mutually_exclusive"`, and promote to replay.
- If Market A winning/clearing a stricter condition necessarily implies Market B
  (championship implies conference, higher threshold implies lower threshold,
  earlier deadline implies later deadline), classify the appropriate implication
  or ladder type and set `a_implies_b` or `b_implies_a`.
- Use `near_duplicate_different_criteria` only when markets are related but the
  different criteria do NOT create mutual exclusion, inverse, or implication.

Embedding candidate method: {candidate.hypothesis_engine}
Embedding similarity: {candidate.similarity}
Embedding heuristic type: {candidate.hypothesis_type}
Outside existing deterministic rulebook candidate: {candidate.outside_current_relationship_space}

Market A id: {market_a.id}
Market A question: {market_a.question}
Market A outcomes: {market_a.outcomes}
Market A description: {(market_a.description or '')[:800]}

Market B id: {market_b.id}
Market B question: {market_b.question}
Market B outcomes: {market_b.outcomes}
Market B description: {(market_b.description or '')[:800]}
"""


def _ollama_generate(
    base: str,
    model: str,
    prompt: str,
    *,
    timeout_s: float,
) -> tuple[str, int]:
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        # Ollama exposes Qwen/DeepSeek reasoning separately when `think=false`.
        # Without this, the model can spend the whole token budget in hidden
        # reasoning and return an empty or truncated JSON response.
        "think": False,
        "options": {"temperature": 0, "num_predict": 768, "num_ctx": 4096},
    }).encode()
    req = urllib.request.Request(
        f"{base}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read())
        return str(data.get("response") or ""), int((time.time() - start) * 1000)
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return json.dumps({"error": repr(exc)}), int((time.time() - start) * 1000)


def _parse_json_response(text: str) -> tuple[dict[str, Any], str]:
    cleaned = text.strip()
    if "<think>" in cleaned and "</think>" in cleaned:
        cleaned = cleaned.split("</think>", 1)[1].strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start:end + 1]
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}, ""
    except json.JSONDecodeError as exc:
        return {}, repr(exc)


def _normalise_deepseek_response(
    parsed: dict[str, Any],
    *,
    parse_error: str,
    candidate: Hypothesis,
    market_a: MarketRow,
    market_b: MarketRow,
    model: str,
    duration_ms: int,
    request_ts_ms: int,
) -> dict[str, Any]:
    relation = str(parsed.get("relationship_type") or "uncertain_human_review_required")
    direction = str(parsed.get("relationship_direction") or parsed.get("direction") or "unknown")
    confidence = max(0.0, min(1.0, _float(parsed.get("confidence"), 0.0)))
    uncertainty = parsed.get("uncertainty_flags") or []
    if isinstance(uncertainty, str):
        uncertainty = [uncertainty]
    failure_modes = parsed.get("failure_modes") or []
    if isinstance(failure_modes, str):
        failure_modes = [failure_modes]
    should_promote = _bool(parsed.get("should_promote_to_replay")) and not parse_error
    if not _deepseek_replay_type({
        "relationship_type": relation,
        "relationship_direction": direction,
    }):
        should_promote = False
    hyp_id = _stable_id("deepseek_hypothesis", candidate.hypothesis_id, relation, direction)
    return {
        "hypothesis_id": hyp_id,
        "source_candidate_id": candidate.hypothesis_id,
        "source_candidate_method": candidate.hypothesis_engine,
        "candidate_similarity": candidate.similarity,
        "market_id_a": market_a.id,
        "market_id_b": market_b.id,
        "question_a": market_a.question or "",
        "question_b": market_b.question or "",
        "outcome_tokens_a": json.dumps(market_a.clob_token_ids),
        "outcome_tokens_b": json.dumps(market_b.clob_token_ids),
        "relationship_type": relation,
        "relationship_direction": direction,
        "outside_existing_deterministic_rulebook": (
            candidate.outside_current_relationship_space
            and _bool(parsed.get("outside_existing_deterministic_rulebook", True))
        ),
        "model_name": model,
        "model_type": "generative",
        "prompt_version": PROMPT_VERSION,
        "confidence": confidence,
        "uncertainty_flags": json.dumps(uncertainty),
        "reasoning_summary": str(parsed.get("reasoning_summary") or "")[:1000],
        "evidence_summary": str(parsed.get("evidence_summary") or "")[:1000],
        "resolution_criteria_comparison": str(
            parsed.get("resolution_criteria_comparison") or ""
        )[:1000],
        "why_relationship_may_be_wrong": str(parsed.get("why_relationship_may_be_wrong") or "")[:1000],
        "proposed_trade_logic": str(parsed.get("proposed_trade_logic") or "")[:1000],
        "should_promote_to_replay": should_promote,
        "human_review_required": _bool(parsed.get("human_review_required", True)),
        "failure_modes": json.dumps(failure_modes),
        "parse_error": parse_error,
        "request_ts_ms": request_ts_ms,
        "duration_ms": duration_ms,
        "label": REPORT_LABEL,
    }


def _model_provenance(cfg: WorkflowConfig) -> dict[str, Any]:
    tags = _ollama_tags(cfg.ollama_base)
    return {
        "label": REPORT_LABEL,
        "ollama_base": cfg.ollama_base,
        "deepseek_model_requested": cfg.deepseek_model,
        "embedding_model_requested": cfg.embedding_model,
        "available_models": tags,
        "deepseek_used_generatively": any(
            m.get("name") == cfg.deepseek_model or m.get("model") == cfg.deepseek_model
            for m in tags
        ),
        "embedding_used_for_retrieval_only": True,
        "prompt_version": PROMPT_VERSION,
    }


def _ollama_tags(base: str) -> list[dict[str, Any]]:
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=5.0) as resp:
            data = json.loads(resp.read())
        models = data.get("models") or []
        return models if isinstance(models, list) else []
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError):
        return []


def _summarise_runs(
    data_root: Path,
    run_outputs: dict[str, dict[str, Any]],
    rel_lookup: dict[str, RelationshipCandidateRow],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    summaries: dict[str, dict[str, Any]] = {}
    trade_rows: list[dict[str, Any]] = []
    for source_group, info in run_outputs.items():
        out_dir = Path(info["output_dir"])
        metrics = _read_json(out_dir / "metrics.json")
        trades = [_enrich_trade_row(r, rel_lookup) for r in _read_csv(out_dir / "trades.csv")]
        for row in trades:
            row["comparison_run_id"] = info["run_id"]
            row["comparison_preset_name"] = info["preset_name"]
            row["source_group"] = _source_group(row, rel_lookup, default=source_group)
            row.update(_trade_accounting(row, data_root))
            row["relationship_family_label"] = _family_label(row)
            trade_rows.append(row)
        summaries[source_group] = _accounting_summary(
            run_name=info["run_id"],
            run_label=source_group,
            metrics=metrics,
            trades=trades,
            data_root=data_root,
            source_filter=None,
        )
        summaries[source_group]["source_group"] = source_group
    return summaries, trade_rows


def _accounting_summary(
    *,
    run_name: str,
    run_label: str,
    metrics: dict[str, Any],
    trades: list[dict[str, Any]],
    data_root: Path,
    source_filter: Any,
) -> dict[str, Any]:
    if source_filter is not None:
        trades = [r for r in trades if source_filter(r)]
    accounted = [_trade_accounting(r, data_root) for r in trades]
    # Merge per-row accounting back onto trade rows so percentage helpers work uniformly.
    enriched = [{**t, **a} for t, a in zip(trades, accounted, strict=True)]
    entry_cost = sum(_float(r.get("entry_cost_usdc")) for r in accounted)
    edge_pnl = sum(_float(r.get("edge_implied_pnl_usdc")) for r in accounted)
    mtm_value = sum(_float(r.get("mark_to_market_value_usdc")) for r in accounted)
    mtm_pnl = sum(_float(r.get("mark_to_market_pnl_usdc")) for r in accounted)
    slippage_cost = sum(_float(r.get("slippage_cost_usdc")) for r in trades)
    realised_values = [
        _float(r.get("realised_or_settled_pnl_usdc"))
        for r in accounted
        if r.get("realised_or_settled_pnl_usdc") not in (None, "")
    ]
    returns = [
        _float(r.get("edge_implied_pnl_usdc")) / _float(r.get("entry_cost_usdc"))
        for r in accounted
        if _float(r.get("entry_cost_usdc")) > 0
    ]
    mtm_returns = [
        _float(r.get("mark_to_market_pnl_usdc")) / _float(r.get("entry_cost_usdc"))
        for r in enriched
        if _float(r.get("entry_cost_usdc")) > 0 and r.get("mark_to_market_pnl_usdc") not in (None, "")
    ]
    groups = {_trade_group_key(t) for t in trades}
    missing_cost = sum(1 for r in accounted if _float(r.get("entry_cost_usdc")) <= 0)
    missing_mtm = sum(1 for r in accounted if r.get("mark_to_market_value_usdc") in (None, ""))
    return {
        "run_id": run_name,
        "run_label": run_label,
        # Percentages first — these are the comparable metrics across source groups.
        "trade_cost_adjusted_return_pct": edge_pnl / entry_cost if entry_cost else 0.0,
        "mark_to_market_return_pct": mtm_pnl / entry_cost if entry_cost else 0.0,
        "average_trade_return_pct": statistics.fmean(returns) if returns else 0.0,
        "median_trade_return_pct": statistics.median(returns) if returns else 0.0,
        "average_mtm_return_pct": statistics.fmean(mtm_returns) if mtm_returns else 0.0,
        "median_mtm_return_pct": statistics.median(mtm_returns) if mtm_returns else 0.0,
        "slippage_pct_of_notional": slippage_cost / entry_cost if entry_cost else 0.0,
        # Counts
        "accepted_trade_count": len(groups),
        "raw_fill_rows": len(trades),
        "distinct_relationships_traded": len({t.get("relationship_id") for t in trades}),
        "named_spaces_traded": len({t.get("context_space_id") or t.get("outcome_space_id") for t in trades}),
        # USD totals — for audit only. Stake-dependent; never the primary ranking metric.
        "headline_net_pnl_usdc": _float(metrics.get("net_pnl_usdc")) if metrics else "",
        "mark_to_market_pnl_usdc": mtm_pnl,
        "mark_to_market_value_usdc": mtm_value,
        "edge_implied_pnl_usdc": edge_pnl,
        "realised_or_settled_pnl_usdc": sum(realised_values) if realised_values else "",
        "entry_cost_usdc": entry_cost,
        "notional_usdc": entry_cost,
        "slippage_cost_usdc": slippage_cost,
        "trade_return_pct": edge_pnl / entry_cost if entry_cost else 0.0,
        # Inclusion / exclusion audit
        "rows_included_edge_pnl": len(accounted) - missing_cost,
        "rows_excluded_edge_pnl": missing_cost,
        "rows_excluded_edge_pnl_reason": "missing_or_zero_entry_cost" if missing_cost else "",
        "rows_included_mark_to_market": len(accounted) - missing_mtm,
        "rows_excluded_mark_to_market": missing_mtm,
        "rows_excluded_mark_to_market_reason": "missing_latest_token_price" if missing_mtm else "",
        "label": REPORT_LABEL,
    }


def _trade_accounting(row: dict[str, Any], data_root: Path) -> dict[str, Any]:
    notional = _float(row.get("notional_usdc"))
    fees = _float(row.get("fees_usdc"))
    edge = _float(row.get("net_edge_after_cost"))
    shares = _float(row.get("shares"))
    token_id = str(row.get("token_id") or "")
    latest = _latest_price(data_root, token_id)
    mtm_value = shares * latest if latest is not None else None
    realised = row.get("realised_pnl_usdc")
    return {
        "entry_cost_usdc": notional + fees,
        "edge_implied_pnl_usdc": edge * notional,
        "mark_to_market_value_usdc": mtm_value,
        "mark_to_market_pnl_usdc": (mtm_value - notional - fees) if mtm_value is not None else "",
        "realised_or_settled_pnl_usdc": realised if realised not in (None, "") else "",
    }


_LATEST_PRICE_CACHE: dict[tuple[str, str], float | None] = {}


def _latest_price(data_root: Path, token_id: str) -> float | None:
    if not token_id:
        return None
    key = (str(data_root), token_id)
    if key in _LATEST_PRICE_CACHE:
        return _LATEST_PRICE_CACHE[key]
    rows = list(ParquetPriceHistoryRepository(data_root).iter_for_token(token_id))
    if not rows:
        _LATEST_PRICE_CACHE[key] = None
        return None
    latest = max(rows, key=lambda r: r.ts_ms)
    value = float(latest.price)
    _LATEST_PRICE_CACHE[key] = value
    return value


def _group_performance(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(str(row.get(k) or "") for k in keys)].append(row)
    out = []
    for key_values, group in grouped.items():
        perf = _group_perf_single(group)
        row = {k: v for k, v in zip(keys, key_values, strict=True)}
        row.update(perf)
        row["label"] = REPORT_LABEL
        out.append(row)
    return sorted(
        out,
        key=lambda r: (_float(r.get("trade_cost_adjusted_return_pct")), _float(r.get("edge_implied_pnl_usdc"))),
        reverse=True,
    )


def _dominant_share(rows: list[dict[str, Any]]) -> float:
    totals: Counter[str] = Counter()
    for row in rows:
        totals[str(row.get("relationship_id") or "")] += _float(row.get("edge_implied_pnl_usdc"))
    total = sum(abs(v) for v in totals.values())
    return max((abs(v) for v in totals.values()), default=0.0) / total if total else 0.0


def _hypothesis_results(
    hypotheses: list[dict[str, Any]],
    trade_rows: list[dict[str, Any]],
    source_group: str,
) -> list[dict[str, Any]]:
    by_source_candidate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_hypothesis_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in trade_rows:
        if row.get("source_group") != source_group:
            continue
        try:
            evidence = json.loads(str(row.get("evidence_json") or "{}"))
        except json.JSONDecodeError:
            evidence = {}
        by_source_candidate[str(evidence.get("source_candidate_id") or "")].append(row)
        by_hypothesis_id[str(evidence.get("hypothesis_id") or "")].append(row)
    out = []
    for h in hypotheses:
        group = by_hypothesis_id.get(str(h.get("hypothesis_id")), [])
        if not group:
            group = by_source_candidate.get(str(h.get("source_candidate_id")), [])
        perf = _group_perf_single(group)
        recommendation = _recommend_hypothesis(h, perf)
        out.append({
            **h,
            **perf,
            "replay_result": "traded" if perf["accepted_trade_count"] else "no_trades",
            "recommendation": recommendation,
            "label": REPORT_LABEL,
        })
    return out


def _embedding_results(candidates: list[Hypothesis], trade_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_source_candidate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in trade_rows:
        if row.get("source_group") != "embedding_only":
            continue
        try:
            evidence = json.loads(str(row.get("evidence_json") or "{}"))
        except json.JSONDecodeError:
            evidence = {}
        by_source_candidate[str(evidence.get("hypothesis_id") or "")].append(row)
    out = []
    for h in candidates:
        perf = _group_perf_single(by_source_candidate.get(h.hypothesis_id, []))
        out.append({
            "hypothesis_id": h.hypothesis_id,
            "source_candidate_method": h.hypothesis_engine,
            "model_type": "embedding",
            "model_name": DEFAULT_EMBED_MODEL,
            "market_id_a": h.market_id_a,
            "market_id_b": h.market_id_b,
            "question_a": h.question_a,
            "question_b": h.question_b,
            "relationship_type": h.hypothesis_type,
            "confidence": h.confidence,
            "candidate_similarity": h.similarity,
            "replay_result": "traded" if perf["accepted_trade_count"] else "no_trades",
            **perf,
            "label": REPORT_LABEL,
        })
    return out


def _group_perf_single(group: list[dict[str, Any]]) -> dict[str, Any]:
    cost = sum(_float(r.get("entry_cost_usdc")) for r in group)
    edge_pnl = sum(_float(r.get("edge_implied_pnl_usdc")) for r in group)
    mtm_pnl = sum(_float(r.get("mark_to_market_pnl_usdc")) for r in group)
    slippage_cost = sum(_float(r.get("slippage_cost_usdc")) for r in group)
    trade_groups = {_trade_group_key(r) for r in group}
    returns = [
        _float(r.get("edge_implied_pnl_usdc")) / _float(r.get("entry_cost_usdc"))
        for r in group
        if _float(r.get("entry_cost_usdc")) > 0
    ]
    mtm_returns = [
        _float(r.get("mark_to_market_pnl_usdc")) / _float(r.get("entry_cost_usdc"))
        for r in group
        if _float(r.get("entry_cost_usdc")) > 0 and r.get("mark_to_market_pnl_usdc") not in (None, "")
    ]
    return {
        "accepted_trade_count": len(trade_groups),
        "raw_fill_rows": len(group),
        "distinct_relationships_traded": len({r.get("relationship_id") for r in group}),
        "named_spaces_traded": len({r.get("context_space_id") or r.get("outcome_space_id") for r in group}),
        # Percentages — primary metrics. Stake-invariant.
        "trade_cost_adjusted_return_pct": edge_pnl / cost if cost else 0.0,
        "mark_to_market_return_pct": mtm_pnl / cost if cost else 0.0,
        "average_trade_return_pct": statistics.fmean(returns) if returns else 0.0,
        "median_trade_return_pct": statistics.median(returns) if returns else 0.0,
        "average_mtm_return_pct": statistics.fmean(mtm_returns) if mtm_returns else 0.0,
        "median_mtm_return_pct": statistics.median(mtm_returns) if mtm_returns else 0.0,
        "slippage_pct_of_notional": slippage_cost / cost if cost else 0.0,
        # USD totals — sizing-dependent, retained for audit only.
        "entry_cost_usdc": cost,
        "edge_implied_pnl_usdc": edge_pnl,
        "mark_to_market_pnl_usdc": mtm_pnl,
        "trade_cost_adjusted_return": edge_pnl / cost if cost else 0.0,
        "independent_windows": len({r.get("violation_window_id") for r in group if r.get("violation_window_id")}),
        "dominant_relationship_share": _dominant_share(group),
    }


def _strict_validation_rows(strict_run: dict[str, Any], trade_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    strict_trades = [r for r in trade_rows if r.get("comparison_run_id") == strict_run["run_id"]]
    perf = _group_perf_single(strict_trades)
    # Strict validation is now PURELY percentage + survival based — raw USDC PnL
    # is not a gate because the stake is normalised across all comparison runs.
    passed = (
        perf["trade_cost_adjusted_return_pct"] >= 0.005
        and perf["median_trade_return_pct"] >= 0.01
        and perf["mark_to_market_return_pct"] >= 0.0
        and perf["distinct_relationships_traded"] >= 3
        and perf["independent_windows"] >= 5
        and perf["dominant_relationship_share"] < 0.8
    )
    return [{
        "run_id": strict_run["run_id"],
        "preset_name": strict_run["preset_name"],
        **perf,
        "strict_validation_passed": passed,
        "grade": "A_PROFITABLE_ROBUST_CANDIDATE" if passed else "NO_STRICT_SURVIVOR",
        "label": REPORT_LABEL,
    }]


def _promotion_candidates(deepseek_results: list[dict[str, Any]], family_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Promote families that survive strict-style percentage gates (item 8)."""
    rows = []
    for row in family_rows:
        if row.get("source_group") != "deepseek_generative":
            continue
        # Percentage-driven gates: edge-implied return positive AND median % positive
        # AND multiple distinct relationships AND multiple independent windows AND
        # low dominance — ranked by trade_cost_adjusted_return_pct rather than USDC.
        if (
            _int(row.get("accepted_trade_count")) >= 10
            and _int(row.get("distinct_relationships_traded")) >= 2
            and _int(row.get("independent_windows")) >= 5
            and _float(row.get("trade_cost_adjusted_return_pct")) >= 0.005
            and _float(row.get("median_trade_return_pct")) >= 0.005
            and _float(row.get("dominant_relationship_share")) < 0.8
        ):
            rows.append({**row, "promotion_reason": "passes percentage + survival gates"})
    if not rows:
        good = [
            r for r in deepseek_results
            if _int(r.get("accepted_trade_count")) > 0
            and _float(r.get("trade_cost_adjusted_return_pct")) > 0
        ][:10]
        for row in good:
            rows.append({
                "relationship_family_label": row.get("relationship_type"),
                "promotion_reason": "single-hypothesis positive % result only; needs more evidence",
                **{k: row.get(k) for k in (
                    "hypothesis_id",
                    "accepted_trade_count",
                    "trade_cost_adjusted_return_pct",
                    "median_trade_return_pct",
                )},
            })
    return rows


def _kill_or_tighten_candidates(deepseek_results: list[dict[str, Any]], family_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Kill or tighten ONLY on real signal failures, not routing.

    Hypotheses routed away from the buy-only engine (unsupported_but_testable,
    diagnostic_only, human_review) are NOT counted as failures here — they are
    handled by simulators or queues. We only flag actual losers and parse errors.
    """
    rows = []
    for row in deepseek_results:
        if row.get("parse_error"):
            rows.append({**row, "kill_or_tighten_reason": "DeepSeek returned unparsable JSON"})
            continue
        if row.get("route") == "invalid":
            rows.append({**row, "kill_or_tighten_reason": f"routed invalid: {row.get('route_handler') or 'unknown'}"})
            continue
        if (
            _int(row.get("accepted_trade_count")) > 0
            and _float(row.get("trade_cost_adjusted_return_pct")) < 0
        ):
            rows.append({**row, "kill_or_tighten_reason": "negative edge-implied % return"})
    for row in family_rows:
        if (
            row.get("source_group") == "deepseek_generative"
            and _float(row.get("trade_cost_adjusted_return_pct")) < 0
        ):
            rows.append({**row, "kill_or_tighten_reason": "family negative edge-implied % return"})
    return rows[:100]


def _bottlenecks(
    *,
    embedding_candidates: list[Hypothesis],
    embedding_promoted: int,
    deepseek_hypotheses: list[dict[str, Any]],
    deepseek_promoted: int,
    deepseek_results: list[dict[str, Any]],
    route_counts: dict[str, int],
    simulator_trade_count: int,
) -> list[dict[str, Any]]:
    counter = Counter()
    counter["embedding_candidates_not_promoted"] = len(embedding_candidates) - embedding_promoted
    counter["deepseek_parse_errors"] = sum(1 for h in deepseek_hypotheses if h.get("parse_error"))
    counter["deepseek_routed_invalid"] = int(route_counts.get("invalid", 0))
    counter["deepseek_routed_human_review"] = int(route_counts.get("human_review", 0))
    counter["deepseek_routed_diagnostic_only"] = int(route_counts.get("diagnostic_only", 0))
    counter["deepseek_routed_unsupported_but_testable"] = int(route_counts.get("unsupported_but_testable", 0))
    counter["closed_form_simulator_trades"] = simulator_trade_count
    counter["deepseek_directly_replayable_no_trades"] = sum(
        1 for h in deepseek_results
        if h.get("route") == "directly_replayable" and _int(h.get("accepted_trade_count")) == 0
    )
    return [
        {"bottleneck": key, "count": value, "label": REPORT_LABEL}
        for key, value in counter.items()
    ]


def _origin_breakdown(
    embedding_candidates: list[Hypothesis],
    embedding_promoted: int,
    deepseek_hypotheses: list[dict[str, Any]],
    deepseek_promoted: int,
    trade_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for source in ("embedding_only", "deepseek_generative"):
        source_trades = [r for r in trade_rows if r.get("source_group") == source]
        rows.append({
            "source_group": source,
            "hypotheses_proposed": len(embedding_candidates) if source == "embedding_only" else len(deepseek_hypotheses),
            "hypotheses_promoted_to_replay": embedding_promoted if source == "embedding_only" else deepseek_promoted,
            "hypotheses_with_trades": len({r.get("relationship_id") for r in source_trades}),
            "accepted_trade_count": len({_trade_group_key(r) for r in source_trades}),
            "raw_fill_rows": len(source_trades),
            "entry_cost_usdc": sum(_float(r.get("entry_cost_usdc")) for r in source_trades),
            "edge_implied_pnl_usdc": sum(_float(r.get("edge_implied_pnl_usdc")) for r in source_trades),
            "mark_to_market_pnl_usdc": sum(_float(r.get("mark_to_market_pnl_usdc")) for r in source_trades),
            "label": REPORT_LABEL,
        })
    return rows


def _source_group(
    row: dict[str, Any],
    rel_lookup: dict[str, RelationshipCandidateRow],
    *,
    default: str,
) -> str:
    subtype = str(row.get("relationship_subtype") or "").lower()
    evidence = {}
    with suppress(json.JSONDecodeError):
        evidence = json.loads(str(row.get("evidence_json") or "{}"))
    source = str(evidence.get("hypothesis_source") or row.get("hypothesis_source") or "").lower()
    rel = rel_lookup.get(str(row.get("relationship_id") or ""))
    if not source and rel:
        try:
            source = str(json.loads(rel.evidence_json or "{}").get("hypothesis_source") or "").lower()
        except json.JSONDecodeError:
            source = ""
    if subtype.startswith("deepseek_hypothesis_") or source == "deepseek_generative":
        return "deepseek_generative"
    if subtype.startswith("embedding_hypothesis_") or source == "embedding_only":
        return "embedding_only"
    if subtype.startswith("llm_hypothesis_") or str(row.get("context_space_id") or "").startswith("llm_hyp_"):
        return "embedding_only_legacy"
    if default in {"baseline_deterministic", "aggressive_deterministic", "ultra_loose_diagnostic"}:
        return default
    return "deterministic"


def _family_label(row: dict[str, Any]) -> str:
    evidence = {}
    with suppress(json.JSONDecodeError):
        evidence = json.loads(str(row.get("evidence_json") or "{}"))
    return (
        str(evidence.get("hypothesis_relationship_type") or "")
        or str(row.get("relationship_subtype") or "")
        or str(row.get("relationship_type") or "")
        or "unknown"
    )


def _enrich_trade_row(
    row: dict[str, Any],
    rel_lookup: dict[str, RelationshipCandidateRow],
) -> dict[str, Any]:
    rel = rel_lookup.get(str(row.get("relationship_id") or ""))
    out = dict(row)
    if rel:
        for key in (
            "relationship_type",
            "relationship_subtype",
            "relationship_family",
            "strategy_family",
            "outcome_space_id",
            "evidence_json",
        ):
            if not out.get(key):
                out[key] = getattr(rel, key, "") or ""
    if not out.get("trade_group_id"):
        out["trade_group_id"] = out.get("candidate_id") or out.get("trade_id") or ""
    return out


def _trade_group_key(row: dict[str, Any]) -> str:
    return "|".join([
        str(row.get("comparison_run_id") or row.get("run_id") or ""),
        str(row.get("trade_group_id") or row.get("candidate_id") or row.get("trade_id") or ""),
    ])


def _recommend_hypothesis(h: dict[str, Any], perf: dict[str, Any]) -> str:
    route = str(h.get("route") or "")
    if route == "invalid":
        return "kill"
    if route == "human_review":
        return "queue_for_human_review"
    if route == "diagnostic_only":
        return "keep_diagnostic_no_trade"
    if route == "unsupported_but_testable":
        return "route_to_closed_form_simulator"
    if perf["accepted_trade_count"] == 0:
        return "keep_exploratory_no_replay_signal"
    if perf["trade_cost_adjusted_return_pct"] > 0:
        return "keep_exploratory"
    return "kill_or_tighten"


def _model_provenance_markdown(provenance: dict[str, Any]) -> str:
    models = provenance.get("available_models") or []
    model_lines = "\n".join(
        f"| `{m.get('name') or m.get('model')}` | `{m.get('details', {}).get('family', '')}` | `{m.get('details', {}).get('parameter_size', '')}` |"
        for m in models
    )
    return (
        f"# Model Provenance Audit\n\n{REPORT_LABEL}\n\n"
        f"| Field | Value |\n| --- | --- |\n"
        f"| Ollama base | `{provenance.get('ollama_base')}` |\n"
        f"| DeepSeek requested | `{provenance.get('deepseek_model_requested')}` |\n"
        f"| Embedding requested | `{provenance.get('embedding_model_requested')}` |\n"
        f"| DeepSeek generative used | `{provenance.get('deepseek_used_generatively')}` |\n"
        f"| Prompt version | `{provenance.get('prompt_version')}` |\n\n"
        "## Available Models\n\n"
        "| Model | Family | Size |\n| --- | --- | --- |\n"
        f"{model_lines}\n"
    )


def _pnl_audit_markdown(audit: dict[str, Any]) -> str:
    s = audit["summary"]
    return (
        f"# PnL Accounting Audit\n\n{REPORT_LABEL}\n\n"
        "The old headline `net_pnl_usdc` values are mark-to-market ending-equity "
        "PnL, while the small cost-adjusted figure is edge-implied PnL from "
        "trade rows. Those are different metrics and must not be compared as one.\n\n"
        f"* Old +10 edge-implied statement for legacy `llm_hyp_*` subset: "
        f"`{s['old_plus_10_edge_implied_correct_for_legacy_llm_subset']}`\n"
        f"* Correct legacy `llm_hyp_*` entry cost across v9 runs: "
        f"{s['legacy_llm_subset_entry_cost_usdc']:.2f} USDC\n"
        f"* Correct legacy `llm_hyp_*` edge-implied PnL: "
        f"{s['legacy_llm_subset_edge_implied_pnl_usdc']:.3f} USDC\n"
        f"* Diagnosis: {s['diagnosis']}\n\n"
        "Use edge-implied PnL/return for exploratory ranking. Use strict validation "
        "with conservative gates before treating any family as credible. Realised or "
        "settled PnL is unavailable unless resolution rows exist.\n"
    )


def _main_report(
    cfg: WorkflowConfig,
    old_audit: dict[str, Any],
    provenance: dict[str, Any],
    source_summaries: dict[str, dict[str, Any]],
    deepseek_results: list[dict[str, Any]],
    family_rows: list[dict[str, Any]],
    losing_rows: list[dict[str, Any]],
    promotion_rows: list[dict[str, Any]],
    kill_rows: list[dict[str, Any]],
    strict_rows: list[dict[str, Any]],
    bottlenecks: list[dict[str, Any]],
    report_dir: Path,
) -> str:
    comparison = _md_table(
        list(source_summaries.values()),
        (
            "source_group",
            "accepted_trade_count",
            "distinct_relationships_traded",
            "named_spaces_traded",
            "trade_cost_adjusted_return_pct",
            "mark_to_market_return_pct",
            "median_trade_return_pct",
            "median_mtm_return_pct",
            "slippage_pct_of_notional",
            "entry_cost_usdc",
        ),
    )
    best_families = _md_table(family_rows[:10], (
        "source_group",
        "relationship_family_label",
        "accepted_trade_count",
        "distinct_relationships_traded",
        "trade_cost_adjusted_return_pct",
        "median_trade_return_pct",
        "mark_to_market_return_pct",
        "entry_cost_usdc",
    ))
    worst_families = _md_table(
        sorted(
            family_rows,
            key=lambda r: (_float(r.get("mark_to_market_return_pct")), _float(r.get("trade_cost_adjusted_return_pct"))),
        )[:10],
        (
            "source_group",
            "relationship_family_label",
            "accepted_trade_count",
            "mark_to_market_return_pct",
            "trade_cost_adjusted_return_pct",
            "median_trade_return_pct",
        ),
    )
    strict = strict_rows[0] if strict_rows else {}
    traded_deepseek = [r for r in deepseek_results if _int(r.get("accepted_trade_count")) > 0]
    stake_audit = (
        "| field | value |\n| --- | --- |\n"
        f"| Normalised stake per leg | {NORMALISED_STAKE_USDC_PER_LEG:.2f} USDC |\n"
        f"| Normalised trade-pair cost target | {2 * NORMALISED_STAKE_USDC_PER_LEG:.2f} USDC |\n"
        f"| Starting cash (infinite-cash proxy) | {NORMALISED_STARTING_CASH_USDC:,.2f} USDC |\n"
        "| Ranking metric | trade_cost_adjusted_return_pct (edge-implied) |\n"
        "| Secondary metric | mark_to_market_return_pct |\n"
    )
    return (
        f"# DeepSeek Exploratory Backtest — {cfg.run_id}\n\n"
        f"{REPORT_LABEL}\n\n"
        "## 1. Executive Verdict\n\n"
        "DeepSeek was used as a local generative model over embedding-retrieved "
        "candidate pairs. This is an exploratory sample, not a profitable strategy "
        "claim. Accounting is now separated into headline mark-to-market, "
        "edge-implied PnL, realised/settled PnL when available, and entry cost.\n\n"
        "## 2. Safety / Research-Only Scope\n\n"
        "No live trading, no wallets, no private keys, no signing, no authenticated "
        "order endpoints, no order placement, no paper routing through live trading "
        "endpoints, and no geoblock bypassing were used.\n\n"
        "## 3. What Was Run\n\n"
        f"* Baseline deterministic: `{cfg.run_id}_baseline`\n"
        f"* Aggressive deterministic: `{cfg.run_id}_aggressive_deterministic`\n"
        f"* Embedding-only hypotheses: `{cfg.run_id}_embedding_only`\n"
        f"* DeepSeek generative hypotheses: `{cfg.run_id}_deepseek_generative`\n"
        f"* Ultra-loose diagnostic: `{cfg.run_id}_ultra_loose_diagnostic`\n"
        f"* Strict validation: `{cfg.run_id}_strict_validation`\n\n"
        "## 4. PnL Accounting Audit Result\n\n"
        f"{old_audit['summary']['diagnosis']}\n\n"
        "## 5. Model Provenance Audit Result\n\n"
        f"DeepSeek generative availability: `{provenance.get('deepseek_used_generatively')}`. "
        f"Model: `{cfg.deepseek_model}`. Embeddings were retrieval-only using "
        f"`{cfg.embedding_model}`.\n\n"
        "## 5b. Stake-Sizing Audit (Position Sizing)\n\n"
        "Every comparison run used the SAME per-leg stake and cash budget, so "
        "percentage metrics are stake-invariant and directly comparable. Raw USDC "
        "PnL is reported only for audit; it is NOT a ranking metric.\n\n"
        f"{stake_audit}\n"
        "## 6. Source Comparison (Percentage Metrics — Primary)\n\n"
        f"{comparison}\n"
        "## 7. DeepSeek Hypothesis Performance\n\n"
        f"* DeepSeek hypotheses proposed: {len(deepseek_results)}\n"
        f"* DeepSeek hypotheses with replay trades: {len(traded_deepseek)}\n"
        f"* Outside existing deterministic rulebook: "
        f"{sum(1 for r in deepseek_results if bool(r.get('outside_existing_deterministic_rulebook')))}\n\n"
        "## 8. Best Relationship Families\n\n"
        f"{best_families}\n"
        "## 9. Worst Relationship Families\n\n"
        f"{worst_families}\n"
        "## 10. Biggest Losses And Lessons\n\n"
        f"{_md_table(losing_rows[:10], ('source_group', 'relationship_id', 'context_space_id', 'mark_to_market_pnl_usdc', 'edge_implied_pnl_usdc'))}\n"
        "Losses mainly show where edge-implied theoretical violations did not align "
        "with end-of-data mark-to-market values. These remain exploratory diagnostics.\n\n"
        "## 11. DeepSeek Failure Modes\n\n"
        f"{_md_table(kill_rows[:15], ('hypothesis_id', 'relationship_type', 'accepted_trade_count', 'kill_or_tighten_reason'))}\n"
        "## 12. Candidate Relationship Classes To Promote\n\n"
        f"{_md_table(promotion_rows[:15], ('relationship_family_label', 'accepted_trade_count', 'edge_implied_pnl_usdc', 'promotion_reason'))}\n"
        "## 13. Candidate Relationship Classes To Kill/Tighten\n\n"
        f"{_md_table(kill_rows[:15], ('relationship_type', 'accepted_trade_count', 'kill_or_tighten_reason'))}\n"
        "## 14. Strict Validation Results\n\n"
        f"{_md_table(strict_rows, ('run_id', 'accepted_trade_count', 'distinct_relationships_traded', 'independent_windows', 'edge_implied_pnl_usdc', 'median_trade_return_pct', 'strict_validation_passed', 'grade'))}\n"
        "## 15. Did We Learn Enough?\n\n"
        "Yes for system refinement: the run separates embedding retrieval from "
        "DeepSeek reasoning, records provenance, and exposes which hypotheses are "
        "unsupported by the current buy-only replay engine. No result is upgraded "
        "to credible strategy status unless strict validation passes.\n\n"
        "## 16. Next Recommended Experiment\n\n"
        "Tighten candidate retrieval toward same-event bundles and explicit ladders, "
        "then rerun DeepSeek on fewer but structurally richer clusters. Add a "
        "same-YES duplicate spread simulator only if it can remain strictly local "
        "and research-only.\n\n"
        "## Bottlenecks\n\n"
        f"{_md_table(bottlenecks, ('bottleneck', 'count'))}\n"
        f"Report folder: `{report_dir}`\n"
        f"Strict validation grade: `{strict.get('grade', 'unknown')}`\n"
    )


def _route_breakdown_rows(counts: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {"route": route, "count": int(counts.get(route, 0)), "label": REPORT_LABEL}
        for route in DEEPSEEK_ROUTES
    ] + [
        {
            "route": "promoted_to_replay_store",
            "count": int(counts.get("promoted_to_replay_store", 0)),
            "label": REPORT_LABEL,
        }
    ]


def _signal_quality_report(
    cfg: WorkflowConfig,
    source_summaries: dict[str, dict[str, Any]],
    family_rows: list[dict[str, Any]],
    simulator_perf: list[dict[str, Any]],
    route_counts: dict[str, int],
) -> str:
    table = _md_table(
        list(source_summaries.values()),
        (
            "source_group",
            "accepted_trade_count",
            "distinct_relationships_traded",
            "trade_cost_adjusted_return_pct",
            "median_trade_return_pct",
            "mark_to_market_return_pct",
            "median_mtm_return_pct",
        ),
    )
    families = _md_table(family_rows[:10], (
        "source_group",
        "relationship_family_label",
        "accepted_trade_count",
        "trade_cost_adjusted_return_pct",
        "median_trade_return_pct",
    ))
    sim = _md_table(simulator_perf, (
        "simulator",
        "relationship_type",
        "accepted_trade_count",
        "edge_implied_return_pct_median",
        "realised_return_pct_median",
        "winning_trade_share",
    ))
    route = _md_table(_route_breakdown_rows(route_counts), ("route", "count"))
    return (
        f"# Signal Quality — {cfg.run_id}\n\n"
        f"{REPORT_LABEL}\n\n"
        "## Source comparison (percentage metrics only)\n\n"
        f"{table}\n"
        "## Top relationship families by edge-implied return %\n\n"
        f"{families}\n"
        "## Closed-form simulator performance (unsupported-but-testable)\n\n"
        f"{sim}\n"
        "## DeepSeek route breakdown\n\n"
        f"{route}\n"
        "Signal quality is measured INDEPENDENTLY of stake size. Each source group "
        "uses the same per-leg stake (see the position sizing report).\n"
    )


def _position_sizing_report(
    cfg: WorkflowConfig,
    source_summaries: dict[str, dict[str, Any]],
) -> str:
    table = _md_table(
        list(source_summaries.values()),
        (
            "source_group",
            "accepted_trade_count",
            "raw_fill_rows",
            "entry_cost_usdc",
            "slippage_pct_of_notional",
            "slippage_cost_usdc",
        ),
    )
    return (
        f"# Position Sizing — {cfg.run_id}\n\n"
        f"{REPORT_LABEL}\n\n"
        "Every comparison run used the SAME per-leg stake. Differences in raw "
        "USDC entry cost across sources are explained by fill count alone, not "
        "by sizing.\n\n"
        f"| field | value |\n| --- | --- |\n"
        f"| Normalised stake per leg | {NORMALISED_STAKE_USDC_PER_LEG:.2f} USDC |\n"
        f"| Normalised trade-pair cost target | {2 * NORMALISED_STAKE_USDC_PER_LEG:.2f} USDC |\n"
        f"| Starting cash (infinite-cash proxy) | {NORMALISED_STARTING_CASH_USDC:,.2f} USDC |\n"
        f"| Slippage model (engine) | 50 bps applied to notional |\n\n"
        "## Realised cost / slippage per source\n\n"
        f"{table}\n"
        "Slippage is expressed as a fraction of notional in the comparison table; "
        "raw USDC values are retained for audit but should never be used to rank "
        "sources.\n"
    )


def _portfolio_growth_report(
    cfg: WorkflowConfig,
    source_summaries: dict[str, dict[str, Any]],
    trade_rows: list[dict[str, Any]],
) -> str:
    table = _md_table(
        list(source_summaries.values()),
        (
            "source_group",
            "accepted_trade_count",
            "entry_cost_usdc",
            "edge_implied_pnl_usdc",
            "mark_to_market_pnl_usdc",
            "trade_cost_adjusted_return_pct",
            "mark_to_market_return_pct",
        ),
    )
    # Aggregate per-source independent windows (rough proxy for non-overlapping bets).
    windows_by_source: dict[str, int] = defaultdict(int)
    seen: dict[str, set[str]] = defaultdict(set)
    for row in trade_rows:
        sg = str(row.get("source_group") or "")
        wid = str(row.get("violation_window_id") or "")
        if wid and wid not in seen[sg]:
            seen[sg].add(wid)
            windows_by_source[sg] += 1
    windows_table = "| source_group | independent_windows |\n| --- | --- |\n" + "".join(
        f"| {sg} | {w} |\n" for sg, w in sorted(windows_by_source.items())
    )
    return (
        f"# Portfolio Growth — {cfg.run_id}\n\n"
        f"{REPORT_LABEL}\n\n"
        "Portfolio growth is reported under the same normalised stake as every "
        "other comparison run. A profitable percentage return on a tiny sample of "
        "trades is NOT a portfolio result — see independent-windows below.\n\n"
        "## Net dollar PnL with normalised stake\n\n"
        f"{table}\n"
        "## Independent violation windows per source\n\n"
        f"{windows_table}\n"
        "Portfolio interpretation: edge_implied PnL assumes resolution at the rule, "
        "mark_to_market assumes liquidation at the latest observed price; neither "
        "is realised PnL. Treat these as upper-bound growth estimates, not strategy "
        "results.\n"
    )


def _md_table(rows: list[dict[str, Any]], cols: tuple[str, ...]) -> str:
    if not rows:
        return "_(none)_\n"
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for row in rows:
        vals = []
        for col in cols:
            val = row.get(col, "")
            if isinstance(val, float):
                val = f"{val:.4f}"
            vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines) + "\n"


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({k for row in rows for k in row})
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:32]


def _slug(value: str) -> str:
    out = "".join(ch if ch.isalnum() else "_" for ch in value.lower()).strip("_")
    while "__" in out:
        out = out.replace("__", "_")
    return out or "unknown"


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def main() -> None:
    run_id = os.environ.get(
        "DEEPSEEK_EXPLORATORY_RUN_ID",
        "deepseek_exploratory_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
    )
    cfg = WorkflowConfig(
        run_id=run_id,
        ollama_base=os.environ.get("DEEPSEEK_OLLAMA_BASE", DEFAULT_OLLAMA_BASE),
        deepseek_model=os.environ.get("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL),
        embedding_model=os.environ.get("DEEPSEEK_EMBED_MODEL", DEFAULT_EMBED_MODEL),
        market_limit=_int(os.environ.get("DEEPSEEK_MARKET_LIMIT"), 500),
        embedding_pair_cap=_int(os.environ.get("DEEPSEEK_PAIR_CAP"), 240),
        deepseek_pair_limit=_int(os.environ.get("DEEPSEEK_PAIR_LIMIT"), 160),
        deepseek_seed_pair_limit=_int(os.environ.get("DEEPSEEK_SEED_PAIR_LIMIT"), 80),
        sim_threshold=_float(os.environ.get("DEEPSEEK_SIM_THRESHOLD"), 0.74),
        max_pairs_per_market=_int(os.environ.get("DEEPSEEK_MAX_PAIRS_PER_MARKET"), 6),
    )
    report_dir = run_workflow(Settings(), cfg)
    print(report_dir)


if __name__ == "__main__":  # pragma: no cover
    main()
