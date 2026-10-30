"""Tests for AdventureWorks-style catalog introspection, key & join inference, and ambiguity resolution."""
import sqlite3
from pathlib import Path
import pytest

from backend.catalog.extractor import introspect_sqlite_database
from backend.catalog.pinning import AccessibleVocabulary
from backend.catalog.schema import AggregationType, JoinCardinality
from backend.intent.schema import AnalysisFamily, StructuredIntent
from backend.intent.validator import IntentValidator


def _vocab(catalog) -> AccessibleVocabulary:
    return AccessibleVocabulary(
        release_id=catalog.release_id,
        source_id=catalog.source_id,
        measures=catalog.measures,
        dimensions=catalog.dimensions,
    )


@pytest.fixture
def catalog_setup(tmp_path: Path):
    db_file = tmp_path / "adventureworks_fixture.sqlite"
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    # 1. Sales_SalesTerritory
    cur.execute(
        "CREATE TABLE Sales_SalesTerritory ("
        "TerritoryID INTEGER, "
        "Name TEXT, "
        "SalesLastYear REAL, "
        "tenant_id TEXT"
        ")"
    )
    cur.execute("INSERT INTO Sales_SalesTerritory VALUES (1, 'Northwest', 50000.0, 'tenant_a')")
    cur.execute("INSERT INTO Sales_SalesTerritory VALUES (2, 'Northeast', 45000.0, 'tenant_a')")

    # 2. Sales_SalesOrderHeader
    cur.execute(
        "CREATE TABLE Sales_SalesOrderHeader ("
        "SalesOrderID INTEGER, "
        "TerritoryID INTEGER, "
        "TotalDue REAL, "
        "OrderDate TEXT, "
        "CreditCeiling REAL, "
        "Status INTEGER, "
        "tenant_id TEXT"
        ")"
    )
    cur.execute("INSERT INTO Sales_SalesOrderHeader VALUES (10, 1, 100.0, '2024-01-01', 5000.0, 1, 'tenant_a')")
    cur.execute("INSERT INTO Sales_SalesOrderHeader VALUES (11, 2, 200.0, '2024-02-01', 5000.0, 1, 'tenant_a')")
    cur.execute("INSERT INTO Sales_SalesOrderHeader VALUES (12, 1, 300.0, '2024-03-01', 5000.0, 1, 'tenant_a')")

    # 3. Sales_SalesOrderDetail (unique SalesOrderDetailID: (1, 10), (2, 10), (3, 11))
    cur.execute(
        "CREATE TABLE Sales_SalesOrderDetail ("
        "SalesOrderDetailID INTEGER, "
        "SalesOrderID INTEGER, "
        "ProductID INTEGER, "
        "LineTotal REAL, "
        "OrderQty INTEGER, "
        "tenant_id TEXT"
        ")"
    )
    cur.execute("INSERT INTO Sales_SalesOrderDetail VALUES (1, 10, 100, 50.0, 1, 'tenant_a')")
    cur.execute("INSERT INTO Sales_SalesOrderDetail VALUES (2, 10, 101, 50.0, 2, 'tenant_a')")
    cur.execute("INSERT INTO Sales_SalesOrderDetail VALUES (3, 11, 100, 200.0, 4, 'tenant_a')")

    # 4. Purchasing_PurchaseOrderHeader
    cur.execute(
        "CREATE TABLE Purchasing_PurchaseOrderHeader ("
        "PurchaseOrderID INTEGER, "
        "TotalDue REAL, "
        "OrderDate TEXT, "
        "tenant_id TEXT"
        ")"
    )
    cur.execute("INSERT INTO Purchasing_PurchaseOrderHeader VALUES (500, 1500.0, '2024-01-10', 'tenant_a')")
    cur.execute("INSERT INTO Purchasing_PurchaseOrderHeader VALUES (501, 2500.0, '2024-02-15', 'tenant_a')")

    # 5. Production_Product
    cur.execute(
        "CREATE TABLE Production_Product ("
        "ProductID INTEGER, "
        "Name TEXT, "
        "ProductSubcategoryID INTEGER, "
        "tenant_id TEXT"
        ")"
    )
    cur.execute("INSERT INTO Production_Product VALUES (100, 'Mountain Bike', 1, 'tenant_a')")
    cur.execute("INSERT INTO Production_Product VALUES (101, 'Road Bike', 1, 'tenant_a')")

    # 6. Production_ProductCostHistory (multiple rows per ProductID -> MANY_TO_ONE)
    cur.execute(
        "CREATE TABLE Production_ProductCostHistory ("
        "ProductID INTEGER, "
        "StartDate TEXT, "
        "EndDate TEXT, "
        "StandardCost REAL, "
        "tenant_id TEXT"
        ")"
    )
    cur.execute("INSERT INTO Production_ProductCostHistory VALUES (100, '2022-01-01', '2022-12-31', 40.0, 'tenant_a')")
    cur.execute("INSERT INTO Production_ProductCostHistory VALUES (100, '2023-01-01', '2023-12-31', 45.0, 'tenant_a')")
    cur.execute("INSERT INTO Production_ProductCostHistory VALUES (101, '2023-01-01', '2023-12-31', 60.0, 'tenant_a')")

    # 7. HumanResources_Department (provides bare Name dimension candidate)
    cur.execute(
        "CREATE TABLE HumanResources_Department ("
        "DepartmentID INTEGER, "
        "Name TEXT, "
        "tenant_id TEXT"
        ")"
    )
    cur.execute("INSERT INTO HumanResources_Department VALUES (1, 'Engineering', 'tenant_a')")
    cur.execute("INSERT INTO HumanResources_Department VALUES (2, 'Sales', 'tenant_a')")

    # 8. Sales_SalesOrderHeaderSalesReason (junction table with non-unique SalesOrderID)
    cur.execute(
        "CREATE TABLE Sales_SalesOrderHeaderSalesReason ("
        "SalesOrderID INTEGER, "
        "SalesReasonID INTEGER, "
        "tenant_id TEXT"
        ")"
    )
    cur.execute("INSERT INTO Sales_SalesOrderHeaderSalesReason VALUES (10, 1, 'tenant_a')")
    cur.execute("INSERT INTO Sales_SalesOrderHeaderSalesReason VALUES (10, 2, 'tenant_a')")
    cur.execute("INSERT INTO Sales_SalesOrderHeaderSalesReason VALUES (11, 2, 'tenant_a')")

    # 9. Calendar and code columns table (ExpMonth, ExpYear, Priority with 150 rows 0..2)
    cur.execute(
        "CREATE TABLE Sales_CreditCard ("
        "CreditCardID INTEGER, "
        "ExpMonth INTEGER, "
        "ExpYear INTEGER, "
        "Priority INTEGER, "
        "tenant_id TEXT"
        ")"
    )
    for i in range(150):
        cur.execute(
            "INSERT INTO Sales_CreditCard VALUES (?, ?, ?, ?, 'tenant_a')",
            (i + 1, (i % 12) + 1, 2026, i % 3),
        )

    conn.commit()
    conn.close()

    catalog, _ = introspect_sqlite_database(db_file, "source_aw_fixture")
    return catalog


@pytest.fixture
def validator():
    return IntentValidator()


def test_camelcase_joins_and_cardinality(catalog_setup):
    """1. CamelCase joins exist with correct cardinality."""
    joins = {
        (j.from_table, j.to_table): j for j in catalog_setup.joins
    }
    # Detail -> Header
    assert ("Sales_SalesOrderDetail", "Sales_SalesOrderHeader") in joins
    assert joins[("Sales_SalesOrderDetail", "Sales_SalesOrderHeader")].cardinality == JoinCardinality.MANY_TO_ONE

    # Header -> Territory
    assert ("Sales_SalesOrderHeader", "Sales_SalesTerritory") in joins
    assert joins[("Sales_SalesOrderHeader", "Sales_SalesTerritory")].cardinality == JoinCardinality.MANY_TO_ONE

    # CostHistory -> Product (multiple rows per product in CostHistory -> MANY_TO_ONE)
    assert ("Production_ProductCostHistory", "Production_Product") in joins
    assert joins[("Production_ProductCostHistory", "Production_Product")].cardinality == JoinCardinality.MANY_TO_ONE


def test_data_proven_primary_keys(catalog_setup):
    """2. Data-proven primary keys correctly identified."""
    assert catalog_setup.tables["Sales_SalesOrderHeader"].primary_key == ("SalesOrderID",)
    assert catalog_setup.tables["Sales_SalesOrderDetail"].primary_key == ("SalesOrderDetailID",)


def test_avg_companion_and_revenue_alias(catalog_setup):
    """3. AVG companion exists and revenue alias is on Sales table, not Purchasing."""
    assert "Sales_SalesOrderHeader_TotalDue_avg" in catalog_setup.measures
    assert catalog_setup.measures["Sales_SalesOrderHeader_TotalDue_avg"].aggregation == AggregationType.AVG

    assert "revenue" in catalog_setup.measures
    assert catalog_setup.measures["revenue"].table.startswith("Sales_")


def test_ambiguous_measure_yields_clarify(catalog_setup, validator):
    """4. TotalDue without specifying Sales or Purchasing yields CLARIFY."""
    vocab = _vocab(catalog_setup)
    intent = StructuredIntent(
        measure="Sales_SalesOrderHeader_TotalDue",
        family=AnalysisFamily.TOTAL,
    )
    res = validator.validate_intent(
        intent=intent,
        vocabulary=vocab,
        user_question="What was total TotalDue in 2024?",
        turn_id="turn_ambig",
        tenant_id="tenant_a",
        subject_id="alice",
        catalog=catalog_setup,
    )
    assert res.status == "CLARIFY"
    assert res.clarification_request is not None
    assert res.clarification_request.ambiguity_code == "metric-disambiguation"
    options = res.clarification_request.options
    labels = [opt.label for opt in options]
    assert any("Purchasing_PurchaseOrderHeader" in l for l in labels)
    assert any("Sales_SalesOrderHeader" in l for l in labels)


def test_question_naming_table_resolves_silently(catalog_setup, validator):
    """5. Question mentioning sales resolves TotalDue silently to Sales without asking."""
    vocab = _vocab(catalog_setup)
    # LLM proposed Purchasing TotalDue, but user asked for sales TotalDue
    intent = StructuredIntent(
        measure="Purchasing_PurchaseOrderHeader_TotalDue",
        family=AnalysisFamily.TOTAL,
    )
    res = validator.validate_intent(
        intent=intent,
        vocabulary=vocab,
        user_question="What was total sales TotalDue in 2024?",
        turn_id="turn_sales_named",
        tenant_id="tenant_a",
        subject_id="alice",
        catalog=catalog_setup,
    )
    assert res.status == "VALID"
    assert res.validated_intent.measure == "Sales_SalesOrderHeader_TotalDue"


def test_tie_break_specific_table_wins(catalog_setup, validator):
    """6. SalesOrderHeader beats SalesOrderHeaderSalesReason for 'sales orders' count."""
    vocab = _vocab(catalog_setup)
    intent = StructuredIntent(
        measure="Sales_SalesOrderHeaderSalesReason_count",
        family=AnalysisFamily.TOTAL,
    )
    res = validator.validate_intent(
        intent=intent,
        vocabulary=vocab,
        user_question="How many sales orders were placed in 2024?",
        turn_id="turn_tie",
        tenant_id="tenant_a",
        subject_id="alice",
        catalog=catalog_setup,
    )
    # Both tables match 'sales' and 'order', but SalesOrderHeader matches more of its own name words
    assert res.status == "VALID"
    assert res.validated_intent.measure == "Sales_SalesOrderHeader_count"


def test_bare_name_remapped_to_joinable_table(catalog_setup, validator):
    """7. Bare Name dimension from unjoinable table remapped to joinable SalesTerritory Name."""
    vocab = _vocab(catalog_setup)
    unjoinable_dim = "HumanResources_Department_Name"
    intent = StructuredIntent(
        measure="Sales_SalesOrderHeader_TotalDue",
        family=AnalysisFamily.TOTAL,
        dimensions=(unjoinable_dim,),
        assumptions=("Metric confirmed by user: Sales_SalesOrderHeader_TotalDue",),
    )
    res = validator.validate_intent(
        intent=intent,
        vocabulary=vocab,
        user_question="Total sales order TotalDue by territory name in 2024",
        turn_id="turn_remap",
        tenant_id="tenant_a",
        subject_id="alice",
        catalog=catalog_setup,
    )
    assert res.status == "VALID"
    remapped_id = res.validated_intent.dimensions[0]
    remap_dim = vocab.get_dimension(remapped_id)
    assert remap_dim is not None
    assert remap_dim.table == "Sales_SalesTerritory"
    assert remap_dim.column == "Name"


def test_calendar_and_small_code_columns_excluded(catalog_setup):
    """8. Calendar parts and small code integers excluded; valid measures kept."""
    measures = catalog_setup.measures
    # Excluded
    assert "Sales_CreditCard_ExpMonth" not in measures
    assert "Sales_CreditCard_ExpYear" not in measures
    assert "Sales_CreditCard_Priority" not in measures

    # Kept
    assert "Sales_SalesOrderDetail_OrderQty" in measures
    assert "Sales_SalesTerritory_SalesLastYear" in measures
    assert "Sales_SalesOrderHeader_CreditCeiling" in measures


def test_measure_column_never_used_as_dimension(catalog_setup, validator):
    """9. A measure column is stripped from dimensions with or without grouping words."""
    vocab = _vocab(catalog_setup)
    measure_id = "Sales_SalesOrderHeader_TotalDue"
    measure_dim = "Sales_SalesOrderHeader_TotalDue"

    # With grouping words: "TotalDue by TotalDue"
    intent1 = StructuredIntent(
        measure=measure_id,
        family=AnalysisFamily.TOTAL,
        dimensions=(measure_dim,),
        assumptions=("Metric confirmed by user: Sales_SalesOrderHeader_TotalDue",),
    )
    res1 = validator.validate_intent(
        intent=intent1,
        vocabulary=vocab,
        user_question="TotalDue by TotalDue",
        turn_id="turn_dim_strip_1",
        tenant_id="tenant_a",
        subject_id="alice",
        catalog=catalog_setup,
    )
    assert res1.status == "VALID"
    assert len(res1.validated_intent.dimensions) == 0

    # Without grouping words: "Total sales TotalDue in 2024"
    intent2 = StructuredIntent(
        measure=measure_id,
        family=AnalysisFamily.TOTAL,
        dimensions=(measure_dim,),
        assumptions=("Metric confirmed by user: Sales_SalesOrderHeader_TotalDue",),
    )
    res2 = validator.validate_intent(
        intent=intent2,
        vocabulary=vocab,
        user_question="Total sales TotalDue in 2024",
        turn_id="turn_dim_strip_2",
        tenant_id="tenant_a",
        subject_id="alice",
        catalog=catalog_setup,
    )
    assert res2.status == "VALID"
    assert len(res2.validated_intent.dimensions) == 0


def test_confirmed_metric_not_asked_again(catalog_setup, validator):
    """10. Confirmed metric in assumptions avoids re-asking disambiguation."""
    vocab = _vocab(catalog_setup)
    intent = StructuredIntent(
        measure="Sales_SalesOrderHeader_TotalDue",
        family=AnalysisFamily.TOTAL,
        assumptions=(f"{IntentValidator.METRIC_CONFIRMED_PREFIX} Sales_SalesOrderHeader_TotalDue",),
    )
    res = validator.validate_intent(
        intent=intent,
        vocabulary=vocab,
        user_question="What was total TotalDue in 2024?",
        turn_id="turn_confirmed",
        tenant_id="tenant_a",
        subject_id="alice",
        catalog=catalog_setup,
    )
    assert res.status == "VALID"
    assert res.validated_intent.measure == "Sales_SalesOrderHeader_TotalDue"


def test_vocab_helper(catalog_setup):
    """11. Helper _vocab correctly builds AccessibleVocabulary."""
    vocab = _vocab(catalog_setup)
    assert vocab.source_id == catalog_setup.source_id
    assert vocab.release_id == catalog_setup.release_id
    assert len(vocab.measures) == len(catalog_setup.measures)
    assert len(vocab.dimensions) == len(catalog_setup.dimensions)
