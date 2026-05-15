from __future__ import annotations

from decimal import Decimal

import pytest
import respx
import yaml
from httpx import Response

from polymarket_arb.compliance.geo_check import GeoChecker
from polymarket_arb.http.client import AsyncHttpClient
from polymarket_arb.risk.checks import (
    BalanceAvailableCheck,
    KillSwitchOffCheck,
    ManualApprovalCheck,
    OrderbookFreshnessCheck,
    OrdersAllowedFlagCheck,
    PaperModeNotActiveCheck,
    RiskLimitsCheck,
    StrategyApprovedCheck,
    VPSReachableCheck,
    default_checks,
)
from polymarket_arb.risk.models import (
    OrderIntent,
    PreflightContext,
    RiskLimits,
)
from polymarket_arb.risk.preflight import PreflightGate
from polymarket_arb.storage.parquet.account_events import ParquetRiskSnapshotsRepository


def _ctx(strategy_id=None, order=None, paper=False, limits=None):
    return PreflightContext(
        strategy_id=strategy_id, order=order, paper_mode=paper,
        limits=limits or RiskLimits(),
    )


@pytest.mark.asyncio
async def test_orders_allowed_flag_blocks_when_false(settings):
    chk = OrdersAllowedFlagCheck(settings)
    res = await chk.check(_ctx())
    assert not res.passed
    assert "orders_allowed=false" in (res.reason or "")


@pytest.mark.asyncio
async def test_orders_allowed_flag_passes_when_true(settings):
    settings.orders_allowed = True
    res = await OrdersAllowedFlagCheck(settings).check(_ctx())
    assert res.passed


@pytest.mark.asyncio
async def test_kill_switch_blocks_when_file_present(tmp_data_root):
    ks = tmp_data_root / ".killswitch"
    ks.write_text("stop")
    res = await KillSwitchOffCheck(ks).check(_ctx())
    assert not res.passed


@pytest.mark.asyncio
async def test_kill_switch_passes_when_absent(tmp_data_root):
    res = await KillSwitchOffCheck(tmp_data_root / ".killswitch").check(_ctx())
    assert res.passed


@pytest.mark.asyncio
async def test_paper_mode_blocks_when_active():
    res = await PaperModeNotActiveCheck().check(_ctx(paper=True))
    assert not res.passed


@pytest.mark.asyncio
async def test_vps_reachable_passes_in_test():
    res = await VPSReachableCheck().check(_ctx())
    assert res.passed


@pytest.mark.asyncio
async def test_strategy_approved_skip_when_no_strategy(tmp_path):
    p = tmp_path / "approved.yaml"
    p.write_text(yaml.safe_dump({"approved": []}))
    res = await StrategyApprovedCheck(p).check(_ctx(strategy_id=None))
    assert res.passed


@pytest.mark.asyncio
async def test_strategy_approved_blocks_unknown(tmp_path):
    p = tmp_path / "approved.yaml"
    p.write_text(yaml.safe_dump({"approved": []}))
    res = await StrategyApprovedCheck(p).check(_ctx(strategy_id="some_strat"))
    assert not res.passed


@pytest.mark.asyncio
async def test_risk_limits_blocks_oversized_stake():
    order = OrderIntent(
        id="o1", strategy_id="s", token_id="t",
        side="buy", price=Decimal("0.5"), size=Decimal("10"),  # stake = 5.0
        quote_age_ms=100, spread_pct=Decimal("0.01"),
        available_balance_usdc=Decimal("100"),
    )
    limits = RiskLimits(max_stake_usdc_per_market=Decimal("1.0"))
    res = await RiskLimitsCheck().check(_ctx(order=order, limits=limits))
    assert not res.passed


@pytest.mark.asyncio
async def test_risk_limits_passes_within_limits():
    order = OrderIntent(
        id="o1", strategy_id="s", token_id="t",
        side="buy", price=Decimal("0.5"), size=Decimal("1"),  # stake = 0.5
        quote_age_ms=100, spread_pct=Decimal("0.01"),
        available_balance_usdc=Decimal("100"),
    )
    res = await RiskLimitsCheck().check(_ctx(order=order))
    assert res.passed


@pytest.mark.asyncio
async def test_balance_blocks_unknown_balance():
    order = OrderIntent(id="o", strategy_id="s", token_id="t",
                        side="buy", price=Decimal("0.5"), size=Decimal("1"),
                        quote_age_ms=100, spread_pct=Decimal("0.01"),
                        available_balance_usdc=None)
    res = await BalanceAvailableCheck().check(_ctx(order=order))
    assert not res.passed


@pytest.mark.asyncio
async def test_orderbook_freshness_blocks_stale():
    order = OrderIntent(id="o", strategy_id="s", token_id="t",
                        side="buy", price=Decimal("0.5"), size=Decimal("1"),
                        quote_age_ms=10_000, spread_pct=Decimal("0.01"),
                        available_balance_usdc=Decimal("100"))
    res = await OrderbookFreshnessCheck().check(
        _ctx(order=order, limits=RiskLimits(max_quote_age_ms=5_000)))
    assert not res.passed


@pytest.mark.asyncio
async def test_manual_approval_consumes_token(tmp_data_root):
    order = OrderIntent(id="ord-x", strategy_id="s", token_id="t",
                        side="buy", price=Decimal("0.5"), size=Decimal("1"),
                        quote_age_ms=100, spread_pct=Decimal("0.01"),
                        available_balance_usdc=Decimal("100"))
    token_path = tmp_data_root / ".live_approved_ord-x"
    token_path.write_text("ok")
    chk = ManualApprovalCheck(tmp_data_root)
    res = await chk.check(_ctx(order=order))
    assert res.passed
    assert not token_path.exists()  # consumed
    res2 = await chk.check(_ctx(order=order))
    assert not res2.passed


@pytest.mark.asyncio
async def test_full_gate_writes_audit_row(settings, tmp_data_root):
    """End-to-end: gate evaluation writes one risk_snapshots row."""
    async with AsyncHttpClient(settings.http) as http, respx.mock() as router:
        router.get("https://ip.example/primary").mock(
            return_value=Response(200, json={"ip": "203.0.113.42"}))
        router.get("https://ip.example/secondary").mock(
            return_value=Response(200, json={"ip": "203.0.113.42", "country_iso": "DE"}))

        geo = GeoChecker(settings, http)
        repo = ParquetRiskSnapshotsRepository(tmp_data_root)
        approved_path = tmp_data_root / "approved.yaml"
        approved_path.write_text(yaml.safe_dump({"approved": []}))

        gate = PreflightGate(
            checks=default_checks(
                settings=settings, geo_checker=geo,
                killswitch_path=tmp_data_root / ".killswitch",
                approved_path=approved_path,
            ),
            risk_repo=repo,
        )
        report = await gate.evaluate(_ctx(strategy_id=None, order=None))
        # orders_allowed=false → expected to fail; that's fine. The point is
        # the audit row was written.
        assert not report.passed
        recent = repo.recent(limit=5)
        assert len(recent) == 1
        assert recent[0].overall == "FAIL"
        names = {c["name"] for c in recent[0].checks}
        assert "orders_allowed_flag" in names
        assert "kill_switch_off" in names
        assert "egress_ip_whitelist" in names


@pytest.mark.asyncio
async def test_token_only_when_all_checks_pass(settings, tmp_data_root):
    """Flip orders_allowed=true and verify a real token is issued."""
    settings.orders_allowed = True
    async with AsyncHttpClient(settings.http) as http, respx.mock() as router:
        router.get("https://ip.example/primary").mock(
            return_value=Response(200, json={"ip": "203.0.113.42"}))
        router.get("https://ip.example/secondary").mock(
            return_value=Response(200, json={"ip": "203.0.113.42", "country_iso": "DE"}))

        geo = GeoChecker(settings, http)
        repo = ParquetRiskSnapshotsRepository(tmp_data_root)
        approved_path = tmp_data_root / "approved.yaml"
        approved_path.write_text(yaml.safe_dump({"approved": []}))

        gate = PreflightGate(
            checks=default_checks(
                settings=settings, geo_checker=geo,
                killswitch_path=tmp_data_root / ".killswitch",
                approved_path=approved_path,
            ),
            risk_repo=repo,
        )
        # No order proposed → checks 6-10 short-circuit PASS, gate issues a token.
        token = await gate.assert_can_trade(_ctx(strategy_id=None, order=None))
        assert token.token_id
        assert token.report.passed
