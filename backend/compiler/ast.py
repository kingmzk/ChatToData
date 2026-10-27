"""Governed SQL AST (Abstract Syntax Tree) and Dialect Renderers."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class PhysicalQueryPackage:
    """Read-only parameterized SQL package with bound parameters and safety attributes."""
    sql: str
    parameters: dict[str, Any]
    dialect: str = "sqlite"  # sqlite, postgresql, tsql
    timeout_seconds: int = 30
    is_aggregate: bool = True


# =============================================================================
# Typed AST Nodes
# =============================================================================

class ASTNode(ABC):
    """Base class for all query AST nodes."""
    pass


class ASTExpr(ASTNode):
    """Base expression node."""
    alias: Optional[str] = None

    def eq(self, other: ASTExpr) -> BinaryOp:
        return BinaryOp(self, "=", other)

    def ne(self, other: ASTExpr) -> BinaryOp:
        return BinaryOp(self, "!=", other)

    def gt(self, other: ASTExpr) -> BinaryOp:
        return BinaryOp(self, ">", other)

    def gte(self, other: ASTExpr) -> BinaryOp:
        return BinaryOp(self, ">=", other)

    def lt(self, other: ASTExpr) -> BinaryOp:
        return BinaryOp(self, "<", other)

    def lte(self, other: ASTExpr) -> BinaryOp:
        return BinaryOp(self, "<=", other)

    def and_(self, other: ASTExpr) -> BinaryOp:
        return BinaryOp(self, "AND", other)

    def or_(self, other: ASTExpr) -> BinaryOp:
        return BinaryOp(self, "OR", other)


@dataclass(frozen=True)
class ColumnRef(ASTExpr):
    """Reference to a table column, e.g. 'table.column'."""
    table: str
    column: str
    alias: Optional[str] = None


@dataclass(frozen=True)
class Literal(ASTExpr):
    """Literal scalar constant."""
    value: Any
    alias: Optional[str] = None


@dataclass(frozen=True)
class ParamRef(ASTExpr):
    """Named bound parameter placeholder, e.g. ':p_param'."""
    name: str
    alias: Optional[str] = None


@dataclass(frozen=True)
class RawExpr(ASTExpr):
    """Pre-validated raw SQL expression snippet."""
    text: str
    alias: Optional[str] = None


@dataclass(frozen=True)
class CoalesceExpr(ASTExpr):
    """COALESCE(expr, default_expr)."""
    expr: ASTExpr
    default_expr: ASTExpr
    alias: Optional[str] = None


@dataclass(frozen=True)
class AggregateExpr(ASTExpr):
    """Aggregate function expression, e.g. SUM(col), COUNT(DISTINCT col)."""
    agg_func: str
    expr: ASTExpr
    is_distinct: bool = False
    alias: Optional[str] = None


@dataclass(frozen=True)
class DecimalDivExpr(ASTExpr):
    """DECIMAL_DIV(numerator, denominator) high-precision division expression."""
    numerator: ASTExpr
    denominator: ASTExpr
    alias: Optional[str] = None


@dataclass(frozen=True)
class CaseWhenExpr(ASTExpr):
    """CASE WHEN condition THEN then_expr ELSE else_expr END."""
    condition: ASTExpr
    then_expr: ASTExpr
    else_expr: ASTExpr
    alias: Optional[str] = None


@dataclass(frozen=True)
class BinaryOp(ASTExpr):
    """Binary operation, e.g. 'left = right' or 'left AND right'."""
    left: ASTExpr
    operator: str
    right: ASTExpr
    alias: Optional[str] = None


@dataclass(frozen=True)
class BetweenOp(ASTExpr):
    """'expr BETWEEN low AND high' predicate."""
    expr: ASTExpr
    low: ASTExpr
    high: ASTExpr
    alias: Optional[str] = None


@dataclass(frozen=True)
class InOp(ASTExpr):
    """'expr IN (v1, v2, ...)' predicate."""
    expr: ASTExpr
    values: tuple[ASTExpr, ...]
    alias: Optional[str] = None


@dataclass(frozen=True)
class SubqueryExpr(ASTExpr):
    """Scalar or correlated subquery expression '(SELECT ...)'."""
    query: SelectQuery
    alias: Optional[str] = None


@dataclass(frozen=True)
class ExistsExpr(ASTExpr):
    """'EXISTS (SELECT ...)' semi-join expression."""
    subquery: SelectQuery
    alias: Optional[str] = None


@dataclass(frozen=True)
class JoinClause(ASTNode):
    """Table join declaration."""
    join_type: str  # INNER, LEFT
    table: str
    condition: ASTExpr


@dataclass(frozen=True)
class OrderByItem(ASTNode):
    """ORDER BY expression and direction."""
    expr: ASTExpr
    direction: str = "ASC"  # ASC, DESC


@dataclass(frozen=True)
class SelectQuery(ASTNode):
    """Full governed SELECT query AST."""
    select_items: tuple[ASTExpr, ...]
    from_table: str
    joins: tuple[JoinClause, ...] = ()
    where_predicates: tuple[ASTExpr, ...] = ()
    group_by: tuple[ASTExpr, ...] = ()
    order_by: tuple[OrderByItem, ...] = ()
    limit: Optional[int | str] = None


# =============================================================================
# Dialect Renderers
# =============================================================================

class BaseSQLRenderer(ABC):
    """Abstract SQL renderer converting AST nodes into parameterized SQL."""

    @abstractmethod
    def render(self, query: SelectQuery) -> str:
        """Render SelectQuery AST to SQL string."""
        pass

    @abstractmethod
    def render_expr(self, expr: ASTExpr) -> str:
        """Render single ASTExpr to SQL string."""
        pass


class SQLiteRenderer(BaseSQLRenderer):
    """SQLite dialect SQL renderer."""

    def render(self, query: SelectQuery) -> str:
        lines: list[str] = []

        # SELECT
        rendered_items = [self.render_expr(item) for item in query.select_items]
        lines.append(f"SELECT {', '.join(rendered_items)}")

        # FROM
        lines.append(f"FROM {query.from_table}")

        # JOINs
        for j in query.joins:
            cond_str = self.render_expr(j.condition)
            lines.append(f"{j.join_type} JOIN {j.table} ON {cond_str}")

        # WHERE
        if query.where_predicates:
            pred_strs = [self.render_expr(p) for p in query.where_predicates]
            lines.append(f"WHERE {' AND '.join(pred_strs)}")

        # GROUP BY
        if query.group_by:
            gb_strs = [self.render_expr(g) for g in query.group_by]
            lines.append(f"GROUP BY {', '.join(gb_strs)}")

        # ORDER BY
        if query.order_by:
            ob_strs = [f"{self.render_expr(o.expr)} {o.direction.upper()}" for o in query.order_by]
            lines.append(f"ORDER BY {', '.join(ob_strs)}")

        # LIMIT
        if query.limit is not None:
            if isinstance(query.limit, ASTExpr):
                lines.append(f"LIMIT {self.render_expr(query.limit)}")
            else:
                lines.append(f"LIMIT {query.limit}")

        return "\n".join(lines)

    def render_expr(self, expr: ASTExpr) -> str:
        res = self._render_expr_core(expr)
        if expr.alias:
            return f"{res} AS {expr.alias}"
        return res

    def _render_expr_core(self, expr: ASTExpr) -> str:
        if isinstance(expr, ColumnRef):
            if not expr.table:
                return expr.column
            return f"{expr.table}.{expr.column}"
        if isinstance(expr, ParamRef):
            return f":{expr.name}"
        if isinstance(expr, Literal):
            if isinstance(expr.value, str):
                escaped = expr.value.replace("'", "''")
                return f"'{escaped}'"
            return str(expr.value)
        if isinstance(expr, RawExpr):
            return expr.text
        if isinstance(expr, CoalesceExpr):
            return f"COALESCE({self.render_expr(expr.expr)}, {self.render_expr(expr.default_expr)})"
        if isinstance(expr, AggregateExpr):
            distinct_str = "DISTINCT " if expr.is_distinct else ""
            return f"{expr.agg_func}({distinct_str}{self.render_expr(expr.expr)})"
        if isinstance(expr, DecimalDivExpr):
            return f"DECIMAL_DIV({self.render_expr(expr.numerator)}, {self.render_expr(expr.denominator)})"
        if isinstance(expr, CaseWhenExpr):
            return (
                f"CASE WHEN {self.render_expr(expr.condition)} "
                f"THEN {self.render_expr(expr.then_expr)} "
                f"ELSE {self.render_expr(expr.else_expr)} END"
            )
        if isinstance(expr, BinaryOp):
            return f"{self.render_expr(expr.left)} {expr.operator} {self.render_expr(expr.right)}"
        if isinstance(expr, BetweenOp):
            return f"{self.render_expr(expr.expr)} BETWEEN {self.render_expr(expr.low)} AND {self.render_expr(expr.high)}"
        if isinstance(expr, InOp):
            val_strs = ", ".join(self.render_expr(v) for v in expr.values)
            return f"{self.render_expr(expr.expr)} IN ({val_strs})"
        if isinstance(expr, SubqueryExpr):
            sub_sql = self.render(expr.query)
            return f"({sub_sql})"
        if isinstance(expr, ExistsExpr):
            sub_sql = self.render(expr.subquery)
            return f"EXISTS ({sub_sql})"

        raise TypeError(f"Unknown ASTExpr node type: {type(expr)}")
