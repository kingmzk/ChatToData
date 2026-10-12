"""Declarative semantic catalog schema definitions."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class AggregationType(str, Enum):
    SUM = "SUM"
    AVG = "AVG"
    COUNT = "COUNT"
    COUNT_DISTINCT = "COUNT_DISTINCT"
    MIN = "MIN"
    MAX = "MAX"


class DataType(str, Enum):
    INTEGER = "INTEGER"
    DECIMAL = "DECIMAL"
    STRING = "STRING"
    DATE = "DATE"
    DATETIME = "DATETIME"
    BOOLEAN = "BOOLEAN"


class JoinCardinality(str, Enum):
    ONE_TO_ONE = "ONE_TO_ONE"
    MANY_TO_ONE = "MANY_TO_ONE"
    ONE_TO_MANY = "ONE_TO_MANY"


class JoinType(str, Enum):
    INNER = "INNER"
    LEFT = "LEFT"


@dataclass(frozen=True)
class ApprovedJoinEdge:
    """Explicitly approved join relationship between tables with cardinality specification."""
    from_table: str
    to_table: str
    from_column: str
    to_column: str
    join_type: JoinType = JoinType.INNER
    cardinality: JoinCardinality = JoinCardinality.MANY_TO_ONE

    @property
    def has_fan_out_risk(self) -> bool:
        """Indicates if joining along this edge can multiply rows of the from_table."""
        return self.cardinality == JoinCardinality.ONE_TO_MANY


@dataclass(frozen=True)
class TableDefinition:
    """Physical table representation with grain and mandatory tenant isolation column."""
    table_name: str
    schema_name: str = "dbo"
    grain: str = "row"  # e.g., 'order', 'order_item', 'customer'
    tenant_column: str = "tenant_id"
    primary_key: tuple[str, ...] = ("id",)
    columns: dict[str, DataType] = field(default_factory=dict)
    requires_unsupported_disclosure_protection: bool = False


@dataclass(frozen=True)
class MeasureDefinition:
    """Declarative metric specification governed by backend rules."""
    id: str
    name: str
    description: str
    table: str
    column: str
    aggregation: AggregationType
    base_grain: str  # Grain at which this metric is naturally defined
    unit: str
    currency: Optional[str] = None
    allowed_dimensions: tuple[str, ...] = ()
    allowed_families: tuple[str, ...] = ("TOTAL", "AVERAGE", "TREND", "COMPARISON", "CONTRIBUTION")
    zero_denominator_behavior: str = "NULL"  # NULL or ZERO
    sensitivity: str = "INTERNAL"  # PUBLIC, INTERNAL, CONFIDENTIAL, RESTRICTED
    required_role: Optional[str] = None
    requires_repeated_query_protection: bool = False
    protected_entity_column: Optional[str] = None
    min_entities: int = 5
    allocation_policy: Optional[str] = None  # None, 'EXPLICIT_EQUAL_SPLIT', etc.
    unmatched_parent_behavior: str = "EXCLUDE"  # 'EXCLUDE' or 'UNALLOCATED'
    requires_unsupported_disclosure_protection: bool = False


@dataclass(frozen=True)
class DimensionDefinition:
    """Declarative attribute dimension for slicing and filtering."""
    id: str
    name: str
    table: str
    column: str
    data_type: DataType
    hierarchy: Optional[str] = None
    sensitivity: str = "INTERNAL"
    required_role: Optional[str] = None
    allowed_operators: tuple[str, ...] = ("=", "!=", "IN", ">", "<", ">=", "<=", "BETWEEN")


@dataclass(frozen=True)
class ScenarioDriverDefinition:
    """Approved driver variable for declarative scenario and goal-seek models."""
    name: str
    unit: str
    min_value: Optional[Decimal] = None
    max_value: Optional[Decimal] = None
    default_value: Optional[Decimal] = None
    is_adjustable: bool = True


@dataclass(frozen=True)
class ScenarioModelDefinition:
    """Approved declarative scenario model governed by backend catalog rules."""
    model_id: str
    name: str
    description: str
    output_metric: str  # e.g., 'sales_revenue'
    drivers: dict[str, ScenarioDriverDefinition]
    formula_expression: str  # e.g., 'price * quantity'
    fixed_assumptions: tuple[str, ...] = ()
    permitted_adjustments: tuple[str, ...] = ()


@dataclass(frozen=True)
class TimeSeriesPolicy:
    """Governed policy for time-series forecasting and anomaly detection."""
    metric: str
    period_dimension: str
    frequency: str = "MONTH"  # "MONTH", "QUARTER", "YEAR"
    min_history_periods: int = 4
    max_forecast_horizon: int = 12
    allow_missing_as_zero: bool = False
    linear_trend_eligible: bool = True
    lower_bound: Optional[Decimal] = None
    upper_bound: Optional[Decimal] = None


@dataclass(frozen=True)
class SemanticCatalog:
    """Immutable versioned semantic snapshot for a specific data source."""
    release_id: str
    epoch: int
    source_id: str
    tables: dict[str, TableDefinition]
    measures: dict[str, MeasureDefinition]
    dimensions: dict[str, DimensionDefinition]
    joins: tuple[ApprovedJoinEdge, ...] = ()
    default_period_dimension: Optional[str] = None
    timezone: str = "UTC"
    fiscal_year_start_month: int = 1
    scenario_models: dict[str, ScenarioModelDefinition] = field(default_factory=dict)
    time_series_policies: dict[str, TimeSeriesPolicy] = field(default_factory=dict)
    disclosure_policy: Optional[dict] = None

