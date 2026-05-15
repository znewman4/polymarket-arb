"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from polymarket_arb.logging_setup import reset_logging_for_tests
from polymarket_arb.monitoring import kill_switch
from polymarket_arb.settings import (
    ComplianceSettings,
    HttpSettings,
    LoggingSettings,
    ParquetSettings,
    RateLimitSpec,
    Settings,
    StorageSettings,
)


@pytest.fixture(autouse=True)
def _isolate_globals():
    """Ensure global state (loguru handlers, kill switch latch) doesn't leak
    across tests."""
    reset_logging_for_tests()
    kill_switch.reset_for_tests()
    yield
    reset_logging_for_tests()
    kill_switch.reset_for_tests()


@pytest.fixture
def tmp_data_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    for sub in ("raw", "normalised", "account", "derived", "logs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def settings(tmp_data_root: Path) -> Settings:
    return Settings(
        env="test",
        gamma_host="https://gamma-api.example",
        clob_host="https://clob.example",
        http=HttpSettings(
            connect_timeout_s=2.0,
            read_timeout_s=2.0,
            max_retries=2,
            backoff_initial_s=0.01,
            backoff_max_s=0.05,
            rate_limits={
                "gamma-api.example": RateLimitSpec(capacity=10, refill_per_s=10),
                "clob.example": RateLimitSpec(capacity=10, refill_per_s=10),
                "ip.example": RateLimitSpec(capacity=10, refill_per_s=10),
            },
        ),
        compliance=ComplianceSettings(
            allowed_egress_countries=["DE", "NL", "FR"],
            ip_check_ttl_s=300,
        ),
        storage=StorageSettings(
            data_root=tmp_data_root,
            parquet=ParquetSettings(compression="zstd", row_group_size=1024),
        ),
        logging=LoggingSettings(
            level="WARNING",
            json_log_path=tmp_data_root / "logs" / "test.jsonl",
        ),
        orders_allowed=False,
        ip_provider_primary="https://ip.example/primary",
        ip_provider_secondary="https://ip.example/secondary",
    )
