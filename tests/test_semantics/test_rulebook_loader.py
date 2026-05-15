from __future__ import annotations

from pathlib import Path

import pytest

from polymarket_arb.semantics.rulebook import RulebookError, load_rulebook, rulebook_content_hash
from polymarket_arb.semantics.rulebook_models import AmbiguityRulebook


def test_load_valid_ambiguity_rulebook():
    path = Path("configs/semantic_rules/ambiguity_v1.yaml")
    rb = load_rulebook(path, kind="ambiguity")
    assert isinstance(rb, AmbiguityRulebook)
    assert rb.rulebook_id == "ambiguity"
    assert rb.rulebook_version == 1
    assert len(rulebook_content_hash(path)) == 64


def test_missing_yaml_fails_closed(tmp_path):
    with pytest.raises(RulebookError):
        load_rulebook(tmp_path / "missing.yaml", kind="ambiguity")


def test_invalid_yaml_fails_closed(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("rulebook_id: [", encoding="utf-8")
    with pytest.raises(RulebookError):
        load_rulebook(path, kind="ambiguity")


def test_missing_required_key_fails_closed(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("rulebook_id: ambiguity\nrulebook_version: 1\n", encoding="utf-8")
    with pytest.raises(RulebookError):
        load_rulebook(path, kind="ambiguity")
