"""Build a deterministic, join-heavy SQLite demo source for browser and pipeline tests."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


SOURCE_ID = "complex_relations"


def add_complex_join_demo(directory: str | Path) -> Path:
    """Add realistic FK/junction tables to a demo DB and register their governed catalog.

    The caller should first create the normal demo with :func:`backend.demo.initialize`.
    Existing demo tables and catalog entries are preserved.
    """
    directory = Path(directory).resolve()
    db_path = directory / "source.sqlite"
    config_path = directory / "runtime.json"
    if not db_path.is_file() or not config_path.is_file():
        raise FileNotFoundError("Initialize the base demo before adding complex join data")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS m2m_regions (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, region_name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS m2m_cities (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, city_name TEXT NOT NULL,
            region_id TEXT NOT NULL REFERENCES m2m_regions(id)
        );
        CREATE TABLE IF NOT EXISTS m2m_customers (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, customer_name TEXT NOT NULL,
            city_id TEXT NOT NULL REFERENCES m2m_cities(id)
        );
        CREATE TABLE IF NOT EXISTS m2m_orders (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
            customer_id TEXT NOT NULL REFERENCES m2m_customers(id),
            order_date TEXT NOT NULL, order_year TEXT NOT NULL, total_amount NUMERIC NOT NULL
        );
        CREATE TABLE IF NOT EXISTS m2m_categories (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, category_name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS m2m_products (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, product_name TEXT NOT NULL,
            category_id TEXT NOT NULL REFERENCES m2m_categories(id)
        );
        CREATE TABLE IF NOT EXISTS m2m_order_items (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
            order_id TEXT NOT NULL REFERENCES m2m_orders(id),
            product_id TEXT NOT NULL REFERENCES m2m_products(id),
            quantity INTEGER NOT NULL, line_total NUMERIC NOT NULL
        );
        CREATE TABLE IF NOT EXISTS m2m_tags (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, tag_name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS m2m_order_tags (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
            order_id TEXT NOT NULL REFERENCES m2m_orders(id),
            tag_id TEXT NOT NULL REFERENCES m2m_tags(id),
            priority TEXT NOT NULL, assigned_by TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS m2m_promotions (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, promo_code TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS m2m_order_promotions (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
            order_id TEXT NOT NULL REFERENCES m2m_orders(id),
            promotion_id TEXT NOT NULL REFERENCES m2m_promotions(id)
        );
        """
    )

    # Replace only this fixture's rows so regenerating the demo is deterministic.
    for table in (
        "m2m_order_promotions", "m2m_order_tags", "m2m_order_items",
        "m2m_orders", "m2m_products", "m2m_customers", "m2m_cities",
        "m2m_promotions", "m2m_tags", "m2m_categories", "m2m_regions",
    ):
        conn.execute(f"DELETE FROM {table}")

    data = {
        "m2m_regions": [("r_na", "North America"), ("r_eu", "Europe")],
        "m2m_cities": [("c_nyc", "New York", "r_na"), ("c_ber", "Berlin", "r_eu")],
        "m2m_customers": [("cu_1", "Acme", "c_nyc"), ("cu_2", "Globex", "c_ber")],
        "m2m_orders": [
            ("o_1", "cu_1", "2024-01-10", "2024", 100),
            ("o_2", "cu_2", "2024-02-10", "2024", 200),
            ("o_3", "cu_1", "2024-03-10", "2024", 300),
        ],
        "m2m_categories": [("cat_hw", "Hardware"), ("cat_sw", "Software")],
        "m2m_products": [("p_laptop", "Laptop", "cat_hw"), ("p_license", "License", "cat_sw")],
        "m2m_order_items": [
            ("li_1", "o_1", "p_laptop", 1, 80),
            ("li_2", "o_1", "p_license", 2, 20),
            ("li_3", "o_2", "p_license", 1, 200),
            ("li_4", "o_3", "p_laptop", 3, 300),
        ],
        "m2m_tags": [("t_tech", "Tech"), ("t_urgent", "Urgent"), ("t_wholesale", "Wholesale")],
        "m2m_order_tags": [
            ("ot_1", "o_1", "t_tech", "HIGH", "Alice"),
            ("ot_2", "o_1", "t_urgent", "CRITICAL", "Alice"),
            ("ot_3", "o_2", "t_tech", "LOW", "Bob"),
            ("ot_4", "o_3", "t_wholesale", "MEDIUM", "Bob"),
        ],
        "m2m_promotions": [("pr_bf", "BLACKFRIDAY"), ("pr_su", "SUMMER")],
        "m2m_order_promotions": [
            ("op_1", "o_1", "pr_bf"), ("op_2", "o_2", "pr_su"), ("op_3", "o_3", "pr_bf")
        ],
    }
    inserts = {
        "m2m_regions": "INSERT INTO m2m_regions VALUES (?, 'tenant_a', ?)",
        "m2m_cities": "INSERT INTO m2m_cities VALUES (?, 'tenant_a', ?, ?)",
        "m2m_customers": "INSERT INTO m2m_customers VALUES (?, 'tenant_a', ?, ?)",
        "m2m_orders": "INSERT INTO m2m_orders VALUES (?, 'tenant_a', ?, ?, ?, ?)",
        "m2m_categories": "INSERT INTO m2m_categories VALUES (?, 'tenant_a', ?)",
        "m2m_products": "INSERT INTO m2m_products VALUES (?, 'tenant_a', ?, ?)",
        "m2m_order_items": "INSERT INTO m2m_order_items VALUES (?, 'tenant_a', ?, ?, ?, ?)",
        "m2m_tags": "INSERT INTO m2m_tags VALUES (?, 'tenant_a', ?)",
        "m2m_order_tags": "INSERT INTO m2m_order_tags VALUES (?, 'tenant_a', ?, ?, ?, ?)",
        "m2m_promotions": "INSERT INTO m2m_promotions VALUES (?, 'tenant_a', ?)",
        "m2m_order_promotions": "INSERT INTO m2m_order_promotions VALUES (?, 'tenant_a', ?, ?)",
    }
    for table, rows in data.items():
        conn.executemany(inserts[table], rows)
    conn.commit()
    conn.close()

    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["tenant_source_permissions"].setdefault("tenant_a", [])
    if SOURCE_ID not in config["tenant_source_permissions"]["tenant_a"]:
        config["tenant_source_permissions"]["tenant_a"].append(SOURCE_ID)
    config["catalogs"] = [c for c in config["catalogs"] if c["source_id"] != SOURCE_ID]
    config["catalogs"].append(_catalog())
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return db_path


def _catalog() -> dict:
    tables = {
        "m2m_regions": ("region", ["id", "tenant_id", "region_name"]),
        "m2m_cities": ("city", ["id", "tenant_id", "city_name", "region_id"]),
        "m2m_customers": ("customer", ["id", "tenant_id", "customer_name", "city_id"]),
        "m2m_orders": ("order", ["id", "tenant_id", "customer_id", "order_date", "order_year", "total_amount"]),
        "m2m_categories": ("category", ["id", "tenant_id", "category_name"]),
        "m2m_products": ("product", ["id", "tenant_id", "product_name", "category_id"]),
        "m2m_order_items": ("order_item", ["id", "tenant_id", "order_id", "product_id", "quantity", "line_total"]),
        "m2m_tags": ("tag", ["id", "tenant_id", "tag_name"]),
        "m2m_order_tags": ("order_tag", ["id", "tenant_id", "order_id", "tag_id", "priority", "assigned_by"]),
        "m2m_promotions": ("promotion", ["id", "tenant_id", "promo_code"]),
        "m2m_order_promotions": ("order_promotion", ["id", "tenant_id", "order_id", "promotion_id"]),
    }
    numeric = {"total_amount", "quantity", "line_total"}
    string_names = {
        "region_name", "city_name", "customer_name", "order_date", "category_name",
        "product_name", "tag_name", "priority", "assigned_by", "promo_code",
    }
    columns = {}
    for table, (_, names) in tables.items():
        columns[table] = {name: ("DECIMAL" if name in numeric else "STRING" if name in string_names else "INTEGER") for name in names}
    table_defs = {
        table: {"table_name": table, "grain": grain, "tenant_column": "tenant_id", "primary_key": ["id"], "columns": columns[table]}
        for table, (grain, _) in tables.items()
    }
    edges = [
        ("m2m_orders", "m2m_customers", "customer_id", "id", "MANY_TO_ONE"),
        ("m2m_customers", "m2m_cities", "city_id", "id", "MANY_TO_ONE"),
        ("m2m_cities", "m2m_regions", "region_id", "id", "MANY_TO_ONE"),
        ("m2m_products", "m2m_categories", "category_id", "id", "MANY_TO_ONE"),
        ("m2m_order_items", "m2m_orders", "order_id", "id", "MANY_TO_ONE"),
        ("m2m_order_items", "m2m_products", "product_id", "id", "MANY_TO_ONE"),
        ("m2m_orders", "m2m_order_tags", "id", "order_id", "ONE_TO_MANY"),
        ("m2m_order_tags", "m2m_tags", "tag_id", "id", "MANY_TO_ONE"),
        ("m2m_orders", "m2m_order_promotions", "id", "order_id", "ONE_TO_MANY"),
        ("m2m_order_promotions", "m2m_promotions", "promotion_id", "id", "MANY_TO_ONE"),
    ]
    joins = [dict(from_table=a, to_table=b, from_column=ac, to_column=bc, cardinality=card, join_type="INNER") for a, b, ac, bc, card in edges]
    dim_specs = {
        "region_name": ("m2m_regions", "region_name"), "city_name": ("m2m_cities", "city_name"),
        "customer_name": ("m2m_customers", "customer_name"), "category_name": ("m2m_categories", "category_name"),
        "product_name": ("m2m_products", "product_name"), "tag_name": ("m2m_tags", "tag_name"),
        "promo_code": ("m2m_promotions", "promo_code"), "priority": ("m2m_order_tags", "priority"),
        "assigned_by": ("m2m_order_tags", "assigned_by"), "order_date": ("m2m_orders", "order_date"),
        "order_year": ("m2m_orders", "order_year"),
    }
    dimensions = {k: {"id": k, "name": k.replace("_", " ").title(), "table": t, "column": c, "data_type": "STRING"} for k, (t, c) in dim_specs.items()}
    allowed = list(dimensions)
    measures = {
        "order_revenue": {"id": "order_revenue", "name": "Order Revenue", "description": "Revenue at order grain", "table": "m2m_orders", "column": "total_amount", "aggregation": "SUM", "base_grain": "order", "unit": "USD", "allowed_dimensions": allowed},
        "order_count": {"id": "order_count", "name": "Order Count", "description": "Distinct orders", "table": "m2m_orders", "column": "id", "aggregation": "COUNT_DISTINCT", "base_grain": "order", "unit": "Orders", "allowed_dimensions": allowed},
        "line_revenue": {"id": "line_revenue", "name": "Line Revenue", "description": "Revenue at order item grain", "table": "m2m_order_items", "column": "line_total", "aggregation": "SUM", "base_grain": "order_item", "unit": "USD", "allowed_dimensions": allowed},
        "item_quantity": {"id": "item_quantity", "name": "Item Quantity", "description": "Units at order item grain", "table": "m2m_order_items", "column": "quantity", "aggregation": "SUM", "base_grain": "order_item", "unit": "Units", "allowed_dimensions": allowed},
    }
    return {"release_id": "complex_relations_v1", "epoch": 1, "source_id": SOURCE_ID,
            "tables": table_defs, "measures": measures, "dimensions": dimensions, "joins": joins,
            "default_period_dimension": "order_year"}
