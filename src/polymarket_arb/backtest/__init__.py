"""Offline replay/backtest foundation.

This package never calls live network APIs and never routes orders. It only
replays locally recorded public data and simulates fills in memory.
"""

from .models import BacktestRunConfig
from .replay_engine import run_backtest

__all__ = ["BacktestRunConfig", "run_backtest"]
