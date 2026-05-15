from __future__ import annotations

from polymarket_arb.logging_setup import configure_logging
from polymarket_arb.settings import Settings


def test_configure_creates_log_dir(tmp_path) -> None:
    log_path = tmp_path / "out" / "log.jsonl"
    s = Settings()
    s.logging.json_log_path = log_path
    configure_logging(s)
    assert log_path.parent.exists()
