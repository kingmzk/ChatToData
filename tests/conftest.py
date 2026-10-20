"""Pytest fixtures providing in-memory database, seed data, and configured Talk2Data services."""
from datetime import datetime, timezone
import sqlite3
import pytest
from decimal import Decimal

from backend.catalog.schema import (
    SemanticCatalog,
    TableDefinition,
    MeasureDefinition,
    DimensionDefinition,
    ApprovedJoinEdge,
    AggregationType,
    DataType,
    JoinCardinality,
    JoinType,
    TimeSeriesPolicy,
    ScenarioModelDefinition,
    ScenarioDriverDefinition,
)
from backend.catalog.store import SemanticCatalogStore
from backend.domain.models import ApiActor
from backend.execution.gateway import GenericSourceGateway
from backend.lifecycle.session import ConversationLifecycleManager
from backend.security.auth import SecurityRuntime


@pytest.fixture
def sqlite_db():
    """Create in-memory SQLite database populated with tenant-scoped test tables."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    cur = conn.cursor()

    # 1. Orders table (using NUMERIC for exact decimal precision)
    cur.execute("""
        CREATE TABLE orders (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            amount NUMERIC NOT NULL,
            period TEXT NOT NULL
        )
    """)

    # 2. Order Items table (child table with item-level amounts and tenant_id)
    cur.execute("""
        CREATE TABLE order_items (
            item_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            product_name TEXT NOT NULL,
            category TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            item_amount NUMERIC NOT NULL
        )
    """)

    # 3. Customers table (dimension table)
    cur.execute("""
        CREATE TABLE customers (
            customer_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            country TEXT NOT NULL,
            segment TEXT NOT NULL
        )
    """)

    # 4. Salaries table (confidential/restricted, using NUMERIC)
    cur.execute("""
        CREATE TABLE salaries (
            emp_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            department TEXT NOT NULL,
            salary NUMERIC NOT NULL
        )
    """)

    # Seed data for tenant_a:
    # 2024 Orders (Total = 150,000.00: US = 80k, Germany = 70k)
    cur.execute("INSERT INTO orders VALUES ('ord_1', 'tenant_a', 'cust_1', 50000.00, '2024')")
    cur.execute("INSERT INTO order_items VALUES ('item_1', 'ord_1', 'tenant_a', 'Laptop', 'Electronics', 1, 30000.00)")
    cur.execute("INSERT INTO order_items VALUES ('item_2', 'ord_1', 'tenant_a', 'Mouse', 'Electronics', 2, 20000.00)")

    cur.execute("INSERT INTO orders VALUES ('ord_2', 'tenant_a', 'cust_2', 70000.00, '2024')")
    cur.execute("INSERT INTO order_items VALUES ('item_3', 'ord_2', 'tenant_a', 'Server', 'Hardware', 1, 70000.00)")

    cur.execute("INSERT INTO orders VALUES ('ord_3', 'tenant_a', 'cust_1', 30000.00, '2024')")
    cur.execute("INSERT INTO order_items VALUES ('item_4', 'ord_3', 'tenant_a', 'Monitor', 'Electronics', 1, 30000.00)")

    # 2023 Orders (Total = 100,000.00: US = 70k, Germany = 30k)
    # Between 2023 & 2024: US grew by 10k (+20% of growth), Germany grew by 40k (+80% of growth)
    cur.execute("INSERT INTO orders VALUES ('ord_4', 'tenant_a', 'cust_1', 70000.00, '2023')")
    cur.execute("INSERT INTO order_items VALUES ('item_5', 'ord_4', 'tenant_a', 'Workstation', 'Hardware', 1, 70000.00)")

    cur.execute("INSERT INTO orders VALUES ('ord_5', 'tenant_a', 'cust_2', 30000.00, '2023')")
    cur.execute("INSERT INTO order_items VALUES ('item_6', 'ord_5', 'tenant_a', 'Cables', 'Accessories', 3, 30000.00)")

    # Relative Date Orders (Period '2024-03' = March 2024, last month relative to April 2024)
    # Total March 2024 = 100,000.00 (US = 45k, Germany = 55k)
    cur.execute("INSERT INTO orders VALUES ('ord_6', 'tenant_a', 'cust_1', 45000.00, '2024-03')")
    cur.execute("INSERT INTO order_items VALUES ('item_7', 'ord_6', 'tenant_a', 'Phone', 'Electronics', 1, 45000.00)")

    cur.execute("INSERT INTO orders VALUES ('ord_7', 'tenant_a', 'cust_2', 55000.00, '2024-03')")
    cur.execute("INSERT INTO order_items VALUES ('item_8', 'ord_7', 'tenant_a', 'Router', 'Hardware', 1, 55000.00)")

    # Quarter Orders for 2024 (Q2 = 40k, Q3 = 60k)
    cur.execute("INSERT INTO orders VALUES ('ord_q2', 'tenant_a', 'cust_1', 40000.00, '2024-Q2')")
    cur.execute("INSERT INTO order_items VALUES ('item_q2', 'ord_q2', 'tenant_a', 'Tablet', 'Electronics', 1, 40000.00)")

    cur.execute("INSERT INTO orders VALUES ('ord_q3', 'tenant_a', 'cust_1', 60000.00, '2024-Q3')")
    cur.execute("INSERT INTO order_items VALUES ('item_q3', 'ord_q3', 'tenant_a', 'Desktop', 'Electronics', 1, 60000.00)")

    # Orders for 2022 to test parent with 0 children and parent with unequal children (5 items)
    # ord_8 has 0 children
    cur.execute("INSERT INTO orders VALUES ('ord_8', 'tenant_a', 'cust_1', 20000.00, '2022')")
    # ord_9 has 5 children
    cur.execute("INSERT INTO orders VALUES ('ord_9', 'tenant_a', 'cust_2', 50000.00, '2022')")
    for i in range(1, 6):
        cur.execute(f"INSERT INTO order_items VALUES ('item_unequal_{i}', 'ord_9', 'tenant_a', 'Part_{i}', 'Electronics', 1, 10000.00)")

    # 2021 Mixed Categories and Unequal Item Values Order (Total = 100,000.00: Hardware = 80k, Software = 20k)
    cur.execute("INSERT INTO orders VALUES ('ord_mixed', 'tenant_a', 'cust_1', 100000.00, '2021')")
    cur.execute("INSERT INTO order_items VALUES ('item_mix_1', 'ord_mixed', 'tenant_a', 'Enterprise Server', 'Hardware', 1, 80000.00)")
    cur.execute("INSERT INTO order_items VALUES ('item_mix_2', 'ord_mixed', 'tenant_a', 'Database License', 'Software', 1, 20000.00)")

    # Seed data for tenant_b (Isolated tenant, total sales = 999999.0)
    cur.execute("INSERT INTO orders VALUES ('ord_b1', 'tenant_b', 'cust_b1', 999999.00, '2024')")
    cur.execute("INSERT INTO order_items VALUES ('item_b1', 'ord_b1', 'tenant_b', 'Aircraft', 'Aerospace', 1, 999999.00)")

    # Customers
    cur.execute("INSERT INTO customers VALUES ('cust_1', 'tenant_a', 'US', 'Enterprise')")
    cur.execute("INSERT INTO customers VALUES ('cust_2', 'tenant_a', 'Germany', 'Enterprise')")
    cur.execute("INSERT INTO customers VALUES ('cust_b1', 'tenant_b', 'UK', 'Enterprise')")

    # Salaries (for restricted metric and minimum-group suppression testing)
    cur.execute("INSERT INTO salaries VALUES ('emp_1', 'tenant_a', 'Engineering', 120000.00)")
    cur.execute("INSERT INTO salaries VALUES ('emp_2', 'tenant_a', 'Engineering', 130000.00)")


    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def catalog_fixture():
    """Build a declarative semantic catalog with governed metrics and approved joins."""
    orders_tbl = TableDefinition(
        table_name="orders",
        grain="order",
        tenant_column="tenant_id",
        primary_key=("id",),
        columns={
            "id": DataType.STRING,
            "tenant_id": DataType.STRING,
            "customer_id": DataType.STRING,
            "amount": DataType.DECIMAL,
            "period": DataType.STRING,
        },
    )

    items_tbl = TableDefinition(
        table_name="order_items",
        grain="order_item",
        tenant_column="tenant_id",
        primary_key=("item_id",),
        columns={
            "item_id": DataType.STRING,
            "order_id": DataType.STRING,
            "tenant_id": DataType.STRING,
            "product_name": DataType.STRING,
            "category": DataType.STRING,
            "quantity": DataType.INTEGER,
            "item_amount": DataType.DECIMAL,
        },
    )

    customers_tbl = TableDefinition(
        table_name="customers",
        grain="customer",
        tenant_column="tenant_id",
        primary_key=("customer_id",),
        columns={
            "customer_id": DataType.STRING,
            "tenant_id": DataType.STRING,
            "country": DataType.STRING,
            "segment": DataType.STRING,
        },
    )

    salaries_tbl = TableDefinition(
        table_name="salaries",
        grain="employee",
        tenant_column="tenant_id",
        primary_key=("emp_id",),
        columns={
            "emp_id": DataType.STRING,
            "tenant_id": DataType.STRING,
            "department": DataType.STRING,
            "salary": DataType.DECIMAL,
        },
    )

    # Measures
    sales_rev = MeasureDefinition(
        id="sales_revenue",
        name="Sales Revenue",
        description="Total monetary value of orders (order grain, no allocation policy)",
        table="orders",
        column="amount",
        aggregation=AggregationType.SUM,
        base_grain="order",
        unit="USD",
        currency="USD",
        allowed_dimensions=("country", "period", "category", "vip_segment"),
        allowed_families=(
            "TOTAL", "AVERAGE", "TREND", "COMPARISON", "CONTRIBUTION",
            "FORECAST", "ANOMALY", "SCENARIO", "GOAL_SEEK"
        ),
        allocation_policy=None,
    )

    item_sales_rev = MeasureDefinition(
        id="item_sales_revenue",
        name="Item Sales Revenue",
        description="Approved item-level revenue defined directly at line-item grain",
        table="order_items",
        column="item_amount",
        aggregation=AggregationType.SUM,
        base_grain="order_item",
        unit="USD",
        currency="USD",
        allowed_dimensions=("category", "period", "country"),
        allowed_families=("TOTAL", "AVERAGE", "TREND", "COMPARISON", "CONTRIBUTION"),
    )

    sales_rev_allocated = MeasureDefinition(
        id="sales_revenue_allocated",
        name="Allocated Sales Revenue",
        description="Order revenue allocated across items via explicit catalog policy",
        table="orders",
        column="amount",
        aggregation=AggregationType.SUM,
        base_grain="order",
        unit="USD",
        currency="USD",
        allowed_dimensions=("category", "country", "period"),
        allowed_families=("TOTAL", "AVERAGE", "TREND", "COMPARISON", "CONTRIBUTION"),
        allocation_policy="EXPLICIT_EQUAL_SPLIT",
        unmatched_parent_behavior="UNALLOCATED",
    )

    unsupported_metric = MeasureDefinition(
        id="unsupported_microdata",
        name="Unsupported Microdata",
        description="Metric requiring unsupported differential privacy",
        table="orders",
        column="amount",
        aggregation=AggregationType.SUM,
        base_grain="order",
        unit="USD",
        requires_unsupported_disclosure_protection=True,
    )

    avg_order = MeasureDefinition(
        id="avg_order_value",
        name="Average Order Value",
        description="Average monetary value per order",
        table="orders",
        column="amount",
        aggregation=AggregationType.AVG,
        base_grain="order",
        unit="USD",
        currency="USD",
        allowed_dimensions=("country", "period"),
        allowed_families=("AVERAGE", "COMPARISON"),
    )

    order_cnt = MeasureDefinition(
        id="order_count",
        name="Order Count",
        description="Total number of orders",
        table="orders",
        column="id",
        aggregation=AggregationType.COUNT,
        base_grain="order",
        unit="orders",
        allowed_dimensions=("country", "period"),
        allowed_families=("TOTAL", "COMPARISON"),
    )

    salary_measure = MeasureDefinition(
        id="employee_salary",
        name="Employee Salary",
        description="Annual compensation for employees",
        table="salaries",
        column="salary",
        aggregation=AggregationType.SUM,
        base_grain="employee",
        unit="USD",
        currency="USD",
        allowed_dimensions=("department",),
        allowed_families=("TOTAL", "AVERAGE"),
        sensitivity="RESTRICTED",
        required_role="hr_admin",
    )

    # Dimensions
    country_dim = DimensionDefinition(
        id="country",
        name="Country",
        table="customers",
        column="country",
        data_type=DataType.STRING,
    )

    period_dim = DimensionDefinition(
        id="period",
        name="Period",
        table="orders",
        column="period",
        data_type=DataType.STRING,
    )

    category_dim = DimensionDefinition(
        id="category",
        name="Category",
        table="order_items",
        column="category",
        data_type=DataType.STRING,
    )

    vip_segment_dim = DimensionDefinition(
        id="vip_segment",
        name="VIP Segment",
        table="customers",
        column="segment",
        data_type=DataType.STRING,
        required_role="sales_director",
        sensitivity="RESTRICTED",
    )

    # Approved Joins
    join_cust = ApprovedJoinEdge(
        from_table="orders",
        to_table="customers",
        from_column="customer_id",
        to_column="customer_id",
        join_type=JoinType.INNER,
        cardinality=JoinCardinality.MANY_TO_ONE,
    )

    join_items = ApprovedJoinEdge(
        from_table="orders",
        to_table="order_items",
        from_column="id",
        to_column="order_id",
        join_type=JoinType.INNER,
        cardinality=JoinCardinality.ONE_TO_MANY,
    )

    join_items_to_orders = ApprovedJoinEdge(
        from_table="order_items",
        to_table="orders",
        from_column="order_id",
        to_column="id",
        join_type=JoinType.INNER,
        cardinality=JoinCardinality.MANY_TO_ONE,
    )

    return SemanticCatalog(
        release_id="rel_2026_01",
        epoch=1,
        source_id="finance_dw",
        tables={
            "orders": orders_tbl,
            "order_items": items_tbl,
            "customers": customers_tbl,
            "salaries": salaries_tbl,
        },
        measures={
            "sales_revenue": sales_rev,
            "item_sales_revenue": item_sales_rev,
            "sales_revenue_allocated": sales_rev_allocated,
            "unsupported_microdata": unsupported_metric,
            "avg_order_value": avg_order,
            "order_count": order_cnt,
            "employee_salary": salary_measure,
        },
        dimensions={
            "country": country_dim,
            "period": period_dim,
            "category": category_dim,
            "vip_segment": vip_segment_dim,
        },
        joins=(join_cust, join_items, join_items_to_orders),
        default_period_dimension="period",
        timezone="America/New_York",
        fiscal_year_start_month=4,
        time_series_policies={
            "sales_revenue": TimeSeriesPolicy(frequency="YEAR", 
                metric="sales_revenue",
                period_dimension="period",
                min_history_periods=3,
                max_forecast_horizon=4,
                linear_trend_eligible=True,
            )
        },
        scenario_models={
            "sales_simulator": ScenarioModelDefinition(
                model_id="sales_simulator",
                name="Revenue Simulator",
                description="Simulate revenue from average order value and order volume",
                output_metric="sales_revenue",
                formula_expression="avg_price * volume",
                drivers={
                    "avg_price": ScenarioDriverDefinition(
                        name="avg_price",
                        unit="USD",
                        min_value=Decimal("1"),
                        max_value=Decimal("1000"),
                        default_value=Decimal("50"),
                        is_adjustable=True,
                    ),
                    "volume": ScenarioDriverDefinition(
                        name="volume",
                        unit="orders",
                        min_value=Decimal("1"),
                        max_value=Decimal("100000"),
                        default_value=Decimal("100"),
                        is_adjustable=True,
                    ),
                },
                permitted_adjustments=("avg_price", "volume"),
            )
        },
    )


@pytest.fixture
def test_setup(sqlite_db, catalog_fixture):
    """Full system setup with configured security, store, gateway, and manager."""
    security = SecurityRuntime()
    security.register_tenant_source("tenant_a", "finance_dw")
    security.register_tenant_source("tenant_b", "finance_dw")

    catalog_store = SemanticCatalogStore()
    catalog_store.register_catalog(catalog_fixture, set_active=True)

    gateway = GenericSourceGateway(connection_or_factory=sqlite_db)

    # Fixed reference date: April 15, 2024 (so 'last month' resolves to March 2024: 2024-03)
    ref_dt = datetime(2024, 4, 15, 12, 0, 0, tzinfo=timezone.utc)

    manager = ConversationLifecycleManager(
        security_runtime=security,
        catalog_store=catalog_store,
        gateway=gateway,
        reference_datetime=ref_dt,
    )

    actor_a = security.authenticate_actor(tenant_id="tenant_a", subject_id="user_alice")
    actor_a2 = security.authenticate_actor(tenant_id="tenant_a", subject_id="user_charlie")
    actor_b = security.authenticate_actor(tenant_id="tenant_b", subject_id="user_bob")
    actor_hr = security.authenticate_actor(
        tenant_id="tenant_a", subject_id="user_hr", roles=("hr_admin",), permissions={"read:employee_salary"}
    )
    actor_director = security.authenticate_actor(
        tenant_id="tenant_a",
        subject_id="user_director",
        roles=("sales_director",),
        permissions={"read:vip_segment"},
    )

    return {
        "security": security,
        "catalogs": catalog_store,
        "gateway": gateway,
        "manager": manager,
        "actor_a": actor_a,
        "actor_a2": actor_a2,
        "actor_b": actor_b,
        "actor_hr": actor_hr,
        "actor_director": actor_director,
        "source_id": "finance_dw",
        "reference_datetime": ref_dt,
    }

