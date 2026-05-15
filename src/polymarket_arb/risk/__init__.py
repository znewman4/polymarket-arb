"""Preflight gate + risk checks. The gate is the only producer of
PreflightToken; the (Phase 10) order client is the only consumer. Together
they make order placement a compile-time impossibility outside the gate."""

from .models import (
    CheckResult,
    OrderIntent,
    PreflightContext,
    PreflightFailure,
    PreflightReport,
    PreflightToken,
    RiskLimits,
)
from .preflight import PreflightCheck, PreflightGate

__all__ = [
    "CheckResult",
    "OrderIntent",
    "PreflightCheck",
    "PreflightContext",
    "PreflightFailure",
    "PreflightGate",
    "PreflightReport",
    "PreflightToken",
    "RiskLimits",
]
