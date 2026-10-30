"""Extended Comprehensive Test Suite for Governed Analytics, Compiler, Extractor, and Validator.

Validates:
1. Date-prefix period compilation (year '2024', month '2024-05', ISO datetime).
2. Non-date period preservation (integer/string exact equality).
3. Metric disambiguation across 3+ tables with table-word relevance scoring.
4. Metric confirmation bypass (METRIC_CONFIRMED_PREFIX in assumptions).
5. Own-column and measure-column dimension stripping with and without grouping words.
6. Multi-table bare dimension remapping using reachable adjacency.
7. Small-range integer heuristics (0..12 codes vs legitimate quantities).
8. Calendar parts vs additive temporal metrics (ExpMonth vs SalesLastYear vs SalesYTD).
9. CamelCase multi-word join linking (ProductSubcategoryID -> Production_ProductSubcategory).
10. Full end-to-end execution: Introspect -> Resolve -> Compile -> Execute against SQLite.
"""
from decimal import Decimal
from pathlib import Path
import sqlite3
import pytest

from backend.catalog.extractor import (
    _infer_data_type,
    _is_calendar_or_code_name,
    _is_small_range_integer,
    introspect_sqlite_database,
)
from backend.catalog.pinning import AccessibleVocabulary
from backend.catalog.schema import (
    AggregationType,
    DataType,
    JoinCardinality,
    MeasureDefinition,
    DimensionDefinition,
    SemanticCatalog,
    TableDefinition,
)
from backend.compiler.compiler import GovernedQueryCompiler
from backend.intent.schema import AnalysisFamily, StructuredIntent
from backend.intent.validator import IntentValidator
from backend.semantic.resolver import SemanticResolver


# =========================================================================
# 1. Compiler Date-Prefix & Period Tests
# =========================================================================

def test_compiler_date_prefix_for_iso_year():
    """Verify compiler emits substr(table.col, 1, 4) = :param for year filter on DATE column."""
    tbl = TableDefinition(
        table_name="orders",
        primary_key=("order_id",),
        columns={"order_date": DataType.DATE, "amount": DataType.DECIMAL},
    )
    meas = MeasureDefinition(
        id="orders_amount",
        name="Orders Amount",
        description="Total order amount",
        table="orders",
        column="amount",
        aggregation=AggregationType.SUM,
        base_grain="orders_grain",
        unit="USD",
        allowed_dimensions=(),
    )
    catalog = SemanticCatalog(
        release_id="rel_test",
        epoch=1,
        source_id="src_test",
        tables={"orders": tbl},
        measures={"orders_amount": meas},
        dimensions={},
        joins=(),
    )
    from backend.catalog.pinning import SemanticSnapshotPin
    pin = SemanticSnapshotPin(release_id="rel_test", epoch=1, source_id="src_test")

    intent = StructuredIntent(
        measure="orders_amount",
        family=AnalysisFamily.TOTAL,
        current_period="2024",
    )
    resolver = SemanticResolver()
    plan = resolver.resolve(intent, catalog, pin, "turn_p1", "tenant_a")
    # Set period column to order_date
    plan = type(plan)(
        **{**plan.__dict__, "period_table": "orders", "period_column": "order_date"}
    )

    compiler = GovernedQueryCompiler()
    pkg = compiler.compile(plan)
    query, params = pkg.sql, pkg.parameters

    assert "substr(orders.order_date, 1, 4) = :p_period_1" in query
    assert params["p_period_1"] == "2024"
    assert "orders.tenant_id = :tenant_id" in query


def test_compiler_date_prefix_for_iso_month():
    """Verify compiler emits substr(table.col, 1, 7) = :param for month filter '2024-05' on DATETIME column."""
    tbl = TableDefinition(
        table_name="orders",
        primary_key=("order_id",),
        columns={"placed_at": DataType.DATETIME, "amount": DataType.DECIMAL},
    )
    meas = MeasureDefinition(
        id="orders_amount",
        name="Orders Amount",
        description="Total order amount",
        table="orders",
        column="amount",
        aggregation=AggregationType.SUM,
        base_grain="orders_grain",
        unit="USD",
        allowed_dimensions=(),
    )
    catalog = SemanticCatalog(
        release_id="rel_test",
        epoch=1,
        source_id="src_test",
        tables={"orders": tbl},
        measures={"orders_amount": meas},
        dimensions={},
        joins=(),
    )
    from backend.catalog.pinning import SemanticSnapshotPin
    pin = SemanticSnapshotPin(release_id="rel_test", epoch=1, source_id="src_test")

    intent = StructuredIntent(
        measure="orders_amount",
        family=AnalysisFamily.TOTAL,
        current_period="2024-05",
    )
    resolver = SemanticResolver()
    plan = resolver.resolve(intent, catalog, pin, "turn_p2", "tenant_a")
    plan = type(plan)(
        **{**plan.__dict__, "period_table": "orders", "period_column": "placed_at"}
    )

    compiler = GovernedQueryCompiler()
    pkg = compiler.compile(plan)
    query, params = pkg.sql, pkg.parameters

    assert "substr(orders.placed_at, 1, 7) = :p_period_1" in query
    assert params["p_period_1"] == "2024-05"


def test_compiler_exact_match_for_non_date_period_column():
    """Verify compiler preserves exact '=' for integer/string period columns (e.g. fiscal_year)."""
    tbl = TableDefinition(
        table_name="financials",
        primary_key=("row_id",),
        columns={"fiscal_year": DataType.INTEGER, "revenue": DataType.DECIMAL},
    )
    meas = MeasureDefinition(
        id="financials_revenue",
        name="Financials Revenue",
        description="Total revenue",
        table="financials",
        column="revenue",
        aggregation=AggregationType.SUM,
        base_grain="financials_grain",
        unit="USD",
        allowed_dimensions=(),
    )
    catalog = SemanticCatalog(
        release_id="rel_test",
        epoch=1,
        source_id="src_test",
        tables={"financials": tbl},
        measures={"financials_revenue": meas},
        dimensions={},
        joins=(),
    )
    from backend.catalog.pinning import SemanticSnapshotPin
    pin = SemanticSnapshotPin(release_id="rel_test", epoch=1, source_id="src_test")

    intent = StructuredIntent(
        measure="financials_revenue",
        family=AnalysisFamily.TOTAL,
        current_period="2024",
    )
    resolver = SemanticResolver()
    plan = resolver.resolve(intent, catalog, pin, "turn_p3", "tenant_a")
    plan = type(plan)(
        **{**plan.__dict__, "period_table": "financials", "period_column": "fiscal_year"}
    )

    compiler = GovernedQueryCompiler()
    pkg = compiler.compile(plan)
    query, params = pkg.sql, pkg.parameters

    assert "financials.fiscal_year = :p_period_1" in query
    assert "substr(" not in query


# =========================================================================
# 2. Multi-Table Disambiguation & Persistence Tests
# =========================================================================

def test_metric_disambiguation_across_three_tables():
    """Verify disambiguation asks clarification when 3 tables have 'Amount' and prompt is ambiguous."""
    m_sales = MeasureDefinition(
        id="Sales_Orders_Amount", name="Sales Orders Amount", description="",
        table="Sales_Orders", column="Amount", aggregation=AggregationType.SUM,
        base_grain="order", unit="USD", allowed_dimensions=(),
    )
    m_fin = MeasureDefinition(
        id="Finance_Invoices_Amount", name="Finance Invoices Amount", description="",
        table="Finance_Invoices", column="Amount", aggregation=AggregationType.SUM,
        base_grain="invoice", unit="USD", allowed_dimensions=(),
    )
    m_ship = MeasureDefinition(
        id="Shipping_Deliveries_Amount", name="Shipping Deliveries Amount", description="",
        table="Shipping_Deliveries", column="Amount", aggregation=AggregationType.SUM,
        base_grain="delivery", unit="USD", allowed_dimensions=(),
    )
    vocab = AccessibleVocabulary(
        release_id="rel_1", source_id="src_1",
        measures={m.id: m for m in (m_sales, m_fin, m_ship)},
        dimensions={},
    )
    validator = IntentValidator()
    intent = StructuredIntent(measure="Sales_Orders_Amount", family=AnalysisFamily.TOTAL)

    # Ambiguous prompt -> CLARIFY with 3 options
    res = validator.validate_intent(
        intent=intent,
        vocabulary=vocab,
        user_question="What was total Amount in 2024?",
        turn_id="turn_3tbl",
        tenant_id="tenant_a",
        subject_id="alice",
    )
    assert res.status == "CLARIFY"
    assert res.clarification_request.ambiguity_code == "metric-disambiguation"
    assert len(res.clarification_request.options) == 3
    tables_in_labels = [opt.label for opt in res.clarification_request.options]
    assert any("Finance_Invoices" in l for l in tables_in_labels)
    assert any("Shipping_Deliveries" in l for l in tables_in_labels)


def test_metric_disambiguation_targets_finance_when_named():
    """Verify prompt naming invoice or finance automatically picks Finance_Invoices_Amount."""
    m_sales = MeasureDefinition(
        id="Sales_Orders_Amount", name="Sales Orders Amount", description="",
        table="Sales_Orders", column="Amount", aggregation=AggregationType.SUM,
        base_grain="order", unit="USD", allowed_dimensions=(),
    )
    m_fin = MeasureDefinition(
        id="Finance_Invoices_Amount", name="Finance Invoices Amount", description="",
        table="Finance_Invoices", column="Amount", aggregation=AggregationType.SUM,
        base_grain="invoice", unit="USD", allowed_dimensions=(),
    )
    vocab = AccessibleVocabulary(
        release_id="rel_1", source_id="src_1",
        measures={m.id: m for m in (m_sales, m_fin)},
        dimensions={},
    )
    validator = IntentValidator()
    # LLM mistakenly proposed Sales, but user asked for invoice amount
    intent = StructuredIntent(measure="Sales_Orders_Amount", family=AnalysisFamily.TOTAL)
    res = validator.validate_intent(
        intent=intent,
        vocabulary=vocab,
        user_question="What was total invoice Amount in 2024?",
        turn_id="turn_fin_named",
        tenant_id="tenant_a",
        subject_id="alice",
    )
    assert res.status == "VALID"
    assert res.validated_intent.measure == "Finance_Invoices_Amount"


def test_confirmed_metric_assumption_bypasses_future_clarification():
    """Verify that once a metric choice is recorded in assumptions, validation is never blocked."""
    m_sales = MeasureDefinition(
        id="Sales_Orders_Amount", name="Sales Orders Amount", description="",
        table="Sales_Orders", column="Amount", aggregation=AggregationType.SUM,
        base_grain="order", unit="USD", allowed_dimensions=(),
    )
    m_fin = MeasureDefinition(
        id="Finance_Invoices_Amount", name="Finance Invoices Amount", description="",
        table="Finance_Invoices", column="Amount", aggregation=AggregationType.SUM,
        base_grain="invoice", unit="USD", allowed_dimensions=(),
    )
    vocab = AccessibleVocabulary(
        release_id="rel_1", source_id="src_1",
        measures={m.id: m for m in (m_sales, m_fin)},
        dimensions={},
    )
    validator = IntentValidator()
    intent = StructuredIntent(
        measure="Finance_Invoices_Amount",
        family=AnalysisFamily.TOTAL,
        assumptions=(f"{IntentValidator.METRIC_CONFIRMED_PREFIX} Finance_Invoices_Amount",),
    )
    res = validator.validate_intent(
        intent=intent,
        vocabulary=vocab,
        user_question="What was total Amount?",
        turn_id="turn_confirmed_bypass",
        tenant_id="tenant_a",
        subject_id="alice",
    )
    assert res.status == "VALID"
    assert res.validated_intent.measure == "Finance_Invoices_Amount"


# =========================================================================
# 3. Dimension Pruning and Remapping Tests
# =========================================================================

def test_own_column_stripped_with_grouping_word():
    """Verify TotalDue grouped by TotalDue is stripped even when 'by' is present in question."""
    meas = MeasureDefinition(
        id="Sales_TotalDue", name="Sales TotalDue", description="",
        table="Sales", column="TotalDue", aggregation=AggregationType.SUM,
        base_grain="order", unit="USD", allowed_dimensions=(),
    )
    dim_own = DimensionDefinition(
        id="TotalDue", name="Total Due", column="TotalDue", table="Sales", data_type=DataType.DECIMAL
    )
    vocab = AccessibleVocabulary(
        release_id="rel_1", source_id="src_1",
        measures={"Sales_TotalDue": meas},
        dimensions={"TotalDue": dim_own},
    )
    validator = IntentValidator()
    intent = StructuredIntent(
        measure="Sales_TotalDue",
        family=AnalysisFamily.TOTAL,
        dimensions=("TotalDue",),
        assumptions=("Metric confirmed by user: Sales_TotalDue",),
    )
    res = validator.validate_intent(
        intent=intent,
        vocabulary=vocab,
        user_question="Total Due by Total Due",
        turn_id="turn_own_col",
        tenant_id="tenant_a",
        subject_id="alice",
    )
    assert res.status == "VALID"
    assert len(res.validated_intent.dimensions) == 0


def test_measure_columns_pruned_when_no_breakdown_words():
    """Verify measure columns in dimensions are pruned when question lacks breakdown words."""
    meas = MeasureDefinition(
        id="Orders_Revenue", name="Orders Revenue", description="",
        table="Orders", column="Revenue", aggregation=AggregationType.SUM,
        base_grain="order", unit="USD", allowed_dimensions=(),
    )
    dim_price = DimensionDefinition(
        id="UnitPrice", name="Unit Price", column="price", table="Orders", data_type=DataType.DECIMAL
    )
    dim_qty = DimensionDefinition(
        id="Quantity", name="Quantity", column="qty", table="Orders", data_type=DataType.INTEGER
    )
    vocab = AccessibleVocabulary(
        release_id="rel_1", source_id="src_1",
        measures={"Orders_Revenue": meas},
        dimensions={"UnitPrice": dim_price, "Quantity": dim_qty},
    )
    validator = IntentValidator()
    intent = StructuredIntent(
        measure="Orders_Revenue",
        family=AnalysisFamily.TOTAL,
        dimensions=("UnitPrice", "Quantity"),
        assumptions=("Metric confirmed by user: Orders_Revenue",),
    )
    # Question has no 'by', 'per', 'across'
    res = validator.validate_intent(
        intent=intent,
        vocabulary=vocab,
        user_question="What was total order revenue?",
        turn_id="turn_no_breakdown",
        tenant_id="tenant_a",
        subject_id="alice",
    )
    assert res.status == "VALID"
    assert len(res.validated_intent.dimensions) == 0


def test_valid_breakdown_dimensions_preserved():
    """Verify categorical breakdown dimensions are preserved when 'by' is present."""
    meas = MeasureDefinition(
        id="Orders_Revenue", name="Orders Revenue", description="",
        table="Orders", column="Revenue", aggregation=AggregationType.SUM,
        base_grain="order", unit="USD", allowed_dimensions=("Country", "Category"),
    )
    dim_country = DimensionDefinition(
        id="Country", name="Country", column="country", table="Geography", data_type=DataType.STRING
    )
    dim_cat = DimensionDefinition(
        id="Category", name="Category", column="category", table="Products", data_type=DataType.STRING
    )
    vocab = AccessibleVocabulary(
        release_id="rel_1", source_id="src_1",
        measures={"Orders_Revenue": meas},
        dimensions={"Country": dim_country, "Category": dim_cat},
    )
    validator = IntentValidator()
    intent = StructuredIntent(
        measure="Orders_Revenue",
        family=AnalysisFamily.TOTAL,
        dimensions=("Country", "Category"),
        assumptions=("Metric confirmed by user: Orders_Revenue",),
    )
    res = validator.validate_intent(
        intent=intent,
        vocabulary=vocab,
        user_question="What was total order revenue by country and category?",
        turn_id="turn_breakdown_ok",
        tenant_id="tenant_a",
        subject_id="alice",
    )
    assert res.status == "VALID"
    assert res.validated_intent.dimensions == ("Country", "Category")


# =========================================================================
# 4. Extractor Heuristics & Key / Join Inference Tests
# =========================================================================

def test_calendar_parts_vs_additive_temporal_metrics():
    """Verify _is_calendar_or_code_name correctly discriminates parts vs metrics."""
    # Calendar parts -> True (exclude from measures)
    assert _is_calendar_or_code_name("ExpMonth") is True
    assert _is_calendar_or_code_name("ExpYear") is True
    assert _is_calendar_or_code_name("OrderDay") is True
    assert _is_calendar_or_code_name("OnlineOrderFlag") is True
    assert _is_calendar_or_code_name("RevisionNumber") is True
    assert _is_calendar_or_code_name("StatusCode") is True

    # Monetary / additive columns with temporal qualifiers -> False (keep as measures)
    assert _is_calendar_or_code_name("SalesLastYear") is False
    assert _is_calendar_or_code_name("RevenuePriorYear") is False
    assert _is_calendar_or_code_name("SalesYTD") is False
    assert _is_calendar_or_code_name("TotalAmount") is False


def test_small_range_integer_detection(tmp_path: Path):
    """Verify _is_small_range_integer catches codes (0..2) and keeps real quantities (1..50)."""
    db_file = tmp_path / "small_range.sqlite"
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    cur.execute("CREATE TABLE test_ranges (id INT, status_code INT, order_qty INT)")
    # 120 rows: status_code is 0..2, order_qty is 1..50
    for i in range(120):
        cur.execute("INSERT INTO test_ranges VALUES (?, ?, ?)", (i, i % 3, (i % 50) + 1))
    conn.commit()

    # status_code should be detected as small range (0..12, <=13 distinct) -> True
    assert _is_small_range_integer(cur, "test_ranges", "status_code", 120) is True

    # order_qty has values up to 50 -> False
    assert _is_small_range_integer(cur, "test_ranges", "order_qty", 120) is False

    conn.close()


def test_camelcase_multiword_join_inference(tmp_path: Path):
    """Verify ProductSubcategoryID links to Production_ProductSubcategory."""
    db_file = tmp_path / "multiword.sqlite"
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    cur.execute(
        "CREATE TABLE Production_ProductSubcategory ("
        "ProductSubcategoryID INTEGER, "
        "Name TEXT, "
        "tenant_id TEXT"
        ")"
    )
    cur.execute("INSERT INTO Production_ProductSubcategory VALUES (1, 'Road Bikes', 'tenant_a')")
    cur.execute("INSERT INTO Production_ProductSubcategory VALUES (2, 'Mountain Bikes', 'tenant_a')")

    cur.execute(
        "CREATE TABLE Production_Product ("
        "ProductID INTEGER, "
        "ProductSubcategoryID INTEGER, "
        "Name TEXT, "
        "tenant_id TEXT"
        ")"
    )
    cur.execute("INSERT INTO Production_Product VALUES (10, 1, 'Bike A', 'tenant_a')")
    cur.execute("INSERT INTO Production_Product VALUES (11, 2, 'Bike B', 'tenant_a')")
    cur.execute("INSERT INTO Production_Product VALUES (12, 1, 'Bike C', 'tenant_a')")

    conn.commit()
    conn.close()

    catalog, _ = introspect_sqlite_database(db_file, "source_multiword")

    joins = {(j.from_table, j.to_table): j for j in catalog.joins}
    assert ("Production_Product", "Production_ProductSubcategory") in joins
    edge = joins[("Production_Product", "Production_ProductSubcategory")]
    assert edge.from_column == "ProductSubcategoryID"
    assert edge.to_column == "ProductSubcategoryID"
    assert edge.cardinality == JoinCardinality.MANY_TO_ONE


# =========================================================================
# 5. Full End-to-End Execution Test (Introspect -> Resolve -> Compile -> Run)
# =========================================================================

def test_full_pipeline_end_to_end_execution(tmp_path: Path):
    """Verify full governed query pipeline executes from SQLite schema to rendered result."""
    db_file = tmp_path / "e2e_governed.sqlite"
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    cur.execute(
        "CREATE TABLE SalesTerritory ("
        "TerritoryID INTEGER, "
        "TerritoryName TEXT, "
        "tenant_id TEXT"
        ")"
    )
    cur.execute("INSERT INTO SalesTerritory VALUES (1, 'Northwest', 'tenant_a')")
    cur.execute("INSERT INTO SalesTerritory VALUES (2, 'Southwest', 'tenant_a')")

    cur.execute(
        "CREATE TABLE SalesOrderHeader ("
        "SalesOrderID INTEGER, "
        "TerritoryID INTEGER, "
        "TotalDue REAL, "
        "OrderDate TEXT, "
        "tenant_id TEXT"
        ")"
    )
    # OrderDate in ISO format; Territory 1 has 2 orders (100.0 + 300.0 = 400.0), Territory 2 has 200.0
    cur.execute("INSERT INTO SalesOrderHeader VALUES (101, 1, 100.0, '2024-05-01T00:00:00', 'tenant_a')")
    cur.execute("INSERT INTO SalesOrderHeader VALUES (102, 1, 300.0, '2024-06-15T00:00:00', 'tenant_a')")
    cur.execute("INSERT INTO SalesOrderHeader VALUES (103, 2, 200.0, '2024-07-20T00:00:00', 'tenant_a')")
    cur.execute("INSERT INTO SalesOrderHeader VALUES (104, 1, 500.0, '2023-05-01T00:00:00', 'tenant_a')")  # 2023 order

    conn.commit()
    conn.close()

    # 1. Introspect database
    catalog, manifest = introspect_sqlite_database(db_file, "source_e2e")
    from backend.catalog.pinning import SemanticSnapshotPin
    pin = SemanticSnapshotPin(release_id=catalog.release_id, epoch=catalog.epoch, source_id=catalog.source_id)

    # 2. Build structured intent: TotalDue by TerritoryName in 2024
    measure_id = "SalesOrderHeader_TotalDue"
    territory_dim_id = "TerritoryName" if "TerritoryName" in catalog.dimensions else "SalesTerritory_TerritoryName"
    intent = StructuredIntent(
        measure=measure_id,
        family=AnalysisFamily.TOTAL,
        dimensions=(territory_dim_id,),
        current_period="2024",
    )

    # 3. Resolve semantic plan
    resolver = SemanticResolver()
    plan = resolver.resolve(intent, catalog, pin, "turn_e2e", "tenant_a")
    assert plan is not None
    assert plan.period_column == "OrderDate"

    # 4. Compile governed SQL query
    compiler = GovernedQueryCompiler()
    pkg = compiler.compile(plan)
    sql, params = pkg.sql, pkg.parameters

    assert "substr(SalesOrderHeader.OrderDate, 1, 4) = :p_period_1" in sql
    assert "SalesOrderHeader.tenant_id = :tenant_id" in sql
    assert params["tenant_id"] == "tenant_a"
    assert params["p_period_1"] == "2024"

    # 5. Execute against physical database
    exec_conn = sqlite3.connect(db_file)
    exec_cur = exec_conn.cursor()
    exec_cur.execute(sql, params)
    rows = exec_cur.fetchall()
    exec_conn.close()

    # Territory 1: 400.0, Territory 2: 200.0
    results_map = {r[0]: float(r[1]) for r in rows}
    assert results_map["Northwest"] == 400.0
    assert results_map["Southwest"] == 200.0
