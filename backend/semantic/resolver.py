"""Semantic resolver mapping validated intent to ResolvedSemanticPlan with fan-out prevention."""
from __future__ import annotations
from decimal import Decimal
import uuid
from backend.catalog.schema import SemanticCatalog, JoinCardinality, ApprovedJoinEdge
from backend.catalog.pinning import SemanticSnapshotPin
from backend.intent.schema import StructuredIntent, AnalysisFamily
from backend.semantic.plan import ResolvedSemanticPlan


class SemanticResolutionError(Exception):
    """Raised when semantic resolution fails due to missing join paths or grain mismatch."""
    pass


class SemanticResolver:
    """Governed resolver creating trusted ResolvedSemanticPlan intermediate representation."""

    def _find_path(
        self,
        catalog: SemanticCatalog,
        start_table: str,
        target_table: str,
        via_path: tuple[str, ...] = (),
    ) -> list[ApprovedJoinEdge]:
        """Find approved join edges connecting start_table and target_table, respecting explicit via_path if declared."""
        if start_table == target_table:
            return []

        if via_path:
            full_seq = list(via_path)
            prefix = self._find_path(catalog, start_table, full_seq[0]) if full_seq[0] != start_table else []
            edges = list(prefix)
            for i in range(len(full_seq) - 1):
                t1, t2 = full_seq[i], full_seq[i + 1]
                step_edge = next(
                    (e for e in catalog.joins if (e.from_table == t1 and e.to_table == t2) or (e.from_table == t2 and e.to_table == t1)),
                    None,
                )
                if not step_edge:
                    raise SemanticResolutionError(f"No approved join edge between '{t1}' and '{t2}' in declared join_path.")
                if step_edge not in edges:
                    edges.append(step_edge)
            return edges

        adj: dict[str, list[tuple[str, ApprovedJoinEdge]]] = {}
        for edge in catalog.joins:
            adj.setdefault(edge.from_table, []).append((edge.to_table, edge))
            adj.setdefault(edge.to_table, []).append((edge.from_table, edge))

        queue: list[tuple[str, list[ApprovedJoinEdge]]] = [(start_table, [])]
        visited = {start_table}

        while queue:
            curr, path = queue.pop(0)
            if curr == target_table:
                return path
            for nxt, edge in adj.get(curr, []):
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append((nxt, path + [edge]))

        raise SemanticResolutionError(
            f"No approved join path between base table '{start_table}' and table '{target_table}'."
        )

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
                path_edges = self._find_path(catalog, base_table.table_name, dim.table, via_path=getattr(dim, "join_path", ()))
                for edge in path_edges:
                    if edge not in join_edges:
                        join_edges.append(edge)
                    for tbl_name in (edge.from_table, edge.to_table):
                        tbl = catalog.tables.get(tbl_name)
                        if tbl and tbl not in required_tables:
                            required_tables.append(tbl)

                dim_table = catalog.tables.get(dim.table)
                for edge in path_edges:
                    if edge.cardinality == JoinCardinality.ONE_TO_MANY:
                        if dim_table and measure_def.base_grain != dim_table.grain:
                            if measure_def.aggregation.value == "COUNT_DISTINCT":
                                # Distinct entity count on base table is mathematically safe across child joins
                                continue
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
                path_edges = self._find_path(catalog, base_table.table_name, f_dim.table, via_path=getattr(f_dim, "join_path", ()))
                for edge in path_edges:
                    if edge not in join_edges:
                        join_edges.append(edge)
                    for tbl_name in (edge.from_table, edge.to_table):
                        tbl = catalog.tables.get(tbl_name)
                        if tbl and tbl not in required_tables:
                            required_tables.append(tbl)

        # Resolve period table and approved join if period is requested
        period_table = base_table.table_name
        period_column = "period"
        if intent.current_period or intent.comparison_period:
            p_dim_name = catalog.default_period_dimension or next(
                (d.id for d in catalog.dimensions.values() if "year" in d.id.lower()),
                next((d.id for d in catalog.dimensions.values() if any(k in d.id.lower() for k in ("period", "date"))), "period"),
            )
            p_dim = catalog.dimensions.get(p_dim_name)
            if p_dim:
                period_table = p_dim.table
                period_column = p_dim.column
                if period_table != base_table.table_name:
                    path_edges = self._find_path(catalog, base_table.table_name, period_table)
                    for edge in path_edges:
                        if edge not in join_edges:
                            join_edges.append(edge)
                        for tbl_name in (edge.from_table, edge.to_table):
                            tbl = catalog.tables.get(tbl_name)
                            if tbl and tbl not in required_tables:
                                required_tables.append(tbl)

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
                if p_dim.table != base_table.table_name:
                    path_edges = self._find_path(catalog, base_table.table_name, p_dim.table)
                    for edge in path_edges:
                        if edge not in join_edges:
                            join_edges.append(edge)
                        for tbl_name in (edge.from_table, edge.to_table):
                            tbl = catalog.tables.get(tbl_name)
                            if tbl and tbl not in required_tables:
                                required_tables.append(tbl)

        # Disclose scope & allocation semantics in assumptions
        assumptions = list(intent.assumptions)
        if fan_out_risk and measure_def.allocation_policy:
            policy_desc = (
                "equal-split allocation: order total divided evenly across its matched child items"
                if measure_def.allocation_policy == "EXPLICIT_EQUAL_SPLIT"
                else f"catalog policy '{measure_def.allocation_policy}'"
            )
            if measure_def.unmatched_parent_behavior == "UNALLOCATED":
                assumptions.append(
                    f"Scope disclosure: Parent metric '{measure_def.name}' allocated across child items using explicit catalog policy '{measure_def.allocation_policy}' ({policy_desc}). Orders without line items are grouped under 'Unallocated'."
                )
            else:
                assumptions.append(
                    f"Scope disclosure: Parent metric '{measure_def.name}' allocated across child items using explicit catalog policy '{measure_def.allocation_policy}' ({policy_desc}). Orders without line items are excluded."
                )

        # Grouping columns
        grouping_cols = [f"{d.table}.{d.column}" for d in resolved_dims]

        # Aggregation expression
        if intent.family == AnalysisFamily.AVERAGE and measure_def.aggregation.value != "AVG":
            agg_func = "AVG"
        else:
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
            limit=intent.limit,
            order_by_metric=bool(intent.order_by_metric) if intent.order_by_metric is not None else False,
            order_direction=intent.order_direction or "DESC",
        )

