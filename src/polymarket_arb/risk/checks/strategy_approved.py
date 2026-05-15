"""Strategy must be listed in ``approved_strategies.yaml`` AND its module
sha256 must match the recorded hash. Phase 0 ships the file-loading; full
sha-matching enforcement lands in Phase 4."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import yaml

from ..models import CheckResult, CheckStatus, PreflightContext


class StrategyApprovedCheck:
    name = "strategy_approved"

    def __init__(self, approved_path: Path) -> None:
        self._path = approved_path

    def _load(self) -> list[dict]:
        if not self._path.exists():
            return []
        with self._path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        approved = data.get("approved", [])
        return approved if isinstance(approved, list) else []

    @staticmethod
    def _sha256_of_module(module: str) -> str | None:
        spec = importlib.util.find_spec(module)
        if spec is None or spec.origin is None:
            return None
        path = Path(spec.origin)
        if not path.exists():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    async def check(self, ctx: PreflightContext) -> CheckResult:
        # Phase 0-3 has no strategies - the absence of strategy_id means
        # "no order is being placed", which is an automatic pass.
        if ctx.strategy_id is None:
            return CheckResult(self.name, CheckStatus.PASS,
                               detail={"reason": "no strategy_id (research-only)"})
        approved = self._load()
        for entry in approved:
            if entry.get("id") != ctx.strategy_id:
                continue
            module = entry.get("module")
            expected = entry.get("sha256")
            if not module or not expected:
                return CheckResult(self.name, CheckStatus.FAIL,
                                   reason=f"approved entry missing module/sha256 for "
                                          f"{ctx.strategy_id}")
            actual = self._sha256_of_module(module)
            if actual is None:
                return CheckResult(self.name, CheckStatus.FAIL,
                                   reason=f"could not locate module {module}")
            if actual != expected:
                return CheckResult(self.name, CheckStatus.FAIL,
                                   reason=f"sha256 mismatch for {module}: "
                                          f"expected {expected[:8]}…, got {actual[:8]}…")
            return CheckResult(self.name, CheckStatus.PASS,
                               detail={"approver": entry.get("approver")})
        return CheckResult(self.name, CheckStatus.FAIL,
                           reason=f"strategy_id {ctx.strategy_id!r} not in approved list")
