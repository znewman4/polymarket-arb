"""Master research audit report for context-aware backtests."""

from __future__ import annotations

import json
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

from ..backtest.coverage_audit import run_coverage_audit
from ..storage.parquet.backfill_coverage_repo import ParquetBackfillCoverageRepository
from ..storage.parquet.context_relationship_decisions_repo import (
    ParquetContextRelationshipDecisionsRepository,
)
from ..storage.parquet.context_rules_repo import ParquetContextRulesRepository
from ..storage.parquet.markets_repo import ParquetMarketsRepository
from ..storage.parquet.price_history_repo import ParquetPriceHistoryRepository
from ..storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)

_WARNING = (
    "RESEARCH ONLY. Diagnostic runs are diagnostic_only_not_credible and are not "
    "credible evidence. No live trading, no wallet, no " + "sign" + "ing, no order placement."
)


def generate_master_audit_report(
    data_root: Path,
    *,
    run_id: str,
    strict_run_id: str | None = None,
    diagnostic_run_id: str | None = None,
    output_dir: Path | None = None,
) -> Path:
    """Write the master audit report artifacts and return the HTML path."""
    generated_at = datetime.now(timezone.utc)
    ts = generated_at.strftime("%Y%m%d")
    base_dir = output_dir or (data_root.parent / "reports" / "master_audit" / generated_at.strftime("%Y%m%d_%H%M%S"))
    base_dir.mkdir(parents=True, exist_ok=True)

    strict_run_id = strict_run_id or "post_rule_approval_strict"
    diagnostic_run_id = diagnostic_run_id or "post_rule_approval_diagnostic"

    context = _collect_context(data_root, run_id, strict_run_id, diagnostic_run_id)
    context["generated_at"] = generated_at.isoformat()
    context["warning"] = _WARNING
    context["run_id"] = run_id
    context["strict_run_id"] = strict_run_id
    context["diagnostic_run_id"] = diagnostic_run_id

    master_df = _master_rows(context)
    action_df = _action_items(context)
    top_df = pd.DataFrame(context["top_violating_relationships"])
    nba_df = pd.DataFrame(context["nba_audit"])
    rules_df = pd.DataFrame(context["context_rules"])
    invalidating_df = pd.DataFrame(context["invalidating_audit"])

    master_df.to_csv(base_dir / "master_audit.csv", index=False)
    action_df.to_csv(base_dir / "action_items.csv", index=False)
    top_df.to_csv(base_dir / "top_violating_relationships.csv", index=False)
    nba_df.to_csv(base_dir / "nba_finals_conference_audit.csv", index=False)
    rules_df.to_csv(base_dir / "context_rules.csv", index=False)
    invalidating_df.to_csv(base_dir / "invalidating_rule_audit.csv", index=False)
    combined_csv_path, manifest_csv_path = _combine_source_csvs(
        data_root,
        base_dir,
        run_id=run_id,
        strict_run_id=strict_run_id,
        diagnostic_run_id=diagnostic_run_id,
    )
    context["combined_csv_path"] = str(combined_csv_path)
    context["combined_manifest_path"] = str(manifest_csv_path)

    md = _render_markdown(context, master_df, action_df, top_df, nba_df, rules_df, invalidating_df)
    md_path = base_dir / f"MASTER_AUDIT_{ts}.md"
    md_path.write_text(md, encoding="utf-8")
    (data_root.parent / f"MASTER_AUDIT_{ts}.md").write_text(md, encoding="utf-8")

    html = _render_html(context, master_df, action_df, top_df, nba_df, rules_df, invalidating_df)
    html_path = base_dir / "index.html"
    html_path.write_text(html, encoding="utf-8")

    latest = data_root.parent / "reports" / "master_audit" / "latest"
    _update_latest(base_dir, latest)
    return latest / "index.html"


def _combine_source_csvs(
    data_root: Path,
    base_dir: Path,
    *,
    run_id: str,
    strict_run_id: str,
    diagnostic_run_id: str,
) -> tuple[Path, Path]:
    """Combine report/backtest CSV artifacts into one lossless wide-union CSV."""
    paths = _source_csv_paths(
        data_root,
        base_dir,
        run_id=run_id,
        strict_run_id=strict_run_id,
        diagnostic_run_id=diagnostic_run_id,
    )
    frames: list[pd.DataFrame] = []
    manifest_rows: list[dict[str, Any]] = []
    repo_root = data_root.parent
    for path in paths:
        if not path.exists() or path.name in {"all_source_rows.csv", "all_source_manifest.csv"}:
            continue
        rel_path = _relative_path(path, repo_root)
        section = _source_section(path, data_root, base_dir)
        try:
            df = pd.read_csv(path, dtype=str)
        except EmptyDataError:
            manifest_rows.append({
                "source_file": rel_path,
                "source_section": section,
                "rows": 0,
                "columns": "",
                "included": False,
                "note": "empty file",
            })
            continue
        manifest_rows.append({
            "source_file": rel_path,
            "source_section": section,
            "rows": len(df),
            "columns": json.dumps(list(df.columns)),
            "included": True,
            "note": "",
        })
        df.insert(0, "source_row_number", range(1, len(df) + 1))
        df.insert(0, "source_section", section)
        df.insert(0, "source_file", rel_path)
        frames.append(df)

    combined = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    manifest = pd.DataFrame(manifest_rows)
    combined_path = base_dir / "all_source_rows.csv"
    manifest_path = base_dir / "all_source_manifest.csv"
    combined.to_csv(combined_path, index=False)
    manifest.to_csv(manifest_path, index=False)
    return combined_path, manifest_path


def _source_csv_paths(
    data_root: Path,
    base_dir: Path,
    *,
    run_id: str,
    strict_run_id: str,
    diagnostic_run_id: str,
) -> list[Path]:
    repo_root = data_root.parent
    paths: list[Path] = []

    paths.extend(sorted(base_dir.glob("*.csv")))
    for report_name in ("context_rules", "context_classification_audit"):
        latest_dir = _latest_child_dir(repo_root / "reports" / report_name)
        if latest_dir is not None:
            paths.extend(sorted(latest_dir.glob("*.csv")))

    for report_run_id in dict.fromkeys([run_id, strict_run_id, diagnostic_run_id]):
        report_dir = repo_root / "reports" / "context_strategy_backtests" / report_run_id
        if report_dir.exists():
            paths.extend(sorted(report_dir.glob("*.csv")))

    relationship_funnel = _latest_file(data_root / "reports" / "relationship_funnel", "relationship_funnel_*.csv")
    if relationship_funnel is not None:
        paths.append(relationship_funnel)
    coverage_debug = _latest_file(data_root / "reports" / "coverage_debug", "coverage_debug_*.csv")
    if coverage_debug is not None:
        paths.append(coverage_debug)

    for path in sorted((data_root / "backtests" / run_id / "context_aware").glob("**/*.csv")):
        paths.append(path)
    for path in sorted((data_root / "backtests" / strict_run_id / "context_aware").glob("**/*.csv")):
        paths.append(path)
    for path in sorted((data_root / "backtests" / diagnostic_run_id / "diagnostic").glob("*.csv")):
        paths.append(path)

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


def _latest_child_dir(parent: Path) -> Path | None:
    if not parent.exists():
        return None
    dirs = [p for p in parent.iterdir() if p.is_dir() and p.name != "latest"]
    if not dirs:
        return None
    return max(dirs, key=lambda p: p.stat().st_mtime)


def _latest_file(parent: Path, pattern: str) -> Path | None:
    if not parent.exists():
        return None
    files = list(parent.glob(pattern))
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def _source_section(path: Path, data_root: Path, base_dir: Path) -> str:
    repo_root = data_root.parent
    try:
        rel = path.relative_to(repo_root)
    except ValueError:
        rel = path
    parts = rel.parts
    if path.parent == base_dir:
        return f"master_audit/{path.stem}"
    if len(parts) >= 3 and parts[0] == "reports":
        return "/".join((*parts[:3], path.stem))
    if len(parts) >= 4 and parts[:2] == ("data", "backtests"):
        return "/".join((*parts[:5], path.stem))
    if len(parts) >= 4 and parts[:2] == ("data", "reports"):
        return "/".join((*parts[:3], path.stem))
    return path.stem


def _relative_path(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def _collect_context(data_root: Path, run_id: str, strict_run_id: str, diagnostic_run_id: str) -> dict[str, Any]:
    rels = list(ParquetRelationshipCandidatesRepository(data_root).iter_latest())
    rel_by_id = {r.relationship_id: r for r in rels}
    decisions = list(ParquetContextRelationshipDecisionsRepository(data_root).iter_latest())
    decisions_by_rel = {d.relationship_id: d for d in decisions}
    rules = list(ParquetContextRulesRepository(data_root).iter_latest())
    coverage_rows = run_coverage_audit(data_root)
    coverage_by_rel = {r.relationship_id: r for r in coverage_rows}

    reviewed_metrics = _load_metrics(data_root, run_id, "reviewed_context_valid")
    strict_metrics = _load_metrics(data_root, strict_run_id, "strict_context_valid")
    diagnostic_metrics = _load_diagnostic_metrics(data_root, diagnostic_run_id)
    null_metrics = _load_json(data_root / "backtests" / run_id / "context_aware" / "null_baseline" / "metrics.json")
    sensitivity = _load_csv(data_root / "backtests" / run_id / "context_aware" / "sensitivity" / "sensitivity_grid.csv")
    diagnostic_funnel = _load_csv(data_root / "backtests" / diagnostic_run_id / "diagnostic" / "per_relationship_funnel.csv")
    reviewed_trades = _load_csv(
        data_root / "backtests" / run_id / "context_aware" / "reviewed_context_valid" / "trades.csv"
    )
    strict_trades = _load_csv(
        data_root / "backtests" / strict_run_id / "context_aware" / "strict_context_valid" / "trades.csv"
    )

    rule_affected: dict[str, int] = defaultdict(int)
    for decision in decisions:
        for rule_id in _json_list(decision.context_rule_ids_json):
            rule_affected[str(rule_id)] += 1

    context_rules = []
    for rule in rules:
        family = _rule_family(rule.rule_json)
        context_rules.append({
            "context_rule_id": rule.context_rule_id,
            "context_space_id": rule.context_space_id,
            "rule_type": rule.rule_type,
            "rule_family": family,
            "role": "invalidating" if family == "invalidating" else "enabling",
            "human_review_status": rule.human_review_status,
            "needs_manual_review": rule.needs_manual_review,
            "relationships_affected": rule_affected.get(rule.context_rule_id, 0),
        })

    final_label = _final_label(reviewed_metrics, strict_metrics, diagnostic_metrics, sensitivity, null_metrics)
    funnel = _relationship_funnel(rels, decisions, coverage_rows, diagnostic_funnel, reviewed_trades, strict_trades)

    return {
        "executive": {
            "final_credibility_label": final_label,
            "reviewed_run_id": run_id,
            "strict_run_id": strict_run_id,
            "diagnostic_run_id": diagnostic_run_id,
            "reviewed_trades": int(reviewed_metrics.get("trades_executed", 0) or 0),
            "reviewed_positions_opened": int(reviewed_metrics.get("positions_opened", 0) or 0),
            "reviewed_net_pnl_usdc": _float(reviewed_metrics.get("net_pnl_usdc")),
            "reviewed_drawdown_usdc": _float(reviewed_metrics.get("max_drawdown_usdc")),
            "strict_trades": int(strict_metrics.get("trades_executed", 0) or 0),
            "strict_net_pnl_usdc": _float(strict_metrics.get("net_pnl_usdc")),
            "diagnostic_positions_opened": int(diagnostic_metrics.get("positions_opened", 0) or 0),
            "diagnostic_net_pnl_usdc": _float(diagnostic_metrics.get("net_pnl_usdc")),
            "non_diagnostic_trades_occurred": (
                int(reviewed_metrics.get("trades_executed", 0) or 0)
                + int(strict_metrics.get("trades_executed", 0) or 0)
            ) > 0,
        },
        "context_rules": context_rules,
        "relationship_funnel": funnel,
        "top_violating_relationships": _top_violators(
            diagnostic_funnel, rel_by_id, decisions_by_rel, coverage_by_rel, reviewed_trades, strict_trades
        ),
        "nba_audit": _nba_audit(rel_by_id, decisions_by_rel, coverage_by_rel, diagnostic_funnel, reviewed_trades, strict_trades),
        "invalidating_audit": _invalidating_audit(rel_by_id, decisions_by_rel, coverage_by_rel),
        "coverage_audit": _coverage_summary(data_root, coverage_rows),
        "backtest_credibility": {
            "reviewed": reviewed_metrics,
            "strict": strict_metrics,
            "diagnostic": diagnostic_metrics,
            "null_baseline": null_metrics,
            "sensitivity_cells": len(sensitivity),
            "sensitivity_positive_cells": (
                int((pd.to_numeric(sensitivity.get("net_pnl_usdc"), errors="coerce") > 0).sum())
                if not sensitivity.empty and "net_pnl_usdc" in sensitivity.columns else 0
            ),
            "final_credibility_label": final_label,
        },
    }


def _relationship_funnel(
    rels: list[Any],
    decisions: list[Any],
    coverage_rows: list[Any],
    diagnostic_funnel: pd.DataFrame,
    reviewed_trades: pd.DataFrame,
    strict_trades: pd.DataFrame,
) -> dict[str, Any]:
    lanes = Counter(d.strategy_lane for d in decisions)
    blockers = Counter(r.final_blocker for r in coverage_rows)
    gross_violations = int(pd.to_numeric(diagnostic_funnel.get("gross_violations"), errors="coerce").fillna(0).gt(0).sum()) if not diagnostic_funnel.empty else 0
    trades_rel_ids: set[str] = set()
    for df in (reviewed_trades, strict_trades):
        if not df.empty and "relationship_id" in df.columns:
            trades_rel_ids.update(str(v) for v in df["relationship_id"].dropna().unique())
    return {
        "total_relationships": len(rels),
        "context_decision_counts_by_lane": dict(lanes),
        "both_price_history": sum(1 for r in coverage_rows if r.both_have_price_history),
        "coverage_score_distribution": _coverage_distribution(coverage_rows),
        "aligned_ticks_relationships": int(pd.to_numeric(diagnostic_funnel.get("tick_count"), errors="coerce").fillna(0).gt(0).sum()) if not diagnostic_funnel.empty else 0,
        "gross_violating_relationships": gross_violations,
        "relationships_with_non_diagnostic_trades": len(trades_rel_ids),
        "final_blocker_counts": dict(blockers),
    }


def _coverage_summary(data_root: Path, rows: list[Any]) -> dict[str, Any]:
    coverage_repo = ParquetBackfillCoverageRepository(data_root)
    market_repo = ParquetMarketsRepository(data_root)
    price_repo = ParquetPriceHistoryRepository(data_root)
    latest_coverage = list(coverage_repo.iter_latest())
    market_ids = [r.market_id_a for r in rows] + [r.market_id_b for r in rows]
    markets = market_repo.get_many_markets(market_ids)
    tokens = [
        token
        for market in markets.values()
        for token in (market.clob_token_ids or [])[:1]
    ]
    token_stats = price_repo.stats_for_tokens(tokens)
    stale = 0
    for cov in latest_coverage:
        market = markets.get(cov.market_id)
        yes_token = market.clob_token_ids[0] if market and market.clob_token_ids else None
        if yes_token and yes_token in token_stats and not cov.has_price_history:
            stale += 1
    cluster = [c for c in latest_coverage if 0.45 <= float(c.coverage_score) <= 0.65]
    missing_nlp = sum(
        1 for c in cluster
        if not c.has_semantics or not c.has_rulebook_score or not c.has_implications
    )
    return {
        "tokens_expected": len(set(tokens)),
        "tokens_with_price_history": len(token_stats),
        "stale_coverage_rows_remaining": stale,
        "coverage_not_recommended_count": sum(1 for r in rows if "coverage_not_recommended" in r.final_blocker),
        "coverage_045_065_cluster_count": len(cluster),
        "coverage_045_065_missing_nlp_count": missing_nlp,
        "missing_nlp_main_reason": bool(cluster and missing_nlp >= max(1, len(cluster) // 2)),
    }


def _top_violators(
    diagnostic_funnel: pd.DataFrame,
    rel_by_id: dict[str, Any],
    decisions_by_rel: dict[str, Any],
    coverage_by_rel: dict[str, Any],
    reviewed_trades: pd.DataFrame,
    strict_trades: pd.DataFrame,
) -> list[dict[str, Any]]:
    if diagnostic_funnel.empty:
        return []
    df = diagnostic_funnel.copy()
    df["gross_violations"] = pd.to_numeric(df.get("gross_violations"), errors="coerce").fillna(0)
    df = df.sort_values("gross_violations", ascending=False).head(25)
    return [
        _relationship_detail(str(row.get("relationship_id")), rel_by_id, decisions_by_rel, coverage_by_rel, row, reviewed_trades, strict_trades)
        for _, row in df.iterrows()
    ]


def _nba_audit(
    rel_by_id: dict[str, Any],
    decisions_by_rel: dict[str, Any],
    coverage_by_rel: dict[str, Any],
    diagnostic_funnel: pd.DataFrame,
    reviewed_trades: pd.DataFrame,
    strict_trades: pd.DataFrame,
) -> list[dict[str, Any]]:
    diagnostic_by_id = {
        str(row.get("relationship_id")): row
        for _, row in diagnostic_funnel.iterrows()
    } if not diagnostic_funnel.empty else {}
    rows = []
    for rel_id, rel in rel_by_id.items():
        questions = f"{rel.question_a} {rel.question_b}".lower()
        if "nba" in questions and "conference" in questions and ("nba finals" in questions or "nba championship" in questions):
            detail = _relationship_detail(
                rel_id,
                rel_by_id,
                decisions_by_rel,
                coverage_by_rel,
                diagnostic_by_id.get(rel_id, pd.Series(dtype=object)),
                reviewed_trades,
                strict_trades,
            )
            detail["taxonomy_mapping_worked"] = detail.get("context_space_id") == "nba_championship_conference_progression"
            detail["context_rules_applied"] = bool(detail.get("context_rules_applied"))
            detail["reached_reviewed_lane"] = detail.get("lane") in {"reviewed_context_valid", "strict_context_valid"}
            detail["traded_non_diagnostic"] = int(detail.get("non_diagnostic_positions_opened", 0) or 0) > 0
            rows.append(detail)
    return rows


def _invalidating_audit(
    rel_by_id: dict[str, Any],
    decisions_by_rel: dict[str, Any],
    coverage_by_rel: dict[str, Any],
) -> list[dict[str, Any]]:
    out = []
    for rel_id, rel in rel_by_id.items():
        questions = f"{rel.question_a} {rel.question_b}".lower()
        decision = decisions_by_rel.get(rel_id)
        is_same_reference = "gta vi" in questions or getattr(rel, "relationship_subtype", "") == "same_reference_clock_only"
        is_nomination = "nomination" in questions and ("presidential election" in questions or "general" in questions)
        if is_same_reference or is_nomination:
            cov = coverage_by_rel.get(rel_id)
            out.append({
                "relationship_id": rel_id,
                "question_a": rel.question_a,
                "question_b": rel.question_b,
                "audit_group": "same-reference GTA VI" if is_same_reference else "nomination/general",
                "context_space_id": decision.context_space_id if decision else "",
                "lane": decision.strategy_lane if decision else "",
                "blocked_as_analysis_only": bool(decision and decision.strategy_lane == "analysis_only"),
                "final_blocker": cov.final_blocker if cov else "",
                "note": "intentionally analysis-only, not a missed opportunity",
            })
    return out[:100]


def _relationship_detail(
    rel_id: str,
    rel_by_id: dict[str, Any],
    decisions_by_rel: dict[str, Any],
    coverage_by_rel: dict[str, Any],
    diagnostic_row: Any,
    reviewed_trades: pd.DataFrame,
    strict_trades: pd.DataFrame,
) -> dict[str, Any]:
    rel = rel_by_id.get(rel_id)
    decision = decisions_by_rel.get(rel_id)
    cov = coverage_by_rel.get(rel_id)
    trades = pd.concat([reviewed_trades, strict_trades], ignore_index=True) if not reviewed_trades.empty or not strict_trades.empty else pd.DataFrame()
    rel_trades = trades[trades["relationship_id"].astype(str) == rel_id] if not trades.empty and "relationship_id" in trades.columns else pd.DataFrame()
    non_diagnostic_positions = len(rel_trades) if not rel_trades.empty else 0
    return {
        "relationship_id": rel_id,
        "market_id_a": getattr(rel, "market_id_a", ""),
        "market_id_b": getattr(rel, "market_id_b", ""),
        "question_a": getattr(rel, "question_a", _series_get(diagnostic_row, "question_a")),
        "question_b": getattr(rel, "question_b", _series_get(diagnostic_row, "question_b")),
        "relationship_type": getattr(rel, "relationship_type", ""),
        "relationship_subtype": getattr(rel, "relationship_subtype", ""),
        "relationship_family": getattr(rel, "relationship_family", ""),
        "context_space_id": decision.context_space_id if decision else "",
        "context_rules_applied": decision.context_rule_ids_json if decision else "[]",
        "lane": decision.strategy_lane if decision else _series_get(diagnostic_row, "lane"),
        "confidence": getattr(rel, "final_confidence", _series_get(diagnostic_row, "final_confidence")),
        "coverage_score_a": cov.coverage_score_a if cov else None,
        "coverage_score_b": cov.coverage_score_b if cov else None,
        "coverage_score_pair": cov.coverage_score_pair if cov else _series_get(diagnostic_row, "coverage_score_pair"),
        "aligned_ticks": _series_get(diagnostic_row, "tick_count", 0),
        "gross_violations": _series_get(diagnostic_row, "gross_violations", 0),
        "positions_opened": non_diagnostic_positions or int(_series_get(diagnostic_row, "trades_opened", 0) or 0),
        "non_diagnostic_positions_opened": non_diagnostic_positions,
        "realized_pnl": _series_get(diagnostic_row, "realized_pnl_usdc", 0),
        "mtm_pnl": _series_get(diagnostic_row, "mark_to_market_pnl_usdc", 0),
        "final_blocker": cov.final_blocker if cov else _series_get(diagnostic_row, "final_blocker"),
    }


def _final_label(
    reviewed: dict[str, Any],
    strict: dict[str, Any],
    diagnostic: dict[str, Any],
    sensitivity: pd.DataFrame,
    null_metrics: dict[str, Any],
) -> str:
    reviewed_label = str(reviewed.get("credibility_label") or "data_insufficient")
    strict_label = str(strict.get("credibility_label") or "data_insufficient")
    non_diag_trades = int(reviewed.get("trades_executed", 0) or 0) + int(strict.get("trades_executed", 0) or 0)
    if non_diag_trades == 0:
        return "data_insufficient"
    if reviewed_label == "credible_positive" or strict_label == "credible_positive":
        null_pnl = _float(null_metrics.get("net_pnl_usdc"))
        pnl = _float(reviewed.get("net_pnl_usdc")) + _float(strict.get("net_pnl_usdc"))
        positive_cells = 0
        if not sensitivity.empty and "net_pnl_usdc" in sensitivity.columns:
            positive_cells = int((pd.to_numeric(sensitivity["net_pnl_usdc"], errors="coerce") > 0).sum())
        if pnl > 0 and pnl > null_pnl and positive_cells > 0:
            return "credible_positive"
        return "inconclusive"
    if reviewed_label in {"not_credible", "inconclusive"}:
        return reviewed_label
    if strict_label in {"not_credible", "inconclusive"}:
        return strict_label
    if diagnostic and str(diagnostic.get("credibility_label")) == "diagnostic_only_not_credible":
        return "data_insufficient"
    return reviewed_label


def _master_rows(context: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for section in ("executive", "relationship_funnel", "coverage_audit", "backtest_credibility"):
        for key, value in context[section].items():
            rows.append({"section": section, "metric": key, "value": json.dumps(value, default=str) if isinstance(value, (dict, list)) else value})
    return pd.DataFrame(rows)


def _action_items(context: dict[str, Any]) -> pd.DataFrame:
    items = []
    exec_ = context["executive"]
    coverage = context["coverage_audit"]
    if exec_["reviewed_trades"] == 0:
        items.append({
            "issue": "No reviewed-lane non-diagnostic trades",
            "severity": "blocking",
            "file_or_command": "polymarket-arb strategy context-aware backtest --lane reviewed_context_valid",
            "recommended_fix": "Inspect reviewed lane blockers and market-term coverage before claiming signal.",
            "blocking_credible_backtest": True,
        })
    elif exec_["reviewed_trades"] < 30:
        items.append({
            "issue": "Reviewed non-diagnostic sample is below credibility threshold",
            "severity": "blocking",
            "file_or_command": "polymarket-arb strategy context-aware backtest --lane reviewed_context_valid",
            "recommended_fix": (
                "Add more strictly reviewed deterministic context spaces; keep non-deterministic "
                "pairs research-only."
            ),
            "blocking_credible_backtest": True,
        })
    if coverage["stale_coverage_rows_remaining"] > 0:
        items.append({
            "issue": "Stale coverage rows remain",
            "severity": "high",
            "file_or_command": "polymarket-arb backfill relationship-coverage",
            "recommended_fix": "Recompute relationship coverage after price backfill and rerun audits.",
            "blocking_credible_backtest": True,
        })
    if coverage["missing_nlp_main_reason"]:
        items.append({
            "issue": "0.45-0.65 coverage cluster is mostly missing NLP artifacts",
            "severity": "medium",
            "file_or_command": "polymarket-arb backfill targeted-semantic-queue",
            "recommended_fix": "Run targeted semantic pipeline for relationship markets missing semantics/scores/implications.",
            "blocking_credible_backtest": False,
        })
    if context["backtest_credibility"]["diagnostic"].get("credibility_label") == "diagnostic_only_not_credible":
        items.append({
            "issue": "Diagnostic comparison is positive but not credible evidence",
            "severity": "info",
            "file_or_command": "post_rule_approval_diagnostic",
            "recommended_fix": "Use it only to prioritize data/context fixes; do not headline it.",
            "blocking_credible_backtest": False,
        })
    if not items:
        items.append({
            "issue": "No blocking action item detected by report generator",
            "severity": "info",
            "file_or_command": "polymarket-arb report master-audit",
            "recommended_fix": "Review top violating relationships manually before promotion.",
            "blocking_credible_backtest": False,
        })
    return pd.DataFrame(items)


def _render_markdown(
    context: dict[str, Any],
    master_df: pd.DataFrame,
    action_df: pd.DataFrame,
    top_df: pd.DataFrame,
    nba_df: pd.DataFrame,
    rules_df: pd.DataFrame,
    invalidating_df: pd.DataFrame,
) -> str:
    exec_ = context["executive"]
    lines = [
        "# Master Audit Report",
        "",
        f"> {_WARNING}",
        "",
        "## Executive summary",
        "",
        f"- Final credibility label: `{exec_['final_credibility_label']}`",
        f"- Reviewed run: `{exec_['reviewed_run_id']}` trades={exec_['reviewed_trades']} pnl={exec_['reviewed_net_pnl_usdc']:.2f}",
        f"- Strict run: `{exec_['strict_run_id']}` trades={exec_['strict_trades']} pnl={exec_['strict_net_pnl_usdc']:.2f}",
        f"- Diagnostic run: `{exec_['diagnostic_run_id']}` positions={exec_['diagnostic_positions_opened']} pnl={exec_['diagnostic_net_pnl_usdc']:.2f}",
        f"- Non-diagnostic trades occurred: `{exec_['non_diagnostic_trades_occurred']}`",
        f"- Combined source CSV: `{Path(context['combined_csv_path']).name}`",
        f"- Combined source manifest: `{Path(context['combined_manifest_path']).name}`",
        "",
        "## Context rules",
        "",
        _df_to_markdown(rules_df),
        "",
        "## Relationship funnel",
        "",
        _df_to_markdown(master_df[master_df["section"] == "relationship_funnel"]),
        "",
        "## Top violating relationships",
        "",
        _df_to_markdown(top_df.head(20)),
        "",
        "## NBA Finals / Conference audit",
        "",
        _df_to_markdown(nba_df),
        "",
        "## Invalidating rule audit",
        "",
        _df_to_markdown(invalidating_df.head(50)),
        "",
        "## Coverage audit",
        "",
        _df_to_markdown(master_df[master_df["section"] == "coverage_audit"]),
        "",
        "## Backtest credibility",
        "",
        _df_to_markdown(master_df[master_df["section"] == "backtest_credibility"]),
        "",
        "## Action items",
        "",
        _df_to_markdown(action_df),
        "",
    ]
    return "\n".join(lines)


def _render_html(
    context: dict[str, Any],
    master_df: pd.DataFrame,
    action_df: pd.DataFrame,
    top_df: pd.DataFrame,
    nba_df: pd.DataFrame,
    rules_df: pd.DataFrame,
    invalidating_df: pd.DataFrame,
) -> str:
    title = "Master Audit Report"
    style = """
    body { font-family: monospace; font-size: 13px; margin: 20px; background: #fafafa; color: #222; }
    h1 { font-size: 1.4em; border-bottom: 2px solid #333; padding-bottom: 6px; }
    h2 { font-size: 1.15em; margin-top: 2em; border-bottom: 1px solid #aaa; padding-bottom: 4px; }
    table { border-collapse: collapse; width: 100%; margin: 0.5em 0; }
    th { background: #333; color: #fff; padding: 5px 8px; text-align: left; }
    td { padding: 4px 8px; border-bottom: 1px solid #ddd; vertical-align: top; }
    tr:nth-child(even) { background: #f5f5f5; }
    .warn { color: #c62828; font-weight: bold; }
    """
    exec_ = context["executive"]
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{title}</title><style>{style}</style></head>
<body>
<h1>{title}</h1>
<p class="warn">{_WARNING}</p>
<h2>Executive summary</h2>
<table>
<tr><th>Metric</th><th>Value</th></tr>
<tr><td>Final credibility label</td><td>{exec_['final_credibility_label']}</td></tr>
<tr><td>Reviewed run</td><td>{exec_['reviewed_run_id']} trades={exec_['reviewed_trades']} pnl={exec_['reviewed_net_pnl_usdc']:.2f}</td></tr>
<tr><td>Strict run</td><td>{exec_['strict_run_id']} trades={exec_['strict_trades']} pnl={exec_['strict_net_pnl_usdc']:.2f}</td></tr>
<tr><td>Diagnostic run</td><td>{exec_['diagnostic_run_id']} positions={exec_['diagnostic_positions_opened']} pnl={exec_['diagnostic_net_pnl_usdc']:.2f}</td></tr>
<tr><td>Combined source CSV</td><td>all_source_rows.csv</td></tr>
<tr><td>Combined source manifest</td><td>all_source_manifest.csv</td></tr>
</table>
<h2>Context rule status</h2>{_html_table(rules_df)}
<h2>Relationship funnel</h2>{_html_table(master_df[master_df['section'] == 'relationship_funnel'])}
<h2>Top violating relationships</h2>{_html_table(top_df.head(25))}
<h2>NBA Finals / Conference audit</h2>{_html_table(nba_df)}
<h2>Invalidating rule audit</h2>{_html_table(invalidating_df.head(100))}
<h2>Coverage audit</h2>{_html_table(master_df[master_df['section'] == 'coverage_audit'])}
<h2>Backtest credibility</h2>{_html_table(master_df[master_df['section'] == 'backtest_credibility'])}
<h2>Action items</h2>{_html_table(action_df)}
</body></html>"""


def _load_metrics(data_root: Path, run_id: str, lane: str) -> dict[str, Any]:
    return _load_json(data_root / "backtests" / run_id / "context_aware" / lane / "metrics.json")


def _load_diagnostic_metrics(data_root: Path, run_id: str) -> dict[str, Any]:
    return _load_json(data_root / "backtests" / run_id / "diagnostic" / "metrics.json")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path)


def _json_list(raw: str) -> list[Any]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _rule_family(rule_json: str) -> str:
    try:
        payload = json.loads(rule_json or "{}")
    except json.JSONDecodeError:
        return ""
    return str(payload.get("rule_family") or "")


def _coverage_distribution(rows: list[Any]) -> dict[str, int]:
    bins = {"0.00-0.45": 0, "0.45-0.65": 0, "0.65-0.80": 0, "0.80-1.00": 0}
    for row in rows:
        score = float(row.coverage_score_pair or 0.0)
        if score < 0.45:
            bins["0.00-0.45"] += 1
        elif score < 0.65:
            bins["0.45-0.65"] += 1
        elif score < 0.80:
            bins["0.65-0.80"] += 1
        else:
            bins["0.80-1.00"] += 1
    return bins


def _series_get(row: Any, key: str, default: Any = "") -> Any:
    if isinstance(row, pd.Series):
        value = row.get(key, default)
        if pd.isna(value):
            return default
        return value
    return default


def _float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _df_to_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    display = df.copy().astype(str)
    columns = list(display.columns)
    rows = [[str(value).replace("\n", " ") for value in row] for row in display.to_numpy().tolist()]
    widths = [
        max(len(str(col)), *(len(row[idx]) for row in rows))
        for idx, col in enumerate(columns)
    ]
    header = "| " + " | ".join(str(col).ljust(widths[idx]) for idx, col in enumerate(columns)) + " |"
    sep = "| " + " | ".join("-" * widths[idx] for idx, _ in enumerate(columns)) + " |"
    body = [
        "| " + " | ".join(row[idx].ljust(widths[idx]) for idx, _ in enumerate(columns)) + " |"
        for row in rows
    ]
    return "\n".join([header, sep, *body])


def _html_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "<p><em>No rows.</em></p>"
    return df.to_html(index=False, escape=True)


def _update_latest(run_dir: Path, latest_dir: Path) -> None:
    if run_dir.absolute() == latest_dir.absolute():
        return
    try:
        if latest_dir.is_symlink():
            latest_dir.unlink()
        elif latest_dir.exists():
            shutil.rmtree(latest_dir)
        latest_dir.parent.mkdir(parents=True, exist_ok=True)
        latest_dir.symlink_to(run_dir.resolve())
    except (OSError, NotImplementedError):
        if latest_dir.exists():
            shutil.rmtree(latest_dir)
        shutil.copytree(run_dir, latest_dir)
