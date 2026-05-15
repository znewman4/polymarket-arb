"""The 10 preflight checks.

Phase 0 ships working impls for the foundational five (1-5). Checks 6-10 are
defined here with stub bodies so the gate plumbing is complete; they are
populated with real logic in their respective phases (see docs/trade_gate.md).
"""

from .balance import BalanceAvailableCheck
from .egress_ip import EgressIPWhitelistCheck
from .kill_switch import KillSwitchOffCheck
from .manual_approval import ManualApprovalCheck
from .orderbook_freshness import OrderbookFreshnessCheck
from .orders_allowed import OrdersAllowedFlagCheck
from .paper_mode import PaperModeNotActiveCheck
from .risk_limits import RiskLimitsCheck
from .strategy_approved import StrategyApprovedCheck
from .vps_reachable import VPSReachableCheck

__all__ = [
    "BalanceAvailableCheck",
    "EgressIPWhitelistCheck",
    "KillSwitchOffCheck",
    "ManualApprovalCheck",
    "OrderbookFreshnessCheck",
    "OrdersAllowedFlagCheck",
    "PaperModeNotActiveCheck",
    "RiskLimitsCheck",
    "StrategyApprovedCheck",
    "VPSReachableCheck",
]


def default_checks(
    *,
    settings,
    geo_checker,
    killswitch_path,
    approved_path,
):
    """Return the 10 default checks in evaluation order.

    Convenience constructor used by the CLI. Tests can build custom check lists.
    """

    return [
        VPSReachableCheck(),
        EgressIPWhitelistCheck(geo_checker, settings),
        KillSwitchOffCheck(killswitch_path),
        OrdersAllowedFlagCheck(settings),
        PaperModeNotActiveCheck(),
        StrategyApprovedCheck(approved_path),
        RiskLimitsCheck(),
        BalanceAvailableCheck(),
        OrderbookFreshnessCheck(),
        ManualApprovalCheck(),
    ]
