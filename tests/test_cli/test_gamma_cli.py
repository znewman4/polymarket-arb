"""CliRunner tests for the gamma subgroup. Network is fully mocked via respx.

We override settings via env vars (highest precedence) rather than fighting
the yaml loader: ``POLYMARKET_ARB_GAMMA_HOST`` redirects HTTP to a mock host,
``POLYMARKET_ARB_STORAGE__DATA_ROOT`` reroutes the data lake to ``tmp_path``.
"""

from __future__ import annotations

import json
from pathlib import Path

import respx
from click.testing import CliRunner
from httpx import Response

from polymarket_arb.cli import cli

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gamma"


def _markets_synth() -> list[dict]:
    return json.loads((FIXTURES / "markets_synthetic.json").read_text())


def _env_for(tmp_path: Path, *, gamma_host: str = "https://gamma.example") -> dict[str, str]:
    return {
        "POLYMARKET_ARB_GAMMA_HOST": gamma_host,
        "POLYMARKET_ARB_STORAGE__DATA_ROOT": str(tmp_path / "data"),
        "POLYMARKET_ARB_LOGGING__JSON_LOG_PATH": str(tmp_path / "logs" / "test.jsonl"),
    }


def test_fetch_markets_persists_then_list_reads_local(tmp_path):
    """End-to-end: fetch with mocked Gamma, then list/search/show read locally."""

    page = _markets_synth()
    runner = CliRunner()
    env = _env_for(tmp_path)

    with respx.mock(assert_all_called=False) as router:
        offsets: list[int] = []

        def _h(request):
            offsets.append(int(request.url.params["offset"]))
            return Response(200, json=page if offsets[-1] == 0 else [])

        router.get("https://gamma.example/markets").mock(side_effect=_h)
        router.get("https://gamma.example/events").mock(
            return_value=Response(200, json=[])
        )

        result = runner.invoke(
            cli, ["gamma", "fetch-markets", "--limit", "3", "--all"], env=env,
        )
        assert result.exit_code == 0, result.output
        assert "3 markets" in result.output

    # Now read locally — no network mock needed.
    result = runner.invoke(cli, ["gamma", "list-markets", "--active"], env=env)
    assert result.exit_code == 0, result.output
    assert "3 markets" in result.output

    result = runner.invoke(cli, ["gamma", "search-markets", "example"], env=env)
    assert result.exit_code == 0, result.output
    assert "match(es)" in result.output

    result = runner.invoke(cli, ["gamma", "show-market", "1001"], env=env)
    assert result.exit_code == 0, result.output
    assert "synthetic-stringified" in result.output
    assert "0xcond1001" in result.output


def test_show_market_returns_1_when_missing(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["gamma", "show-market", "nope"], env=_env_for(tmp_path))
    assert result.exit_code == 1, result.output
    assert "not found" in result.output


def test_list_markets_empty_lake(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["gamma", "list-markets"], env=_env_for(tmp_path))
    assert result.exit_code == 0
    assert "no markets" in result.output


def test_alias_fetch_markets_routes_through_gamma(tmp_path):
    """Flat alias ``polymarket-arb fetch-markets`` runs the same callback."""

    page = _markets_synth()[:1]  # single market is enough
    runner = CliRunner()
    env = _env_for(tmp_path)

    with respx.mock(assert_all_called=False) as router:
        offsets: list[int] = []
        def _h(request):
            offsets.append(int(request.url.params["offset"]))
            return Response(200, json=page if offsets[-1] == 0 else [])
        router.get("https://gamma.example/markets").mock(side_effect=_h)
        router.get("https://gamma.example/events").mock(
            return_value=Response(200, json=[])
        )
        result = runner.invoke(
            cli, ["fetch-markets", "--limit", "1", "--all"], env=env,
        )
        assert result.exit_code == 0, result.output
        assert "1 markets" in result.output
