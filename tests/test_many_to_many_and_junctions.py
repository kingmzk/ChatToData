"""Comprehensive tests for Many-to-Many relations, multiple M:N junctions, join tables, multi-hop joins, and zero-fanout governance.

Covers:
1. Single Many-to-Many relation via bridge/junction table (orders <-> order_tags <-> tags).
2. Multiple Many-to-Many relations concurrently on the same base table:
   (orders <-> order_tags <-> tags) AND (orders <-> order_promos <-> promotions).
3. Multi-hop linear relations (3+ hops: orders -> customers -> cities -> regions).
4. Junction table with attributes and relations (order_tags.priority, order_tags -> users).
5. Zero fan-out mathematical integrity under M:N filters (EXISTS semi-join prevents row inflation).
6. Rejection of unapproved implicit revenue allocation across M:N child tables.
7. End-to-end SQLite auto-introspection of database with junction tables and M:N relations.
"""
from datetime import datetime, timezone
from decimal import Decimal
import os
import sqlite3
import tempfile
import pytest

from pathlib import Path
from talk2data.catalog.extractor import introspect_sqlite_database
from talk2data.catalog.pinning import SemanticSnapshotPin
from talk2data.catalog.schema import (
    SemanticCatalog,
    TableDefinition,
    MeasureDefinition,
    DimensionDefinition,
    ApprovedJoinEdge,
    AggregationType,
    DataType,
    JoinCardinality,
    JoinType,
)
from talk2data.catalog.store import SemanticCatalogStore
from talk2data.compiler.compiler import GovernedQueryCompiler
from talk2data.domain.models import ApiActor, TurnStatus
from talk2data.execution.gateway import GenericSourceGateway
from talk2data.intent.schema import AnalysisFamily, FilterSpec, StructuredIntent
from talk2data.lifecycle.session import ConversationLifecycleManager
from talk2data.security.auth import SecurityRuntime
from talk2data.semantic.resolver import SemanticResolver, SemanticResolutionError


@pytest.fixture
def m2m_environment():
    """Set up an in-memory SQLite database and semantic catalog with rich M:N and multi-hop structures."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    cur = conn.cursor()

    # 1. Base table: orders
    cur.execute("""
        CREATE TABLE orders (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            amount NUMERIC NOT NULL,
            period TEXT NOT NULL
        )
    """)

    # 2. Customers -> Cities -> Regions (3-hop linear join)
    cur.execute("""
        CREATE TABLE customers (
            customer_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            city_id TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE cities (
            city_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            city_name TEXT NOT NULL,
            region_id TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE regions (
            region_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            region_name TEXT NOT NULL
        )
    """)

    # 3. M:N #1: orders <-> order_tags <-> tags
    cur.execute("""
        CREATE TABLE tags (
            tag_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            tag_name TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE order_tags (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            tag_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            priority TEXT NOT NULL,
            assigned_by_user_id TEXT NOT NULL
        )
    """)

    # 4. Relation from junction table to users
    cur.execute("""
        CREATE TABLE users (
            user_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            user_name TEXT NOT NULL
        )
    """)

    # 5. M:N #2: orders <-> order_promos <-> promotions
    cur.execute("""
        CREATE TABLE promotions (
            promo_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            promo_code TEXT NOT NULL,
            discount_pct NUMERIC NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE order_promos (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            promo_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL
        )
    """)

    # Seed data (tenant_a)
    # Regions and Cities
    cur.execute("INSERT INTO regions VALUES ('reg_na', 'tenant_a', 'North America')")
    cur.execute("INSERT INTO regions VALUES ('reg_eu', 'tenant_a', 'Europe')")

    cur.execute("INSERT INTO cities VALUES ('city_nyc', 'tenant_a', 'New York', 'reg_na')")
    cur.execute("INSERT INTO cities VALUES ('city_ber', 'tenant_a', 'Berlin', 'reg_eu')")

    # Customers
    cur.execute("INSERT INTO customers VALUES ('cust_1', 'tenant_a', 'Acme Corp', 'city_nyc')")
    cur.execute("INSERT INTO customers VALUES ('cust_2', 'tenant_a', 'Globex', 'city_ber')")

    # Users
    cur.execute("INSERT INTO users VALUES ('usr_alice', 'tenant_a', 'Alice')")
    cur.execute("INSERT INTO users VALUES ('usr_bob', 'tenant_a', 'Bob')")

    # Tags
    cur.execute("INSERT INTO tags VALUES ('tag_tech', 'tenant_a', 'Tech')")
    cur.execute("INSERT INTO tags VALUES ('tag_urg', 'tenant_a', 'Urgent')")
    cur.execute("INSERT INTO tags VALUES ('tag_whol', 'tenant_a', 'Wholesale')")

    # Promotions
    cur.execute("INSERT INTO promotions VALUES ('p_bf', 'tenant_a', 'BLACKFRIDAY', 20.0)")
    cur.execute("INSERT INTO promotions VALUES ('p_sum', 'tenant_a', 'SUMMER', 10.0)")

    # Orders:
    # ord_1: $100, cust_1 (NYC / NA), tags: Tech + Urgent, promo: BLACKFRIDAY
    cur.execute("INSERT INTO orders VALUES ('ord_1', 'tenant_a', 'cust_1', 100.00, '2024')")
    cur.execute("INSERT INTO order_tags VALUES ('ot_1', 'ord_1', 'tag_tech', 'tenant_a', 'HIGH', 'usr_alice')")
    cur.execute("INSERT INTO order_tags VALUES ('ot_2', 'ord_1', 'tag_urg', 'tenant_a', 'CRITICAL', 'usr_alice')")
    cur.execute("INSERT INTO order_promos VALUES ('op_1', 'ord_1', 'p_bf', 'tenant_a')")

    # ord_2: $200, cust_2 (Berlin / EU), tags: Tech, promo: SUMMER
    cur.execute("INSERT INTO orders VALUES ('ord_2', 'tenant_a', 'cust_2', 200.00, '2024')")
    cur.execute("INSERT INTO order_tags VALUES ('ot_3', 'ord_2', 'tag_tech', 'tenant_a', 'LOW', 'usr_bob')")
    cur.execute("INSERT INTO order_promos VALUES ('op_2', 'ord_2', 'p_sum', 'tenant_a')")

    # ord_3: $300, cust_1 (NYC / NA), tags: Wholesale, promo: BLACKFRIDAY
    cur.execute("INSERT INTO orders VALUES ('ord_3', 'tenant_a', 'cust_1', 300.00, '2024')")
    cur.execute("INSERT INTO order_tags VALUES ('ot_4', 'ord_3', 'tag_whol', 'tenant_a', 'MEDIUM', 'usr_bob')")
    cur.execute("INSERT INTO order_promos VALUES ('op_3', 'ord_3', 'p_bf', 'tenant_a')")

    conn.commit()

    # Define Semantic Catalog
    tables = {
        "orders": TableDefinition(
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
        ),
        "customers": TableDefinition(
            table_name="customers",
            grain="customer",
            tenant_column="tenant_id",
            primary_key=("customer_id",),
            columns={"customer_id": DataType.STRING, "tenant_id": DataType.STRING, "customer_name": DataType.STRING, "city_id": DataType.STRING},
        ),
        "cities": TableDefinition(
            table_name="cities",
            grain="city",
            tenant_column="tenant_id",
            primary_key=("city_id",),
            columns={"city_id": DataType.STRING, "tenant_id": DataType.STRING, "city_name": DataType.STRING, "region_id": DataType.STRING},
        ),
        "regions": TableDefinition(
            table_name="regions",
            grain="region",
            tenant_column="tenant_id",
            primary_key=("region_id",),
            columns={"region_id": DataType.STRING, "tenant_id": DataType.STRING, "region_name": DataType.STRING},
        ),
        "tags": TableDefinition(
            table_name="tags",
            grain="tag",
            tenant_column="tenant_id",
            primary_key=("tag_id",),
            columns={"tag_id": DataType.STRING, "tenant_id": DataType.STRING, "tag_name": DataType.STRING},
        ),
        "order_tags": TableDefinition(
            table_name="order_tags",
            grain="order_tag",
            tenant_column="tenant_id",
            primary_key=("id",),
            columns={"id": DataType.STRING, "order_id": DataType.STRING, "tag_id": DataType.STRING, "tenant_id": DataType.STRING, "priority": DataType.STRING, "assigned_by_user_id": DataType.STRING},
        ),
        "users": TableDefinition(
            table_name="users",
            grain="user",
            tenant_column="tenant_id",
            primary_key=("user_id",),
            columns={"user_id": DataType.STRING, "tenant_id": DataType.STRING, "user_name": DataType.STRING},
        ),
        "promotions": TableDefinition(
            table_name="promotions",
            grain="promotion",
            tenant_column="tenant_id",
            primary_key=("promo_id",),
            columns={"promo_id": DataType.STRING, "tenant_id": DataType.STRING, "promo_code": DataType.STRING, "discount_pct": DataType.DECIMAL},
        ),
        "order_promos": TableDefinition(
            table_name="order_promos",
            grain="order_promo",
            tenant_column="tenant_id",
            primary_key=("id",),
            columns={"id": DataType.STRING, "order_id": DataType.STRING, "promo_id": DataType.STRING, "tenant_id": DataType.STRING},
        ),
    }

    joins = (
        # 3-hop linear: orders -> customers -> cities -> regions
        ApprovedJoinEdge("orders", "customers", "customer_id", "customer_id", join_type=JoinType.INNER, cardinality=JoinCardinality.MANY_TO_ONE),
        ApprovedJoinEdge("customers", "cities", "city_id", "city_id", join_type=JoinType.INNER, cardinality=JoinCardinality.MANY_TO_ONE),
        ApprovedJoinEdge("cities", "regions", "region_id", "region_id", join_type=JoinType.INNER, cardinality=JoinCardinality.MANY_TO_ONE),

        # M:N #1: orders <-> order_tags <-> tags
        ApprovedJoinEdge("orders", "order_tags", "id", "order_id", join_type=JoinType.INNER, cardinality=JoinCardinality.ONE_TO_MANY),
        ApprovedJoinEdge("order_tags", "tags", "tag_id", "tag_id", join_type=JoinType.INNER, cardinality=JoinCardinality.MANY_TO_ONE),

        # Junction relation to users: order_tags -> users
        ApprovedJoinEdge("order_tags", "users", "assigned_by_user_id", "user_id", join_type=JoinType.INNER, cardinality=JoinCardinality.MANY_TO_ONE),

        # M:N #2: orders <-> order_promos <-> promotions
        ApprovedJoinEdge("orders", "order_promos", "id", "order_id", join_type=JoinType.INNER, cardinality=JoinCardinality.ONE_TO_MANY),
        ApprovedJoinEdge("order_promos", "promotions", "promo_id", "promo_id", join_type=JoinType.INNER, cardinality=JoinCardinality.MANY_TO_ONE),
    )

    dimensions = {
        "tag_name": DimensionDefinition("tag_name", "Tag Name", "tags", "tag_name", DataType.STRING),
        "priority": DimensionDefinition("priority", "Tag Priority", "order_tags", "priority", DataType.STRING),
        "reviewer_name": DimensionDefinition("reviewer_name", "Reviewer Name", "users", "user_name", DataType.STRING),
        "promo_code": DimensionDefinition("promo_code", "Promotion Code", "promotions", "promo_code", DataType.STRING),
        "region_name": DimensionDefinition("region_name", "Region Name", "regions", "region_name", DataType.STRING),
        "city_name": DimensionDefinition("city_name", "City Name", "cities", "city_name", DataType.STRING),
        "customer_name": DimensionDefinition("customer_name", "Customer Name", "customers", "customer_name", DataType.STRING),
        "period": DimensionDefinition("period", "Period", "orders", "period", DataType.STRING),
    }

    measures = {
        "order_count": MeasureDefinition(
            id="order_count",
            name="Order Count",
            description="Distinct count of orders placed",
            table="orders",
            column="id",
            aggregation=AggregationType.COUNT_DISTINCT,
            base_grain="order",
            unit="Orders",
            allowed_dimensions=tuple(dimensions.keys()),
            allowed_families=("TOTAL", "AVERAGE", "TREND", "COMPARISON", "CONTRIBUTION"),
        ),
        "sales_revenue": MeasureDefinition(
            id="sales_revenue",
            name="Sales Revenue",
            description="Total monetary sales amount (order grain, no allocation)",
            table="orders",
            column="amount",
            aggregation=AggregationType.SUM,
            base_grain="order",
            unit="USD",
            currency="USD",
            allowed_dimensions=tuple(dimensions.keys()),
            allowed_families=("TOTAL", "AVERAGE", "TREND", "COMPARISON", "CONTRIBUTION"),
            allocation_policy=None,
        ),
        "sales_revenue_allocated": MeasureDefinition(
            id="sales_revenue_allocated",
            name="Allocated Sales Revenue",
            description="Order revenue allocated across child items/tags via explicit policy",
            table="orders",
            column="amount",
            aggregation=AggregationType.SUM,
            base_grain="order",
            unit="USD",
            currency="USD",
            allowed_dimensions=tuple(dimensions.keys()),
            allowed_families=("TOTAL", "AVERAGE", "TREND", "COMPARISON", "CONTRIBUTION"),
            allocation_policy="EXPLICIT_EQUAL_SPLIT",
            unmatched_parent_behavior="UNALLOCATED",
        ),
    }

    catalog = SemanticCatalog(
        release_id="rel_m2m_1",
        epoch=1,
        source_id="m2m_source",
        tables=tables,
        measures=measures,
        dimensions=dimensions,
        joins=joins,
        default_period_dimension="period",
    )

    catalog_store = SemanticCatalogStore()
    catalog_store.register_catalog(catalog)
    pin = catalog_store.pin(tenant_id="tenant_a", source_id="m2m_source")

    gateway = GenericSourceGateway(connection_or_factory=conn)

    security = SecurityRuntime()
    security.register_tenant_source("tenant_a", "m2m_source")
    actor = security.authenticate_actor(tenant_id="tenant_a", subject_id="analyst_1")

    manager = ConversationLifecycleManager(
        security_runtime=security,
        catalog_store=catalog_store,
        gateway=gateway,
    )

    return {
        "conn": conn,
        "catalog": catalog,
        "pin": pin,
        "manager": manager,
        "actor": actor,
        "source_id": "m2m_source",
        "resolver": SemanticResolver(),
        "compiler": GovernedQueryCompiler(dialect="sqlite"),
        "gateway": gateway,
    }


def test_single_many_to_many_junction_query(m2m_environment):
    """Test 1: Single Many-to-Many query via junction table (orders <-> order_tags <-> tags).
    
    Orders tagged:
    - Tech: ord_1, ord_2 (distinct count = 2)
    - Urgent: ord_1 (distinct count = 1)
    - Wholesale: ord_3 (distinct count = 1)
    """
    env = m2m_environment
    manager = env["manager"]
    actor = env["actor"]
    source_id = env["source_id"]

    turn, answer = manager.submit_turn(
        actor=actor,
        conversation_id="conv_m2m_single",
        source_id=source_id,
        question="Order count by tag in 2024.",
        proposed_llm_json='{"measure": "order_count", "family": "TOTAL", "dimensions": ["tag_name"], "current_period": "2024"}',
    )

    assert turn.status == TurnStatus.ANSWERED, f"Turn status {turn.status}, err: {turn.error_message}, decline: {turn.decline_reason}"
    assert answer is not None

    rows = {r["tag_name"]: int(Decimal(r["Value"])) for r in answer.table_rows}
    assert rows["Tech"] == 2
    assert rows["Urgent"] == 1
    assert rows["Wholesale"] == 1


def test_multiple_many_to_many_junctions_on_same_base(m2m_environment):
    """Test 2: Multiple Many-to-Many relations concurrently on the same base table:
    - M:N #1: orders <-> order_tags <-> tags
    - M:N #2: orders <-> order_promos <-> promotions
    
    Verifies that the compiler emits all 4 join clauses in correct topological order
    and returns exact grouped metrics.
    """
    env = m2m_environment
    manager = env["manager"]
    actor = env["actor"]
    source_id = env["source_id"]

    turn, answer = manager.submit_turn(
        actor=actor,
        conversation_id="conv_m2m_multi",
        source_id=source_id,
        question="Order count by tag and promo code in 2024.",
        proposed_llm_json='{"measure": "order_count", "family": "TOTAL", "dimensions": ["tag_name", "promo_code"], "current_period": "2024"}',
    )

    assert turn.status == TurnStatus.ANSWERED, f"Turn status {turn.status}, err: {turn.error_message}, decline: {turn.decline_reason}"
    assert answer is not None

    results = {(r["tag_name"], r["promo_code"]): int(Decimal(r["Value"])) for r in answer.table_rows}
    # ord_1: Tech + BLACKFRIDAY, Urgent + BLACKFRIDAY
    # ord_2: Tech + SUMMER
    # ord_3: Wholesale + BLACKFRIDAY
    assert results[("Tech", "BLACKFRIDAY")] == 1
    assert results[("Urgent", "BLACKFRIDAY")] == 1
    assert results[("Tech", "SUMMER")] == 1
    assert results[("Wholesale", "BLACKFRIDAY")] == 1


def test_three_hop_linear_relation(m2m_environment):
    """Test 3: 3-hop linear join (orders -> customers -> cities -> regions).
    
    North America: cust_1 (ord_1: $100 + ord_3: $300) = $400.00
    Europe: cust_2 (ord_2: $200) = $200.00
    Total = $600.00.
    """
    env = m2m_environment
    manager = env["manager"]
    actor = env["actor"]
    source_id = env["source_id"]

    turn, answer = manager.submit_turn(
        actor=actor,
        conversation_id="conv_3hop",
        source_id=source_id,
        question="Sales revenue by region in 2024.",
        proposed_llm_json='{"measure": "sales_revenue", "family": "TOTAL", "dimensions": ["region_name"], "current_period": "2024"}',
    )

    assert turn.status == TurnStatus.ANSWERED, f"err: {turn.error_message}, decline: {turn.decline_reason}"
    assert answer is not None

    rows = {r["region_name"]: Decimal(r["Value"].replace(",", "")) for r in answer.table_rows}
    assert rows["North America"] == Decimal("400.00")
    assert rows["Europe"] == Decimal("200.00")
    assert sum(rows.values()) == Decimal("600.00")


def test_junction_table_with_attributes_and_relations(m2m_environment):
    """Test 4: Junction table with attributes and relations.
    - Attribute on junction table: order_tags.priority
    - Relation from junction table to users: order_tags -> users (reviewer_name)
    """
    env = m2m_environment
    manager = env["manager"]
    actor = env["actor"]
    source_id = env["source_id"]

    # Query grouping by junction attribute 'priority'
    turn, answer = manager.submit_turn(
        actor=actor,
        conversation_id="conv_junction_attr",
        source_id=source_id,
        question="Order count by priority in 2024.",
        proposed_llm_json='{"measure": "order_count", "family": "TOTAL", "dimensions": ["priority"], "current_period": "2024"}',
    )
    assert turn.status == TurnStatus.ANSWERED
    rows_prio = {r["priority"]: int(Decimal(r["Value"])) for r in answer.table_rows}
    assert rows_prio["HIGH"] == 1      # ord_1
    assert rows_prio["CRITICAL"] == 1  # ord_1
    assert rows_prio["LOW"] == 1       # ord_2
    assert rows_prio["MEDIUM"] == 1    # ord_3

    # Query grouping through junction to relation 'reviewer_name'
    turn2, answer2 = manager.submit_turn(
        actor=actor,
        conversation_id="conv_junction_rel",
        source_id=source_id,
        question="Order count by reviewer in 2024.",
        proposed_llm_json='{"measure": "order_count", "family": "TOTAL", "dimensions": ["reviewer_name"], "current_period": "2024"}',
    )
    assert turn2.status == TurnStatus.ANSWERED
    rows_rev = {r["reviewer_name"]: int(Decimal(r["Value"])) for r in answer2.table_rows}
    assert rows_rev["Alice"] == 1  # assigned ord_1 (HIGH & CRITICAL) -> distinct orders = 1
    assert rows_rev["Bob"] == 2    # assigned ord_2 and ord_3 -> distinct orders = 2


def test_zero_fanout_mathematical_integrity_with_many_to_many_filter(m2m_environment):
    """Test 5: Zero fanout mathematical integrity with Many-to-Many filter.
    
    ord_1 has amount $100.00 and has TWO tags: 'Tech' and 'Urgent'.
    ord_2 has amount $200.00 and has ONE tag: 'Tech'.
    
    When filtering by tag 'Tech':
    The true parent total is 100 + 200 = $300.00.
    Because ord_1 has 2 tags, joining order_tags naively in the outer query would duplicate ord_1.
    The compiler must use an EXISTS semi-join spanning the junction table to guarantee exactly $300.00.
    """
    env = m2m_environment
    manager = env["manager"]
    actor = env["actor"]
    source_id = env["source_id"]

    turn, answer = manager.submit_turn(
        actor=actor,
        conversation_id="conv_m2m_zero_fanout_filter",
        source_id=source_id,
        question="Total sales in 2024 for Tech orders.",
        proposed_llm_json='{"measure": "sales_revenue", "family": "TOTAL", "current_period": "2024", "filters": [{"dimension": "tag_name", "operator": "=", "values": ["Tech"]}]}',
    )

    assert turn.status == TurnStatus.ANSWERED
    assert answer is not None
    kpi_clean = answer.kpi_cards[0].value.replace(",", "")
    assert Decimal(kpi_clean) == Decimal("300.00"), f"Expected 300.00, got {kpi_clean}. Fanout occurred!"


def test_unapproved_allocation_rejection_on_many_to_many(m2m_environment):
    """Test 6: Grouping unallocated parent revenue across M:N child dimension is DECLINED."""
    env = m2m_environment
    manager = env["manager"]
    actor = env["actor"]
    source_id = env["source_id"]

    turn, answer = manager.submit_turn(
        actor=actor,
        conversation_id="conv_m2m_decline_unallocated",
        source_id=source_id,
        question="Sales revenue by tag in 2024.",
        proposed_llm_json='{"measure": "sales_revenue", "family": "TOTAL", "dimensions": ["tag_name"], "current_period": "2024"}',
    )

    assert turn.status == TurnStatus.DECLINED
    assert answer is None
    assert "cannot be aggregated across child dimension" in turn.decline_reason
    assert "allocation policy" in turn.decline_reason.lower()


def test_auto_introspection_of_junction_and_many_to_many():
    """Test 7: Auto-introspection of uploaded database containing junction tables and M:N foreign keys."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE orders (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                total_amount NUMERIC NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE tags (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                title TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE order_tag_bridge (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                order_id TEXT NOT NULL,
                tag_id TEXT NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders(id),
                FOREIGN KEY (tag_id) REFERENCES tags(id)
            )
        """)
        conn.commit()
        conn.close()

        catalog, manifest = introspect_sqlite_database(
            db_path=Path(db_path),
            source_id="uploaded_m2m_source",
            default_tenant_id="tenant_custom",
        )

        assert "orders" in catalog.tables
        assert "tags" in catalog.tables
        assert "order_tag_bridge" in catalog.tables

        # Check detected joins
        join_edges = catalog.joins
        assert len(join_edges) >= 2

        # Verify bridge joins detected
        has_order_join = any(
            (e.from_table == "order_tag_bridge" and e.to_table == "orders") or
            (e.from_table == "orders" and e.to_table == "order_tag_bridge")
            for e in join_edges
        )
        has_tag_join = any(
            (e.from_table == "order_tag_bridge" and e.to_table == "tags") or
            (e.from_table == "tags" and e.to_table == "order_tag_bridge")
            for e in join_edges
        )

        assert has_order_join, "Failed to auto-introspect join between bridge and orders"
        assert has_tag_join, "Failed to auto-introspect join between bridge and tags"

        # Verify path finding on the introspected catalog
        resolver = SemanticResolver()
        path = resolver._find_path(catalog, "orders", "tags")
        assert len(path) == 2, f"Expected 2-hop path across bridge, got {len(path)}"

    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
