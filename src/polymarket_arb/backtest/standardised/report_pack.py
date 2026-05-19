"""Standardised report pack.

Given a list of ``StandardisedTradeRow`` and a ``StandardisedRunManifest``,
write the standard outputs:

* run_manifest.json
* standardised_trade_log.parquet
* standardised_trade_log.csv
* relationship_family_performance.csv
* context_space_performance.csv
* source_lane_performance.csv
* rulebook_trade_report.md
* ai_discovery_report.md
* controls_report.csv
* main_backtest_report.md

Reports are PERCENTAGE-LED.  USDC PnL is shown for audit only and never as the
primary ranking metric.  No automatic promotion/demotion is performed — review
status remains ``unreviewed`` until a human inspects the log.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .contract import StandardisedRunManifest, StandardisedTradeRow

REPORT_LABEL = (
    "STANDARDISED_BACKTEST_v1 — evidence capture only; "
    "no automatic promotion / demotion / kill decisions."
)


def write_report_pack(
    out_dir: Path,
    trades: list[StandardisedTradeRow],
    manifest: StandardisedRunManifest,
    *,
    deepseek_funnel: list[dict[str, Any]] | None = None,
) -> list[Path]:
    """Write the full standardised report pack and return the list of file paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    trade_dicts = [t.model_dump(mode="json") for t in trades]
    _assert_funded_lane_sizing(trades)

    # Populate manifest counts up-front so they appear in the markdown.
    manifest.total_trades = len(trades)
    manifest.trades_by_lane = dict(Counter(t.source_lane for t in trades))
    manifest.deepseek_trades_with_justification = sum(
        1 for t in trades if t.is_ai_generated and t.justification_present
    )
    manifest.deepseek_hypotheses_with_justification = len(
        {t.hypothesis_id for t in trades if t.is_ai_generated and t.justification_present}
    )
    manifest.output_dir = str(out_dir)

    written: list[Path] = []

    # ── 1. trade log (parquet + csv) ──────────────────────────────────────────
    trade_log_parquet = out_dir / "standardised_trade_log.parquet"
    trade_log_csv = out_dir / "standardised_trade_log.csv"
    _write_parquet(trade_log_parquet, trade_dicts)
    _write_csv(trade_log_csv, trade_dicts)
    written += [trade_log_parquet, trade_log_csv]

    # ── 2. group performance tables ──────────────────────────────────────────
    family_rows = group_performance(trades, ("source_lane", "relationship_family"))
    context_rows = group_performance(trades, ("source_lane", "context_space_id"))
    lane_rows = group_performance(trades, ("source_lane",))

    family_csv = out_dir / "relationship_family_performance.csv"
    context_csv = out_dir / "context_space_performance.csv"
    lane_csv = out_dir / "source_lane_performance.csv"
    _write_csv(family_csv, family_rows)
    _write_csv(context_csv, context_rows)
    _write_csv(lane_csv, lane_rows)
    written += [family_csv, context_csv, lane_csv]

    # ── 2b. lane-type split reports ─────────────────────────────────────────
    funded_csv = out_dir / "funded_replay_performance.csv"
    synthetic_csv = out_dir / "synthetic_diagnostic_performance.csv"
    controls_split_csv = out_dir / "controls_performance.csv"
    resolved_only_csv = out_dir / "resolved_only_source_lane_performance.csv"
    _write_csv(funded_csv, group_performance(_funded_replay_trades(trades), ("source_lane",)))
    _write_csv(
        synthetic_csv,
        group_performance(_synthetic_diagnostic_trades(trades), ("source_lane",)),
    )
    _write_csv(controls_split_csv, group_performance(_control_trades(trades), ("source_lane",)))
    _write_csv(
        resolved_only_csv,
        group_performance(_fully_resolved_market_trades(trades), ("source_lane",)),
    )
    written += [funded_csv, synthetic_csv, controls_split_csv, resolved_only_csv]

    # ── 3. controls table (CSV only — keep it as raw evidence) ────────────────
    controls_rows = [t.model_dump(mode="json") for t in trades if t.is_control]
    controls_csv = out_dir / "controls_report.csv"
    _write_csv(controls_csv, controls_rows)
    written.append(controls_csv)

    # ── 3b. DeepSeek funnel (where do hypotheses end up?) ────────────────────
    if deepseek_funnel is not None:
        funnel_csv = out_dir / "deepseek_hypothesis_funnel.csv"
        _write_csv(funnel_csv, deepseek_funnel)
        written.append(funnel_csv)

    # ── 4. markdown reports ──────────────────────────────────────────────────
    rulebook_md = out_dir / "rulebook_trade_report.md"
    rulebook_md.write_text(
        _rulebook_md(trades, lane_rows, family_rows),
        encoding="utf-8",
    )
    ai_md = out_dir / "ai_discovery_report.md"
    ai_md.write_text(
        _ai_discovery_md(trades, lane_rows, family_rows),
        encoding="utf-8",
    )
    main_md = out_dir / "main_backtest_report.md"
    main_md.write_text(
        _main_md(manifest, trades, lane_rows, family_rows, context_rows),
        encoding="utf-8",
    )
    written += [rulebook_md, ai_md, main_md]

    # ── 5. manifest ──────────────────────────────────────────────────────────
    manifest.output_files = [p.name for p in written]

    manifest_json = out_dir / "run_manifest.json"
    manifest_json.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, default=str),
        encoding="utf-8",
    )
    written.append(manifest_json)

    return written


# ── group performance ────────────────────────────────────────────────────────


def group_performance(
    trades: Iterable[StandardisedTradeRow],
    keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Aggregate trades by ``keys`` and produce percentage-led group metrics.

    Sort order: descending ``trade_cost_adjusted_return_pct``, then descending
    ``mark_to_market_return_pct``.  Stake-dependent USDC totals are included
    for audit but are never the primary sort key.
    """
    grouped: dict[tuple[str, ...], list[StandardisedTradeRow]] = defaultdict(list)
    for t in trades:
        grouped[tuple(_key_of(t, k) for k in keys)].append(t)
    out: list[dict[str, Any]] = []
    for key_values, group in grouped.items():
        row = {k: v for k, v in zip(keys, key_values, strict=True)}
        row.update(_group_metrics(group))
        row["label"] = REPORT_LABEL
        out.append(row)
    return sorted(
        out,
        key=lambda r: (
            _safe_float(r.get("trade_cost_adjusted_return_pct")),
            _safe_float(r.get("mark_to_market_return_pct")),
        ),
        reverse=True,
    )


def _group_metrics(group: list[StandardisedTradeRow]) -> dict[str, Any]:
    cost = sum(t.entry_cost_usdc for t in group)
    edge_pnl = sum((t.edge_implied_pnl_usdc or 0.0) for t in group)
    mtm_pnl = sum((t.mark_to_market_pnl_usdc or 0.0) for t in group)
    realised_pnl = sum((t.realised_pnl_usdc or 0.0) for t in group if t.realised_pnl_usdc is not None)
    realised_observed = sum(1 for t in group if t.realised_pnl_usdc is not None)

    # Per-trade-group entry-cost stats so cross-lane comparison is honest.
    cost_per_group: dict[str, float] = {}
    for t in group:
        cost_per_group[t.trade_group_id] = cost_per_group.get(t.trade_group_id, 0.0) + t.entry_cost_usdc
    group_costs = sorted(cost_per_group.values()) if cost_per_group else [0.0]
    mean_cost_per_group = sum(group_costs) / len(group_costs)
    median_cost_per_group = group_costs[len(group_costs) // 2]

    trade_returns = [t.trade_cost_adjusted_return_pct for t in group if t.trade_cost_adjusted_return_pct is not None]
    mtm_returns = [t.mark_to_market_return_pct for t in group if t.mark_to_market_return_pct is not None]
    realised_returns = [t.realised_return_pct for t in group if t.realised_return_pct is not None]

    wins = sum(1 for r in trade_returns if r is not None and r > 0)
    win_share = wins / len(trade_returns) if trade_returns else 0.0

    distinct_rels = {t.relationship_id for t in group if t.relationship_id}
    distinct_spaces = {t.context_space_id or t.outcome_space_id for t in group if (t.context_space_id or t.outcome_space_id)}
    groups = {t.trade_group_id for t in group}

    dominance = _dominance(group)
    holding_periods = [t.holding_period_ms for t in group if t.holding_period_ms is not None]

    return {
        "accepted_trade_count": len(groups),
        "raw_fill_rows": len(group),
        "unique_relationships": len(distinct_rels),
        "unique_spaces": len(distinct_spaces),
        # ── percentage-led ranking columns ───────────────────────────────────
        "trade_cost_adjusted_return_pct": (edge_pnl / cost) if cost else 0.0,
        "edge_implied_return_pct": (edge_pnl / cost) if cost else 0.0,
        "mark_to_market_return_pct": (mtm_pnl / cost) if cost else 0.0,
        "realised_return_pct": (realised_pnl / cost) if cost and realised_observed else None,
        "median_trade_return_pct": statistics.median(trade_returns) if trade_returns else 0.0,
        "median_mtm_return_pct": statistics.median(mtm_returns) if mtm_returns else 0.0,
        "median_realised_return_pct": statistics.median(realised_returns) if realised_returns else None,
        "win_share": win_share,
        "dominance_share": dominance,
        # ── audit-only USDC totals ────────────────────────────────────────────
        "entry_cost_usdc": cost,
        "mean_entry_cost_per_trade_group_usdc": mean_cost_per_group,
        "median_entry_cost_per_trade_group_usdc": median_cost_per_group,
        "edge_implied_pnl_usdc": edge_pnl,
        "mark_to_market_pnl_usdc": mtm_pnl,
        "realised_pnl_usdc": realised_pnl if realised_observed else None,
        "realised_observed_count": realised_observed,
        "median_holding_period_ms": statistics.median(holding_periods) if holding_periods else None,
    }


def _dominance(group: list[StandardisedTradeRow]) -> float:
    by_rel: Counter[str] = Counter()
    for t in group:
        by_rel[t.relationship_id or ""] += abs(t.edge_implied_pnl_usdc or 0.0) or abs(t.mark_to_market_pnl_usdc or 0.0)
    total = sum(by_rel.values())
    if not total:
        return 0.0
    return max(by_rel.values()) / total


def _funded_replay_trades(trades: Iterable[StandardisedTradeRow]) -> list[StandardisedTradeRow]:
    return [
        t for t in trades
        if t.trade_kind != "closed_form" and not t.is_control
    ]


def _synthetic_diagnostic_trades(
    trades: Iterable[StandardisedTradeRow],
) -> list[StandardisedTradeRow]:
    return [
        t for t in trades
        if t.trade_kind == "closed_form" and not t.is_control
    ]


def _control_trades(trades: Iterable[StandardisedTradeRow]) -> list[StandardisedTradeRow]:
    return [t for t in trades if t.is_control]


def _fully_resolved_market_trades(
    trades: Iterable[StandardisedTradeRow],
) -> list[StandardisedTradeRow]:
    grouped: dict[str, list[StandardisedTradeRow]] = defaultdict(list)
    for t in trades:
        if t.trade_kind == "closed_form":
            continue
        grouped[t.trade_group_id].append(t)
    out: list[StandardisedTradeRow] = []
    for group in grouped.values():
        if group and all(t.resolution_outcome in ("yes", "no") for t in group):
            out.extend(group)
    return out


def _assert_funded_lane_sizing(trades: Iterable[StandardisedTradeRow]) -> None:
    """Fail loudly if funded replay lanes have different group entry costs."""
    by_group: dict[tuple[str, str], float] = defaultdict(float)
    for t in _funded_replay_trades(trades):
        by_group[(t.source_lane, t.trade_group_id)] += t.entry_cost_usdc
    if not by_group:
        return
    costs = sorted((lane, gid, cost) for (lane, gid), cost in by_group.items())
    expected = costs[0][2]
    mismatches = [
        (lane, gid, cost)
        for lane, gid, cost in costs
        if abs(cost - expected) > 0.01
    ]
    if mismatches:
        sample = ", ".join(
            f"{lane}/{gid}={cost:.2f}" for lane, gid, cost in mismatches[:5]
        )
        raise ValueError(
            "funded replay group sizing is not standardised: "
            f"expected {expected:.2f} USDC per group; mismatches: {sample}"
        )


# ── markdown reports ─────────────────────────────────────────────────────────


def _rulebook_md(
    trades: list[StandardisedTradeRow],
    lane_rows: list[dict[str, Any]],
    family_rows: list[dict[str, Any]],
) -> str:
    rulebook_trades = [t for t in trades if t.is_rulebook]
    families = [r for r in family_rows if "rulebook" in str(r.get("source_lane", ""))]
    lines = [
        "# Rulebook Trade Report",
        "",
        REPORT_LABEL,
        "",
        f"Total rulebook trade legs: **{len(rulebook_trades)}**",
        f"Distinct rulebook pair-executions: **{len({t.trade_group_id for t in rulebook_trades})}**",
        "",
        "## Rulebook lanes (percentage-led)",
        "",
        _md_table(
            [r for r in lane_rows if "rulebook" in str(r.get("source_lane", ""))],
            ("source_lane", "accepted_trade_count", "trade_cost_adjusted_return_pct",
             "mark_to_market_return_pct", "median_trade_return_pct", "win_share",
             "unique_relationships", "unique_spaces", "dominance_share"),
        ),
        "",
        "## Top rulebook relationship families",
        "",
        _md_table(
            families[:15],
            ("source_lane", "relationship_family", "accepted_trade_count",
             "trade_cost_adjusted_return_pct", "mark_to_market_return_pct",
             "median_trade_return_pct", "win_share", "unique_relationships"),
        ),
        "",
        "## Audit reminder",
        "",
        "Reports are for evidence capture only.  Review fields are neutral "
        "(`review_status`, `review_notes`, `observed_failure_mode`, "
        "`observed_pattern_tag`, `needs_manual_review`).  No automatic "
        "rulebook promotion / demotion / kill decisions are made.",
        "",
    ]
    return "\n".join(lines)


def _ai_discovery_md(
    trades: list[StandardisedTradeRow],
    lane_rows: list[dict[str, Any]],
    family_rows: list[dict[str, Any]],
) -> str:
    ai_trades = [t for t in trades if t.is_ai_generated]
    with_justification = [t for t in ai_trades if t.justification_present]
    without_justification = [t for t in ai_trades if not t.justification_present]
    outside_rulebook = [
        t for t in with_justification if t.justification_outside_rulebook
    ]
    inside_rulebook = [
        t for t in with_justification if t.justification_inside_rulebook and not t.justification_outside_rulebook
    ]
    ai_lane_rows = [r for r in lane_rows if str(r.get("source_lane", "")).startswith("ai_") or "closed_form" in str(r.get("source_lane", ""))]
    ai_families = [
        r for r in family_rows
        if str(r.get("source_lane", "")).startswith("ai_") or "closed_form" in str(r.get("source_lane", ""))
    ]
    lines = [
        "# AI Discovery (Free-Reign) Report",
        "",
        REPORT_LABEL,
        "",
        f"Total AI-generated trade legs: **{len(ai_trades)}**",
        f"Legs with structured pre-trade justification: **{len(with_justification)}**",
        f"Legs WITHOUT justification (data-pipeline bug — investigate): **{len(without_justification)}**",
        f"Justifications claiming OUTSIDE existing rulebook: **{len(outside_rulebook)}**",
        f"Justifications claiming INSIDE existing rulebook: **{len(inside_rulebook)}**",
        "",
        "## AI lanes (percentage-led)",
        "",
        _md_table(
            ai_lane_rows,
            ("source_lane", "accepted_trade_count", "trade_cost_adjusted_return_pct",
             "mark_to_market_return_pct", "median_trade_return_pct", "win_share",
             "unique_relationships", "unique_spaces", "dominance_share"),
        ),
        "",
        "## AI relationship families",
        "",
        _md_table(
            ai_families[:20],
            ("source_lane", "relationship_family", "accepted_trade_count",
             "trade_cost_adjusted_return_pct", "mark_to_market_return_pct",
             "median_trade_return_pct", "win_share", "unique_relationships"),
        ),
        "",
        "## Sample pre-trade justifications (first 5 trades with justification)",
        "",
    ]
    seen_hyps: set[str] = set()
    sample_count = 0
    for t in with_justification:
        if t.hypothesis_id in seen_hyps:
            continue
        seen_hyps.add(t.hypothesis_id)
        lines += [
            f"### hypothesis_id `{t.hypothesis_id}`",
            f"- **Source agent:** `{t.source_agent}`",
            f"- **Claim:** {t.justification_relationship_claim}",
            f"- **Why it should work:** {t.justification_why_works}",
            f"- **Why it may fail:** {t.justification_why_may_fail}",
            f"- **Evidence:** {t.justification_evidence}",
            f"- **Simulator / method:** `{t.justification_simulator_or_method}`",
            f"- **Model confidence:** {t.justification_confidence}",
            f"- **Outside rulebook (model's claim):** {t.justification_outside_rulebook}",
            "",
        ]
        sample_count += 1
        if sample_count >= 5:
            break
    if sample_count == 0:
        lines.append("_No AI trades with structured justification in this run._")
    return "\n".join(lines)


def _main_md(
    manifest: StandardisedRunManifest,
    trades: list[StandardisedTradeRow],
    lane_rows: list[dict[str, Any]],
    family_rows: list[dict[str, Any]],
    context_rows: list[dict[str, Any]],
) -> str:
    exploratory = sum(1 for t in trades if t.is_exploratory)
    strict = sum(1 for t in trades if t.is_strict_validation)
    diagnostic = sum(1 for t in trades if t.is_diagnostic)
    controls = sum(1 for t in trades if t.is_control)
    ai = sum(1 for t in trades if t.is_ai_generated)
    rb = sum(1 for t in trades if t.is_rulebook)
    resolved = sum(1 for t in trades if t.resolution_outcome in ("yes", "no"))
    unresolved = sum(1 for t in trades if t.resolution_outcome not in ("yes", "no"))
    pct_unresolved = (100.0 * unresolved / max(1, len(trades)))
    realised_observed = sum(1 for t in trades if t.realised_pnl_usdc is not None)
    funded_rows = group_performance(_funded_replay_trades(trades), ("source_lane",))
    synthetic_rows = group_performance(_synthetic_diagnostic_trades(trades), ("source_lane",))
    control_rows = group_performance(_control_trades(trades), ("source_lane",))
    resolved_only_rows = group_performance(_fully_resolved_market_trades(trades), ("source_lane",))

    banner = (
        "**RUN STATUS: FULLY RESOLVED.**" if unresolved == 0
        else f"**RUN STATUS: PARTIAL — {unresolved}/{len(trades)} legs ({pct_unresolved:.1f}%) on unresolved markets.**  "
        "Realised PnL is only available for resolved legs."
    )

    causality = (manifest.config_overrides or {}).get("causality_summary") or {}
    causality_total = int(causality.get("suppressed", 0) or 0)
    causality_per_lane = causality.get("per_lane_suppressed") or {}
    causality_lines: list[str] = []
    if causality_total > 0:
        per_lane_str = ", ".join(
            f"{k}={v}" for k, v in causality_per_lane.items() if v
        ) or "-"
        causality_lines = [
            f"**CAUSALITY GATE: {causality_total} legs suppressed** "
            f"(entry_ts >= inferred resolution_ts).  Per-lane: {per_lane_str}.",
            "",
            "These trades had access to the market outcome at entry time and "
            "would be look-ahead leakage if reported.  They are excluded from "
            "the standardised trade log; only audit-resolved survivors are below.",
            "",
        ]

    depth = (manifest.config_overrides or {}).get("depth_execution_summary") or {}
    depth_lines: list[str] = []
    if depth:
        cov = float(depth.get("depth_coverage_pct") or 0.0)
        filled = int(depth.get("legs_filled_from_depth") or 0)
        fallback = int(depth.get("legs_fallback_price_history_only") or 0)
        status = (
            "**DEPTH-AWARE EXECUTION: FULL COVERAGE.**" if cov >= 99.0
            else f"**DEPTH-AWARE EXECUTION: {cov:.1f}% coverage.**  "
            f"{filled}/{filled + fallback} legs filled from recorded orderbook depth; "
            f"the rest fell back to flat-bps slippage and are tagged "
            f"`execution_model_used=price_history_only_fallback`."
        )
        depth_lines = [status, ""]
        if cov < 50.0:
            depth_lines.append(
                "_Coverage is low because the historical orderbook lake is sparse — "
                "the realism gap is explicit in the trade log, not hidden.  "
                "Continuous orderbook recording on the VPS (Phase 4) closes this gap "
                "going forward._"
            )
            depth_lines.append("")

    lines = [
        "# Main Standardised Backtest Report",
        "",
        REPORT_LABEL,
        "",
        banner,
        "",
        *causality_lines,
        *depth_lines,
        f"- run_id: `{manifest.run_id}`",
        f"- output: `{manifest.output_dir}`",
        f"- total trade legs: **{len(trades)}**",
        f"- causality-suppressed (excluded from totals): **{causality_total}**",
        f"- rulebook legs: **{rb}** | AI legs: **{ai}** | exploratory: **{exploratory}** | strict-validation: **{strict}** | controls: **{controls}** | diagnostic: **{diagnostic}**",
        f"- resolved: **{resolved}** | unresolved: **{unresolved}** ({pct_unresolved:.1f}%) | realised-PnL legs: **{realised_observed}**",
        "",
        "## Source-lane performance (percentage-led)",
        "",
        _md_table(
            lane_rows,
            ("source_lane", "accepted_trade_count",
             "mean_entry_cost_per_trade_group_usdc", "median_entry_cost_per_trade_group_usdc",
             "trade_cost_adjusted_return_pct",
             "mark_to_market_return_pct", "median_trade_return_pct", "win_share",
             "unique_relationships", "unique_spaces", "dominance_share",
             "entry_cost_usdc"),
        ),
        "",
        "## Funded replay lanes",
        "",
        "These rows are funded historical replay fills.  Realised PnL is only populated for resolved markets.",
        "",
        _md_table(
            funded_rows,
            ("source_lane", "accepted_trade_count",
             "mean_entry_cost_per_trade_group_usdc", "median_entry_cost_per_trade_group_usdc",
             "median_realised_return_pct", "realised_return_pct",
             "mark_to_market_return_pct", "win_share"),
        ),
        "",
        "## Synthetic diagnostic lanes",
        "",
        "These rows are closed-form simulations.  They are not funded replay and should not be compared directly to realised controls.",
        "",
        _md_table(
            synthetic_rows,
            ("source_lane", "accepted_trade_count",
             "median_realised_return_pct", "realised_return_pct",
             "mark_to_market_return_pct", "win_share"),
        ),
        "",
        "## Controls",
        "",
        _md_table(
            control_rows,
            ("source_lane", "accepted_trade_count",
             "mean_entry_cost_per_trade_group_usdc", "median_entry_cost_per_trade_group_usdc",
             "median_realised_return_pct", "realised_return_pct",
             "mark_to_market_return_pct", "win_share"),
        ),
        "",
        "## Resolved-only funded/control lanes",
        "",
        _md_table(
            resolved_only_rows,
            ("source_lane", "accepted_trade_count",
             "median_realised_return_pct", "realised_return_pct", "win_share",
             "unique_relationships", "unique_spaces"),
        ),
        "",
        "## Top relationship families across all lanes",
        "",
        _md_table(
            family_rows[:15],
            ("source_lane", "relationship_family", "accepted_trade_count",
             "trade_cost_adjusted_return_pct", "mark_to_market_return_pct",
             "median_trade_return_pct", "win_share"),
        ),
        "",
        "## Top context spaces",
        "",
        _md_table(
            context_rows[:15],
            ("source_lane", "context_space_id", "accepted_trade_count",
             "trade_cost_adjusted_return_pct", "mark_to_market_return_pct",
             "win_share"),
        ),
        "",
        "## Lane separation reminder",
        "",
        "- **Exploratory** lanes (rulebook_aggressive_deterministic, ai_*, "
        "closed_form_simulator) are RESEARCH-ONLY and may have positive results "
        "by chance.  They are NEVER headline-credible.",
        "- **Strict validation** lane is the only one whose positive result is "
        "potentially credible — and even then, only after human review.",
        "- **Controls** (null baseline / random pairs) measure noise-floor PnL.",
        "- **Diagnostic** lanes intentionally bypass coverage / lane / "
        "confidence gates.  They are for data-pipeline auditing only.",
        "",
        "No automatic promotion / demotion / kill decisions are made.  Review "
        "fields stay `unreviewed` until a human inspects the log.",
        "",
    ]
    return "\n".join(lines)


# ── small helpers ────────────────────────────────────────────────────────────


def _md_table(rows: list[dict[str, Any]], cols: tuple[str, ...]) -> str:
    if not rows:
        return "_no rows_"
    header = "| " + " | ".join(cols) + " |"
    divider = "| " + " | ".join("---" for _ in cols) + " |"
    body_lines = []
    for r in rows:
        body_lines.append(
            "| " + " | ".join(_md_cell(r.get(c)) for c in cols) + " |"
        )
    return "\n".join([header, divider, *body_lines])


def _md_cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({k for r in rows for k in r})
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields})


def _write_parquet(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        # Empty parquet — emit a sentinel file so downstream tools don't crash on missing path.
        empty = pa.table({"schema_version": pa.array([], type=pa.int64())})
        pq.write_table(empty, path)
        return
    # Stringify any nested / decimal-like values so pyarrow can infer types cleanly.
    cleaned: list[dict[str, Any]] = []
    for r in rows:
        cleaned.append({k: _parquet_safe(v) for k, v in r.items()})
    fields = sorted({k for r in cleaned for k in r})
    table = pa.table({f: [r.get(f) for r in cleaned] for f in fields})
    pq.write_table(table, path)


def _parquet_safe(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (int, float, str, bool)):
        return v
    return str(v)


def _key_of(t: StandardisedTradeRow, key: str) -> str:
    val = getattr(t, key, "")
    return str(val) if val is not None else ""


def _safe_float(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
