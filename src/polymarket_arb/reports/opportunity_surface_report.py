"""Opportunity surface report — trade-surface expansion diagnostic.

Reads one or more existing backtest run directories and produces the Phase G
opportunity surface files ranked by trade count (not PnL). This is a
RESEARCH-ONLY diagnostic report.

Outputs written to data/reports/opportunity_surface/<run_id>/:
  summary.md                    — human-readable summary, trade-count headline
  opportunity_surface.csv       — every gross violation signal (pre-execution)
  trade_candidates.csv          — signals that passed economic evaluation
  accepted_simulated_trades.csv — executed simulated trades
  blocked_opportunities.csv     — rejected candidates with blocker reason
  expansion_family_summary.csv  — per relationship_subtype / strategy_family rollup
  suspicious_matches.csv        — relationships with audit flags (Phase G: enriched)
  before_after_counts.csv       — count summary for this run
  master_report.md              — narrative/table report with issues, improvements, achievements

Usage::

    from polymarket_arb.reports.opportunity_surface_report import (
        generate_opportunity_surface_report,
    )
    out_dir = generate_opportunity_surface_report(
        data_root=Path("data"),
        run_id="my_run_id",
        include_exploratory=True,
    )
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LABEL = "RESEARCH-ONLY — diagnostic / exploratory. Not trading advice."

# Columns that the suspicious_matches stub always emits (enriched in Phase G).
_SUSPICIOUS_COLS = [
    "relationship_id",
    "source",
    "question_a",
    "question_b",
    "relationship_type",
    "relationship_subtype",
    "outcome_space_id",
    "strategy_lane",
    "template_id",
    "flags",
    "guard_results_json",
    "source_row_json",
    "suggested_action",
]

_FAMILY_SUMMARY_COLS = [
    "strategy_family",
    "relationship_subtype",
    "relationships_seen",
    "gross_violations",
    "accepted_simulated_trades",
    "distinct_relationships_traded",
    "simulated_pnl_note",
]

_BEFORE_AFTER_COLS = [
    "run_id",
    "generated_at",
    "relationships_loaded",
    "price_history_present",
    "aligned_price_series",
    "gross_violations",
    "candidates_accepted",
    "trades_executed",
    "distinct_relationships_traded",
    "distinct_spaces_traded",
    "credibility_label",
    "preset_label",
    "note",
]

_OPPORTUNITY_SURFACE_COLS = [
    "relationship_id",
    "signal_ts_ms",
    "opportunity_type",
    "relationship_type",
    "relationship_subtype",
    "outcome_space_id",
    "strategy_lane",
    "gross_edge",
    "net_edge",
    "accepted_for_simulation",
    "flags",
    "label",
]

_TRADE_CANDIDATE_COLS = [
    "relationship_id",
    "signal_ts_ms",
    "relationship_type",
    "relationship_subtype",
    "outcome_space_id",
    "strategy_lane",
    "gross_edge",
    "net_edge",
    "accepted_for_simulation",
    "flags",
    "label",
]

_ACCEPTED_TRADE_COLS = [
    "trade_id",
    "relationship_id",
    "bundle_event_id",
    "outcome_space_id",
    "token_id",
    "side",
    "leg",
    "fill_ts_ms",
    "notional_usdc",
    "replay_path",
    "preset",
]

_BLOCKED_OPPORTUNITY_COLS = [
    "relationship_id",
    "bundle_event_id",
    "opportunity_type",
    "relationship_type",
    "relationship_subtype",
    "outcome_space_id",
    "strategy_lane",
    "rejection_reason",
    "flags",
    "label",
]


def generate_opportunity_surface_report(
    data_root: Path,
    run_id: str,
    include_exploratory: bool = True,
    output_dir: Path | None = None,
    preset_label: str = "",
) -> Path:
    """Generate all 8 opportunity-surface report files for a backtest run.

    Args:
        data_root: Root of the Parquet data lake (typically Path("data")).
        run_id: Backtest run ID to analyse.
        include_exploratory: When True, includes exploratory lane results.
        output_dir: Override output location; default is
            data_root/../reports/opportunity_surface/<run_id>.
        preset_label: Optional label from the preset (e.g. "EXPLORATORY").

    Returns:
        Path to the output directory.
    """
    out_dir = output_dir or (
        data_root.parent / "reports" / "opportunity_surface" / run_id
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    run_base = data_root / "backtests" / run_id

    # ── Load pairwise lane data ────────────────────────────────────────────────
    signals: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    funnel_counts: dict[str, Any] = {}
    metrics: dict[str, Any] = {}
    execution_counts: list[int] = []
    credibility_label = "data_insufficient"

    context_dir = run_base / "context_aware"
    if context_dir.exists():
        for lane_dir in sorted(context_dir.iterdir()):
            if not lane_dir.is_dir():
                continue
            is_exploratory = lane_dir.name in {
                "exploratory_context_unreviewed",
                "exploratory_context_auto_approved",
            }
            if is_exploratory and not include_exploratory:
                continue
            signals.extend(_tag_rows(_read_csv(lane_dir / "signals.csv"), strategy_lane=lane_dir.name))
            trades.extend(_tag_rows(_read_csv(lane_dir / "trades.csv"), strategy_lane=lane_dir.name))
            rejected.extend(_tag_rows(_read_csv(lane_dir / "rejected_candidates.csv"), strategy_lane=lane_dir.name))
            lane_funnel = _read_json(lane_dir / "funnel_audit.json")
            _merge_funnel(funnel_counts, lane_funnel.get("counts", {}))
            lane_metrics = _read_json(lane_dir / "metrics.json")
            if lane_metrics:
                metrics = lane_metrics
                trade_count = _as_int(lane_metrics.get("trades_executed"))
                if trade_count is not None:
                    execution_counts.append(trade_count)
                credibility_label = lane_metrics.get("credibility_label", credibility_label)

    # ── Load exploratory research replay data ────────────────────────────────
    research_metrics: list[dict[str, Any]] = []
    research_dir = run_base / "research"
    if research_dir.exists():
        for preset_dir in sorted(research_dir.iterdir()):
            if not preset_dir.is_dir():
                continue
            signals.extend(_tag_rows(
                _read_csv(preset_dir / "signals.csv"),
                replay_path="research",
                preset=preset_dir.name,
            ))
            trades.extend(_tag_rows(
                _read_csv(preset_dir / "trades.csv"),
                replay_path="research",
                preset=preset_dir.name,
            ))
            rejected.extend(_tag_rows(
                _read_csv(preset_dir / "rejected_candidates.csv"),
                replay_path="research",
                preset=preset_dir.name,
            ))
            preset_funnel = _read_json(preset_dir / "funnel.json")
            _merge_funnel(funnel_counts, preset_funnel.get("counts", preset_funnel))
            preset_metrics = _read_json(preset_dir / "metrics.json")
            if preset_metrics:
                research_metrics.append(preset_metrics)
                if not metrics:
                    metrics = preset_metrics
                trade_count = _as_int(preset_metrics.get("trades_executed"))
                if trade_count is not None:
                    execution_counts.append(trade_count)
                credibility_label = preset_metrics.get("credibility_label", credibility_label)

    # ── Load bundle data ───────────────────────────────────────────────────────
    bundle_dir = run_base / "template_bundle"
    bundle_opps: list[dict[str, Any]] = []
    bundle_trades: list[dict[str, Any]] = []
    bundle_diagnostics: list[dict[str, Any]] = []
    bundle_metrics: dict[str, Any] = {}
    if bundle_dir.exists():
        bundle_opps = _read_csv(bundle_dir / "opportunities.csv")
        bundle_trades = _read_csv(bundle_dir / "trades.csv")
        bundle_diagnostics = _read_csv(bundle_dir / "bundle_diagnostics.csv")
        bundle_funnel = _read_json(bundle_dir / "funnel.json")
        _merge_funnel(funnel_counts, bundle_funnel)
        bundle_metrics = _read_json(bundle_dir / "metrics.json")
        bundle_count = _as_int(bundle_metrics.get("bundles_executed"))
        if bundle_count is not None:
            execution_counts.append(bundle_count)
        if bundle_metrics and not metrics:
            metrics = bundle_metrics
        credibility_label = bundle_metrics.get("credibility_label", credibility_label)

    relationship_index = _relationship_index(data_root)

    # ── Enrich signals ─────────────────────────────────────────────────────────
    opp_surface = _build_opportunity_surface(signals, relationship_index)
    trade_candidates = [s for s in signals if str(s.get("accepted_for_simulation", "")).lower() == "true"]
    accepted_trades = trades + bundle_trades
    blocked = _build_blocked_opportunities(rejected, bundle_opps, relationship_index)

    # ── Family summary ─────────────────────────────────────────────────────────
    family_summary = _build_family_summary(opp_surface, accepted_trades)
    suspicious = _build_suspicious_matches(
        opportunity_surface=opp_surface,
        blocked=blocked,
        accepted_trades=accepted_trades,
        bundle_diagnostics=bundle_diagnostics,
        relationship_index=relationship_index,
    )

    # ── Counts ────────────────────────────────────────────────────────────────
    distinct_rels_traded = len({t.get("relationship_id") for t in accepted_trades if t.get("relationship_id")})
    distinct_spaces_traded = len({t.get("outcome_space_id") for t in bundle_trades if t.get("outcome_space_id")})
    before_after = _build_before_after(
        run_id=run_id,
        funnel=funnel_counts,
        trades_executed=sum(execution_counts) if execution_counts else (len(accepted_trades) // 2 if accepted_trades else 0),
        distinct_rels=distinct_rels_traded,
        distinct_spaces=distinct_spaces_traded,
        credibility_label=credibility_label,
        preset_label=preset_label,
    )
    report_stats = _report_stats(
        before_after=before_after,
        family_summary=family_summary,
        blocked=blocked,
        suspicious=suspicious,
        bundle_diagnostics=bundle_diagnostics,
        research_metrics=research_metrics,
    )

    # ── Write outputs ─────────────────────────────────────────────────────────
    _write_csv_with_columns(out_dir / "opportunity_surface.csv", opp_surface, _OPPORTUNITY_SURFACE_COLS)
    _write_csv_with_columns(out_dir / "trade_candidates.csv", trade_candidates, _TRADE_CANDIDATE_COLS)
    _write_csv_with_columns(out_dir / "accepted_simulated_trades.csv", accepted_trades, _ACCEPTED_TRADE_COLS)
    _write_csv_with_columns(out_dir / "blocked_opportunities.csv", blocked, _BLOCKED_OPPORTUNITY_COLS)
    _write_csv_with_columns(out_dir / "expansion_family_summary.csv", family_summary, _FAMILY_SUMMARY_COLS)
    _write_csv_with_columns(out_dir / "suspicious_matches.csv", suspicious, _SUSPICIOUS_COLS)
    _write_csv(out_dir / "before_after_counts.csv", [before_after])
    _write_summary_md(
        path=out_dir / "summary.md",
        run_id=run_id,
        before_after=before_after,
        family_summary=family_summary,
        blocked=blocked,
        metrics=metrics,
        preset_label=preset_label,
    )
    _write_master_report_md(
        path=out_dir / "master_report.md",
        run_id=run_id,
        before_after=before_after,
        family_summary=family_summary,
        blocked=blocked,
        suspicious=suspicious,
        bundle_diagnostics=bundle_diagnostics,
        metrics=metrics,
        report_stats=report_stats,
        preset_label=preset_label,
    )

    try:
        from .suspicious_match_audit import generate_suspicious_match_audit

        generate_suspicious_match_audit(out_dir, samples_per_bucket=20)
    except Exception:
        # Optional sampler failure should not prevent the core report.
        pass

    return out_dir


# ── builders ──────────────────────────────────────────────────────────────────


def _build_opportunity_surface(
    signals: list[dict[str, Any]],
    relationship_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Tag each signal row with standard opportunity-surface columns."""
    rows = []
    for s in signals:
        row = dict(s)
        _merge_relationship_metadata(row, relationship_index)
        row.setdefault("opportunity_type", "pairwise")
        row.setdefault("accepted_for_simulation", False)
        row.setdefault("strategy_lane", row.get("lane", ""))
        row.setdefault("relationship_subtype", row.get("relationship_subtype", "unknown"))
        row.setdefault("outcome_space_id", row.get("outcome_space_id", ""))
        row["label"] = _LABEL
        row["flags"] = "; ".join(_suspicious_flags(row))
        rows.append(row)
    return rows


def _build_blocked_opportunities(
    rejected: list[dict[str, Any]],
    bundle_opps: list[dict[str, Any]],
    relationship_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for r in rejected:
        row = dict(r)
        _merge_relationship_metadata(row, relationship_index)
        row.setdefault("opportunity_type", "pairwise")
        row.setdefault("rejection_reason", row.get("blocked_reason") or "unknown")
        row["label"] = _LABEL
        row["flags"] = "; ".join(_suspicious_flags(row))
        rows.append(row)
    for b in bundle_opps:
        if str(b.get("accepted_for_simulation", "true")).lower() == "false":
            row = dict(b)
            row.setdefault("opportunity_type", "bundle")
            row["label"] = _LABEL
            row["flags"] = "; ".join(_bundle_diagnostic_flags(row))
            rows.append(row)
    return rows


def _build_family_summary(
    signals: list[dict[str, Any]],
    trades: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    family_signals: dict[tuple[str, str], int] = defaultdict(int)
    family_violations: dict[tuple[str, str], int] = defaultdict(int)
    family_traded_rels: dict[tuple[str, str], set[str]] = defaultdict(set)

    for s in signals:
        key = (
            s.get("relationship_type", "unknown"),
            s.get("relationship_subtype", "unknown"),
        )
        family_signals[key] += 1
        if str(s.get("accepted_for_simulation", "")).lower() == "true":
            family_violations[key] += 1

    traded_rel_ids = {t.get("relationship_id") for t in trades if t.get("relationship_id")}
    for s in signals:
        rel_id = s.get("relationship_id", "")
        if rel_id in traded_rel_ids:
            key = (
                s.get("relationship_type", "unknown"),
                s.get("relationship_subtype", "unknown"),
            )
            family_traded_rels[key].add(rel_id)

    all_keys = family_signals.keys() | family_violations.keys() | family_traded_rels.keys()
    rows = []
    for key in sorted(all_keys, key=lambda k: -family_violations.get(k, 0)):
        strategy_family, subtype = key
        rows.append({
            "strategy_family": strategy_family,
            "relationship_subtype": subtype,
            "relationships_seen": family_signals.get(key, 0),
            "gross_violations": family_violations.get(key, 0),
            "accepted_simulated_trades": len(family_traded_rels.get(key, set())),
            "distinct_relationships_traded": len(family_traded_rels.get(key, set())),
            "simulated_pnl_note": "see accepted_simulated_trades.csv — RESEARCH-ONLY",
        })
    return rows


def _build_suspicious_matches(
    *,
    opportunity_surface: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
    accepted_trades: list[dict[str, Any]],
    bundle_diagnostics: list[dict[str, Any]],
    relationship_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str]] = set()

    for source_name, source_rows in (
        ("opportunity_surface", opportunity_surface),
        ("blocked_opportunities", blocked),
    ):
        for row in source_rows:
            flags = _suspicious_flags(row)
            if not flags:
                continue
            key = (source_name, str(row.get("relationship_id", "")), str(row.get("signal_ts_ms", "")))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            rows.append(_suspicious_row(source_name, row, flags))

    traded_relationship_ids = {str(t.get("relationship_id", "")) for t in accepted_trades if t.get("relationship_id")}
    for rel_id in sorted(traded_relationship_ids):
        meta = relationship_index.get(rel_id, {})
        flags = _suspicious_flags({**meta, "relationship_id": rel_id, "source": "traded"})
        if flags:
            rows.append(_suspicious_row("traded_relationship", {**meta, "relationship_id": rel_id}, flags))

    for diag in bundle_diagnostics:
        flags = _bundle_diagnostic_flags(diag)
        if flags:
            rows.append(_suspicious_row("bundle_diagnostics", diag, flags))

    return sorted(rows, key=lambda r: (-len(str(r.get("flags", "")).split("; ")), r.get("relationship_id", "")))


def _suspicious_flags(row: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    relationship_type = str(row.get("relationship_type", ""))
    subtype = str(row.get("relationship_subtype", ""))
    lane = str(row.get("strategy_lane", ""))
    status = str(row.get("validation_status", row.get("relationship_validity_status", ""))).lower()
    reason = str(row.get("rejection_reason", row.get("blocked_reason", ""))).lower()

    if status == "rejected":
        flags.append("validation_rejected")
    confidence = _first_float(row, "final_confidence", "deterministic_confidence", "execution_model_confidence")
    if confidence is not None and confidence < 0.35:
        flags.append("low_confidence")
    outcome_space_id = str(row.get("outcome_space_id", ""))
    if row.get("outcome_space_id") in (None, "", "unknown", "same_topic_no_trade"):
        flags.append("outcome_space_missing")
    if outcome_space_id.startswith("proxy_") or "same_topic" in outcome_space_id:
        flags.append("outcome_space_proxy")
    if relationship_type in {"mutually_exclusive_category", "mutually_exclusive"}:
        if not row.get("shared_event") and not row.get("outcome_space_id"):
            flags.append("shared_event_missing")
    if "sports" in subtype or str(row.get("entity_type_a", "")) == "team":
        if not row.get("team_a") or not row.get("team_b"):
            flags.append("team_missing")
        if not row.get("season_a") and not row.get("season_b") and not row.get("season"):
            flags.append("season_missing")
        if not row.get("competition_a") and not row.get("competition_b") and not row.get("competition"):
            flags.append("competition_missing")
    if "candidate" in subtype or str(row.get("entity_type_a", "")) == "candidate":
        if not row.get("candidate_a") or not row.get("candidate_b"):
            flags.append("candidate_missing")
        if not row.get("season_a") and not row.get("season_b") and not row.get("election_year"):
            flags.append("season_missing")
    if any(term in reason for term in ("ambiguous", "unit", "date")):
        if "unit" in reason or "threshold" in reason:
            flags.append("ambiguous_units")
        if "date" in reason or "deadline" in reason:
            flags.append("ambiguous_date")
        if "unit" not in reason and "threshold" not in reason and "date" not in reason and "deadline" not in reason:
            flags.append("ambiguous_terms")
    evidence = _safe_json(row.get("evidence_json"))
    guard_results = evidence.get("guard_results", {}) if isinstance(evidence, dict) else {}
    if row.get("evidence_json") and not guard_results:
        flags.append("weak_guard_evidence")
    parse_evidence = evidence.get("parse_evidence", {}) if isinstance(evidence, dict) else {}
    closure_depth = _as_int(row.get("closure_depth") or parse_evidence.get("closure_depth"))
    if closure_depth is not None and closure_depth > 1:
        flags.append("closure_depth_gt_1")
    if lane.startswith("exploratory") or "exploratory" in str(row.get("preset_label", "")).lower():
        flags.append("exploratory_only_approval")
    if "observed > known" in reason or "observed>" in reason:
        flags.append("observed_gt_known_total")
    return sorted(set(flags))


def _bundle_diagnostic_flags(row: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    observed = _as_int(row.get("observed_count"))
    known = _as_int(row.get("known_total"))
    blocker = str(row.get("blocker", "")).lower()
    if known is not None and observed is not None and observed > known:
        flags.append("observed_gt_known_total")
    if row.get("completeness_status") != "complete" and row.get("basket") == "buy_all_yes":
        flags.append("incomplete_yes_risk")
    if "incomplete_bundle_buy_all_yes_blocked" in blocker:
        flags.append("incomplete_yes_blocked")
    if row.get("basket") == "buy_all_no" and row.get("completeness_status") != "complete":
        flags.append("subset_no_incomplete_but_valid")
    return flags


def _suspicious_row(source: str, row: dict[str, Any], flags: list[str]) -> dict[str, Any]:
    evidence = _safe_json(row.get("evidence_json"))
    guard_results = evidence.get("guard_results", {}) if isinstance(evidence, dict) else {}
    return {
        "relationship_id": row.get("relationship_id", row.get("bundle_event_id", "")),
        "source": source,
        "question_a": row.get("question_a", ""),
        "question_b": row.get("question_b", ""),
        "relationship_type": row.get("relationship_type", ""),
        "relationship_subtype": row.get("relationship_subtype", ""),
        "outcome_space_id": row.get("outcome_space_id", ""),
        "strategy_lane": row.get("strategy_lane", ""),
        "template_id": _template_id_from_reason(str(row.get("decision_reason", ""))),
        "flags": "; ".join(flags),
        "guard_results_json": json.dumps(guard_results, sort_keys=True),
        "source_row_json": json.dumps(_compact_row(row), sort_keys=True),
        "suggested_action": _suggested_action(flags),
    }


def _report_stats(
    *,
    before_after: dict[str, Any],
    family_summary: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
    suspicious: list[dict[str, Any]],
    bundle_diagnostics: list[dict[str, Any]],
    research_metrics: list[dict[str, Any]],
) -> dict[str, Any]:
    blockers = Counter(str(row.get("rejection_reason", "unknown")) for row in blocked)
    suspicious_flags = Counter()
    for row in suspicious:
        suspicious_flags.update(f for f in str(row.get("flags", "")).split("; ") if f)
    top_family = family_summary[0] if family_summary else {}
    return {
        "top_blockers": blockers.most_common(10),
        "top_suspicious_flags": suspicious_flags.most_common(10),
        "top_family": top_family,
        "bundle_diagnostic_rows": len(bundle_diagnostics),
        "research_presets_seen": sorted({str(m.get("preset_name")) for m in research_metrics if m.get("preset_name")}),
        "trade_count": before_after.get("trades_executed", 0),
    }


def _build_before_after(
    *,
    run_id: str,
    funnel: dict[str, Any],
    trades_executed: int,
    distinct_rels: int,
    distinct_spaces: int,
    credibility_label: str,
    preset_label: str,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "generated_at": _now_iso(),
        "relationships_loaded": funnel.get("relationships_loaded", 0),
        "price_history_present": funnel.get("price_history_present", 0),
        "aligned_price_series": funnel.get("aligned_price_series", 0),
        "gross_violations": funnel.get("gross_violations", 0),
        "candidates_accepted": funnel.get("candidates_accepted", 0),
        "trades_executed": trades_executed,
        "distinct_relationships_traded": distinct_rels,
        "distinct_spaces_traded": distinct_spaces,
        "credibility_label": credibility_label,
        "preset_label": preset_label or "default",
        "note": _LABEL,
    }


def _write_summary_md(
    *,
    path: Path,
    run_id: str,
    before_after: dict[str, Any],
    family_summary: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
    metrics: dict[str, Any],
    preset_label: str,
) -> None:
    ts = _now_iso()
    lines = [
        f"# Opportunity Surface Report — {ts}",
        "",
        f"> {_LABEL}",
        f"> run_id: `{run_id}`",
        f"> preset: `{preset_label or 'default'}`",
        "",
        "## Summary (ranked by trade count — PnL is secondary)",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Relationships loaded | {before_after['relationships_loaded']} |",
        f"| Price history present | {before_after['price_history_present']} |",
        f"| Aligned price series | {before_after['aligned_price_series']} |",
        f"| Gross violations | {before_after['gross_violations']} |",
        f"| Candidates accepted | {before_after['candidates_accepted']} |",
        f"| **Simulated trades executed** | **{before_after['trades_executed']}** |",
        f"| Distinct relationships traded | {before_after['distinct_relationships_traded']} |",
        f"| Distinct spaces traded | {before_after['distinct_spaces_traded']} |",
        f"| Credibility | `{before_after['credibility_label']}` |",
        "",
    ]

    if metrics.get("net_pnl_usdc"):
        lines += [
            "## Simulated PnL (SECONDARY — do not use as primary criterion)",
            "",
            f"Net PnL: **{metrics['net_pnl_usdc']} USDC** (simulated, research-only, "
            f"credibility = `{metrics.get('credibility_label', 'unknown')}`)",
            "",
            "> PnL is reported for completeness only. This refactor's goal is "
            "> **trade count and coverage**, not profit optimisation.",
            "",
        ]

    if family_summary:
        lines += [
            "## Top relationship families by gross violations",
            "",
            "| strategy_family | subtype | relationships | gross_violations | traded |",
            "| --- | --- | --- | --- | --- |",
        ]
        for row in family_summary[:20]:
            lines.append(
                f"| {row['strategy_family']} "
                f"| {row['relationship_subtype']} "
                f"| {row['relationships_seen']} "
                f"| {row['gross_violations']} "
                f"| {row['distinct_relationships_traded']} |"
            )
        lines.append("")

    blocker_counts: dict[str, int] = {}
    for b in blocked:
        reason = str(b.get("rejection_reason", "unknown"))
        blocker_counts[reason] = blocker_counts.get(reason, 0) + 1
    if blocker_counts:
        lines += [
            "## Top blockers (why opportunities were not accepted)",
            "",
            "| Blocker | Count |",
            "| --- | --- |",
        ]
        for reason, count in sorted(blocker_counts.items(), key=lambda x: -x[1])[:15]:
            lines.append(f"| {reason} | {count} |")
        lines.append("")

    lines += [
        "## Files in this report",
        "",
        "| File | Contents |",
        "| --- | --- |",
        "| `opportunity_surface.csv` | Every gross violation signal (pre-execution) |",
        "| `trade_candidates.csv` | Signals that passed economic evaluation |",
        "| `accepted_simulated_trades.csv` | Executed simulated trades |",
        "| `blocked_opportunities.csv` | Rejected candidates with blocker reason |",
        "| `expansion_family_summary.csv` | Per-family rollup (by trade count) |",
        "| `suspicious_matches.csv` | Audit flags (Phase G: enriched) |",
        "| `suspicious_match_audit.csv` | Random spot-check sampler by bucket |",
        "| `before_after_counts.csv` | Count summary for this run |",
        "| `master_report.md` | Narrative + statistics report |",
        "",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")


def _write_master_report_md(
    *,
    path: Path,
    run_id: str,
    before_after: dict[str, Any],
    family_summary: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
    suspicious: list[dict[str, Any]],
    bundle_diagnostics: list[dict[str, Any]],
    metrics: dict[str, Any],
    report_stats: dict[str, Any],
    preset_label: str,
) -> None:
    """Write the mixed narrative/table report requested for Phase G."""
    ts = _now_iso()
    top_blockers = report_stats.get("top_blockers", [])
    top_flags = report_stats.get("top_suspicious_flags", [])
    top_family = report_stats.get("top_family", {})
    research_presets = report_stats.get("research_presets_seen", [])
    pnl = metrics.get("net_pnl_usdc", "")

    achievements = [
        (
            f"Captured {before_after['gross_violations']} gross opportunity signals and "
            f"{before_after['trades_executed']} simulated trades for this run."
        ),
        (
            f"Produced {len(family_summary)} family rollup rows, so the surface is ranked by "
            "trade count and coverage before simulated PnL."
        ),
        (
            f"Generated {len(suspicious)} suspicious-match audit rows and "
            f"{len(bundle_diagnostics)} bundle diagnostic rows for manual review."
        ),
    ]
    if research_presets:
        achievements.append(
            "Included research replay output from presets: "
            + ", ".join(f"`{p}`" for p in research_presets)
            + "."
        )

    issues = []
    if top_blockers:
        issues.append(
            f"The largest blocker is `{top_blockers[0][0]}` with {top_blockers[0][1]} rows."
        )
    if top_flags:
        issues.append(
            f"The most common suspicious flag is `{top_flags[0][0]}` with {top_flags[0][1]} rows."
        )
    if before_after.get("credibility_label") == "data_insufficient":
        issues.append(
            "Credibility remains `data_insufficient`, which is expected for surface expansion but blocks viability claims."
        )
    if not issues:
        issues.append("No dominant blocker or suspicious flag was present in this fixture/report run.")

    improvements = [
        "Prioritise market-data coverage for the highest-count blockers before tuning strategy economics.",
        "Manually inspect `suspicious_matches.csv` and `suspicious_match_audit.md` before accepting newly expanded families.",
        "Strengthen deterministic evidence where rows show weak guard evidence, missing teams/candidates, or missing outcome spaces.",
        "Keep PnL secondary until strict and exploratory credibility labels improve beyond data-insufficient replay quality.",
    ]
    if any(flag == "observed_gt_known_total" for flag, _ in top_flags):
        improvements.append(
            "Refresh known-total registries for bundle spaces where observed candidates exceed the configured total."
        )

    lines = [
        f"# Master Opportunity Surface Report — {run_id}",
        "",
        f"> {_LABEL}",
        f"> generated_at: `{ts}`",
        f"> preset: `{preset_label or before_after.get('preset_label', 'default')}`",
        "",
        "## Executive Summary",
        "",
        (
            "This report combines the Phase G statistics with a short narrative review. "
            "It is designed to answer three questions: what expanded, what looks risky, "
            "and what should be improved before any stronger claims are made."
        ),
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Relationships loaded | {before_after['relationships_loaded']} |",
        f"| Price history present | {before_after['price_history_present']} |",
        f"| Aligned price series | {before_after['aligned_price_series']} |",
        f"| Gross violations | {before_after['gross_violations']} |",
        f"| Candidates accepted | {before_after['candidates_accepted']} |",
        f"| Simulated trades executed | {before_after['trades_executed']} |",
        f"| Distinct relationships traded | {before_after['distinct_relationships_traded']} |",
        f"| Distinct spaces traded | {before_after['distinct_spaces_traded']} |",
        f"| Suspicious rows | {len(suspicious)} |",
        f"| Bundle diagnostic rows | {len(bundle_diagnostics)} |",
        f"| Credibility | `{before_after['credibility_label']}` |",
        f"| Simulated PnL, secondary | {pnl or 'not reported'} |",
        "",
        "## Main Achievements",
        "",
    ]
    lines.extend(f"- {item}" for item in achievements)
    lines += [
        "",
        "## Main Issues",
        "",
    ]
    lines.extend(f"- {item}" for item in issues)
    lines += [
        "",
        "## Main Improvement Points",
        "",
    ]
    lines.extend(f"- {item}" for item in improvements)

    if top_family:
        lines += [
            "",
            "## Leading Family",
            "",
            "| Field | Value |",
            "| --- | --- |",
            f"| Strategy family | {_md(top_family.get('strategy_family', ''))} |",
            f"| Relationship subtype | {_md(top_family.get('relationship_subtype', ''))} |",
            f"| Gross violations | {top_family.get('gross_violations', 0)} |",
            f"| Distinct relationships traded | {top_family.get('distinct_relationships_traded', 0)} |",
        ]

    lines += [
        "",
        "## Top Families By Trade Count",
        "",
        "| Strategy family | Relationship subtype | Seen | Gross violations | Traded |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for row in family_summary[:15]:
        lines.append(
            "| "
            f"{_md(row.get('strategy_family', ''))} | "
            f"{_md(row.get('relationship_subtype', ''))} | "
            f"{row.get('relationships_seen', 0)} | "
            f"{row.get('gross_violations', 0)} | "
            f"{row.get('distinct_relationships_traded', 0)} |"
        )
    if not family_summary:
        lines.append("| none | none | 0 | 0 | 0 |")

    lines += [
        "",
        "## Top Blockers",
        "",
        "| Blocker | Count |",
        "| --- | ---: |",
    ]
    for reason, count in top_blockers[:15]:
        lines.append(f"| {_md(reason)} | {count} |")
    if not top_blockers:
        lines.append("| none | 0 |")

    lines += [
        "",
        "## Suspicious Match Flags",
        "",
        "| Flag | Count | Interpretation |",
        "| --- | ---: | --- |",
    ]
    for flag, count in top_flags[:15]:
        lines.append(f"| {_md(flag)} | {count} | {_md(_flag_interpretation(flag))} |")
    if not top_flags:
        lines.append("| none | 0 | No suspicious rows were generated. |")

    lines += [
        "",
        "## File Guide",
        "",
        "| File | Why it matters |",
        "| --- | --- |",
        "| `summary.md` | Compact headline counts with PnL caveated as secondary. |",
        "| `master_report.md` | This narrative/statistical review. |",
        "| `opportunity_surface.csv` | Every gross opportunity signal, including suspicious flags. |",
        "| `trade_candidates.csv` | Signals that passed economic filters. |",
        "| `accepted_simulated_trades.csv` | Simulated fills/legs. Research-only. |",
        "| `blocked_opportunities.csv` | Rejections and blockers to improve next. |",
        "| `expansion_family_summary.csv` | Family rollup ranked by coverage/trade count. |",
        "| `suspicious_matches.csv` | Audit flags for deterministic/context quality review. |",
        "| `suspicious_match_audit.csv` | Deterministic random spot-check sample by bucket. |",
        "| `before_after_counts.csv` | One-row machine-readable count summary. |",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _flag_interpretation(flag: str) -> str:
    descriptions = {
        "shared_event_missing": "Mutual-exclusion or category relationship lacks a stable shared event.",
        "outcome_space_missing": "The relationship could not be tied to a clean outcome space.",
        "outcome_space_proxy": "The outcome space appears to be a placeholder/proxy.",
        "team_missing": "Sports relationship metadata is missing one or both teams.",
        "candidate_missing": "Election/candidate metadata is incomplete.",
        "season_missing": "Sports/election relationship lacks an explicit season or year.",
        "competition_missing": "Sports relationship lacks a competition/league field.",
        "validation_rejected": "Context validation rejected this relationship.",
        "low_confidence": "Deterministic/context confidence is low.",
        "ambiguous_units": "Threshold/unit parsing needs manual review.",
        "ambiguous_date": "Date/deadline parsing needs manual review.",
        "observed_gt_known_total": "Observed bundle candidates exceeded known total, so YES-side completeness is unsafe.",
        "weak_guard_evidence": "Evidence JSON did not include strong guard results.",
        "closure_depth_gt_1": "Transitive relationship was derived through a multi-hop closure.",
        "exploratory_only_approval": "Relationship came from an exploratory lane/preset.",
    }
    return descriptions.get(flag, "Manual review recommended.")


def _md(value: Any) -> str:
    return str(value).replace("|", "\\|")


# ── I/O helpers ───────────────────────────────────────────────────────────────


def _relationship_index(data_root: Path) -> dict[str, dict[str, Any]]:
    try:
        from ..storage.parquet.relationship_candidates_repo import (
            ParquetRelationshipCandidatesRepository,
        )

        repo = ParquetRelationshipCandidatesRepository(data_root)
        rows = {}
        for rel in repo.iter_latest():
            row = asdict(rel) if is_dataclass(rel) else dict(rel)
            rel_id = str(row.get("relationship_id", ""))
            if rel_id:
                rows[rel_id] = row
        return rows
    except Exception:
        return {}


def _merge_relationship_metadata(
    row: dict[str, Any],
    relationship_index: dict[str, dict[str, Any]],
) -> None:
    rel_id = str(row.get("relationship_id", ""))
    if not rel_id:
        return
    meta = relationship_index.get(rel_id)
    if not meta:
        return
    for key, value in meta.items():
        if _empty(row.get(key)) and not _empty(value):
            row[key] = value


def _tag_rows(rows: list[dict[str, Any]], **tags: Any) -> list[dict[str, Any]]:
    tagged = []
    for row in rows:
        next_row = dict(row)
        for key, value in tags.items():
            next_row.setdefault(key, value)
        tagged.append(next_row)
    return tagged


def _write_csv_with_columns(
    path: Path,
    rows: list[dict[str, Any]],
    columns: list[str],
) -> None:
    extras = sorted({k for row in rows for k in row if k not in columns})
    fieldnames = list(dict.fromkeys(columns + extras))
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _safe_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return {}
    if not isinstance(value, str):
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


def _first_float(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _template_id_from_reason(reason: str) -> str:
    if not reason:
        return ""
    for marker in ("template=", "template_id="):
        if marker not in reason:
            continue
        tail = reason.split(marker, 1)[1]
        return tail.split()[0].strip(";,")
    return ""


def _compact_row(row: dict[str, Any]) -> dict[str, Any]:
    keep = [
        "relationship_id",
        "bundle_event_id",
        "relationship_type",
        "relationship_subtype",
        "question_a",
        "question_b",
        "outcome_space_id",
        "strategy_lane",
        "validation_status",
        "relationship_validity_status",
        "final_confidence",
        "deterministic_confidence",
        "rejection_reason",
        "blocked_reason",
        "decision_reason",
        "completeness_status",
        "basket",
        "observed_count",
        "known_total",
    ]
    compact = {k: row.get(k) for k in keep if k in row and row.get(k) not in (None, "")}
    for key in ("evidence_json", "source_row_json"):
        value = row.get(key)
        if value not in (None, ""):
            text = str(value)
            compact[key] = text[:500] + ("..." if len(text) > 500 else "")
    return compact


def _suggested_action(flags: list[str]) -> str:
    flag_set = set(flags)
    if "validation_rejected" in flag_set:
        return "exclude from execution; inspect context validation before reuse"
    if "observed_gt_known_total" in flag_set:
        return "refresh known-total registry; keep buy_all_yes blocked"
    if {"team_missing", "candidate_missing", "season_missing", "competition_missing"} & flag_set:
        return "repair semantic metadata/extraction and rerun expansion"
    if {"ambiguous_units", "ambiguous_date"} & flag_set:
        return "manual parse review; add deterministic parser regression if accepted"
    if "weak_guard_evidence" in flag_set:
        return "strengthen expansion evidence JSON guard_results"
    if "closure_depth_gt_1" in flag_set:
        return "review transitive path before treating as strategy eligible"
    if "exploratory_only_approval" in flag_set:
        return "route through strict/reviewed context before credibility claims"
    if "low_confidence" in flag_set:
        return "review manually or raise confidence threshold for this family"
    return "manual review"


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except Exception:
        return []


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({k for row in rows for k in row})
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _merge_funnel(dest: dict[str, Any], src: dict[str, Any]) -> None:
    for k, v in src.items():
        if isinstance(v, (int, float)):
            dest[k] = dest.get(k, 0) + v
        elif k not in dest:
            dest[k] = v


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
