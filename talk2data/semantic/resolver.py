"""Semantic resolver mapping validated intent to ResolvedSemanticPlan with fan-out prevention."""
from __future__ import annotations
from decimal import Decimal
import uuid
from talk2data.catalog.schema import SemanticCatalog, JoinCardinality
from talk2data.catalog.pinning import SemanticSnapshotPin
from talk2data.intent.schema import StructuredIntent, AnalysisFamily
from talk2data.semantic.plan import ResolvedSemanticPlan


class SemanticResolutionError(Exception):
    """Raised when semantic resolution fails due to missing join paths or grain mismatch."""
    pass


class SemanticResolver:
    """Governed resolver creating trusted ResolvedSemanticPlan intermediate representation."""

    def resolve(
        self,
        intent: StructuredIntent,
        catalog: SemanticCatalog,
        pin: SemanticSnapshotPin,
        turn_id: str,
        tenant_id: str,
    ) -> ResolvedSemanticPlan:
        """Deterministically resolve validated intent against pinned catalog."""
        if not intent.measure or intent.measure not in catalog.measures:
            raise SemanticResolutionError(f"Measure '{intent.measure}' not found in pinned catalog.")

        measure_def = catalog.measures[intent.measure]
        if measure_def.table not in catalog.tables:
            raise SemanticResolutionError(f"Base table '{measure_def.table}' not found in catalog.")
        base_table = catalog.tables[measure_def.table]
        if intent.family == AnalysisFamily.AVERAGE and measure_def.aggregation.value != "AVG":
            raise SemanticResolutionError("Average requests require an approved average metric")
        if intent.family == AnalysisFamily.CONTRIBUTION and measure_def.aggregation.value not in ("SUM", "COUNT"):
            raise SemanticResolutionError("Contribution requires an approved additive metric")

        resolved_dims = []
        for d_id in intent.dimensions:
            if d_id not in catalog.dimensions:
                raise SemanticResolutionError(f"Dimension '{d_id}' not found in catalog.")
            resolved_dims.append(catalog.dimensions[d_id])

        if intent.family == AnalysisFamily.COMPARISON and intent.current_period and intent.comparison_period:
            # Time identifies each comparison population; it is not a category to join across periods.
            period_dimension = catalog.default_period_dimension
            resolved_dims = [d for d in resolved_dims if d.id != period_dimension]

        # Identify required tables and approved join edges
        required_tables = [base_table]
        join_edges = []
        fan_out_risk = False

        for dim in resolved_dims:
            if dim.table != base_table.table_name:
                # Find approved join edge
                matched_edge = None
                for edge in catalog.joins:
                    if (edge.from_table == base_table.table_name and edge.to_table == dim.table) or \
                       (edge.from_table == dim.table and edge.to_table == base_table.table_name):
                        matched_edge = edge
                        break

                if not matched_edge:
                    raise SemanticResolutionError(
                        f"No approved join path between base table '{base_table.table_name}' "
                        f"and dimension table '{dim.table}'."
                    )

                join_edges.append(matched_edge)
                dim_table = catalog.tables.get(dim.table)
                if dim_table and dim_table not in required_tables:
                    required_tables.append(dim_table)

                # Check cardinality for fan-out risk
                # If base table is at order grain and joined table is at item grain (ONE_TO_MANY), fan out occurs
                if matched_edge.from_table == base_table.table_name and matched_edge.cardinality == JoinCardinality.ONE_TO_MANY:
                    if dim_table and measure_def.base_grain != dim_table.grain:
                        if measure_def.allocation_policy != "EXPLICIT_EQUAL_SPLIT":
                            raise SemanticResolutionError(
                                f"Metric '{measure_def.name}' (defined at grain '{measure_def.base_grain}') cannot be aggregated "
                                f"across child dimension '{dim.name}' (table '{dim.table}', grain '{dim_table.grain}') because no explicit "
                                f"allocation policy is configured in the catalog. Equal division is not assumed. "
                                f"Please query an approved item-level revenue metric or configure an explicit allocation policy."
                            )
                        if measure_def.aggregation.value not in ("SUM", "COUNT"):
                            raise SemanticResolutionError("Child allocation is unsupported for this aggregation")
                        fan_out_risk = True

        # Check filters for required join edges
        for f in intent.filters:
            f_dim = catalog.dimensions.get(f.dimension)
            if f_dim and f_dim.table != base_table.table_name:
                matched_edge = None
                for edge in catalog.joins:
                    if (edge.from_table == base_table.table_name and edge.to_table == f_dim.table) or \
                       (edge.from_table == f_dim.table and edge.to_table == base_table.table_name):
                        matched_edge = edge
                        break

                if not matched_edge:
                    raise SemanticResolutionError(
                        f"No approved join path between base table '{base_table.table_name}' "
                        f"and filter dimension table '{f_dim.table}'."
                    )

                if matched_edge not in join_edges:
                    join_edges.append(matched_edge)
                f_table = catalog.tables.get(f_dim.table)
                if f_table and f_table not in required_tables:
                    required_tables.append(f_table)

        # Resolve period table and approved join if period is requested
        period_table = base_table.table_name
        period_column = "period"
        if intent.current_period or intent.comparison_period:
            p_dim_name = catalog.default_period_dimension or "period"
            p_dim = catalog.dimensions.get(p_dim_name)
            if p_dim:
                period_table = p_dim.table
                period_column = p_dim.column
                if period_table != base_table.table_name:
                    matched_edge = None
                    for edge in catalog.joins:
                        if (edge.from_table == base_table.table_name and edge.to_table == period_table) or \
                           (edge.from_table == period_table and edge.to_table == base_table.table_name):
                            matched_edge = edge
                            break

                    if not matched_edge:
                        raise SemanticResolutionError(
                            f"No approved join path between base table '{base_table.table_name}' and period table '{period_table}'."
                        )

                    if matched_edge not in join_edges:
                        join_edges.append(matched_edge)
                    p_tbl = catalog.tables.get(period_table)
                    if p_tbl and p_tbl not in required_tables:
                        required_tables.append(p_tbl)

        # For FORECAST and ANOMALY: ensure period dimension is present to pull time series aggregates
        if intent.family in (AnalysisFamily.FORECAST, AnalysisFamily.ANOMALY, AnalysisFamily.TREND) and not resolved_dims:
            p_dim_name = catalog.default_period_dimension or next(
                (d.id for d in catalog.dimensions.values() if "period" in d.id.lower() or "year" in d.id.lower() or "date" in d.id.lower()),
                None,
            )
            if p_dim_name and p_dim_name in catalog.dimensions:
                p_dim = catalog.dimensions[p_dim_name]
                resolved_dims.append(p_dim)
                p_tbl = catalog.tables.get(p_dim.table)
                if p_tbl and p_tbl not in required_tables:
                    required_tables.append(p_tbl)

        # Disclose scope & allocation semantics in assumptions
        assumptions = list(intent.assumptions)
        if fan_out_risk and measure_def.allocation_policy:
            if measure_def.unmatched_parent_behavior == "UNALLOCATED":
                assumptions.append(
                    f"Scope disclosure: Parent metric '{measure_def.name}' allocated across child items using explicit catalog policy '{measure_def.allocation_policy}'. Orders without line items are grouped under 'Unallocated'."
                )
            else:
                assumptions.append(
                    f"Scope disclosure: Parent metric '{measure_def.name}' allocated across child items using explicit catalog policy '{measure_def.allocation_policy}'. Orders without line items are excluded."
                )

        # Grouping columns
        grouping_cols = [f"{d.table}.{d.column}" for d in resolved_dims]

        # Aggregation expression
        agg_func = measure_def.aggregation.value
        if agg_func == "COUNT_DISTINCT":
            agg_expr = f"COUNT(DISTINCT {base_table.table_name}.{measure_def.column})"
        else:
            agg_expr = f"{agg_func}({base_table.table_name}.{measure_def.column})"

        plan_id = f"plan_{uuid.uuid4().hex[:12]}"
        return ResolvedSemanticPlan(
            plan_id=plan_id,
            turn_id=turn_id,
            tenant_id=tenant_id,
            measure=measure_def,
            family=intent.family or AnalysisFamily.TOTAL,
            dimensions=tuple(resolved_dims),
            filters=intent.filters,
            filter_dimensions=tuple(catalog.dimensions[f.dimension] for f in intent.filters),
            current_period=intent.current_period,
            comparison_period=intent.comparison_period,
            base_table=base_table,
            required_tables=tuple(required_tables),
            join_edges=tuple(join_edges),
            fan_out_protection_required=fan_out_risk,
            aggregation_expression=agg_expr,
            grouping_columns=tuple(grouping_cols),
            units=measure_def.unit,
            currency=measure_def.currency,
            assumptions=tuple(assumptions),
            allocation_policy=measure_def.allocation_policy,
            unmatched_parent_behavior=measure_def.unmatched_parent_behavior,
            period_table=period_table,
            period_column=period_column,
            horizon=intent.horizon,
            scenario_model=intent.scenario_model,
            adjustments=dict(intent.adjustments),
            target_metric=intent.target_metric,
            target_value=Decimal(str(intent.target_value)) if intent.target_value is not None else None,
            adjustable_driver=intent.adjustable_driver,
            bounds=intent.bounds,
            time_series_policy=catalog.time_series_policies.get(intent.measure) if intent.measure else None,
            scenario_model_def=catalog.scenario_models.get(intent.scenario_model) if intent.scenario_model else None,
        )

