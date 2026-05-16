"""Tests for the research preset loader and apply_preset()."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from polymarket_arb.research_presets import (
    ResearchPreset,
    apply_preset,
    list_presets,
    load_preset,
)
from polymarket_arb.strategies.models import ContextAwareBacktestConfig

PRESET_PATH = Path(__file__).resolve().parents[2] / "configs" / "research_presets" / "trade_surface_v1.yaml"

EXPECTED_PRESET_NAMES = {
    "strict_research",
    "exploratory_trade_surface",
    "gross_violation_scan",
    "net_after_cost_scan",
    "replay_many_entries",
}


# ── YAML integrity ────────────────────────────────────────────────────────────


def test_preset_yaml_exists() -> None:
    assert PRESET_PATH.exists(), f"Preset YAML not found at {PRESET_PATH}"


def test_preset_yaml_is_valid_yaml() -> None:
    data = yaml.safe_load(PRESET_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert "presets" in data


def test_all_five_presets_present() -> None:
    data = yaml.safe_load(PRESET_PATH.read_text(encoding="utf-8"))
    found = set(data["presets"].keys())
    assert EXPECTED_PRESET_NAMES.issubset(found), (
        f"Missing presets: {EXPECTED_PRESET_NAMES - found}"
    )


# ── load_preset ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(EXPECTED_PRESET_NAMES))
def test_load_preset_parses_each_preset(name: str) -> None:
    preset = load_preset(name)
    assert isinstance(preset, ResearchPreset)
    assert preset.preset_name == name
    assert preset.label  # must not be empty


def test_load_preset_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown preset"):
        load_preset("does_not_exist")


def test_load_preset_custom_path(tmp_path: Path) -> None:
    custom = tmp_path / "custom.yaml"
    custom.write_text(
        "schema_version: 1\npresets:\n  my_preset:\n"
        "    label: TEST\n    lane: all_context_research\n"
        "    relationship_universe: reviewed_lanes\n"
        "    include_auto_approved: false\n"
        "    min_relationship_confidence: 0.5\n"
        "    min_combined_prob: 0.0\n"
        "    min_single_prob: 0.0\n"
        "    min_gross_edge: 0.0\n"
        "    min_net_edge: 0.0\n"
        "    slippage_bps: 50\n"
        "    max_staleness_minutes: 360\n"
        "    stake_size_usdc: 100\n"
        "    include_exploratory_relationships: false\n"
        "    label_all_outputs_exploratory: false\n",
        encoding="utf-8",
    )
    preset = load_preset("my_preset", preset_path=custom)
    assert preset.preset_name == "my_preset"
    assert preset.label == "TEST"


# ── list_presets ──────────────────────────────────────────────────────────────


def test_list_presets_returns_all_names() -> None:
    names = list_presets()
    assert EXPECTED_PRESET_NAMES.issubset(set(names))


# ── ResearchPreset field constraints ─────────────────────────────────────────


def test_strict_research_is_conservative() -> None:
    preset = load_preset("strict_research")
    assert preset.include_auto_approved is False
    assert preset.include_exploratory_relationships is False
    assert preset.label_all_outputs_exploratory is False
    assert preset.min_relationship_confidence >= 0.30
    assert preset.min_combined_prob >= 0.30
    assert preset.execute_trades is True


def test_exploratory_is_permissive() -> None:
    preset = load_preset("exploratory_trade_surface")
    assert preset.include_auto_approved is True
    assert preset.include_exploratory_relationships is True
    assert preset.label_all_outputs_exploratory is True
    assert preset.min_relationship_confidence <= 0.25
    assert preset.stake_size_usdc <= 10.0  # small stakes for sample-size exploration


def test_gross_violation_scan_does_not_execute() -> None:
    preset = load_preset("gross_violation_scan")
    assert preset.execute_trades is False
    assert preset.min_gross_edge == 0.0
    assert preset.stake_size_usdc == 0.0


def test_all_exploratory_presets_label_outputs() -> None:
    for name in ("exploratory_trade_surface", "gross_violation_scan",
                 "net_after_cost_scan", "replay_many_entries"):
        preset = load_preset(name)
        assert preset.label_all_outputs_exploratory is True, (
            f"Preset {name!r} does not label outputs as exploratory"
        )


# ── apply_preset ──────────────────────────────────────────────────────────────


def test_apply_strict_research_sets_lane() -> None:
    preset = load_preset("strict_research")
    cfg = apply_preset(preset)
    assert cfg.lane == "all_context_research"
    assert cfg.relationship_universe == "reviewed_lanes"
    assert cfg.include_auto_approved is False


def test_apply_exploratory_sets_permissive_params() -> None:
    preset = load_preset("exploratory_trade_surface")
    cfg = apply_preset(preset)
    assert cfg.include_auto_approved is True
    assert cfg.relationship_universe == "all_with_context_decisions"
    assert cfg.min_relationship_confidence == pytest.approx(preset.min_relationship_confidence)
    assert cfg.min_combined_prob_for_pairwise == pytest.approx(preset.min_combined_prob)
    assert cfg.slippage_bps == Decimal(str(preset.slippage_bps))


def test_apply_preset_preserves_run_id() -> None:
    preset = load_preset("exploratory_trade_surface")
    base = ContextAwareBacktestConfig(run_id="my_run_abc")
    cfg = apply_preset(preset, base)
    assert cfg.run_id == "my_run_abc"


def test_apply_preset_preserves_starting_cash_when_no_override() -> None:
    preset = load_preset("strict_research")
    base = ContextAwareBacktestConfig(starting_cash_usdc=Decimal("5000"))
    cfg = apply_preset(preset, base)
    assert cfg.starting_cash_usdc == Decimal("5000")


def test_apply_gross_violation_scan_sets_zero_stake() -> None:
    preset = load_preset("gross_violation_scan")
    # stake_size_usdc == 0 → preserve base's max_stake_per_trade_usdc
    base = ContextAwareBacktestConfig(max_stake_per_trade_usdc=Decimal("99"))
    cfg = apply_preset(preset, base)
    assert cfg.max_stake_per_trade_usdc == Decimal("99")


def test_apply_exploratory_sets_small_stake() -> None:
    preset = load_preset("exploratory_trade_surface")
    cfg = apply_preset(preset)
    assert cfg.max_stake_per_trade_usdc == Decimal(str(preset.stake_size_usdc))


def test_apply_preset_staleness_converted_to_ms() -> None:
    preset = load_preset("exploratory_trade_surface")
    cfg = apply_preset(preset)
    assert cfg.quote_staleness_limit_ms == preset.max_staleness_minutes * 60 * 1000


def test_apply_preset_result_is_valid_config() -> None:
    for name in EXPECTED_PRESET_NAMES:
        preset = load_preset(name)
        cfg = apply_preset(preset)
        assert isinstance(cfg, ContextAwareBacktestConfig)
        # Pydantic validation should not have raised
        assert cfg.lane in {
            "strict_context_valid",
            "reviewed_context_valid",
            "exploratory_context_unreviewed",
            "exploratory_context_auto_approved",
            "all_context_research",
        }
