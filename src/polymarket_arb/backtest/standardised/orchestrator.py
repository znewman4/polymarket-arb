"""Standardised backtest orchestrator.

Runs the existing replay engines for each lane, post-processes their outputs
through the adapter layer, attaches DeepSeek pre-trade justifications, and
emits the standardised report pack.

NO automatic promotion / demotion / kill decisions are made here.  The
orchestrator collects evidence; humans review.
"""

from __future__ import annotations

import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from ...settings import Settings
from .adapters import (
    adapt_closed_form_simulator_trades,
    adapt_replay_trade_rows,
    build_resolution_lookup,
    latest_price_lookup_for,
    load_deepseek_justifications_iterable,
    load_deepseek_justifications_jsonl,
    read_trades_csv,
)
from .contract import (
    DeepSeekPreTradeJustification,
    StandardisedRunManifest,
    StandardisedTradeRow,
)
from .report_pack import write_report_pack


def _log(msg: str) -> None:
    """Verbose progress to stderr — visible in `tail -f` of the run log."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[standardised-backtest {ts}] {msg}", file=sys.stderr, flush=True)


@dataclass
class StandardisedBacktestConfig:
    """Configuration for one standardised backtest run."""

    run_id: str
    enable_rulebook_baseline: bool = True
    enable_rulebook_aggressive: bool = True
    enable_ai_freereign_deepseek: bool = True
    enable_ai_embedding_only: bool = True
    enable_closed_form_simulators: bool = True
    enable_control_null_baseline: bool = True
    enable_strict_validation: bool = True
    enable_diagnostic_ultra_loose: bool = False  # off by default — opt-in
    smoke_mode: bool = False  # if True, smaller pair limits + skips heavy lanes
    # Standardised stake: applied to every replay lane via preset override so
    # lane-to-lane comparison is at the same notional.  None = keep preset default.
    stake_per_leg_usdc: float | None = 50.0
    # Random-pair control lane stake (per leg, same notional as main lanes).
    control_pairs: int = 100
    # Pretend cash is unlimited so trade counts aren't capped by starting balance.
    # Replay engines treat this as `starting_cash_usdc=1e12 + allow_negative_cash=True`.
    infinite_cash: bool = True
    # Optional: reuse DeepSeek raw responses from a prior run instead of calling
    # Ollama again.  Path to a deepseek_raw_responses.jsonl file.  When set,
    # skips the ~20-minute hypothesis-generation step.
    reuse_deepseek_responses_from: str | None = None
    extra_config: dict[str, Any] = field(default_factory=dict)


@dataclass
class LaneRunResult:
    subrun_id: str
    output_dir: Path
    preset_name: str = ""
    trades_csv: Path | None = None
    extras: dict[str, Any] = field(default_factory=dict)


def run_standardised_backtest(
    settings: Settings,
    cfg: StandardisedBacktestConfig,
) -> Path:
    """Run every enabled lane and emit the standardised report pack.

    Returns the output directory containing the standardised pack.
    """
    data_root = settings.data_root
    out_dir = data_root / "backtests" / cfg.run_id / "standardised"
    out_dir.mkdir(parents=True, exist_ok=True)

    overall_start = time.time()
    _log(f"=== STANDARDISED BACKTEST START — run_id={cfg.run_id} smoke={cfg.smoke_mode} ===")
    _log(f"output_dir={out_dir}")

    manifest = StandardisedRunManifest(
        run_id=cfg.run_id,
        created_ts_ms=_now_ms(),
        data_root=str(data_root),
        config_overrides=cfg.extra_config,
    )

    trades: list[StandardisedTradeRow] = []
    deepseek_hypotheses: list[dict[str, Any]] = []

    # ── Rulebook: baseline deterministic ────────────────────────────────────
    if cfg.enable_rulebook_baseline:
        lane_t = time.time()
        _log("[1/8] rulebook_baseline_deterministic (strict_research) — starting")
        result = _run_research_preset(
            data_root, cfg, "strict_research", "rulebook_baseline_strict"
        )
        if result is not None:
            manifest.rulebook_lane_subruns.append({
                "subrun_id": result.subrun_id,
                "preset": "strict_research",
                "output_dir": str(result.output_dir),
            })
            new = _adapt_replay_result(
                data_root, result, cfg.run_id,
                source_lane="rulebook_baseline_deterministic",
                source_agent="deterministic_template_strict",
                is_rulebook=True, is_exploratory=False,
                reason_default="rulebook strict-research deterministic relationship triggered an edge violation",
            )
            trades.extend(new)
            _log(f"[1/8] rulebook_baseline_deterministic done — {len(new)} legs in {time.time()-lane_t:.1f}s")
        else:
            _log("[1/8] rulebook_baseline_deterministic skipped (preset load failed)")

    # ── Rulebook: aggressive deterministic ──────────────────────────────────
    if cfg.enable_rulebook_aggressive:
        lane_t = time.time()
        _log("[2/8] rulebook_aggressive_deterministic (aggressive_deterministic_surface) — starting")
        result = _run_research_preset(
            data_root, cfg, "aggressive_deterministic_surface", "rulebook_aggressive"
        )
        if result is not None:
            manifest.rulebook_lane_subruns.append({
                "subrun_id": result.subrun_id,
                "preset": "aggressive_deterministic_surface",
                "output_dir": str(result.output_dir),
            })
            new = _adapt_replay_result(
                data_root, result, cfg.run_id,
                source_lane="rulebook_aggressive_deterministic",
                source_agent="deterministic_template_aggressive",
                is_rulebook=True, is_exploratory=True,
                reason_default="rulebook aggressive deterministic surface — exploratory",
            )
            trades.extend(new)
            _log(f"[2/8] rulebook_aggressive_deterministic done — {len(new)} legs in {time.time()-lane_t:.1f}s")
        else:
            _log("[2/8] rulebook_aggressive_deterministic skipped (preset load failed)")

    # ── AI free-reign: DeepSeek (VALIDATED — only model-approved hypotheses) ─
    justification_map: dict[str, DeepSeekPreTradeJustification] = {}
    deepseek_replay_legs = 0
    deepseek_wild_legs = 0
    deepseek_synthetic_control_legs = 0
    if cfg.enable_ai_freereign_deepseek:
        lane_t = time.time()
        _log("[3/8] ai_freereign_deepseek_validated — starting (Ollama generative hypotheses + buy-only replay of model-approved only)")
        ds_result, ds_hypotheses = _run_deepseek_lane(data_root, cfg)
        deepseek_hypotheses = ds_hypotheses
        justification_map = load_deepseek_justifications_iterable(ds_hypotheses)
        if ds_result is not None:
            manifest.ai_freereign_lane_subruns.append({
                "subrun_id": ds_result.subrun_id,
                "preset": "deepseek_hypothesis_surface",
                "output_dir": str(ds_result.output_dir),
                "hypotheses_generated": len(ds_hypotheses),
                "hypotheses_with_justification": len(justification_map),
                "lane_kind": "deepseek_validated",
            })
            new = _adapt_replay_result(
                data_root, ds_result, cfg.run_id,
                source_lane="ai_freereign_deepseek_validated",
                source_agent="deepseek_generative_validated",
                is_ai_generated=True, is_exploratory=True,
                justification_by_hypothesis=justification_map,
                reason_default="DeepSeek-validated hypothesis (should_promote_to_replay=True)",
            )
            trades.extend(new)
            deepseek_replay_legs = len(new)
            _log(
                f"[3/8] ai_freereign_deepseek_validated done — "
                f"{len(ds_hypotheses)} hypotheses generated, "
                f"{len(justification_map)} with justification, "
                f"{len(new)} validated-replay legs in {time.time()-lane_t:.1f}s"
            )
        else:
            _log(f"[3/8] ai_freereign_deepseek_validated did not produce a replay subrun (took {time.time()-lane_t:.1f}s)")

    # ── AI free-reign: DeepSeek WILD DIAGNOSTIC ──────────────────────────────
    # Sidestep DeepSeek's veto: simulate EVERY reviewed pair through a
    # closed-form simulator, even invalid_same_topic_only / uncertain / rejected.
    # This gives the noise reading of "what if we trusted the embedding retriever
    # but ignored the LLM gate".  Diagnostic-only — never headline-credible.
    if cfg.enable_ai_freereign_deepseek and deepseek_hypotheses:
        lane_t = time.time()
        _log(f"[3b/8] ai_freereign_deepseek_wild_diagnostic — simulating all {len(deepseek_hypotheses)} hypotheses (incl. rejected)")
        wild_trades = _run_deepseek_wild_diagnostic(
            data_root, cfg, deepseek_hypotheses,
        )
        wild_std = adapt_closed_form_simulator_trades(
            wild_trades,
            run_id=cfg.run_id,
            subrun_id=f"{cfg.run_id}__ai_freereign_deepseek_wild_diagnostic",
            source_lane="ai_freereign_deepseek_wild_diagnostic",
            source_agent="deepseek_wild_mutex_overround_simulator",
            source_preset="deepseek_wild_diagnostic",
            is_diagnostic=True,
            justification_by_hypothesis=justification_map,
        )
        trades.extend(wild_std)
        deepseek_wild_legs = len(wild_std)
        manifest.ai_freereign_lane_subruns.append({
            "subrun_id": f"{cfg.run_id}__ai_freereign_deepseek_wild_diagnostic",
            "lane_kind": "deepseek_wild_diagnostic",
            "simulator": "mutex_yes_yes_overround_simulator",
            "hypotheses_total": len(deepseek_hypotheses),
            "legs_emitted": len(wild_std),
        })
        _log(
            f"[3b/8] ai_freereign_deepseek_wild_diagnostic done — "
            f"{len(wild_std)} simulated legs in {time.time()-lane_t:.1f}s"
        )

        ctrl_t = time.time()
        _log(
            f"[3c/8] synthetic_control — simulating {len(deepseek_hypotheses)} "
            "random/shuffled pairs through the same mutex simulator"
        )
        synthetic_control_trades = _run_deepseek_synthetic_control(
            data_root, cfg, deepseek_hypotheses,
        )
        synthetic_control_std = adapt_closed_form_simulator_trades(
            synthetic_control_trades,
            run_id=cfg.run_id,
            subrun_id=f"{cfg.run_id}__synthetic_control",
            source_lane="synthetic_control",
            source_agent="random_pair_mutex_overround_simulator",
            source_preset="deepseek_wild_synthetic_control",
            is_diagnostic=True,
            is_control=True,
            is_ai_generated=False,
            relationship_family="synthetic_control_random_pair",
            hypothesis_source="synthetic_control",
        )
        trades.extend(synthetic_control_std)
        deepseek_synthetic_control_legs = len(synthetic_control_std)
        manifest.control_subruns.append({
            "subrun_id": f"{cfg.run_id}__synthetic_control",
            "kind": "synthetic_closed_form_random_pairs",
            "simulator": "mutex_yes_yes_overround_simulator",
            "hypotheses_total": len(deepseek_hypotheses),
            "legs_emitted": len(synthetic_control_std),
        })
        _log(
            f"[3c/8] synthetic_control done — "
            f"{len(synthetic_control_std)} simulated legs in {time.time()-ctrl_t:.1f}s"
        )

    # ── AI embedding-only ──────────────────────────────────────────────────
    if cfg.enable_ai_embedding_only:
        lane_t = time.time()
        _log("[4/8] ai_embedding_only (embedding_hypothesis_surface) — starting")
        result = _run_research_preset(
            data_root, cfg, "embedding_hypothesis_surface", "ai_embedding_only"
        )
        if result is not None:
            manifest.embedding_only_subruns.append({
                "subrun_id": result.subrun_id,
                "preset": "embedding_hypothesis_surface",
                "output_dir": str(result.output_dir),
            })
            new = _adapt_replay_result(
                data_root, result, cfg.run_id,
                source_lane="ai_embedding_only",
                source_agent="embedding_only",
                is_ai_generated=True, is_exploratory=True,
                reason_default="embedding-only hypothesis promoted to buy-only replay",
            )
            trades.extend(new)
            _log(f"[4/8] ai_embedding_only done — {len(new)} legs in {time.time()-lane_t:.1f}s")
        else:
            _log("[4/8] ai_embedding_only skipped (preset load failed)")

    # ── Closed-form simulators (DeepSeek hypotheses that the buy-only engine can't trade) ──
    if cfg.enable_closed_form_simulators and deepseek_hypotheses:
        lane_t = time.time()
        _log("[5/8] closed_form_simulator — scoring unsupported_but_testable DeepSeek hypotheses")
        from ...research.closed_form_simulators import run_simulators
        from ...storage.parquet.markets_repo import ParquetMarketsRepository

        markets_repo = ParquetMarketsRepository(data_root)
        markets = list(markets_repo.iter_active_markets())
        sim_trades = run_simulators(
            data_root,
            deepseek_hypotheses,
            markets,
            slippage_bps=50,
        )
        if sim_trades:
            sub = f"{cfg.run_id}_closed_form_simulators"
            std_rows = adapt_closed_form_simulator_trades(
                sim_trades,
                run_id=cfg.run_id,
                subrun_id=sub,
                justification_by_hypothesis=justification_map,
            )
            trades.extend(std_rows)
            manifest.closed_form_simulator_subruns.append({
                "subrun_id": sub,
                "simulator_trades": len(sim_trades),
            })
            _log(f"[5/8] closed_form_simulator done — {len(sim_trades)} simulator trades in {time.time()-lane_t:.1f}s")
        else:
            _log(f"[5/8] closed_form_simulator: 0 hypotheses routed to simulators (took {time.time()-lane_t:.1f}s)")
    elif cfg.enable_closed_form_simulators:
        _log("[5/8] closed_form_simulator skipped — no DeepSeek hypotheses available")

    # ── Strict validation lane ─────────────────────────────────────────────
    if cfg.enable_strict_validation:
        lane_t = time.time()
        _log("[6/8] strict_validation (strict_research / accepted_only universe) — starting")
        sv_result = _run_strict_validation(data_root, cfg)
        if sv_result is not None:
            manifest.strict_validation_subruns.append({
                "subrun_id": sv_result.subrun_id,
                "output_dir": str(sv_result.output_dir),
            })
            new = _adapt_replay_result(
                data_root, sv_result, cfg.run_id,
                source_lane="strict_validation",
                source_agent="strict_validation",
                is_strict_validation=True,
                reason_default="strict-validation lane — accepted deterministic relationship",
            )
            trades.extend(new)
            _log(f"[6/8] strict_validation done — {len(new)} legs in {time.time()-lane_t:.1f}s")
        else:
            _log(f"[6/8] strict_validation skipped (took {time.time()-lane_t:.1f}s)")

    # ── Diagnostic / ultra-loose lane ──────────────────────────────────────
    if cfg.enable_diagnostic_ultra_loose:
        lane_t = time.time()
        _log("[7/8] diagnostic_ultra_loose (ultra_loose_diagnostic_surface) — starting")
        result = _run_research_preset(
            data_root, cfg, "ultra_loose_diagnostic_surface", "diagnostic_ultra_loose"
        )
        if result is not None:
            manifest.diagnostic_subruns.append({
                "subrun_id": result.subrun_id,
                "preset": "ultra_loose_diagnostic_surface",
                "output_dir": str(result.output_dir),
            })
            new = _adapt_replay_result(
                data_root, result, cfg.run_id,
                source_lane="diagnostic_ultra_loose",
                source_agent="ultra_loose_diagnostic",
                is_diagnostic=True,
                reason_default="diagnostic ultra-loose run — coverage/lane gates bypassed",
            )
            trades.extend(new)
            _log(f"[7/8] diagnostic_ultra_loose done — {len(new)} legs in {time.time()-lane_t:.1f}s")
        else:
            _log("[7/8] diagnostic_ultra_loose skipped (preset load failed)")

    # ── Control: random-pair noise floor ───────────────────────────────────
    if cfg.enable_control_null_baseline:
        lane_t = time.time()
        _log(f"[8/8] control — starting ({cfg.control_pairs} random eligible pairs)")
        ctl_trades = _run_random_pair_control(data_root, cfg)
        trades.extend(ctl_trades)
        manifest.control_subruns.append({
            "subrun_id": f"{cfg.run_id}__control",
            "kind": "random_pair_noise_floor",
            "pairs_requested": cfg.control_pairs,
            "legs_emitted": len(ctl_trades),
        })
        _log(f"[8/8] control done — {len(ctl_trades)} legs in {time.time()-lane_t:.1f}s")

    # ── Post-pass: resolve every market & fill realised PnL ─────────────────
    _log(f"All lanes done — {len(trades)} total legs collected.  Running resolution pass…")
    res_t = time.time()
    raw_trade_dicts = [{"market_id": t.market_id, "token_id": t.token_id} for t in trades]
    resolution_lookup = build_resolution_lookup(data_root, raw_trade_dicts)
    _apply_resolution_to_trades(trades, resolution_lookup)
    n_resolved = sum(
        1 for t in trades if t.resolution_outcome in ("yes", "no")
    )
    n_unresolved = sum(
        1 for t in trades if t.resolution_outcome in ("unresolved", None)
    )
    pct_unresolved = (100.0 * n_unresolved / max(1, len(trades)))
    manifest.config_overrides.setdefault("resolution_summary", {})
    manifest.config_overrides["resolution_summary"] = {
        "total_legs": len(trades),
        "resolved": n_resolved,
        "unresolved": n_unresolved,
        "unresolved_pct": pct_unresolved,
        "method": "infer_resolutions (price_convergence + closed_flag, ε=0.05)",
    }
    _log(
        f"Resolution pass done in {time.time()-res_t:.1f}s — "
        f"resolved={n_resolved} unresolved={n_unresolved} ({pct_unresolved:.1f}%)"
    )

    # ── DeepSeek funnel: where do hypotheses end up? ─────────────────────
    deepseek_funnel: list[dict[str, Any]] = []
    closed_form_legs = sum(
        1 for t in trades if t.source_lane == "closed_form_simulator"
    )
    if cfg.enable_ai_freereign_deepseek:
        deepseek_funnel = _build_deepseek_funnel(
            hypotheses=deepseek_hypotheses,
            replay_legs=deepseek_replay_legs,
            closed_form_legs=closed_form_legs,
            wild_diagnostic_legs=deepseek_wild_legs,
            synthetic_control_legs=deepseek_synthetic_control_legs,
        )

    # ── write the report pack ───────────────────────────────────────────────
    if cfg.enable_ai_freereign_deepseek:
        manifest.deepseek_workflow_config = {
            "smoke_mode": cfg.smoke_mode,
            "stake_per_leg_usdc": cfg.stake_per_leg_usdc,
            **cfg.extra_config.get("deepseek", {}),
        }
    _log(f"Writing report pack → {out_dir}")
    write_report_pack(out_dir, trades, manifest, deepseek_funnel=deepseek_funnel)
    _log(
        f"=== STANDARDISED BACKTEST COMPLETE — {len(trades)} legs, "
        f"{pct_unresolved:.1f}% unresolved, total {time.time()-overall_start:.1f}s ==="
    )
    return out_dir


def _apply_resolution_to_trades(
    trades: list[StandardisedTradeRow],
    resolution_lookup: dict,
) -> None:
    """Post-pass: fill resolution_outcome / realised_pnl_usdc / realised_return_pct
    on every trade leg using the inferred resolution map.

    Closed-form simulator trades keep their own realised_return_pct (already
    computed from price drift).  Buy-only-replay trades get a 0/1 payout based
    on YES/NO token identity and yes_outcome.
    """
    for t in trades:
        if t.trade_kind == "closed_form":
            # Closed-form simulators score price drift directly; leave their
            # realised_return_pct intact.
            continue
        if t.realised_pnl_usdc is not None:
            continue
        inferred = resolution_lookup.get(t.market_id)
        if inferred is None:
            t.resolution_outcome = t.resolution_outcome or "unresolved"
            continue
        t.resolution_outcome = inferred.yes_outcome  # type: ignore[assignment]
        t.resolution_ts_ms = inferred.resolution_ts_ms or t.resolution_ts_ms
        if inferred.yes_outcome not in ("yes", "no"):
            continue
        if t.token_id == inferred.token_id_yes:
            payout_per_share = 1.0 if inferred.yes_outcome == "yes" else 0.0
        elif t.token_id == inferred.token_id_no:
            payout_per_share = 1.0 if inferred.yes_outcome == "no" else 0.0
        else:
            continue
        payout = t.shares * payout_per_share
        t.realised_pnl_usdc = payout - t.stake_usdc - t.fees_usdc
        if t.entry_cost_usdc and t.entry_cost_usdc > 0:
            t.realised_return_pct = t.realised_pnl_usdc / t.entry_cost_usdc


# ── lane runners ─────────────────────────────────────────────────────────────


def _run_research_preset(
    data_root: Path,
    cfg: StandardisedBacktestConfig,
    preset_name: str,
    subrun_label: str,
) -> LaneRunResult | None:
    """Run one research_replay preset and return its output handle."""
    from ...research_presets import apply_preset, load_preset
    from ...strategies.models import ContextAwareBacktestConfig
    from ..research_replay import run_research_backtest

    subrun_id = f"{cfg.run_id}__{subrun_label}"
    try:
        preset = load_preset(preset_name)
    except (ValueError, FileNotFoundError):
        return None
    # Apply standardised stake override to the preset before apply_preset() runs,
    # so every research lane trades the same notional per leg.
    if cfg.stake_per_leg_usdc is not None:
        preset = preset.model_copy(update={"stake_size_usdc": cfg.stake_per_leg_usdc})
    start_cash = Decimal("1000000000000") if cfg.infinite_cash else Decimal("10000")
    base_cfg = ContextAwareBacktestConfig(
        run_id=subrun_id,
        starting_cash_usdc=start_cash,
        fee_bps=Decimal("0"),
        allow_negative_cash=cfg.infinite_cash,
    )
    research_cfg = apply_preset(preset, base_cfg)
    overrides: dict[str, Any] = {"run_id": subrun_id, "allow_negative_cash": cfg.infinite_cash}
    if cfg.stake_per_leg_usdc is not None:
        overrides["max_stake_per_trade_usdc"] = Decimal(str(cfg.stake_per_leg_usdc))
    if cfg.infinite_cash:
        overrides["starting_cash_usdc"] = start_cash
        overrides["max_stake_per_market_usdc"] = start_cash
    research_cfg = research_cfg.model_copy(update=overrides)
    result = run_research_backtest(data_root, research_cfg, preset)
    return LaneRunResult(
        subrun_id=subrun_id,
        output_dir=Path(result["output_dir"]),
        preset_name=preset_name,
        trades_csv=Path(result["output_dir"]) / "trades.csv",
        extras={"metrics": result.get("metrics", {}), "funnel": result.get("funnel", {})},
    )


def _run_deepseek_lane(
    data_root: Path,
    cfg: StandardisedBacktestConfig,
) -> tuple[LaneRunResult | None, list[dict[str, Any]]]:
    """Run the DeepSeek free-reign lane.

    We delegate hypothesis generation + promotion + buy-only replay to the
    existing workflow helpers in ``deepseek_exploratory_backtest``, then read
    the standard outputs back.  Pre-trade justifications are extracted from
    the in-memory hypothesis list.
    """
    import json as _json

    from ...research import deepseek_exploratory_backtest as ds_mod

    subrun_id = f"{cfg.run_id}__ai_freereign_deepseek"
    deepseek_hypotheses: list[dict[str, Any]] = []

    # Option A: reuse prior raw responses (deepseek_raw_responses.jsonl) — skips Ollama.
    if cfg.reuse_deepseek_responses_from:
        reuse_path = Path(cfg.reuse_deepseek_responses_from)
        if reuse_path.exists():
            _log(f"  reusing DeepSeek responses from {reuse_path}")
            for line in reuse_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    envelope = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                parsed = envelope.get("parsed") if isinstance(envelope, dict) else None
                if isinstance(parsed, dict) and parsed.get("hypothesis_id"):
                    # Re-route the hypothesis the same way the live workflow does.
                    parsed = dict(parsed)
                    route, handler = ds_mod._route_deepseek_hypothesis(parsed)
                    parsed["route"] = route
                    parsed["route_handler"] = handler
                    deepseek_hypotheses.append(parsed)
            _log(f"  loaded {len(deepseek_hypotheses)} reused hypotheses")
            # No replay subrun for reused hypotheses — the wild-diagnostic lane
            # will simulate them and the validated lane will report 0 legs.
            if deepseek_hypotheses:
                return None, deepseek_hypotheses
        else:
            _log(f"  reuse_deepseek_responses_from path missing: {reuse_path}")

    # Try to load any prior DeepSeek hypotheses file first — keeps smoke runs fast.
    prior_jsonl = data_root / "reports" / "deepseek_exploratory_backtest" / cfg.run_id / "deepseek_hypotheses.jsonl"
    if prior_jsonl.exists():
        loaded_map = load_deepseek_justifications_jsonl(prior_jsonl)
        deepseek_hypotheses = [
            {**v.model_dump(), "hypothesis_id": v.hypothesis_id} for v in loaded_map.values()
        ]

    # If no prior, attempt fresh generation + replay.  In smoke mode we keep the
    # candidate set tiny and tolerate network failure (Ollama may not be running).
    try:
        markets = ds_mod._candidate_market_sample(data_root, 60 if cfg.smoke_mode else 500)
        existing_pairs = ds_mod._existing_pair_keys(data_root)
        embedding_candidates, _meta = ds_mod.generate_hypotheses(
            markets,
            existing_pair_keys=existing_pairs,
            sim_threshold=0.74,
            max_pairs_per_market=6,
            overall_pair_cap=20 if cfg.smoke_mode else 240,
            ollama_base=ds_mod.DEFAULT_OLLAMA_BASE,
        )
        seed_candidates = ds_mod.seed_deepseek_candidates(
            data_root, 10 if cfg.smoke_mode else 80
        )
        deepseek_candidates = (embedding_candidates + seed_candidates)[
            : 20 if cfg.smoke_mode else 160
        ]
        deepseek_markets = ds_mod._markets_for_candidates(data_root, markets, deepseek_candidates)
        report_dir = (
            data_root / "reports" / "deepseek_exploratory_backtest" / subrun_id
        )
        report_dir.mkdir(parents=True, exist_ok=True)
        workflow_cfg = ds_mod.WorkflowConfig(
            run_id=subrun_id,
            market_limit=60 if cfg.smoke_mode else 500,
            embedding_pair_cap=20 if cfg.smoke_mode else 240,
            deepseek_pair_limit=20 if cfg.smoke_mode else 160,
        )
        deepseek_hypotheses = ds_mod.reason_with_deepseek(
            deepseek_candidates, deepseek_markets, workflow_cfg, report_dir,
        )
        ds_mod.promote_deepseek_hypotheses(
            data_root, deepseek_hypotheses, deepseek_markets, subrun_id,
        )
    except Exception as exc:
        # If hypothesis generation fails we still emit an empty subrun so the
        # pack stays consistent; an audit row records the failure.
        return None, deepseek_hypotheses or [{"hypothesis_id": "_failure_marker", "error": repr(exc)}]

    # Promote any prior in-memory hypotheses to the replay store (idempotent on hypothesis_id).
    # Now run the deepseek-hypothesis-surface preset, whose subtype filter picks
    # up exactly the rows we promoted above.
    replay = _run_research_preset(
        data_root, cfg, "deepseek_hypothesis_surface", "ai_freereign_deepseek_replay"
    )
    if replay is None:
        return None, deepseek_hypotheses
    replay.subrun_id = subrun_id
    return replay, deepseek_hypotheses


def _run_strict_validation(
    data_root: Path,
    cfg: StandardisedBacktestConfig,
) -> LaneRunResult | None:
    """Run the strict-validation lane against accepted relationships only."""
    from ...research_presets import apply_preset, load_preset
    from ...strategies.models import ContextAwareBacktestConfig
    from ..research_replay import run_research_backtest

    subrun_id = f"{cfg.run_id}__strict_validation"
    try:
        preset = load_preset("strict_research")
    except (ValueError, FileNotFoundError):
        return None
    if cfg.stake_per_leg_usdc is not None:
        preset = preset.model_copy(update={"stake_size_usdc": cfg.stake_per_leg_usdc})
    sv_start_cash = Decimal("1000000000000") if cfg.infinite_cash else Decimal("10000")
    base_cfg = ContextAwareBacktestConfig(
        run_id=subrun_id,
        starting_cash_usdc=sv_start_cash,
        fee_bps=Decimal("0"),
        relationship_universe="accepted_only",
        lane="strict_context_valid",
        min_relationship_confidence=0.65,
        min_coverage_score=0.60,
        allow_negative_cash=cfg.infinite_cash,
    )
    research_cfg = apply_preset(preset, base_cfg)
    sv_overrides: dict[str, Any] = {
        "run_id": subrun_id,
        "relationship_universe": "accepted_only",
        "lane": "strict_context_valid",
        "min_relationship_confidence": 0.65,
        "allow_negative_cash": cfg.infinite_cash,
    }
    if cfg.stake_per_leg_usdc is not None:
        sv_overrides["max_stake_per_trade_usdc"] = Decimal(str(cfg.stake_per_leg_usdc))
    if cfg.infinite_cash:
        sv_overrides["starting_cash_usdc"] = sv_start_cash
        sv_overrides["max_stake_per_market_usdc"] = sv_start_cash
    research_cfg = research_cfg.model_copy(update=sv_overrides)
    result = run_research_backtest(data_root, research_cfg, preset)
    return LaneRunResult(
        subrun_id=subrun_id,
        output_dir=Path(result["output_dir"]),
        preset_name="strict_research",
        trades_csv=Path(result["output_dir"]) / "trades.csv",
        extras={"metrics": result.get("metrics", {}), "funnel": result.get("funnel", {})},
    )


def _run_random_pair_control(
    data_root: Path,
    cfg: StandardisedBacktestConfig,
    *,
    seed: int = 42,
) -> list[StandardisedTradeRow]:
    """Direct random-pair noise-floor control.

    Sample N random eligible markets, pair them, buy YES of each at the entry
    price observed at the earliest co-observed timestamp, then mark / resolve
    via the post-pass resolution lookup.  Skips the legacy null_baseline path
    which silently fails because it writes relationships to a stripped sub-lake
    that has no price/markets/coverage data.

    All output rows carry ``source_lane="control"``,
    ``trade_kind="control"``, ``is_control=True``.
    """
    import random
    import uuid as _uuid

    from ...storage.parquet.backfill_coverage_repo import (
        ParquetBackfillCoverageRepository,
    )
    from ...storage.parquet.markets_repo import ParquetMarketsRepository
    from ...storage.parquet.price_history_repo import ParquetPriceHistoryRepository

    subrun_id = f"{cfg.run_id}__control"
    n_pairs = max(0, int(cfg.control_pairs))
    if n_pairs == 0:
        return []

    markets = list(ParquetMarketsRepository(data_root).iter_active_markets())
    cov = {r.market_id: r for r in ParquetBackfillCoverageRepository(data_root).iter_latest()}
    eligible = [
        m for m in markets
        if len(m.outcomes) == 2 and len(m.clob_token_ids) == 2
        and cov.get(m.id) and cov[m.id].coverage_score >= 0.60
    ]
    if len(eligible) < 2:
        _log(f"  control: insufficient eligible markets ({len(eligible)})")
        return []

    rng = random.Random(seed)
    rng.shuffle(eligible)
    pairs = [(eligible[i], eligible[i + 1]) for i in range(0, min(2 * n_pairs, len(eligible)) - 1, 2)]

    price_repo = ParquetPriceHistoryRepository(data_root)
    stake = float(cfg.stake_per_leg_usdc or 50.0)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    out: list[StandardisedTradeRow] = []
    skipped_no_prices = 0
    for ma, mb in pairs:
        tok_a = ma.clob_token_ids[0]
        tok_b = mb.clob_token_ids[0]
        rows_a = sorted(price_repo.iter_for_token(tok_a), key=lambda r: r.ts_ms)
        rows_b = sorted(price_repo.iter_for_token(tok_b), key=lambda r: r.ts_ms)
        if not rows_a or not rows_b:
            skipped_no_prices += 1
            continue
        # Entry at the earliest co-observed tick: max of the two series' first ts.
        start_ts = max(rows_a[0].ts_ms, rows_b[0].ts_ms)
        entry_a = next((r for r in rows_a if r.ts_ms >= start_ts), rows_a[0])
        entry_b = next((r for r in rows_b if r.ts_ms >= start_ts), rows_b[0])
        trade_group_id = f"control|{ma.id}|{mb.id}"
        for leg, m, tok, entry in (("a", ma, tok_a, entry_a), ("b", mb, tok_b, entry_b)):
            entry_price = float(entry.price)
            shares = stake / entry_price if entry_price > 0 else 0.0
            row = StandardisedTradeRow(
                trade_id=_uuid.uuid4().hex,
                trade_group_id=trade_group_id,
                run_id=cfg.run_id,
                subrun_id=subrun_id,
                source_lane="control",
                source_agent="random_pair_noise_floor",
                source_preset="random_pair_noise_floor",
                trade_kind="control",
                is_control=True,
                reason="control: random eligible market pair (noise floor)",
                relationship_id=trade_group_id,
                relationship_family="control_random_pair",
                relationship_type="control",
                relationship_subtype="random_pair",
                context_space_id="control",
                outcome_space_id="control",
                hypothesis_source="null_baseline",
                hypothesis_id=trade_group_id,
                leg=leg,  # type: ignore[arg-type]
                token_id=tok,
                market_id=m.id,
                side="buy",
                entry_ts_ms=int(entry.ts_ms),
                entry_price=entry_price,
                shares=shares,
                stake_usdc=stake,
                notional_usdc=stake,
                fees_usdc=0.0,
                slippage_cost_usdc=0.0,
                entry_cost_usdc=stake,
                execution_model="price_history_only",
                ingested_ts_ms=now_ms,
            )
            out.append(row)
    if skipped_no_prices:
        _log(f"  control: skipped {skipped_no_prices} pairs with no price history")
    return out


# ── adapter helper ───────────────────────────────────────────────────────────


def _adapt_replay_result(
    data_root: Path,
    result: LaneRunResult,
    run_id: str,
    *,
    source_lane: str,
    source_agent: str,
    is_rulebook: bool = False,
    is_ai_generated: bool = False,
    is_exploratory: bool = False,
    is_strict_validation: bool = False,
    is_control: bool = False,
    is_diagnostic: bool = False,
    trade_kind: str = "replay_fill",
    reason_default: str = "",
    justification_by_hypothesis: dict[str, DeepSeekPreTradeJustification] | None = None,
) -> list[StandardisedTradeRow]:
    if result.trades_csv is None or not result.trades_csv.exists():
        return []
    raw = read_trades_csv(result.trades_csv)
    if not raw:
        return []
    latest = latest_price_lookup_for(data_root, raw)
    return adapt_replay_trade_rows(
        raw,
        run_id=run_id,
        subrun_id=result.subrun_id,
        source_lane=source_lane,
        source_agent=source_agent,
        source_preset=result.preset_name,
        trade_kind=trade_kind,
        reason_default=reason_default,
        is_rulebook=is_rulebook,
        is_ai_generated=is_ai_generated,
        is_exploratory=is_exploratory,
        is_strict_validation=is_strict_validation,
        is_control=is_control,
        is_diagnostic=is_diagnostic,
        justification_by_hypothesis=justification_by_hypothesis,
        latest_price_lookup=latest,
    )


def _run_deepseek_wild_diagnostic(
    data_root: Path,
    cfg: StandardisedBacktestConfig,
    hypotheses: list[dict[str, Any]],
) -> list[Any]:
    """Force every DeepSeek-reviewed pair through the mutex_yes_yes_overround simulator,
    bypassing the model's veto.

    Returns ``SimulatedTrade`` objects (the closed_form_simulators DTO).  Adapt them
    into standardised rows via ``adapt_closed_form_simulator_trades`` with
    ``source_lane="ai_freereign_deepseek_wild_diagnostic"`` and ``is_diagnostic=True``.
    """
    from ...research.closed_form_simulators import run_simulators
    from ...storage.parquet.markets_repo import ParquetMarketsRepository

    if not hypotheses:
        return []
    # Force-route every hypothesis to mutex_yes_yes_overround_simulator.  The
    # simulator works for any YES/YES pair regardless of what DeepSeek said.
    forced: list[dict[str, Any]] = []
    for h in hypotheses:
        forced.append({
            **h,
            "route": "unsupported_but_testable",
            "route_handler": "mutex_yes_yes_overround_simulator",
        })
    markets_repo = ParquetMarketsRepository(data_root)
    markets = list(markets_repo.iter_active_markets())
    return run_simulators(data_root, forced, markets, slippage_bps=50)


def _run_deepseek_synthetic_control(
    data_root: Path,
    cfg: StandardisedBacktestConfig,
    hypotheses: list[dict[str, Any]],
    *,
    seed: int = 1337,
) -> list[Any]:
    """Run fair synthetic controls for DeepSeek wild diagnostic.

    The controls use the same closed-form simulator and return calculation as
    DeepSeek wild, but market pairs are random/shuffled from the same market-id
    pool present in the DeepSeek-reviewed hypotheses.
    """
    import random

    from ...research.closed_form_simulators import run_simulators
    from ...storage.parquet.markets_repo import ParquetMarketsRepository

    if not hypotheses:
        return []

    markets_repo = ParquetMarketsRepository(data_root)
    markets = list(markets_repo.iter_active_markets())
    active_ids = {m.id for m in markets}
    pool = sorted({
        str(h.get("market_id_a") or "")
        for h in hypotheses
        if str(h.get("market_id_a") or "") in active_ids
    } | {
        str(h.get("market_id_b") or "")
        for h in hypotheses
        if str(h.get("market_id_b") or "") in active_ids
    })
    if len(pool) < 2:
        return []

    rng = random.Random(seed)
    control_hypotheses: list[dict[str, Any]] = []
    target = len(hypotheses)
    attempts = 0
    while len(control_hypotheses) < target and attempts < target * 20:
        attempts += 1
        ma, mb = rng.sample(pool, 2)
        control_hypotheses.append({
            "hypothesis_id": f"{cfg.run_id}__synthetic_control_{len(control_hypotheses):04d}",
            "relationship_type": "random_synthetic_control",
            "market_id_a": ma,
            "market_id_b": mb,
            "route": "unsupported_but_testable",
            "route_handler": "mutex_yes_yes_overround_simulator",
        })

    return run_simulators(data_root, control_hypotheses, markets, slippage_bps=50)


def _build_deepseek_funnel(
    *,
    hypotheses: list[dict[str, Any]],
    replay_legs: int,
    closed_form_legs: int,
    wild_diagnostic_legs: int = 0,
    synthetic_control_legs: int = 0,
) -> list[dict[str, Any]]:
    """Build a funnel breakdown of the DeepSeek hypothesis pipeline.

    Each row is one stage with a count.  Used to make it obvious WHERE the
    pipeline drops hypotheses — currently the dominant drop is the
    ``invalid_same_topic_only`` classification at the LLM step.
    """
    from collections import Counter

    if not hypotheses:
        return [{
            "stage": "hypotheses_generated",
            "count": 0,
            "note": "no DeepSeek hypotheses available — Ollama unreachable / model returned no responses",
        }]

    by_rel_type = Counter(str(h.get("relationship_type", "?")) for h in hypotheses)
    by_route = Counter(str(h.get("route", "?")) for h in hypotheses)
    promoted = sum(1 for h in hypotheses if h.get("should_promote_to_replay"))
    outside_rulebook = sum(
        1 for h in hypotheses if h.get("outside_existing_deterministic_rulebook")
    )
    parse_errors = sum(1 for h in hypotheses if h.get("parse_error"))
    human_review = sum(
        1 for h in hypotheses
        if h.get("relationship_type") == "uncertain_human_review_required"
        or h.get("human_review_required")
    )

    rows: list[dict[str, Any]] = [
        {"stage": "1. hypotheses_generated", "count": len(hypotheses), "note": "LLM responses captured"},
        {"stage": "2. parse_error", "count": parse_errors, "note": "JSON parse failed"},
        {"stage": "3. outside_existing_rulebook (model claim)", "count": outside_rulebook},
        {"stage": "4. should_promote_to_replay", "count": promoted, "note": "model said replay-worthy"},
        {"stage": "5. flagged_human_review", "count": human_review},
        {"stage": "6. replayed_to_legs (validated lane)", "count": replay_legs, "note": "buy-only replay trade legs emitted"},
        {"stage": "7. closed_form_simulator_legs", "count": closed_form_legs, "note": "legs from unsupported_but_testable simulators"},
        {"stage": "8. wild_diagnostic_legs", "count": wild_diagnostic_legs, "note": "ALL hypotheses force-routed to mutex_yes_yes_overround_simulator (ignoring LLM veto)"},
        {"stage": "9. synthetic_control_legs", "count": synthetic_control_legs, "note": "random/shuffled pairs through the same mutex simulator"},
    ]
    # Breakdown by relationship_type
    for rtype, n in by_rel_type.most_common():
        rows.append({
            "stage": f"  by relationship_type: {rtype}",
            "count": n,
            "note": "model classification",
        })
    # Breakdown by route
    for route, n in by_route.most_common():
        if route == "?":
            continue
        rows.append({
            "stage": f"  by route: {route}",
            "count": n,
            "note": "where the orchestrator sent it",
        })
    return rows


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def new_run_id(prefix: str = "standardised") -> str:
    """Generate a default run id with date prefix."""
    today = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{today}_{uuid.uuid4().hex[:6]}"
