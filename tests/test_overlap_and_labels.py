"""Regression tests for slice overlap detection in kernel and label deduplication in presentation."""
from decimal import Decimal
from backend.catalog.pinning import SemanticSnapshotPin
from backend.catalog.schema import (
    AggregationType,
    ApprovedJoinEdge,
    DataType,
    DimensionDefinition,
    JoinCardinality,
    JoinType,
    MeasureDefinition,
    TableDefinition,
)
from backend.domain.facts import ProtectedFact, SealedEvidencePackage
from backend.execution.kernel import DeterministicAnalysisKernel
from backend.intent.schema import AnalysisFamily
from backend.presentation.fallback import DeterministicPresentationFallback
from backend.semantic.plan import ResolvedSemanticPlan


def _create_sample_plan(
    base_table_name: str,
    dim_table_name: str,
    join_edges: tuple[ApprovedJoinEdge, ...],
    allocation_policy: str | None = None,
    aggregation: AggregationType = AggregationType.SUM,
) -> ResolvedSemanticPlan:
    base_table = TableDefinition(
        table_name=base_table_name,
        grain="id",
        tenant_column="tenant_id",
        primary_key=("id",),
        columns={"id": DataType.STRING, "amount": DataType.DECIMAL, "tenant_id": DataType.STRING},
    )
    dim_table = TableDefinition(
        table_name=dim_table_name,
        grain="id",
        tenant_column="tenant_id",
        primary_key=("id",),
        columns={"id": DataType.STRING, "name": DataType.STRING, "tenant_id": DataType.STRING},
    )
    measure = MeasureDefinition(
        id="sample_metric",
        name="Sample Metric",
        description="Sample metric for testing",
        table=base_table_name,
        column="amount",
        aggregation=aggregation,
        base_grain="id",
        unit="USD",
        allowed_dimensions=("test_dim",),
        allowed_families=("TOTAL", "AVERAGE"),
        allocation_policy=allocation_policy,
    )
    dimension = DimensionDefinition(
        id="test_dim",
        name="Test Dim",
        table=dim_table_name,
        column="name",
        data_type=DataType.STRING,
    )
    return ResolvedSemanticPlan(
        plan_id="plan_test_001",
        turn_id="turn_test_001",
        tenant_id="tenant_1",
        measure=measure,
        family=AnalysisFamily.TOTAL,
        dimensions=(dimension,),
        filters=(),
        current_period="2024",
        comparison_period=None,
        base_table=base_table,
        required_tables=(base_table, dim_table),
        join_edges=join_edges,
        fan_out_protection_required=False,
        aggregation_expression="SUM(amount)",
        grouping_columns=(f"{dim_table_name}.name",),
        units="USD",
        allocation_policy=allocation_policy,
    )


def test_order_by_tag_overlaps():
    """Orders joined to tags through products/order_tags can overlap across tags."""
    kernel = DeterministicAnalysisKernel()
    pin = SemanticSnapshotPin(release_id="rel_1", epoch=1, source_id="src_1")

    edges = (
        ApprovedJoinEdge(
            "orders", "order_tags", "id", "order_id",
            join_type=JoinType.INNER, cardinality=JoinCardinality.ONE_TO_MANY
        ),
        ApprovedJoinEdge(
            "order_tags", "tags", "tag_id", "id",
            join_type=JoinType.INNER, cardinality=JoinCardinality.MANY_TO_ONE
        ),
    )
    plan = _create_sample_plan(
        base_table_name="orders",
        dim_table_name="tags",
        join_edges=edges,
    )

    assert kernel._slices_may_overlap(plan) is True

    rows = [
        {"tags.name": "Electronics", "metric_value": 100.0},
        {"tags.name": "Sale", "metric_value": 100.0},
    ]
    facts = kernel.analyze(plan, rows, pin)

    # Must NOT produce an overall TOTAL fact (fact_total_001)
    assert not any(f.fact_id == "fact_total_001" for f in facts)
    assert not any(not f.dimension_values for f in facts)
    # Must attach overlap disclaimer note to limitations
    expected_note = (
        "Groups overlap because a record can belong to several values of the grouping dimension; "
        "values are not summed into an overall total."
    )
    assert all(expected_note in f.limitations for f in facts)


def test_line_item_by_tag_overlaps():
    """Line items joined to tags through products and product tags overlap."""
    kernel = DeterministicAnalysisKernel()
    pin = SemanticSnapshotPin(release_id="rel_1", epoch=1, source_id="src_1")

    edges = (
        ApprovedJoinEdge(
            "line_items", "products", "product_id", "id",
            join_type=JoinType.INNER, cardinality=JoinCardinality.MANY_TO_ONE
        ),
        ApprovedJoinEdge(
            "products", "product_tags", "id", "product_id",
            join_type=JoinType.INNER, cardinality=JoinCardinality.ONE_TO_MANY
        ),
        ApprovedJoinEdge(
            "product_tags", "tags", "tag_id", "id",
            join_type=JoinType.INNER, cardinality=JoinCardinality.MANY_TO_ONE
        ),
    )
    plan = _create_sample_plan(
        base_table_name="line_items",
        dim_table_name="tags",
        join_edges=edges,
    )

    assert kernel._slices_may_overlap(plan) is True

    rows = [
        {"tags.name": "Clearance", "metric_value": 50.0},
        {"tags.name": "Seasonal", "metric_value": 50.0},
    ]
    facts = kernel.analyze(plan, rows, pin)

    assert not any(f.fact_id == "fact_total_001" for f in facts)
    assert not any(not f.dimension_values for f in facts)
    expected_note = (
        "Groups overlap because a record can belong to several values of the grouping dimension; "
        "values are not summed into an overall total."
    )
    assert all(expected_note in f.limitations for f in facts)


def test_allocated_child_edge_and_many_to_one_chain_do_not_overlap():
    """Standard hierarchical dimensions (many-to-one) and allocated child edges do not overlap."""
    kernel = DeterministicAnalysisKernel()
    pin = SemanticSnapshotPin(release_id="rel_1", epoch=1, source_id="src_1")

    # Case A: Standard many-to-one hierarchy (Orders -> Customers -> Countries)
    m2o_edges = (
        ApprovedJoinEdge(
            "orders", "customers", "customer_id", "id",
            join_type=JoinType.INNER, cardinality=JoinCardinality.MANY_TO_ONE
        ),
        ApprovedJoinEdge(
            "customers", "countries", "country_id", "id",
            join_type=JoinType.INNER, cardinality=JoinCardinality.MANY_TO_ONE
        ),
    )
    m2o_plan = _create_sample_plan(
        base_table_name="orders",
        dim_table_name="countries",
        join_edges=m2o_edges,
    )
    assert kernel._slices_may_overlap(m2o_plan) is False

    rows = [
        {"countries.name": "USA", "metric_value": 200.0},
        {"countries.name": "Canada", "metric_value": 100.0},
    ]
    facts_m2o = kernel.analyze(m2o_plan, rows, pin)
    total_fact = next((f for f in facts_m2o if f.calculation_type == "TOTAL"), None)
    assert total_fact is not None
    assert total_fact.value == Decimal("300.0")

    # Case B: Explicit catalog allocation policy (e.g. EXPLICIT_EQUAL_SPLIT)
    alloc_edges = (
        ApprovedJoinEdge(
            "orders", "order_items", "id", "order_id",
            join_type=JoinType.INNER, cardinality=JoinCardinality.ONE_TO_MANY
        ),
    )
    alloc_plan = _create_sample_plan(
        base_table_name="orders",
        dim_table_name="order_items",
        join_edges=alloc_edges,
        allocation_policy="EXPLICIT_EQUAL_SPLIT",
    )
    assert kernel._slices_may_overlap(alloc_plan) is False

    facts_alloc = kernel.analyze(alloc_plan, rows, pin)
    assert any(f.calculation_type == "TOTAL" for f in facts_alloc)


def test_metric_label_has_no_doubled_prefix():
    """Confirms 'Total Total Revenue' is never generated and metric labels are properly deduplicated."""
    fallback = DeterministicPresentationFallback()

    # Direct static method check
    assert fallback._metric_label("TOTAL", "Total Revenue") == "Total Revenue"
    assert fallback._metric_label("TOTAL", "total revenue") == "total revenue"
    assert fallback._metric_label("TOTAL", "Sales Revenue") == "Total Sales Revenue"
    assert fallback._metric_label("AVERAGE", "Average Order Value") == "Average Order Value"
    assert fallback._metric_label("AVERAGE", "Order Value") == "Average Order Value"

    # End-to-end presentation check with total_fact
    total_fact = ProtectedFact(
        fact_id="fact_tot_001",
        metric="Total Revenue",
        calculation_type="TOTAL",
        value=Decimal("150000.00"),
        unit="USD",
        period=None,
    )
    pkg = SealedEvidencePackage.seal(
        package_id="pkg_001",
        turn_id="turn_001",
        tenant_id="tenant_1",
        semantic_release_id="rel_1",
        facts=[total_fact],
    )
    view = fallback.render(pkg)

    assert len(view.kpi_cards) == 1
    assert view.kpi_cards[0].title == "Total Revenue"
    assert "Total Total Revenue" not in view.summary_text
    assert "across all periods" in view.summary_text
