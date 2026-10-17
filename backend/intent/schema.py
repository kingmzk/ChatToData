"""Structured intent schemas and representation of user analytical questions."""
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Optional


class AnalysisFamily(str, Enum):
    """Governed analytical families supported by backend analysis."""
    TOTAL = "TOTAL"
    AVERAGE = "AVERAGE"
    TREND = "TREND"
    COMPARISON = "COMPARISON"
    CONTRIBUTION = "CONTRIBUTION"
    FORECAST = "FORECAST"
    ANOMALY = "ANOMALY"
    SCENARIO = "SCENARIO"
    GOAL_SEEK = "GOAL_SEEK"


@dataclass(frozen=True)
class FilterSpec:
    """Predicate condition restricted to allowlisted dimensions and operators."""
    dimension: str
    operator: str  # '=', '!=', 'IN', '>', '<', '>=', '<=', 'BETWEEN'
    values: tuple[Any, ...]

    def __post_init__(self):
        valid_ops = {"=", "!=", "IN", ">", "<", ">=", "<=", "BETWEEN"}
        if self.operator.upper() not in valid_ops:
            raise ValueError(f"Operator '{self.operator}' is not permitted.")


@dataclass(frozen=True)
class StructuredIntent:
    """Structured analytical intent proposed from natural language."""
    measure: Optional[str] = None
    family: Optional[AnalysisFamily] = None
    dimensions: tuple[str, ...] = ()
    filters: tuple[FilterSpec, ...] = ()
    current_period: Optional[str] = None
    comparison_period: Optional[str] = None
    ambiguity_code: Optional[str] = None
    assumptions: tuple[str, ...] = ()

    # Decision Science Parameters (Phase 3)
    horizon: Optional[int] = None
    scenario_model: Optional[str] = None
    adjustments: dict[str, Any] = field(default_factory=dict)
    target_metric: Optional[str] = None
    target_value: Optional[Decimal] = None
    adjustable_driver: Optional[str] = None
    bounds: Optional[tuple[Decimal, Decimal]] = None
