"""Unit and integration tests for the 6 high-value query-generation improvements."""
import json
import sqlite3
from decimal import Decimal
import pytest

from backend.catalog.pinning import SemanticSnapshotPin
from backend.catalog.schema import (
    AggregationType,
    ApprovedJoinEdge,
    DataType,
    DimensionDefinition,
    JoinCardinality,
    JoinType,
    MeasureDefinition,
    SemanticCatalog,
    TableDefinition,
)
from backend.compiler.ast import SelectQuery, ColumnRef, ParamRef, SQLiteRenderer, OrderByItem
from backend.compiler.compiler import GovernedQueryCompiler
from backend.domain.facts import ProtectedFact, SealedEvidencePackage
from backend.execution.gateway import GenericSourceGateway
from backend.execution.kernel import DeterministicAnalysisKernel
from backend.intent.parser import IntentParser
from backend.intent.schema import AnalysisFamily, StructuredIntent
from backend.intent.validator import IntentValidator
from backend.presentation.fallback import DeterministicPresentationFallback
from backend.semantic.plan import ResolvedSemanticPlan
from backend.semantic.resolver import SemanticResolver


# ---------------------------------------------------------------------------
# Test 1: Ranking, Top-N, and Limit in Intent, Plan, and Compiler
# ---------------------------------------------------------------------------

def test_intent_parsing_and_validation_ranking():
    """Verify intent parser and validator capture limit, order_by_metric, and direction."""
    parser = IntentParser()
    raw = {
        "measure": "revenue",
        "family": "TOTAL",
        "dimensions": ["product_name"],
        "limit": 5,
        "order_by_metric": True,
        "order_direction": "DESC",
    }
    intent = parser._dict_to_intent(raw)
    assert intent.limit == 5
    assert intent.order_by_metric is True
    assert intent.order_direction == "DESC"

    # Auto-detection from JSON parsing
    res = parser.parse_with_bounded_retry('{"measure": "revenue", "family": "TOTAL", "limit": 10}')
    assert res.success is True
    assert res.intent.limit == 10
    assert res.intent.order_by_metric is True
    assert res.intent.order_direction == "DESC"


def test_compiler_renders_ast_with_metric_order_and_limit():
    """Verify compiler and SQLiteRenderer emit ORDER BY metric_value DESC LIMIT :p_limit."""
    table = TableDefinition(
        table_name="orders",
        grain="id",
        tenant_column="tenant_id",
        primary_key=("id",),
        columns={"id": DataType.STRING, "revenue": DataType.DECIMAL, "product": DataType.STRING, "tenant_id": DataType.STRING},
    )
    measure = MeasureDefinition(
        id="revenue",
        name="Revenue",
        description="Total revenue",
        table="orders",
        column="revenue",
        aggregation=AggregationType.SUM,
        base_grain="id",
        unit="USD",
        allowed_dimensions=("product",),
        allowed_families=("TOTAL",),
    )
    dimension = DimensionDefinition(
        id="product",
        name="Product",
        table="orders",
        column="product",
        data_type=DataType.STRING,
    )
    plan = ResolvedSemanticPlan(
        plan_id="plan_top_5",
        turn_id="turn_top_5",
        tenant_id="tenant_1",
        measure=measure,
        family=AnalysisFamily.TOTAL,
        dimensions=(dimension,),
        filters=(),
        current_period=None,
        comparison_period=None,
        base_table=table,
        required_tables=(table,),
        join_edges=(),
        fan_out_protection_required=False,
        aggregation_expression="SUM(orders.revenue)",
        grouping_columns=("orders.product",),
        units="USD",
        limit=5,
        order_by_metric=True,
        order_direction="DESC",
    )
    compiler = GovernedQueryCompiler(dialect="sqlite")
    pkg = compiler.compile(plan)

    assert "ORDER BY metric_value DESC" in pkg.sql
    assert ":p_limit" in pkg.sql
    assert any(k.startswith("p_limit") and v == 5 for k, v in pkg.parameters.items())


# ---------------------------------------------------------------------------
# Test 2: Allocation Policy Method Disclosure in Answer Narrative
# ---------------------------------------------------------------------------

def test_allocation_policy_disclosure_in_presentation():
    """Verify answers disclose equal-split allocation method in summary text."""
    orders_table = TableDefinition(
        table_name="orders",
        grain="order_id",
        tenant_column="tenant_id",
        primary_key=("order_id",),
        columns={"order_id": DataType.STRING, "total_amount": DataType.DECIMAL, "tenant_id": DataType.STRING},
    )
    items_table = TableDefinition(
        table_name="order_items",
        grain="item_id",
        tenant_column="tenant_id",
        primary_key=("item_id",),
        columns={"item_id": DataType.STRING, "order_id": DataType.STRING, "tag": DataType.STRING, "tenant_id": DataType.STRING},
    )
    measure = MeasureDefinition(
        id="order_total",
        name="Order Total",
        description="Total order value",
        table="orders",
        column="total_amount",
        aggregation=AggregationType.SUM,
        base_grain="order_id",
        unit="USD",
        allowed_dimensions=("item_tag",),
        allowed_families=("TOTAL",),
        allocation_policy="EXPLICIT_EQUAL_SPLIT",
    )
    dimension = DimensionDefinition(
        id="item_tag",
        name="Item Tag",
        table="order_items",
        column="tag",
        data_type=DataType.STRING,
    )
    edge = ApprovedJoinEdge(
        from_table="orders",
        to_table="order_items",
        from_column="order_id",
        to_column="order_id",
        join_type=JoinType.INNER,
        cardinality=JoinCardinality.ONE_TO_MANY,
    )
    catalog = SemanticCatalog(
        release_id="rel_alloc",
        epoch=1,
        source_id="src_alloc",
        tables={"orders": orders_table, "order_items": items_table},
        measures={"order_total": measure},
        dimensions={"item_tag": dimension},
        joins=(edge,),
    )
    pin = SemanticSnapshotPin(release_id="rel_alloc", epoch=1, source_id="src_alloc")
    resolver = SemanticResolver()
    intent = StructuredIntent(measure="order_total", family=AnalysisFamily.TOTAL, dimensions=("item_tag",))
    plan = resolver.resolve(intent=intent, catalog=catalog, pin=pin, turn_id="turn_alloc", tenant_id="tenant_1")

    # Check resolver assumption
    assert any("equal-split allocation" in a for a in plan.assumptions)

    # Check presentation renderer embeds this explanation in summary text
    fact = ProtectedFact(
        fact_id="fact_1",
        metric="order_total",
        calculation_type="TOTAL",
        value=Decimal("175000.00"),
        unit="USD",
        dimension_values={"item_tag": "Bestseller"},
        assumptions=list(plan.assumptions),
    )
    evidence = SealedEvidencePackage.seal(
        package_id="pkg_test",
        turn_id="turn_alloc",
        tenant_id="tenant_1",
        semantic_release_id="rel_alloc",
        facts=[fact],
    )
    fallback = DeterministicPresentationFallback()
    view = fallback.render(evidence)

    assert "equal-split allocation" in view.summary_text.lower()
    assert "divided evenly across its matched child items" in view.summary_text.lower()


# ---------------------------------------------------------------------------
# Test 3: Exact Decimal Precision in DECIMAL_DIV and DECIMAL_SUM / SUM
# ---------------------------------------------------------------------------

def test_decimal_precision_exact_arithmetic():
    """Verify DECIMAL_DIV and SUM handle precision without floating-point drift."""
    conn = sqlite3.connect(":memory:")
    gateway = GenericSourceGateway(connection_or_factory=conn)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE test_data (id INT, val TEXT, divisor TEXT)")
    
    # Insert 10,000 rows where val=1, divisor=10 -> each division is exactly 0.1
    # In IEEE-754 floats: sum(0.1 for _ in range(10000)) can drift or suffer binary float errors.
    cursor.executemany(
        "INSERT INTO test_data VALUES (?, ?, ?)",
        [(i, "1.0000000000", "10") for i in range(10000)]
    )
    conn.commit()

    # Query using DECIMAL_DIV inside SUM
    # Using our custom aggregate registered on connection
    cursor.execute("SELECT SUM(DECIMAL_DIV(val, divisor)) FROM test_data")
    row = cursor.fetchone()
    result = row[0]

    # Result must be exactly 1000.0000000000 without IEEE 754 float drift (e.g. not 999.999999999999)
    d_result = Decimal(str(result))
    assert d_result == Decimal("1000.0000000000")


# ---------------------------------------------------------------------------
# Test 4: Role and Explicit Join Path for Ambiguous Dimensions
# ---------------------------------------------------------------------------

def test_role_and_explicit_join_path_resolution():
    """Verify distinct join paths resolve correctly when dimensions specify join_path."""
    orders = TableDefinition(
        table_name="orders", grain="id", tenant_column="t_id", primary_key=("id",),
        columns={"id": DataType.STRING, "cust_id": DataType.STRING, "supp_id": DataType.STRING, "amt": DataType.DECIMAL, "t_id": DataType.STRING}
    )
    customers = TableDefinition(
        table_name="customers", grain="id", tenant_column="t_id", primary_key=("id",),
        columns={"id": DataType.STRING, "country_id": DataType.STRING, "t_id": DataType.STRING}
    )
    suppliers = TableDefinition(
        table_name="suppliers", grain="id", tenant_column="t_id", primary_key=("id",),
        columns={"id": DataType.STRING, "country_id": DataType.STRING, "t_id": DataType.STRING}
    )
    countries = TableDefinition(
        table_name="countries", grain="id", tenant_column="t_id", primary_key=("id",),
        columns={"id": DataType.STRING, "country_name": DataType.STRING, "t_id": DataType.STRING}
    )
    edges = (
        ApprovedJoinEdge("orders", "customers", "cust_id", "id", JoinType.INNER, JoinCardinality.MANY_TO_ONE),
        ApprovedJoinEdge("orders", "suppliers", "supp_id", "id", JoinType.INNER, JoinCardinality.MANY_TO_ONE),
        ApprovedJoinEdge("customers", "countries", "country_id", "id", JoinType.INNER, JoinCardinality.MANY_TO_ONE),
        ApprovedJoinEdge("suppliers", "countries", "country_id", "id", JoinType.INNER, JoinCardinality.MANY_TO_ONE),
    )
    measure = MeasureDefinition(
        id="spend", name="Spend", description="Order spend", table="orders", column="amt",
        aggregation=AggregationType.SUM, base_grain="id", unit="USD",
        allowed_dimensions=("customer_country", "supplier_country"),
        allowed_families=("TOTAL",),
    )
    dim_cust_country = DimensionDefinition(
        id="customer_country",
        name="Customer Country",
        table="countries",
        column="country_name",
        data_type=DataType.STRING,
        role="customer",
        join_path=("orders", "customers", "countries"),
    )
    dim_supp_country = DimensionDefinition(
        id="supplier_country",
        name="Supplier Country",
        table="countries",
        column="country_name",
        data_type=DataType.STRING,
        role="supplier",
        join_path=("orders", "suppliers", "countries"),
    )
    catalog = SemanticCatalog(
        release_id="rel_roles",
        epoch=1,
        source_id="src_roles",
        tables={"orders": orders, "customers": customers, "suppliers": suppliers, "countries": countries},
        measures={"spend": measure},
        dimensions={"customer_country": dim_cust_country, "supplier_country": dim_supp_country},
        joins=edges,
    )
    pin = SemanticSnapshotPin(release_id="rel_roles", epoch=1, source_id="src_roles")
    resolver = SemanticResolver()
    
    # 1. Resolve customer country
    plan_cust = resolver.resolve(
        intent=StructuredIntent(measure="spend", family=AnalysisFamily.TOTAL, dimensions=("customer_country",)),
        catalog=catalog, pin=pin, turn_id="turn_cust", tenant_id="t1"
    )
    cust_joined_tables = [e.to_table if e.from_table == "orders" else e.to_table for e in plan_cust.join_edges]
    # Check that customers is in join edges and suppliers is not
    all_cust_tables = {e.from_table for e in plan_cust.join_edges} | {e.to_table for e in plan_cust.join_edges}
    assert "customers" in all_cust_tables
    assert "countries" in all_cust_tables
    assert "suppliers" not in all_cust_tables

    # 2. Resolve supplier country
    plan_supp = resolver.resolve(
        intent=StructuredIntent(measure="spend", family=AnalysisFamily.TOTAL, dimensions=("supplier_country",)),
        catalog=catalog, pin=pin, turn_id="turn_supp", tenant_id="t1"
    )
    all_supp_tables = {e.from_table for e in plan_supp.join_edges} | {e.to_table for e in plan_supp.join_edges}
    assert "suppliers" in all_supp_tables
    assert "countries" in all_supp_tables
    assert "customers" not in all_supp_tables


# ---------------------------------------------------------------------------
# Test 5: AST Rendering and Comparison SQL Capture
# ---------------------------------------------------------------------------

def test_ast_select_query_and_sqlite_renderer():
    """Verify SelectQuery AST can be cleanly rendered to SQL."""
    ast = SelectQuery(
        select_items=(
            ColumnRef("dim_customers", "country_name", alias="country"),
            ColumnRef("fact_orders", "amount", alias="metric_value"),
        ),
        from_table="fact_orders",
        joins=(),
        where_predicates=(ColumnRef("fact_orders", "tenant_id").eq(ParamRef("p_tenant")),),
        group_by=(ColumnRef("dim_customers", "country_name"),),
        order_by=(OrderByItem(ColumnRef("", "metric_value"), direction="DESC"),),
        limit=ParamRef("p_limit"),
    )
    renderer = SQLiteRenderer()
    sql = renderer.render(ast)
    assert "SELECT dim_customers.country_name AS country, fact_orders.amount AS metric_value" in sql
    assert "FROM fact_orders" in sql
    assert "WHERE fact_orders.tenant_id = :p_tenant" in sql
    assert "GROUP BY dim_customers.country_name" in sql
    assert "ORDER BY metric_value DESC" in sql
    assert "LIMIT :p_limit" in sql


def test_comparison_captures_sql_query(test_setup):
    """Verify comparison queries populate compiled_sql so sql_query is non-empty in answers."""
    manager = test_setup["manager"]
    actor = test_setup["actor_a"]
    turn, answer = manager.submit_turn(
        actor,
        "sess_comp_test",
        "finance_dw",
        "Compare sales revenue in 2024 vs 2023",
        proposed_llm_json=json.dumps({
            "measure": "sales_revenue",
            "family": "COMPARISON",
            "current_period": "2024",
            "comparison_period": "2023",
        }),
    )
    assert answer is not None
    assert answer.sql_query is not None
    assert "Current Period" in answer.sql_query
    assert "Comparison Period" in answer.sql_query
    assert "2024" in answer.sql_query
    assert "2023" in answer.sql_query
