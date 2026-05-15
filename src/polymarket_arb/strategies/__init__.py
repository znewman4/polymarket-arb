"""Simulated research strategies package."""

from .models import RelationshipBacktestConfig
from .nesting_contradiction import evaluate_relationship_at_tick

__all__ = ["RelationshipBacktestConfig", "evaluate_relationship_at_tick"]
