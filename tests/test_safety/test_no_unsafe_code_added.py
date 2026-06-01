"""Static safety checks for the backfill and reports packages.

Ensures no wallet/signing code, no order placement, no authenticated CLOB
calls, and no chain-of-thought content is introduced.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
_SRC_BACKFILL = _REPO_ROOT / "src" / "polymarket_arb" / "backfill"
_SRC_REPORTS = _REPO_ROOT / "src" / "polymarket_arb" / "reports"
_SRC_RELATIONSHIPS = _REPO_ROOT / "src" / "polymarket_arb" / "relationships"
_SRC_STRATEGIES = _REPO_ROOT / "src" / "polymarket_arb" / "strategies"
_SRC_BACKTEST = _REPO_ROOT / "src" / "polymarket_arb" / "backtest"
_SRC_CONTEXT = _REPO_ROOT / "src" / "polymarket_arb" / "context"
_SRC_CLI = _REPO_ROOT / "src" / "polymarket_arb" / "cli"

_FORBIDDEN_WALLET = re.compile(
    r"\b(private_key|mnemonic|signing|eth_account|web3)\b",
    re.IGNORECASE,
)
_FORBIDDEN_ORDER = re.compile(
    r"\b(place_order|submit_order|OrderClient|signed_order)\b",
    re.IGNORECASE,
)
_FORBIDDEN_AUTH_HEADER = re.compile(
    r"Authorization",
    re.IGNORECASE,
)

_ALLOWED_CLOB_FETCHES = {
    "fetch_book",
    "fetch_books",
    "fetch_price",
    "fetch_midpoint",
    "fetch_spread",
    "fetch_prices_history",
    "fetch_batch_prices_history",
    "fetch_trades",
}
_FORBIDDEN_CLOB_AUTH = re.compile(
    r"\b(fetch_order|place_order|cancel_order|sign_order|signed_order)\b",
    re.IGNORECASE,
)


# The Phase 3 live-trading infrastructure (paper_mode flag) lives in
# ``src/polymarket_arb/live/`` and ``src/polymarket_arb/cli/live.py``.  Those
# files legitimately reference OrderClient + signing as part of the paper-mode
# scaffold and are EXEMPT from the wallet/order safety scan.  Backfill /
# reports / relationships / strategies / backtest / context are still scanned.
_PHASE3_LIVE_EXEMPT = {
    "live.py",        # cli/live.py
    "limitless.py",   # cli/limitless.py — dual-leg arb executor, uses OrderClient
    "maintenance.py", # cli/maintenance.py — live-credential setup + CLOB connectivity check
    "_aliases.py",    # cli aliasing layer
}


def _python_files(path: Path) -> list[Path]:
    return [p for p in path.rglob("*.py") if p.name not in _PHASE3_LIVE_EXEMPT]


def _read_all(paths: list[Path]) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in paths)


def test_no_private_key_or_wallet_code_added():
    sources = (
        _python_files(_SRC_BACKFILL)
        + _python_files(_SRC_REPORTS)
        + _python_files(_SRC_RELATIONSHIPS)
        + _python_files(_SRC_STRATEGIES)
        + _python_files(_SRC_BACKTEST)
        + _python_files(_SRC_CONTEXT)
        + _python_files(_SRC_CLI)
    )
    combined = _read_all(sources)
    match = _FORBIDDEN_WALLET.search(combined)
    assert match is None, f"Forbidden wallet-related token found: {match.group()!r}"


def test_no_place_order_or_order_client_added():
    sources = (
        _python_files(_SRC_BACKFILL)
        + _python_files(_SRC_REPORTS)
        + _python_files(_SRC_RELATIONSHIPS)
        + _python_files(_SRC_STRATEGIES)
        + _python_files(_SRC_BACKTEST)
        + _python_files(_SRC_CONTEXT)
        + _python_files(_SRC_CLI)
    )
    combined = _read_all(sources)
    match = _FORBIDDEN_ORDER.search(combined)
    assert match is None, f"Forbidden order-placement token found: {match.group()!r}"


def test_no_authenticated_clob_calls_in_backfill():
    sources = (
        _python_files(_SRC_BACKFILL)
        + _python_files(_SRC_RELATIONSHIPS)
        + _python_files(_SRC_CONTEXT)
        + _python_files(_SRC_CLI)
    )
    combined = _read_all(sources)
    match = _FORBIDDEN_CLOB_AUTH.search(combined)
    assert match is None, f"Possible authenticated CLOB call found: {match.group()!r}"
    assert "Authorization" not in combined


def test_no_shorting_in_strategies():
    """strategies/ package must never have allow_unsupported_shorting=True or short logic."""
    sources = _python_files(_SRC_STRATEGIES)
    combined = _read_all(sources)
    # The field exists as a safety flag that is always False — that's fine
    # But there must be no code that sets it to True dynamically
    assert 'allow_unsupported_shorting = True' not in combined
    assert 'allow_unsupported_shorting=True' not in combined


def test_no_chain_of_thought_in_reports_or_outputs(tmp_path):
    """Render a minimal report against fixture data and verify no <think> in output."""

    data_root = tmp_path / "data"
    for sub in ("raw", "normalised", "account", "derived", "logs"):
        (data_root / sub).mkdir(parents=True, exist_ok=True)

    from polymarket_arb.reports.historical_dataset_report import (
        generate_historical_dataset_report,
    )
    from polymarket_arb.reports.semantic_quality_report import (
        generate_semantic_quality_report,
    )

    out1 = tmp_path / "r1"
    out2 = tmp_path / "r2"
    p1 = generate_historical_dataset_report(data_root, out1)
    p2 = generate_semantic_quality_report(data_root, out2)

    for p in (p1, p2):
        html = p.read_text(encoding="utf-8")
        assert "<think>" not in html.lower(), (
            f"<think> found in report output: {p}"
        )
