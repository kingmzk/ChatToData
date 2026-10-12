"""Tests for Deterministic Analysis Kernel Math and Precision."""
from decimal import Decimal
from talk2data.domain.facts import ProtectedFact
from talk2data.catalog.pinning import SemanticSnapshotPin
from talk2data.execution.kernel import DeterministicAnalysisKernel
from talk2data.intent.schema import StructuredIntent, AnalysisFamily


def test_zero_denominator_safe_handling():
    """Verify that zero baseline in comparison does not raise ZeroDivisionError and sets safe assumption."""
    kernel = DeterministicAnalysisKernel()
    pin = SemanticSnapshotPin(release_id="rel_1", epoch=1, source_id="src_1")

    current_fact = ProtectedFact(
        fact_id="fact_curr",
        metric="Sales Revenue",
        calculation_type="TOTAL",
        value=Decimal("50000.00"),
        period="2024",
    )
    # Comparison period value is zero
    comp_fact = ProtectedFact(
        fact_id="fact_comp",
        metric="Sales Revenue",
        calculation_type="TOTAL",
        value=Decimal("0.00"),
        period="2023",
    )

    facts = kernel.compute_comparison(current_fact, comp_fact, pin)

    growth_fact = next(f for f in facts if f.calculation_type == "GROWTH_PERCENT")
    assert growth_fact.value is None  # Safe undefined
    assert any("Baseline comparison value is 0" in a for a in growth_fact.assumptions)


def test_growth_rate_exact_decimal_calculation():
    """Verify exact Decimal calculation of growth percentage."""
    kernel = DeterministicAnalysisKernel()
    pin = SemanticSnapshotPin(release_id="rel_1", epoch=1, source_id="src_1")

    current_fact = ProtectedFact(
        fact_id="fact_curr",
        metric="Sales Revenue",
        calculation_type="TOTAL",
        value=Decimal("150000.00"),
        period="2024",
    )
    comp_fact = ProtectedFact(
        fact_id="fact_comp",
        metric="Sales Revenue",
        calculation_type="TOTAL",
        value=Decimal("100000.00"),
        period="2023",
    )

    facts = kernel.compute_comparison(current_fact, comp_fact, pin)

    delta_fact = next(f for f in facts if f.calculation_type == "DELTA")
    growth_fact = next(f for f in facts if f.calculation_type == "GROWTH_PERCENT")

    assert delta_fact.value == Decimal("50000.00")
    assert growth_fact.value == Decimal("50.00")  # Exactly +50.00%


def test_growth_contribution_distinguishes_growth_from_sales_share():
    """Verify compute_growth_contribution computes Delta_i / Delta_Total * 100%.
    
    Between 2023 and 2024:
    Total grew by 50k (100k -> 150k).
    US grew from 70k to 80k (+10k delta -> 20.00% of growth).
    Germany grew from 30k to 70k (+40k delta -> 80.00% of growth).
    Even though US has higher current sales (80k vs 70k),
    Germany is the top growth contributor (80% vs 20%).
    """
    kernel = DeterministicAnalysisKernel()
    pin = SemanticSnapshotPin(release_id="rel_1", epoch=1, source_id="src_1")

    curr_total = ProtectedFact("f_tot_24", "Sales Revenue", "TOTAL", Decimal("150000.00"), period="2024")
    comp_total = ProtectedFact("f_tot_23", "Sales Revenue", "TOTAL", Decimal("100000.00"), period="2023")

    curr_slices = [
        ProtectedFact("f_us_24", "Sales Revenue", "TOTAL", Decimal("80000.00"), dimension_values={"country": "US"}, period="2024"),
        ProtectedFact("f_de_24", "Sales Revenue", "TOTAL", Decimal("70000.00"), dimension_values={"country": "Germany"}, period="2024"),
    ]
    comp_slices = [
        ProtectedFact("f_us_23", "Sales Revenue", "TOTAL", Decimal("70000.00"), dimension_values={"country": "US"}, period="2023"),
        ProtectedFact("f_de_23", "Sales Revenue", "TOTAL", Decimal("30000.00"), dimension_values={"country": "Germany"}, period="2023"),
    ]

    facts = kernel.compute_growth_contribution(
        current_facts=[curr_total, *curr_slices],
        comparison_facts=[comp_total, *comp_slices],
        pin=pin,
    )

    top_fact = next(f for f in facts if f.calculation_type == "TOP_GROWTH_CONTRIBUTOR")
    assert top_fact.dimension_values["country"] == "Germany"
    assert top_fact.value == Decimal("40000.00")  # Absolute delta: +$40k

    de_contrib = next(f for f in facts if f.calculation_type == "GROWTH_CONTRIBUTION_PERCENT" and f.dimension_values.get("country") == "Germany")
    us_contrib = next(f for f in facts if f.calculation_type == "GROWTH_CONTRIBUTION_PERCENT" and f.dimension_values.get("country") == "US")

    assert de_contrib.value == Decimal("80.00")
    assert us_contrib.value == Decimal("20.00")


def test_sql_compiler_avoids_floating_point_multiplier(test_setup):
    """Verify that GovernedQueryCompiler does not generate '* 1.0' floating-point expressions."""
    manager = test_setup["manager"]
    compiler = manager.compiler
    catalogs = test_setup["catalogs"]
    pin = catalogs.pin("tenant_a", "finance_dw")
    cat = catalogs.get_catalog("finance_dw", pin.release_id)

    intent = StructuredIntent(
        measure="sales_revenue_allocated",
        family=AnalysisFamily.TOTAL,
        dimensions=("category",),
        current_period="2022",
    )
    plan = manager.resolver.resolve(intent, cat, pin, "turn_prec_test", "tenant_a")
    pkg = compiler.compile(plan)

    assert "* 1.0" not in pkg.sql
    assert "DECIMAL_DIV(" in pkg.sql
    assert "AS NUMERIC" not in pkg.sql


def test_kernel_exact_decimal_arithmetic_prevents_binary_float_drift():
    """Verify kernel preserves exact decimal arithmetic without IEEE 754 float drift."""
    kernel = DeterministicAnalysisKernel()
    pin = SemanticSnapshotPin(release_id="rel_1", epoch=1, source_id="src_1")

    # In IEEE 754 binary floating point, 0.1 + 0.2 != 0.3 (0.30000000000000004)
    # With exact Decimal handling, sum is precisely 0.3000
    rows = [
        {"metric_value": Decimal("0.1000") + Decimal("0.2000")},
    ]

    from talk2data.catalog.schema import TableDefinition, MeasureDefinition, AggregationType
    from talk2data.semantic.plan import ResolvedSemanticPlan

    tbl = TableDefinition("test_t", grain="row", tenant_column="tenant_id")
    meas = MeasureDefinition("m", "M", "desc", "test_t", "val", AggregationType.SUM, "row", "USD")
    plan = ResolvedSemanticPlan(
        plan_id="p1", turn_id="t1", tenant_id="t1", measure=meas, family=AnalysisFamily.TOTAL,
        dimensions=(), filters=(), current_period=None, comparison_period=None,
        base_table=tbl, required_tables=(tbl,), join_edges=(), fan_out_protection_required=False,
        aggregation_expression="SUM(val)", grouping_columns=(), units="USD"
    )

    facts = kernel.analyze(plan, rows, pin)
    assert facts[0].value == Decimal("0.3000")


def test_sqlite_storage_and_driver_conversion_against_independent_fixture():
    """Verify end-to-end SQLite storage, query execution, and driver conversion against an independent Decimal fixture."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute("CREATE TABLE precision_test (id TEXT, amount NUMERIC)")

    # Insert independently calculated fixtures with decimal values known to drift in IEEE 754
    # (e.g. 0.1 + 0.2 = 0.30000000000000004 in raw float)
    test_values = [
        Decimal("12345.67"),
        Decimal("8910.11"),
        Decimal("0.10"),
        Decimal("0.20"),
    ]
    for idx, v in enumerate(test_values):
        cur.execute("INSERT INTO precision_test VALUES (?, ?)", (f"id_{idx}", str(v)))
    conn.commit()

    # Independently calculated exact sum
    expected_sum = sum(test_values)  # Decimal('21256.08')
    assert expected_sum == Decimal("21256.08")

    # Execute SQLite query
    cur.execute("SELECT CAST(SUM(amount) AS NUMERIC) AS metric_value FROM precision_test")
    row = cur.fetchone()
    raw_sqlite_val = row[0]

    # Analysis kernel converts raw database result
    kernel = DeterministicAnalysisKernel()
    decimal_result = kernel._to_decimal(raw_sqlite_val)

    assert decimal_result == expected_sum
    assert decimal_result == Decimal("21256.08")



