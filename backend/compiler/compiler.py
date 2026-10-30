"""Governed SQL compiler generating parameterized, read-only queries with allowlisted identifiers."""
from __future__ import annotations
from backend.observability import traced_stage
import re
from typing import Any
from backend.catalog.schema import JoinCardinality, ApprovedJoinEdge
from backend.compiler.ast import (
    PhysicalQueryPackage,
    SelectQuery,
    JoinClause,
    OrderByItem,
    RawExpr,
    BaseSQLRenderer,
    SQLiteRenderer,
)
from backend.semantic.plan import ResolvedSemanticPlan


class QueryCompilationError(Exception):
    """Raised when compilation violates security or schema constraints."""
    pass


class GovernedQueryCompiler:
    """Builds parameterized, read-only SQL queries strictly from ResolvedSemanticPlan."""

    IDENTIFIER_REGEX = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    FORBIDDEN_KEYWORDS = [
        "DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "TRUNCATE",
        "EXEC", "EXECUTE", "CREATE", "GRANT", "REVOKE", ";", "--", "/*"
    ]

    def __init__(self, dialect: str = "sqlite", timeout_seconds: int = 30):
        self.dialect = dialect
        self.timeout_seconds = timeout_seconds

    def _find_path(
        self,
        edges: tuple[ApprovedJoinEdge, ...] | list[ApprovedJoinEdge],
        start_table: str,
        target_table: str,
        via_path: tuple[str, ...] = (),
    ) -> list[ApprovedJoinEdge]:
        """Find approved join edges connecting start_table and target_table, respecting via_path if declared."""
        if start_table == target_table:
            return []

        if via_path:
            full_seq = list(via_path)
            prefix = self._find_path(edges, start_table, full_seq[0]) if full_seq[0] != start_table else []
            res_edges = list(prefix)
            for i in range(len(full_seq) - 1):
                t1, t2 = full_seq[i], full_seq[i + 1]
                step_edge = next(
                    (e for e in edges if (e.from_table == t1 and e.to_table == t2) or (e.from_table == t2 and e.to_table == t1)),
                    None,
                )
                if step_edge and step_edge not in res_edges:
                    res_edges.append(step_edge)
            if res_edges:
                return res_edges

        adj: dict[str, list[tuple[str, ApprovedJoinEdge]]] = {}
        for edge in edges:
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

        return []

    @traced_stage('query_compilation')
    def compile(self, plan: ResolvedSemanticPlan) -> PhysicalQueryPackage:
        """Compile a ResolvedSemanticPlan into a PhysicalQueryPackage with bound parameters."""
        parameters: dict[str, Any] = {}

        # 1. Allowlist and validate base table and tenant isolation column
        base_tbl = plan.base_table
        self._validate_identifier(base_tbl.table_name)
        self._validate_identifier(base_tbl.tenant_column)

        # Mandatory tenant filter parameter
        parameters["tenant_id"] = plan.tenant_id

        # Security check: User filters cannot override or target tenant isolation column
        for f in plan.filters:
            if f.dimension.lower() in ("tenant_id", base_tbl.tenant_column.lower()):
                raise QueryCompilationError("Security violation: user filters cannot target tenant isolation column.")

        # 2. Select and Grouping expressions
        select_parts = []
        group_by_parts = []
        dimension_tables = {d.table for d in plan.dimensions}

        # Check if any child table is joined with UNALLOCATED policy
        use_unallocated_grouping = (
            plan.fan_out_protection_required and plan.unmatched_parent_behavior == "UNALLOCATED"
        )

        for dim in plan.dimensions:
            self._validate_identifier(dim.table)
            self._validate_identifier(dim.column)
            if use_unallocated_grouping and dim.table != base_tbl.table_name:
                col_ref = f"COALESCE({dim.table}.{dim.column}, 'Unallocated')"
                select_parts.append(f"{col_ref} AS {dim.column}")
                group_by_parts.append(col_ref)
            else:
                col_ref = f"{dim.table}.{dim.column}"
                select_parts.append(col_ref)
                group_by_parts.append(col_ref)

        # 3. Aggregation expression with join fan-out prevention
        measure_col = plan.measure.column
        self._validate_identifier(measure_col)
        fam = getattr(plan.family, "value", plan.family)
        agg_type = "AVG" if (fam == "AVERAGE" and plan.measure.aggregation.value != "COUNT_DISTINCT") else plan.measure.aggregation.value

        if plan.fan_out_protection_required and len(plan.join_edges) > 0:
            # Find the edge connecting directly to the base_table with correct orientation
            child_edge = next(
                (e for e in plan.join_edges if e.from_table == base_tbl.table_name or e.to_table == base_tbl.table_name),
                plan.join_edges[0]
            )
            if child_edge.from_table == base_tbl.table_name:
                child_table = child_edge.to_table
                child_to_col = child_edge.to_column
                parent_from_col = child_edge.from_column
            else:
                child_table = child_edge.from_table
                child_to_col = child_edge.from_column
                parent_from_col = child_edge.to_column

            # Correlated child subquery MUST enforce tenant scope
            child_tbl_def = next((t for t in plan.required_tables if t.table_name == child_table), None)
            child_tenant_pred = (
                f" AND {child_table}.{child_tbl_def.tenant_column} = :tenant_id"
                if (child_tbl_def and child_tbl_def.tenant_column) else ""
            )

            if agg_type == "SUM":
                # Guard against division by zero; avoid floating-point '* 1.0' by using CAST(... AS NUMERIC)
                subquery_cnt = (
                    f"(SELECT COUNT(*) FROM {child_table} "
                    f"WHERE {child_table}.{child_to_col} = {base_tbl.table_name}.{parent_from_col}{child_tenant_pred})"
                )
                metric_expr = (
                    f"SUM(CASE WHEN {subquery_cnt} > 0 "
                    f"THEN DECIMAL_DIV({base_tbl.table_name}.{measure_col}, {subquery_cnt}) "
                    f"ELSE {base_tbl.table_name}.{measure_col} END)"
                )
            elif agg_type == "COUNT":
                pk_col = plan.base_table.primary_key[0] if plan.base_table.primary_key else "id"
                metric_expr = f"COUNT(DISTINCT {base_tbl.table_name}.{pk_col})"
            else:
                metric_expr = f"{agg_type}({base_tbl.table_name}.{measure_col})"
        else:
            if agg_type == "COUNT_DISTINCT":
                metric_expr = f"COUNT(DISTINCT {base_tbl.table_name}.{measure_col})"
            else:
                metric_expr = f"{agg_type}({base_tbl.table_name}.{measure_col})"

        select_parts.append(f"{metric_expr} AS metric_value")
        if plan.measure.protected_entity_column:
            self._validate_identifier(plan.measure.protected_entity_column)
            select_parts.append(f"COUNT(DISTINCT {base_tbl.table_name}.{plan.measure.protected_entity_column}) AS protected_entity_count")

        # 4. FROM & JOIN clauses
        # If UNALLOCATED behavior is requested for child tables, use LEFT JOIN
        from_clause = f"FROM {base_tbl.table_name}"
        join_clauses = []
        joined_tables = {base_tbl.table_name}
        target_period_table = getattr(plan, "period_table", None) or base_tbl.table_name
        target_period_col = getattr(plan, "period_column", None) or "period"

        # Determine all tables that need to be reachable for dimensions and period
        target_tables = set(dimension_tables)
        if target_period_table and target_period_table != base_tbl.table_name:
            target_tables.add(target_period_table)

        tables_to_join = {base_tbl.table_name}
        for t in target_tables:
            path = self._find_path(plan.join_edges, base_tbl.table_name, t)
            for edge in path:
                tables_to_join.add(edge.from_table)
                tables_to_join.add(edge.to_table)

        remaining_edges = list(plan.join_edges)
        while remaining_edges:
            progressed = False
            for edge in list(remaining_edges):
                self._validate_identifier(edge.from_table)
                self._validate_identifier(edge.to_table)
                self._validate_identifier(edge.from_column)
                self._validate_identifier(edge.to_column)

                if edge.from_table in joined_tables and edge.to_table not in joined_tables:
                    source_tbl = edge.from_table
                    source_col = edge.from_column
                    new_tbl = edge.to_table
                    new_col = edge.to_column
                    effective_cardinality = edge.cardinality
                elif edge.to_table in joined_tables and edge.from_table not in joined_tables:
                    source_tbl = edge.to_table
                    source_col = edge.to_column
                    new_tbl = edge.from_table
                    new_col = edge.from_column
                    effective_cardinality = (
                        JoinCardinality.MANY_TO_ONE if edge.cardinality == JoinCardinality.ONE_TO_MANY
                        else JoinCardinality.ONE_TO_MANY if edge.cardinality == JoinCardinality.MANY_TO_ONE
                        else edge.cardinality
                    )
                else:
                    if edge.from_table in joined_tables and edge.to_table in joined_tables:
                        remaining_edges.remove(edge)
                        progressed = True
                    continue

                if (
                    new_tbl in tables_to_join
                    or effective_cardinality == JoinCardinality.MANY_TO_ONE
                    or (use_unallocated_grouping and effective_cardinality == JoinCardinality.ONE_TO_MANY)
                ):
                    new_tbl_def = next((t for t in plan.required_tables if t.table_name == new_tbl), None)
                    join_tenant_pred = (
                        f" AND {new_tbl}.{new_tbl_def.tenant_column} = :tenant_id"
                        if (new_tbl_def and new_tbl_def.tenant_column) else ""
                    )

                    if use_unallocated_grouping and effective_cardinality == JoinCardinality.ONE_TO_MANY:
                        join_type_str = "LEFT"
                    else:
                        join_type_str = edge.join_type.value

                    join_str = (
                        f"{join_type_str} JOIN {new_tbl} "
                        f"ON {source_tbl}.{source_col} = {new_tbl}.{new_col}{join_tenant_pred}"
                    )
                    join_clauses.append(join_str)
                    joined_tables.add(new_tbl)
                    remaining_edges.remove(edge)
                    progressed = True
                else:
                    # Table is not on a dimension/period join path (e.g. child table used only for EXISTS filter)
                    remaining_edges.remove(edge)
                    progressed = True

            if not progressed:
                break

        # 5. WHERE clause (Strict Tenant Scope + User Filters)
        where_clauses = [f"{base_tbl.table_name}.{base_tbl.tenant_column} = :tenant_id"]

        param_idx = 1
        definitions = {d.id: d for d in plan.filter_dimensions}
        for predicate in plan.filters:
            definition = definitions.get(predicate.dimension)
            if definition is None:
                raise QueryCompilationError("Filter has no resolved catalog definition")
            table, column = definition.table, definition.column
            self._validate_identifier(table)
            self._validate_identifier(column)
            if column == base_tbl.tenant_column:
                raise QueryCompilationError("Filters cannot override tenant scope")
            operator = predicate.operator.upper()
            expected = 2 if operator == "BETWEEN" else 1
            if operator == "IN":
                if not predicate.values:
                    raise QueryCompilationError("IN requires at least one value")
            elif len(predicate.values) != expected:
                raise QueryCompilationError("Invalid filter arity")
            names = []
            for value in predicate.values:
                name = f"p_filt_{param_idx}"
                parameters[name] = value
                names.append(":" + name)
                param_idx += 1
            reference = f"{table}.{column}"
            if operator == "IN":
                clause = f"{reference} IN ({', '.join(names)})"
            elif operator == "BETWEEN":
                clause = f"{reference} BETWEEN {names[0]} AND {names[1]}"
            elif operator in ("=", "!=", ">", "<", ">=", "<="):
                clause = f"{reference} {operator} {names[0]}"
            else:
                raise QueryCompilationError("Unsupported filter operator")
            if table not in joined_tables:
                path = self._find_path(plan.join_edges, base_tbl.table_name, table)
                if not path:
                    raise QueryCompilationError("No approved filter join")
                first_edge = path[0]
                if first_edge.from_table == base_tbl.table_name:
                    base_col = first_edge.from_column
                    sub_first_col = first_edge.to_column
                    sub_first_tbl = first_edge.to_table
                else:
                    base_col = first_edge.to_column
                    sub_first_col = first_edge.from_column
                    sub_first_tbl = first_edge.from_table

                sub_first_def = next(t for t in plan.required_tables if t.table_name == sub_first_tbl)
                self._validate_identifier(sub_first_def.tenant_column)
                sub_first_tenant_pred = (
                    f" AND {sub_first_tbl}.{sub_first_def.tenant_column} = :tenant_id"
                    if sub_first_def.tenant_column else ""
                )

                if len(path) == 1:
                    clause = f"EXISTS (SELECT 1 FROM {table} WHERE {table}.{sub_first_col} = {base_tbl.table_name}.{base_col}{sub_first_tenant_pred} AND {clause})"
                else:
                    sub_joins = []
                    sub_joined = {sub_first_tbl}
                    for sub_edge in path[1:]:
                        if sub_edge.from_table in sub_joined and sub_edge.to_table not in sub_joined:
                            s_tbl, s_col, n_tbl, n_col = sub_edge.from_table, sub_edge.from_column, sub_edge.to_table, sub_edge.to_column
                        else:
                            s_tbl, s_col, n_tbl, n_col = sub_edge.to_table, sub_edge.to_column, sub_edge.from_table, sub_edge.from_column
                        n_def = next((t for t in plan.required_tables if t.table_name == n_tbl), None)
                        t_pred = f" AND {n_tbl}.{n_def.tenant_column} = :tenant_id" if (n_def and n_def.tenant_column) else ""
                        sub_joins.append(f"JOIN {n_tbl} ON {s_tbl}.{s_col} = {n_tbl}.{n_col}{t_pred}")
                        sub_joined.add(n_tbl)
                    sub_joins_str = (" " + " ".join(sub_joins)) if sub_joins else ""
                    clause = f"EXISTS (SELECT 1 FROM {sub_first_tbl}{sub_joins_str} WHERE {sub_first_tbl}.{sub_first_col} = {base_tbl.table_name}.{base_col}{sub_first_tenant_pred} AND {clause})"
            where_clauses.append(clause)

        # Current period filter if specified
        if plan.current_period:
            p_period = f"p_period_{param_idx}"
            parameters[p_period] = plan.current_period
            param_idx += 1
            period_tbl_def = next((t for t in plan.required_tables if t.table_name == target_period_table), None)
            period_col_type = period_tbl_def.columns.get(target_period_col) if period_tbl_def and period_tbl_def.columns else None
            if getattr(period_col_type, "value", period_col_type) in ("DATE", "DATETIME"):
                where_clauses.append(f"substr({target_period_table}.{target_period_col}, 1, {len(str(plan.current_period))}) = :{p_period}")
            else:
                where_clauses.append(f"{target_period_table}.{target_period_col} = :{p_period}")

        # Construct SelectQuery AST
        ast_select_items = [RawExpr(p) for p in select_parts]
        ast_joins = []
        for j in join_clauses:
            parts = j.split(" ON ", 1)
            prefix_parts = parts[0].split()
            j_type = prefix_parts[0]
            j_table = prefix_parts[2]
            ast_joins.append(JoinClause(join_type=j_type, table=j_table, condition=RawExpr(parts[1])))

        ast_where = [RawExpr(w) for w in where_clauses]
        ast_group_by = [RawExpr(g) for g in group_by_parts]

        # Determine ORDER BY (Ranking by metric vs standard dimension order)
        ast_order_by = []
        if getattr(plan, "order_by_metric", False):
            direction = "DESC" if getattr(plan, "order_direction", "DESC").upper() == "DESC" else "ASC"
            ast_order_by.append(OrderByItem(expr=RawExpr("metric_value"), direction=direction))
        elif group_by_parts:
            for g in group_by_parts:
                ast_order_by.append(OrderByItem(expr=RawExpr(g), direction="ASC"))

        # Determine LIMIT
        ast_limit = None
        if getattr(plan, "limit", None) is not None:
            p_limit = f"p_limit_{param_idx}"
            parameters[p_limit] = int(plan.limit)
            param_idx += 1
            ast_limit = f":{p_limit}"

        query_ast = SelectQuery(
            select_items=tuple(ast_select_items),
            from_table=base_tbl.table_name,
            joins=tuple(ast_joins),
            where_predicates=tuple(ast_where),
            group_by=tuple(ast_group_by),
            order_by=tuple(ast_order_by),
            limit=ast_limit,
        )

        renderer = self._get_renderer(self.dialect)
        compiled_sql = renderer.render(query_ast)

        # Sanity check: Ensure no forbidden keywords or multi-statements injected
        self._assert_read_only(compiled_sql)

        return PhysicalQueryPackage(
            sql=compiled_sql,
            parameters=parameters,
            dialect=self.dialect,
            timeout_seconds=self.timeout_seconds,
            is_aggregate=True,
        )

    def _get_renderer(self, dialect: str) -> BaseSQLRenderer:
        """Get dialect-specific AST SQL renderer."""
        if dialect == "sqlite":
            return SQLiteRenderer()
        return SQLiteRenderer()

    def _validate_identifier(self, ident: str) -> None:
        """Allowlist identifier check."""
        if not ident or not self.IDENTIFIER_REGEX.match(ident):
            raise QueryCompilationError(f"Disallowed identifier '{ident}' violates allowlist.")

    def _assert_read_only(self, sql: str) -> None:
        """Enforce strictly read-only parameterized query syntax."""
        upper_sql = sql.upper()
        if ";" in sql:
            raise QueryCompilationError("Multi-statement queries containing ';' are strictly forbidden.")
        for kw in self.FORBIDDEN_KEYWORDS:
            if kw != ";" and re.search(rf"\b{re.escape(kw)}\b", upper_sql):
                raise QueryCompilationError(f"Forbidden SQL keyword or token detected: '{kw}'.")
