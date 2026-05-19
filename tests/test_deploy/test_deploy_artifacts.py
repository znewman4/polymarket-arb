"""Sanity tests for the VPS deployment artifacts.

These don't actually run Docker or systemd — they verify the files parse,
contain the expected service definitions, honour the safe-by-default contract,
and reference the right CLI commands.  Run on every CI build so a typo in
``deploy/`` can't ship silently.
"""

from __future__ import annotations

import configparser
import os
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY = REPO_ROOT / "deploy"


def test_dockerfile_agent_exists_and_runs_cli() -> None:
    text = (DEPLOY / "Dockerfile.agent").read_text(encoding="utf-8")
    # Same base + entrypoint shape as the repo Dockerfile.
    assert 'FROM python:3.11-slim' in text
    assert 'ENTRYPOINT ["python", "-m", "polymarket_arb.cli"]' in text
    # Healthcheck wires through the live healthcheck shim.
    assert "HEALTHCHECK" in text
    assert "agent-healthcheck" in text


def test_healthcheck_script_exists_and_is_executable() -> None:
    script = DEPLOY / "healthcheck.sh"
    assert script.exists(), "deploy/healthcheck.sh missing"
    assert os.access(script, os.X_OK), "deploy/healthcheck.sh is not executable"
    body = script.read_text(encoding="utf-8")
    # The script must call into the new CLI subcommand, not a hard-coded URL.
    assert "polymarket-arb live healthcheck" in body or "polymarket_arb.cli live healthcheck" in body
    # And explicitly fail on kill-switch or live-config drift.
    assert "kill_switch_active" in body
    assert "paper_mode" in body
    assert "orders_allowed" in body


def test_docker_compose_yaml_parses_and_declares_safe_defaults() -> None:
    compose_path = DEPLOY / "docker-compose.yml"
    data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = data.get("services") or {}
    assert "recorder" in services, "missing recorder service"
    assert "agent" in services, "missing agent service"

    # Recorder hard-codes the safe flags (never flips, even if .env is wrong).
    rec_env = services["recorder"].get("environment", [])
    rec_env_str = "\n".join(rec_env)
    assert "POLYMARKET_ARB_ORDERS_ALLOWED=false" in rec_env_str
    assert "POLYMARKET_ARB_PAPER_MODE=true" in rec_env_str

    # Agent defaults to paper / disallowed when .env doesn't override.
    agent_env = services["agent"].get("environment", [])
    agent_env_str = "\n".join(agent_env)
    assert "POLYMARKET_ARB_PAPER_MODE=${POLYMARKET_ARB_PAPER_MODE:-true}" in agent_env_str
    assert "POLYMARKET_ARB_ORDERS_ALLOWED=${POLYMARKET_ARB_ORDERS_ALLOWED:-false}" in agent_env_str

    # Both restart on crash.
    assert services["recorder"]["restart"] == "unless-stopped"
    assert services["agent"]["restart"] == "unless-stopped"

    # Both bind-mount the same ./data so the kill-switch file is shared.
    rec_vols = services["recorder"]["volumes"]
    agent_vols = services["agent"]["volumes"]
    assert any("/app/data" in v for v in rec_vols)
    assert any("/app/data" in v for v in agent_vols)

    # Agent depends_on recorder (book lake must populate first).
    assert services["agent"].get("depends_on") == ["recorder"]

    # The agent's command runs the new `live agent` CLI.
    agent_cmd = services["agent"]["command"]
    assert "live" in agent_cmd and "agent" in agent_cmd


@pytest.mark.parametrize(
    "unit_name,exec_substr",
    [
        ("polymarket-recorder.service", "record snapshots"),
        ("polymarket-agent.service", "live agent"),
    ],
)
def test_systemd_units_parse_and_use_correct_commands(unit_name, exec_substr) -> None:
    unit = DEPLOY / "systemd" / unit_name
    assert unit.exists(), f"missing systemd unit {unit_name}"
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    # configparser doesn't read multi-line ExecStart well; just check the raw text.
    text = unit.read_text(encoding="utf-8")
    cp.read_string(text)
    assert cp["Service"]["Restart"] == "always"
    assert cp["Service"]["User"] == "polymarket"
    assert "POLYMARKET_ARB_ENV=prod" in cp["Service"]["Environment"] or \
           "POLYMARKET_ARB_ENV=prod" in text
    assert exec_substr in text


def test_recorder_systemd_hard_codes_safe_flags() -> None:
    """The recorder must never run with orders_allowed=true or paper_mode=false,
    even if .env is misconfigured."""
    text = (DEPLOY / "systemd" / "polymarket-recorder.service").read_text(encoding="utf-8")
    assert "POLYMARKET_ARB_ORDERS_ALLOWED=false" in text
    assert "POLYMARKET_ARB_PAPER_MODE=true" in text


def test_env_example_documents_new_flags() -> None:
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for var in (
        "POLYMARKET_ARB_PAPER_MODE",
        "POLYMARKET_ARB_AGENT_POLL_INTERVAL_S",
        "POLYMARKET_ARB_WATCHED_TOKENS",
        "POLYMARKET_ARB_AGENT_STRATEGY",
    ):
        assert var in text, f".env.example missing {var}"
    # PAPER_MODE defaults to true.
    assert "POLYMARKET_ARB_PAPER_MODE=true" in text


def test_deploy_readme_covers_critical_runbook_steps() -> None:
    readme = (DEPLOY / "README.md").read_text(encoding="utf-8")
    for needle in (
        ".killswitch",
        "POLYMARKET_PRIVATE_KEY",
        "paper_mode",
        "live healthcheck",
        "Docker Compose",
        "systemd",
        "polymarket-recorder",
        "polymarket-agent",
    ):
        assert needle in readme, f"deploy/README.md missing reference to {needle!r}"
