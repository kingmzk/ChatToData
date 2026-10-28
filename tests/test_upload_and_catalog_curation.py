"""Tests verifying upload integrity, tenant column injection, catalog curation, and dimension guards."""
import sqlite3
import pytest
from pathlib import Path
from decimal import Decimal

from backend.catalog.extractor import (
    introspect_sqlite_database,
    ensure_tenant_columns,
    _matches_table,
)
from backend.domain.models import ApiActor
from backend.intent.schema import StructuredIntent, AnalysisFamily
from backend.intent.validator import IntentValidator
from backend.catalog.store import SemanticCatalogStore
from backend.catalog.schema import (
    SemanticCatalog, TableDefinition, MeasureDefinition, DimensionDefinition,
    DataType, AggregationType
)
from backend.presentation.fallback import DeterministicPresentationFallback
from backend.domain.facts import ProtectedFact
from fastapi.testclient import TestClient
from backend.api.app import create_app
from backend.storage.repository import PersistentApplicationStore


@pytest.fixture
def api_client(test_setup):
    """Create test client with shared configured services."""
    store = PersistentApplicationStore(":memory:")
    app = create_app(
        store=store,
        manager=test_setup["manager"],
        security=test_setup["security"],
        catalogs=test_setup["catalogs"],
    )
    client = TestClient(app)
    return client, store, test_setup


def test_ensure_tenant_columns_injects_and_populates(tmp_path):
    """Verify that tables missing tenant_id have it physically injected and populated."""
    db_file = tmp_path / "raw_legacy.sqlite"
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, price NUMERIC)")
    cur.execute("INSERT INTO products VALUES (1, 'Widget', 19.99), (2, 'Gadget', 49.99)")
    conn.commit()
    conn.close()

    # Run injection
    modified = ensure_tenant_columns(db_file, default_tenant_id="tenant_x")
    assert "products" in modified

    # Verify column physically exists in schema and rows
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cols = [c[1] for c in cur.execute("PRAGMA table_info('products')").fetchall()]
    assert "tenant_id" in cols

    rows = cur.execute("SELECT id, name, price, tenant_id FROM products").fetchall()
    conn.close()
    assert len(rows) == 2
    assert rows[0][3] == "tenant_x"
    assert rows[1][3] == "tenant_x"


def test_morphological_table_matching():
    """Verify entity-to-table matching handles compound names, plurals, and prefix stripping."""
    assert _matches_table("variant", "product_variants") is True
    assert _matches_table("customer", "customers") is True
    assert _matches_table("order", "commerce_orders") is True
    assert _matches_table("user", "tbl_users") is True
    assert _matches_table("category", "categories") is True
    assert _matches_table("item", "order_items") is True
    assert _matches_table("tag", "tags") is True
    assert _matches_table("unrelated", "orders") is False


def test_introspect_catalog_curation_and_reachability(tmp_path):
    """Verify catalog extraction scopes allowed_dimensions, ignores ID measures, and creates count/revenue."""
    db_file = tmp_path / "store.sqlite"
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE customers (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            country TEXT NOT NULL
        );
    """)
    cur.execute("""
        CREATE TABLE orders (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            total_amount NUMERIC NOT NULL,
            order_year INTEGER NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        );
    """)
    cur.execute("""
        CREATE TABLE unrelated_audit (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            action TEXT NOT NULL,
            status_code INTEGER NOT NULL
        );
    """)
    conn.commit()
    conn.close()

    catalog, manifest = introspect_sqlite_database(db_file, "store_test")

    # 1. order_year and status_code must NOT be converted to SUM measures
    assert "orders_order_year" not in catalog.measures
    assert "unrelated_audit_status_code" not in catalog.measures

    # 2. orders_total_amount and revenue must exist
    assert "orders_total_amount" in catalog.measures
    assert "revenue" in catalog.measures

    # 3. Entity count measures on primary tables must exist
    assert "orders_count" in catalog.measures
    assert catalog.measures["orders_count"].aggregation == AggregationType.COUNT_DISTINCT
    assert "customers_count" in catalog.measures

    # 4. Scoped allowed_dimensions: orders measures can access orders + customers dims, but NOT unrelated_audit dims
    meas = catalog.measures["orders_total_amount"]
    allowed = meas.allowed_dimensions
    assert "customers_country" in allowed or "country" in allowed
    assert "unrelated_audit_action" not in allowed


def test_validator_guard_strips_hallucinated_metric_dimensions():
    """Verify IntentValidator drops stray metric column dimensions when no breakdown was requested."""
    cat = SemanticCatalog(
        release_id="rel_v1",
        epoch=1,
        source_id="test_src",
        tables={
            "orders": TableDefinition(table_name="orders", primary_key=("id",), tenant_column="tenant_id"),
            "payments": TableDefinition(table_name="payments", primary_key=("id",), tenant_column="tenant_id"),
        },
        measures={
            "orders_amount": MeasureDefinition(
                id="orders_amount",
                name="Order Amount",
                description="Total order amount",
                table="orders",
                column="amount",
                aggregation=AggregationType.SUM,
                base_grain="order_grain",
                unit="USD",
                allowed_dimensions=("amount", "payments_amount", "country"),
            ),
        },
        dimensions={
            "amount": DimensionDefinition(id="amount", name="Amount", table="orders", column="amount", data_type=DataType.DECIMAL),
            "payments_amount": DimensionDefinition(id="payments_amount", name="Payment Amount", table="payments", column="amount", data_type=DataType.DECIMAL),
            "country": DimensionDefinition(id="country", name="Country", table="orders", column="country", data_type=DataType.STRING),
        },
    )
    cat_store = SemanticCatalogStore()
    cat_store.register_catalog(cat)
    actor = ApiActor(tenant_id="tenant_a", subject_id="user_alice", roles=("analyst",))
    pin = cat_store.pin(actor.tenant_id, "test_src")
    voc = cat_store.get_accessible_vocabulary(actor, pin)

    validator = IntentValidator()

    # Query without grouping keyword: "What is the total order amount?"
    intent_hallucinated = StructuredIntent(
        measure="orders_amount",
        family=AnalysisFamily.TOTAL,
        dimensions=("payments_amount",),
    )
    res = validator.validate_intent(
        intent=intent_hallucinated,
        vocabulary=voc,
        user_question="What is the total order amount?",
        turn_id="turn_1",
        tenant_id="tenant_a",
        subject_id="user_alice",
        catalog=cat,
    )
    # Stray metric dimension must be stripped, leaving a clean scalar aggregate query
    assert res.status == "VALID"
    assert res.validated_intent.dimensions == ()

    # Query WITH explicit grouping: "Total order amount by country"
    intent_with_by = StructuredIntent(
        measure="orders_amount",
        family=AnalysisFamily.TOTAL,
        dimensions=("country",),
    )
    res_by = validator.validate_intent(
        intent=intent_with_by,
        vocabulary=voc,
        user_question="Total order amount by country",
        turn_id="turn_2",
        tenant_id="tenant_a",
        subject_id="user_alice",
        catalog=cat,
    )
    assert res_by.status == "VALID"
    assert res_by.validated_intent.dimensions == ("country",)


def test_presentation_fallback_zero_records_note():
    """Verify narrative discloses 0 matching records when query returns empty matching population."""
    from backend.domain.facts import SealedEvidencePackage
    fallback = DeterministicPresentationFallback()
    facts = [
        ProtectedFact(
            fact_id="fact_total_001",
            metric="Revenue",
            calculation_type="TOTAL",
            value=Decimal("0.00"),
            unit="USD",
            period="2024",
            semantic_release="rel_1",
            lineage=(),
            completeness_status="COMPLETE",
            assumptions=["Query returned 0 matching records."],
        )
    ]
    pkg = SealedEvidencePackage.seal(
        package_id="pkg_test",
        turn_id="t1",
        tenant_id="tenant_a",
        semantic_release_id="rel_1",
        facts=facts,
    )
    view = fallback.render(pkg)
    assert "0.00 USD" in view.summary_text
    assert "0 matching records" in view.summary_text.lower()


def test_upload_endpoint_validation_and_rejection(api_client, tmp_path):
    """Verify upload route rejects corrupt/invalid files with appropriate status codes."""
    client, _, _ = api_client

    # 1. Non-SQLite file (bad header) -> 400 Bad Request
    res_bad = client.post(
        "/api/v1/sources/upload",
        files={"file": ("malicious.sqlite", b"NOT_SQLITE_DATA_PADDING_TO_OVER_100_BYTES_" * 5, "application/octet-stream")},
        data={"source_name": "bad_src"},
    )
    assert res_bad.status_code == 400
    assert "Missing standard SQLite header" in res_bad.json()["detail"]

    # 2. Corrupt SQLite file (header present but corrupted data) -> 422 Unprocessable Entity
    corrupt_content = b"SQLite format 3\x00" + b"\x00" * 100 + b"CORRUPTED_GARBAGE_PAYLOAD" * 10
    res_corrupt = client.post(
        "/api/v1/sources/upload",
        files={"file": ("corrupt.sqlite", corrupt_content, "application/octet-stream")},
        data={"source_name": "corrupt_src"},
    )
    assert res_corrupt.status_code == 422

    # 3. Valid SQLite file without tenant column -> 201 Created with tenant injection
    valid_db = tmp_path / "valid.sqlite"
    conn = sqlite3.connect(valid_db)
    cur = conn.cursor()
    cur.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, amount NUMERIC)")
    cur.execute("INSERT INTO orders VALUES (1, 100.50)")
    conn.commit()
    conn.close()

    res_valid = client.post(
        "/api/v1/sources/upload",
        files={"file": ("valid.sqlite", valid_db.read_bytes(), "application/octet-stream")},
        data={"source_name": "valid_uploaded_src"},
    )
    assert res_valid.status_code == 201
    assert res_valid.json()["source_id"] == "valid_uploaded_src"

