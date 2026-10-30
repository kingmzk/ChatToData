"""Tests for period dimension selection, date inference, and CamelCase key exclusion."""
import sqlite3
from pathlib import Path
import pytest

from backend.catalog.extractor import _infer_data_type, introspect_sqlite_database
from backend.catalog.schema import DataType
from backend.semantic.resolver import SemanticResolver


def test_date_inference_for_temporal_suffixes():
    """Verify *_at and *_on are inferred as dates when non-numeric."""
    assert _infer_data_type("TEXT", "placed_at") == DataType.DATE
    assert _infer_data_type("TEXT", "created_on") == DataType.DATE
    assert _infer_data_type("INTEGER", "retry_at") == DataType.INTEGER


def test_camelcase_primary_key_not_measure(tmp_path: Path):
    """Verify CamelCase ID primary keys are not exposed as measures."""
    db_file = tmp_path / "test_camel.sqlite"
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE SalesOrderHeader ("
        "SalesOrderID INTEGER PRIMARY KEY, "
        "TotalDue REAL, "
        "tenant_id TEXT"
        ")"
    )
    cur.execute("INSERT INTO SalesOrderHeader VALUES (1, 150.50, 'tenant_a')")
    conn.commit()
    conn.close()

    catalog, _ = introspect_sqlite_database(db_file, "source_camel")
    assert "SalesOrderHeader_SalesOrderID" not in catalog.measures
    assert "SalesOrderHeader_TotalDue" in catalog.measures


def test_period_dimension_prefers_base_table_date(tmp_path: Path):
    """Verify period dimension resolution prefers base-table date over distant year column."""
    db_file = tmp_path / "test_period.sqlite"
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE orders ("
        "order_id INTEGER PRIMARY KEY, "
        "placed_at TEXT, "
        "total_amount REAL, "
        "tenant_id TEXT"
        ")"
    )
    cur.execute(
        "CREATE TABLE territory ("
        "territory_id INTEGER PRIMARY KEY, "
        "SalesLastYear REAL, "
        "name TEXT, "
        "tenant_id TEXT"
        ")"
    )
    cur.execute("INSERT INTO orders VALUES (1, '2024-05-01T00:00:00', 869.00, 'tenant_a')")
    cur.execute("INSERT INTO territory VALUES (1, 50000.0, 'Northwest', 'tenant_a')")
    conn.commit()
    conn.close()

    catalog, _ = introspect_sqlite_database(db_file, "source_period")
    
    assert catalog.tables["orders"].columns["placed_at"] == DataType.DATE

    chosen_period_dim = SemanticResolver._pick_period_dimension(
        catalog, "orders", ("year", "period", "date")
    )
    assert chosen_period_dim is not None
    assert "placed_at" in chosen_period_dim
