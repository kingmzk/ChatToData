"""ResolvedSemanticPlan - intermediate representation between validated intent and SQL compilation."""
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional
from backend.catalog.schema import (
    MeasureDefinition,
    DimensionDefinition,
    TableDefinition,
    ApprovedJoinEdge,
    TimeSeriesPolicy,
    ScenarioModelDefinition,
)
from backend.intent.schema import AnalysisFamily, FilterSpec


@dataclass(frozen=True)
class ResolvedSemanticPlan:
    """Deterministic, validated intermediate representation for physical query generation."""
    plan_id: str
    turn_id: str
    tenant_id: str
    measure: MeasureDefinition
    family: AnalysisFamily
    dimensions: tuple[DimensionDefinition, ...]
    filters: tuple[FilterSpec, ...]
    current_period: Optional[str]
    comparison_period: Optional[str]
    base_table: TableDefinition
    required_tables: tuple[TableDefinition, ...]
    join_edges: tuple[ApprovedJoinEdge, ...]
    fan_out_protection_required: bool
    aggregation_expression: str
    grouping_columns: tuple[str, ...]
    units: str
    currency: Optional[str] = None
    assumptions: tuple[str, ...] = field(default_factory=tuple)
    allocation_policy: Optional[str] = None
    unmatched_parent_behavior: str = "EXCLUDE"
    period_table: Optional[str] = None
    period_column: Optional[str] = None

    # Decision Science Parameters (Phase 3)
    horizon: Optional[int] = None
    scenario_model: Optional[str] = None
    adjustments: dict[str, Any] = field(default_factory=dict)
    target_metric: Optional[str] = None
    target_value: Optional[Decimal] = None
    adjustable_driver: Optional[str] = None
    bounds: Optional[tuple[Decimal, Decimal]] = None
    time_series_policy: Optional[TimeSeriesPolicy] = None
    scenario_model_def: Optional[ScenarioModelDefinition] = None
    filter_dimensions: tuple[DimensionDefinition, ...] = ()

