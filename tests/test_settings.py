from __future__ import annotations

from pathlib import Path

import yaml

from polymarket_arb.settings import load_settings


def test_load_yaml_then_env_override(tmp_path: Path, monkeypatch) -> None:
    cfg_dir = tmp_path / "configs"
    cfg_dir.mkdir()
    (cfg_dir / "dev.yaml").write_text(
        yaml.safe_dump(
            {
                "env": "dev",
                "gamma_host": "https://yaml.host",
                "orders_allowed": False,
                "compliance": {"allowed_egress_countries": ["DE"]},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("POLYMARKET_ARB_GAMMA_HOST", "https://env.host")
    monkeypatch.setenv("POLYMARKET_ARB_ORDERS_ALLOWED", "false")

    s = load_settings(env="dev", config_dir=cfg_dir)

    assert s.gamma_host == "https://env.host"  # env wins
    assert s.compliance.allowed_egress_countries == ["DE"]
    assert s.orders_allowed is False


def test_default_orders_allowed_false() -> None:
    # Even without any yaml, the default must be safe.
    from polymarket_arb.settings import Settings

    s = Settings()
    assert s.orders_allowed is False
