"""Every flat alias must route to the same Click Command object as its
canonical subgroup form. If this test fails, a copy-paste in `_aliases.py`
has decoupled the two — fix it there, not here."""

from __future__ import annotations

from click.testing import CliRunner

from polymarket_arb.cli import cli
from polymarket_arb.cli._aliases import alias_pairs


def test_every_alias_routes_to_canonical_callback() -> None:
    triples = alias_pairs(cli)
    assert triples, "no aliases registered — _aliases.py wiring broke"
    for name, canonical, alias in triples:
        assert canonical is alias, (
            f"alias {name!r} resolves to a different Click Command than its "
            f"canonical form"
        )


def test_root_help_lists_subgroups_and_aliases() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0, result.output
    for subgroup in ("gamma", "nlp", "clob", "score"):
        assert subgroup in result.output
    # one canonical alias from each phase appears:
    for alias in ("fetch-markets", "extract-market-semantics", "fetch-orderbook",
                  "score-markets"):
        assert alias in result.output


def test_canonical_invocation_runs_without_data(tmp_path) -> None:
    # All registered user-facing commands are implemented; with an empty lake,
    # fetch-quotes completes as a no-op.
    env = {"POLYMARKET_ARB_STORAGE__DATA_ROOT": str(tmp_path / "data")}
    result = CliRunner().invoke(cli, ["clob", "fetch-quotes", "--limit", "1"], env=env)
    assert result.exit_code == 0
    assert "0 best quotes" in result.output


def test_alias_invocation_runs_same_command_without_data(tmp_path) -> None:
    env = {"POLYMARKET_ARB_STORAGE__DATA_ROOT": str(tmp_path / "data")}
    result = CliRunner().invoke(cli, ["fetch-quotes", "--limit", "1"], env=env)
    assert result.exit_code == 0
    assert "0 best quotes" in result.output
