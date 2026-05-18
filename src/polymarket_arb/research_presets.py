"""Named research presets for the trade-surface expansion layer.

Presets let callers refer to a named exploration mode instead of spelling out
every threshold. The preset YAML lives at:
  configs/research_presets/trade_surface_v1.yaml

Usage::

    from polymarket_arb.research_presets import load_preset, apply_preset
    from polymarket_arb.strategies.models import ContextAwareBacktestConfig

    preset = load_preset("exploratory_trade_surface")
    cfg = apply_preset(preset, ContextAwareBacktestConfig(run_id="my_run"))

Phase A: preset fields that Phase B/C will enforce (alignment_mode, reentry_policy,
entry_policy, exit_policy, sizing_policy, include_transitive_closure) are stored
on ResearchPreset but not yet wired into the replay engine.  apply_preset() only
writes the fields that ContextAwareBacktestConfig already understands.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRESET_PATH = REPO_ROOT / "configs" / "research_presets" / "trade_surface_v1.yaml"

PRESET_NAMES = frozenset({
    "strict_research",
    "exploratory_trade_surface",
    "gross_violation_scan",
    "net_after_cost_scan",
    "replay_many_entries",
    # Aggressive exploratory presets (RESEARCH-ONLY).
    "aggressive_learning_surface",
    "aggressive_deterministic_surface",
    "embedding_hypothesis_surface",
    "deepseek_hypothesis_surface",
    "ultra_loose_diagnostic_surface",
    "ollama_hypothesis_surface",
})


class ResearchPreset(BaseModel):
    """A named trade-surface research configuration.

    Fields labelled "Phase B" or "Phase C+" are stored here for forward-compatibility
    but are not yet enforced by the replay engine.  They will be wired in when
    research_replay.py (Phase B) is implemented.
    """

    preset_name: str
    label: str

    # ── Lane / universe ────────────────────────────────────────────────────────
    lane: Literal[
        "strict_context_valid",
        "reviewed_context_valid",
        "exploratory_context_unreviewed",
        "exploratory_context_auto_approved",
        "all_context_research",
    ] = "all_context_research"
    relationship_universe: Literal[
        "accepted_only",
        "all_with_context_decisions",
        "reviewed_lanes",
    ] = "reviewed_lanes"
    include_auto_approved: bool = False

    # ── Confidence / probability gates ─────────────────────────────────────────
    min_relationship_confidence: float = Field(default=0.35, ge=0.0, le=1.0)
    min_combined_prob: float = Field(default=0.0, ge=0.0, le=1.0)
    min_single_prob: float = Field(default=0.0, ge=0.0, le=1.0)

    # ── Edge gates ─────────────────────────────────────────────────────────────
    min_gross_edge: float = Field(default=0.02, ge=0.0)
    # Diagnostic presets are allowed to record negative-edge trades for failure-mode
    # study — the hard floor is -0.50 to catch accidental sign-flips.
    min_net_edge: float = Field(default=0.01, ge=-0.5)

    # ── Costs ──────────────────────────────────────────────────────────────────
    slippage_bps: int = Field(default=50, ge=0)

    # ── Price alignment (Phase B) ──────────────────────────────────────────────
    alignment_mode: str = "forward_fill_max_age"
    max_staleness_minutes: int = Field(default=360, ge=0)

    # ── Replay / re-entry policy (Phase B) ────────────────────────────────────
    reentry_policy: str = "one_trade_per_relationship"
    cooldown_minutes: int = Field(default=0, ge=0)
    max_trades_per_relationship: int = Field(default=1, ge=0)
    entry_policy: str = "first_violation_only"
    exit_policy: str = "hold_to_resolution"
    sizing_policy: str = "flat_small"

    # ── Sizing ─────────────────────────────────────────────────────────────────
    stake_size_usdc: float = Field(default=250.0, ge=0.0)

    # ── Expansion features (Phase C+) ─────────────────────────────────────────
    include_transitive_closure: bool = False
    include_exploratory_relationships: bool = False
    include_relationship_subtype_prefixes: list[str] = Field(default_factory=list)
    exclude_relationship_subtype_prefixes: list[str] = Field(default_factory=list)

    # ── Output labelling ───────────────────────────────────────────────────────
    label_all_outputs_exploratory: bool = False
    record_before_costs: bool = False
    execute_trades: bool = True


def load_preset(
    name: str,
    preset_path: Path | None = None,
) -> ResearchPreset:
    """Load a named preset from YAML.

    Args:
        name: One of the preset names defined in trade_surface_v1.yaml.
        preset_path: Override path; defaults to DEFAULT_PRESET_PATH.

    Raises:
        ValueError: If the preset name is not found.
        FileNotFoundError: If the preset file does not exist.
    """
    path = preset_path or DEFAULT_PRESET_PATH
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    presets: dict[str, Any] = data.get("presets", {})
    if name not in presets:
        available = sorted(presets.keys())
        raise ValueError(
            f"Unknown preset {name!r}. Available presets: {available}"
        )
    raw = presets[name]
    return ResearchPreset(preset_name=name, **raw)


def apply_preset(
    preset: ResearchPreset,
    base: ContextAwareBacktestConfig | None = None,  # noqa: F821  (imported lazily)
) -> ContextAwareBacktestConfig:  # noqa: F821  (imported lazily)
    """Return a ContextAwareBacktestConfig with preset values merged in.

    Only fields that ContextAwareBacktestConfig already knows about are written.
    Phase B/C fields (alignment_mode, reentry_policy, entry_policy, etc.) are
    NOT written here — they live on ResearchPreset for the future replay engine.

    The caller's base config (run_id, start/end dates, etc.) is preserved unless
    the preset overrides a specific field.
    """
    from .strategies.models import ContextAwareBacktestConfig

    cfg = base or ContextAwareBacktestConfig()

    overrides: dict[str, Any] = {
        "lane": preset.lane,
        "relationship_universe": preset.relationship_universe,
        "include_auto_approved": preset.include_auto_approved,
        "min_relationship_confidence": preset.min_relationship_confidence,
        "min_combined_prob_for_pairwise": preset.min_combined_prob,
        "min_single_prob_for_pairwise": preset.min_single_prob,
        "min_gross_edge": preset.min_gross_edge,
        "min_net_edge": preset.min_net_edge,
        "slippage_bps": Decimal(str(preset.slippage_bps)),
        "quote_staleness_limit_ms": preset.max_staleness_minutes * 60 * 1000,
    }

    if preset.stake_size_usdc > 0:
        overrides["max_stake_per_trade_usdc"] = Decimal(str(preset.stake_size_usdc))

    return cfg.model_copy(update=overrides)


def list_presets(preset_path: Path | None = None) -> list[str]:
    """Return the sorted list of preset names from the YAML."""
    path = preset_path or DEFAULT_PRESET_PATH
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return sorted(data.get("presets", {}).keys())
