"""Test the maintenance compact-lake CLI command."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import respx
from click.testing import CliRunner
from httpx import Response

from polymarket_arb.cli import cli
from polymarket_arb.cli import maintenance as maintenance_mod


def _env_for(tmp_path: Path) -> dict[str, str]:
    return {
        "POLYMARKET_ARB_STORAGE__DATA_ROOT": str(tmp_path / "data"),
        "POLYMARKET_ARB_LOGGING__JSON_LOG_PATH": str(tmp_path / "logs" / "test.jsonl"),
    }


def _write_tiny_parquet(target: Path, value: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table({"x": [value]})
    pq.write_table(table, target)


def test_compact_lake_merges_old_partitions(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    old_dt = date.today() - timedelta(days=5)
    partition = data_root / "normalised" / "demo" / f"dt={old_dt.isoformat()}"
    for i in range(15):
        _write_tiny_parquet(partition / f"part-{i:03d}.parquet", i)

    # Recent partition should be skipped by --older-than-days.
    today = date.today()
    recent = data_root / "normalised" / "demo" / f"dt={today.isoformat()}"
    for i in range(15):
        _write_tiny_parquet(recent / f"part-{i:03d}.parquet", i + 100)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["maintenance", "compact-lake", "--older-than-days", "1", "--min-files", "10"],
        env=_env_for(tmp_path),
    )
    assert result.exit_code == 0, result.output

    # Old partition is collapsed to a single compacted file.
    remaining = sorted(partition.glob("*.parquet"))
    assert len(remaining) == 1
    assert remaining[0].name == "part-compacted.parquet"

    # Row count preserved.
    con = duckdb.connect(":memory:")
    rows = con.execute(
        f"SELECT COUNT(*) FROM read_parquet('{remaining[0]}')"
    ).fetchone()
    assert rows[0] == 15

    # Recent partition untouched.
    recent_files = sorted(recent.glob("*.parquet"))
    assert len(recent_files) == 15


def test_compact_lake_idempotent(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    old_dt = date.today() - timedelta(days=5)
    partition = data_root / "normalised" / "demo" / f"dt={old_dt.isoformat()}"
    for i in range(12):
        _write_tiny_parquet(partition / f"part-{i:03d}.parquet", i)

    runner = CliRunner()
    env = _env_for(tmp_path)
    args = ["maintenance", "compact-lake", "--older-than-days", "1", "--min-files", "10"]

    result1 = runner.invoke(cli, args, env=env)
    assert result1.exit_code == 0

    result2 = runner.invoke(cli, args, env=env)
    assert result2.exit_code == 0
    # Second run should be a no-op — the single compacted file is below min-files.
    assert "compacted 0 partition" in result2.output

    files = sorted(partition.glob("*.parquet"))
    assert len(files) == 1
    assert files[0].name == "part-compacted.parquet"


def test_compact_lake_dry_run_keeps_files(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    old_dt = date.today() - timedelta(days=5)
    partition = data_root / "normalised" / "demo" / f"dt={old_dt.isoformat()}"
    for i in range(12):
        _write_tiny_parquet(partition / f"part-{i:03d}.parquet", i)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["maintenance", "compact-lake", "--older-than-days", "1", "--dry-run"],
        env=_env_for(tmp_path),
    )
    assert result.exit_code == 0
    assert len(list(partition.glob("*.parquet"))) == 12


def test_test_connectivity_passes_all_checks(tmp_path: Path, monkeypatch) -> None:
    class FakePolymarketClient:
        def get_api_keys(self):
            return [{"key": "poly-key"}]

        def get_order_book(self, token_id):
            assert token_id == "0x123456789"
            return SimpleNamespace(
                bids=[SimpleNamespace(price="0.42", size="12")],
                asks=[SimpleNamespace(price="0.44", size="10")],
            )

    monkeypatch.setattr(
        maintenance_mod,
        "_load_limitless_credentials",
        lambda: {
            "key_id": "limitless-key",
            "key_secret": "not-base64-but-ok",
            "wallet_address": "0xWallet",
        },
    )
    monkeypatch.setattr(
        maintenance_mod,
        "_load_polymarket_credentials",
        lambda: {
            "private_key": "0xprivate",
            "api_key": "abcdefghi",
            "api_secret": "secret",
            "api_passphrase": "pass",
        },
    )
    monkeypatch.setattr(
        maintenance_mod,
        "_build_polymarket_client",
        lambda settings, creds: FakePolymarketClient(),
    )
    monkeypatch.setattr(
        maintenance_mod,
        "_first_relationship_token",
        lambda data_root: "0x123456789",
    )

    env = {
        **_env_for(tmp_path),
        "POLYMARKET_ARB_LIMITLESS_HOST": "https://limitless.example",
        "POLYMARKET_ARB_POLYMARKET_CLOB_HOST": "https://clob.example",
    }
    with respx.mock(assert_all_called=True) as router:
        router.get("https://limitless.example/profiles/public/0xWallet").mock(
            return_value=Response(200, json={"id": 12345})
        )
        router.get("https://limitless.example/markets/active").mock(
            return_value=Response(
                200,
                json={
                    "data": [
                        {
                            "slug": "will-x-happen",
                            "title": "Will X happen?",
                            "address": "0xABCDEF123456789",
                            "marketType": "single",
                            "prices": [0.51, 0.49],
                        }
                    ]
                },
            )
        )
        result = CliRunner().invoke(
            cli,
            ["maintenance", "test-connectivity"],
            env=env,
        )

    assert result.exit_code == 0, result.output
    assert "[✓] Limitless auth" in result.output
    assert "owner_id=12345" in result.output
    assert "[✓] Limitless market" in result.output
    assert "address=0xABCD...789" in result.output
    assert "[✓] Polymarket auth" in result.output
    assert "api_key=abcdefghi active" in result.output
    assert "[✓] Polymarket book" in result.output
    assert "bid=0.42 ask=0.44" in result.output
    assert "All checks passed. Safe to go live." in result.output


def test_test_connectivity_prints_raw_failure_and_continues(tmp_path: Path, monkeypatch) -> None:
    class FakePolymarketClient:
        def get_api_keys(self):
            return [{"key": "poly-key"}]

        def get_order_book(self, token_id):
            return SimpleNamespace(
                bids=[SimpleNamespace(price="0.42", size="12")],
                asks=[SimpleNamespace(price="0.44", size="10")],
            )

    monkeypatch.setattr(
        maintenance_mod,
        "_load_limitless_credentials",
        lambda: {
            "key_id": "limitless-key",
            "key_secret": "bad-secret",
            "wallet_address": "0xWallet",
        },
    )
    monkeypatch.setattr(
        maintenance_mod,
        "_load_polymarket_credentials",
        lambda: {
            "private_key": "0xprivate",
            "api_key": "abcdefghi",
            "api_secret": "secret",
            "api_passphrase": "pass",
        },
    )
    monkeypatch.setattr(
        maintenance_mod,
        "_build_polymarket_client",
        lambda settings, creds: FakePolymarketClient(),
    )
    monkeypatch.setattr(
        maintenance_mod,
        "_first_relationship_token",
        lambda data_root: "0x123456789",
    )

    env = {
        **_env_for(tmp_path),
        "POLYMARKET_ARB_LIMITLESS_HOST": "https://limitless.example",
    }
    with respx.mock(assert_all_called=True) as router:
        router.get("https://limitless.example/profiles/public/0xWallet").mock(
            return_value=Response(401, text="bad signature")
        )
        router.get("https://limitless.example/markets/active").mock(
            return_value=Response(
                200,
                json={
                    "data": [
                        {
                            "slug": "will-x-happen",
                            "title": "Will X happen?",
                            "address": "0xABCDEF123456789",
                            "marketType": "single",
                            "prices": [0.51, 0.49],
                        }
                    ]
                },
            )
        )
        result = CliRunner().invoke(
            cli,
            ["maintenance", "test-connectivity"],
            env=env,
        )

    assert result.exit_code == 1
    assert "[✗] Limitless auth" in result.output
    assert "status=401" in result.output
    assert "body=bad signature" in result.output
    assert "[✓] Limitless market" in result.output
    assert "[✓] Polymarket auth" in result.output
    assert "[✓] Polymarket book" in result.output
    assert "All checks passed" not in result.output
