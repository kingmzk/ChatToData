"""Deterministic enterprise database generator with complex, multi-hop, and many-to-many relationships.

Creates 'enterprise_nexus' with:
1. Multi-hop chained joins: Continents -> Regions -> Countries -> Customers/Warehouses -> Orders -> Order Items -> Products -> Categories -> Suppliers
2. Many-to-Many junctions:
   - Products <-> Tags (via nexus_product_tags)
   - Orders <-> Promotions (via nexus_order_promotions)
   - Customers <-> Loyalty Tiers (via nexus_customer_loyalty)
3. Self-referencing hierarchical joins:
   - nexus_categories (parent_category_id -> nexus_categories.id)
   - nexus_employees (manager_id -> nexus_employees.id)
4. Multi-dimensional operational joins:
   - nexus_shipments (linking Orders, Warehouses, and Carriers)
   - nexus_inventory (linking Warehouses and Products)
   - nexus_customer_reviews (linking Customers and Products)
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SOURCE_ID = "enterprise_nexus"


def create_standalone_database(target_path: str | Path) -> Path:
    """Create a standalone SQLite database file with all foreign keys and data for testing and upload."""
    target_path = Path(target_path).resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        target_path.unlink()

    conn = sqlite3.connect(target_path)
    conn.execute("PRAGMA foreign_keys = ON")
    
    # We create tables without prefix for the standalone database
    conn.executescript(
        """
        CREATE TABLE continents (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            continent_name TEXT NOT NULL
        );

        CREATE TABLE regions (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            region_name TEXT NOT NULL,
            continent_id TEXT NOT NULL REFERENCES continents(id)
        );

        CREATE TABLE countries (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            country_name TEXT NOT NULL,
            region_id TEXT NOT NULL REFERENCES regions(id)
        );

        CREATE TABLE warehouses (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            warehouse_name TEXT NOT NULL,
            country_id TEXT NOT NULL REFERENCES countries(id),
            capacity_sqft INTEGER NOT NULL
        );

        CREATE TABLE categories (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            category_name TEXT NOT NULL,
            parent_category_id TEXT REFERENCES categories(id)
        );

        CREATE TABLE suppliers (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            supplier_name TEXT NOT NULL,
            country_id TEXT NOT NULL REFERENCES countries(id),
            reliability_score NUMERIC NOT NULL
        );

        CREATE TABLE products (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            product_name TEXT NOT NULL,
            category_id TEXT NOT NULL REFERENCES categories(id),
            supplier_id TEXT NOT NULL REFERENCES suppliers(id),
            list_price NUMERIC NOT NULL,
            cost_price NUMERIC NOT NULL
        );

        CREATE TABLE tags (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            tag_name TEXT NOT NULL
        );

        CREATE TABLE product_tags (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            product_id TEXT NOT NULL REFERENCES products(id),
            tag_id TEXT NOT NULL REFERENCES tags(id)
        );

        CREATE TABLE inventory (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            warehouse_id TEXT NOT NULL REFERENCES warehouses(id),
            product_id TEXT NOT NULL REFERENCES products(id),
            stock_quantity INTEGER NOT NULL,
            reorder_point INTEGER NOT NULL
        );

        CREATE TABLE departments (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            department_name TEXT NOT NULL
        );

        CREATE TABLE employees (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            employee_name TEXT NOT NULL,
            department_id TEXT NOT NULL REFERENCES departments(id),
            manager_id TEXT REFERENCES employees(id),
            salary NUMERIC NOT NULL
        );

        CREATE TABLE customers (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            country_id TEXT NOT NULL REFERENCES countries(id),
            customer_tier TEXT NOT NULL
        );

        CREATE TABLE loyalty_tiers (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            tier_name TEXT NOT NULL,
            discount_rate NUMERIC NOT NULL
        );

        CREATE TABLE customer_loyalty (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            customer_id TEXT NOT NULL REFERENCES customers(id),
            tier_id TEXT NOT NULL REFERENCES loyalty_tiers(id),
            points_balance INTEGER NOT NULL
        );

        CREATE TABLE orders (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            customer_id TEXT NOT NULL REFERENCES customers(id),
            sales_rep_id TEXT NOT NULL REFERENCES employees(id),
            order_date TEXT NOT NULL,
            order_year TEXT NOT NULL,
            status TEXT NOT NULL,
            total_amount NUMERIC NOT NULL
        );

        CREATE TABLE order_items (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            order_id TEXT NOT NULL REFERENCES orders(id),
            product_id TEXT NOT NULL REFERENCES products(id),
            quantity INTEGER NOT NULL,
            unit_price NUMERIC NOT NULL,
            line_total NUMERIC NOT NULL,
            profit_margin NUMERIC NOT NULL
        );

        CREATE TABLE promotions (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            promo_code TEXT NOT NULL,
            discount_percent NUMERIC NOT NULL
        );

        CREATE TABLE order_promotions (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            order_id TEXT NOT NULL REFERENCES orders(id),
            promotion_id TEXT NOT NULL REFERENCES promotions(id),
            discount_applied NUMERIC NOT NULL
        );

        CREATE TABLE shipments (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            order_id TEXT NOT NULL REFERENCES orders(id),
            warehouse_id TEXT NOT NULL REFERENCES warehouses(id),
            carrier TEXT NOT NULL,
            shipping_cost NUMERIC NOT NULL,
            delivery_days INTEGER NOT NULL
        );

        CREATE TABLE customer_reviews (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            product_id TEXT NOT NULL REFERENCES products(id),
            customer_id TEXT NOT NULL REFERENCES customers(id),
            rating INTEGER NOT NULL,
            review_title TEXT NOT NULL
        );
        """
    )

    _insert_seed_data(conn, prefix="")
    conn.commit()
    conn.close()
    return target_path


def add_enterprise_nexus_demo(directory: str | Path) -> Path:
    """Register enterprise_nexus in demo runtime (source.sqlite and runtime.json)."""
    directory = Path(directory).resolve()
    db_path = directory / "source.sqlite"
    config_path = directory / "runtime.json"
    if not db_path.is_file() or not config_path.is_file():
        raise FileNotFoundError("Demo database and runtime config must exist")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS nexus_continents (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, continent_name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS nexus_regions (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, region_name TEXT NOT NULL,
            continent_id TEXT NOT NULL REFERENCES nexus_continents(id)
        );
        CREATE TABLE IF NOT EXISTS nexus_countries (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, country_name TEXT NOT NULL,
            region_id TEXT NOT NULL REFERENCES nexus_regions(id)
        );
        CREATE TABLE IF NOT EXISTS nexus_warehouses (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, warehouse_name TEXT NOT NULL,
            country_id TEXT NOT NULL REFERENCES nexus_countries(id), capacity_sqft INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS nexus_categories (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, category_name TEXT NOT NULL,
            parent_category_id TEXT REFERENCES nexus_categories(id)
        );
        CREATE TABLE IF NOT EXISTS nexus_suppliers (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, supplier_name TEXT NOT NULL,
            country_id TEXT NOT NULL REFERENCES nexus_countries(id), reliability_score NUMERIC NOT NULL
        );
        CREATE TABLE IF NOT EXISTS nexus_products (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, product_name TEXT NOT NULL,
            category_id TEXT NOT NULL REFERENCES nexus_categories(id),
            supplier_id TEXT NOT NULL REFERENCES nexus_suppliers(id),
            list_price NUMERIC NOT NULL, cost_price NUMERIC NOT NULL
        );
        CREATE TABLE IF NOT EXISTS nexus_tags (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, tag_name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS nexus_product_tags (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
            product_id TEXT NOT NULL REFERENCES nexus_products(id),
            tag_id TEXT NOT NULL REFERENCES nexus_tags(id)
        );
        CREATE TABLE IF NOT EXISTS nexus_inventory (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
            warehouse_id TEXT NOT NULL REFERENCES nexus_warehouses(id),
            product_id TEXT NOT NULL REFERENCES nexus_products(id),
            stock_quantity INTEGER NOT NULL, reorder_point INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS nexus_departments (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, department_name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS nexus_employees (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, employee_name TEXT NOT NULL,
            department_id TEXT NOT NULL REFERENCES nexus_departments(id),
            manager_id TEXT REFERENCES nexus_employees(id),
            salary NUMERIC NOT NULL
        );
        CREATE TABLE IF NOT EXISTS nexus_customers (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, customer_name TEXT NOT NULL,
            country_id TEXT NOT NULL REFERENCES nexus_countries(id),
            customer_tier TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS nexus_loyalty_tiers (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, tier_name TEXT NOT NULL, discount_rate NUMERIC NOT NULL
        );
        CREATE TABLE IF NOT EXISTS nexus_customer_loyalty (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
            customer_id TEXT NOT NULL REFERENCES nexus_customers(id),
            tier_id TEXT NOT NULL REFERENCES nexus_loyalty_tiers(id),
            points_balance INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS nexus_orders (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
            customer_id TEXT NOT NULL REFERENCES nexus_customers(id),
            sales_rep_id TEXT NOT NULL REFERENCES nexus_employees(id),
            order_date TEXT NOT NULL, order_year TEXT NOT NULL,
            status TEXT NOT NULL, total_amount NUMERIC NOT NULL
        );
        CREATE TABLE IF NOT EXISTS nexus_order_items (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
            order_id TEXT NOT NULL REFERENCES nexus_orders(id),
            product_id TEXT NOT NULL REFERENCES nexus_products(id),
            quantity INTEGER NOT NULL, unit_price NUMERIC NOT NULL,
            line_total NUMERIC NOT NULL, profit_margin NUMERIC NOT NULL
        );
        CREATE TABLE IF NOT EXISTS nexus_promotions (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, promo_code TEXT NOT NULL, discount_percent NUMERIC NOT NULL
        );
        CREATE TABLE IF NOT EXISTS nexus_order_promotions (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
            order_id TEXT NOT NULL REFERENCES nexus_orders(id),
            promotion_id TEXT NOT NULL REFERENCES nexus_promotions(id),
            discount_applied NUMERIC NOT NULL
        );
        CREATE TABLE IF NOT EXISTS nexus_shipments (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
            order_id TEXT NOT NULL REFERENCES nexus_orders(id),
            warehouse_id TEXT NOT NULL REFERENCES nexus_warehouses(id),
            carrier TEXT NOT NULL, shipping_cost NUMERIC NOT NULL, delivery_days INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS nexus_customer_reviews (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
            product_id TEXT NOT NULL REFERENCES nexus_products(id),
            customer_id TEXT NOT NULL REFERENCES nexus_customers(id),
            rating INTEGER NOT NULL, review_title TEXT NOT NULL
        );
        """
    )

    _clean_tables(conn, prefix="nexus_")
    _insert_seed_data(conn, prefix="nexus_")
    conn.commit()
    conn.close()

    # Update runtime.json
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["tenant_source_permissions"].setdefault("tenant_a", [])
    if SOURCE_ID not in config["tenant_source_permissions"]["tenant_a"]:
        config["tenant_source_permissions"]["tenant_a"].append(SOURCE_ID)

    config["catalogs"] = [c for c in config["catalogs"] if c["source_id"] != SOURCE_ID]
    config["catalogs"].append(_build_catalog_definition())
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return db_path


def _clean_tables(conn: sqlite3.Connection, prefix: str):
    tables = [
        "customer_reviews", "shipments", "order_promotions", "promotions",
        "order_items", "orders", "customer_loyalty", "loyalty_tiers",
        "customers", "employees", "departments", "inventory",
        "product_tags", "tags", "products", "suppliers",
        "categories", "warehouses", "countries", "regions", "continents"
    ]
    for tbl in tables:
        try:
            conn.execute(f"DELETE FROM {prefix}{tbl}")
        except Exception:
            pass


def _insert_seed_data(conn: sqlite3.Connection, prefix: str):
    p = prefix
    # 1. Continents
    conn.executemany(
        f"INSERT INTO {p}continents VALUES (?, 'tenant_a', ?)",
        [
            ("cont_na", "North America"),
            ("cont_eu", "Europe"),
            ("cont_apac", "Asia Pacific"),
        ],
    )

    # 2. Regions
    conn.executemany(
        f"INSERT INTO {p}regions VALUES (?, 'tenant_a', ?, ?)",
        [
            ("reg_us_east", "US East Coast", "cont_na"),
            ("reg_us_west", "US West Coast", "cont_na"),
            ("reg_eu_west", "Western Europe", "cont_eu"),
            ("reg_apac_east", "East Asia", "cont_apac"),
        ],
    )

    # 3. Countries
    conn.executemany(
        f"INSERT INTO {p}countries VALUES (?, 'tenant_a', ?, ?)",
        [
            ("ctry_us", "United States", "reg_us_east"),
            ("ctry_ca", "Canada", "reg_us_west"),
            ("ctry_de", "Germany", "reg_eu_west"),
            ("ctry_uk", "United Kingdom", "reg_eu_west"),
            ("ctry_jp", "Japan", "reg_apac_east"),
        ],
    )

    # 4. Warehouses
    conn.executemany(
        f"INSERT INTO {p}warehouses VALUES (?, 'tenant_a', ?, ?, ?)",
        [
            ("wh_ny", "New York Megahub", "ctry_us", 150000),
            ("wh_sf", "Silicon Valley Logistics", "ctry_us", 110000),
            ("wh_ber", "Berlin Central Depot", "ctry_de", 95000),
            ("wh_tok", "Tokyo Robotics Terminal", "ctry_jp", 125000),
        ],
    )

    # 5. Categories (Self-referential parent-child)
    conn.executemany(
        f"INSERT INTO {p}categories VALUES (?, 'tenant_a', ?, ?)",
        [
            ("cat_tech", "Technology", None),
            ("cat_computers", "Computers", "cat_tech"),
            ("cat_laptops", "High-End Laptops", "cat_computers"),
            ("cat_audio", "Professional Audio", "cat_tech"),
            ("cat_accessories", "Ergonomic Accessories", "cat_tech"),
        ],
    )

    # 6. Suppliers
    conn.executemany(
        f"INSERT INTO {p}suppliers VALUES (?, 'tenant_a', ?, ?, ?)",
        [
            ("sup_intellect", "Intellect Dynamics Micro", "ctry_us", 98.5),
            ("sup_soundwave", "SoundWave Acoustics", "ctry_de", 96.2),
            ("sup_hypertech", "HyperTech Components", "ctry_jp", 99.1),
            ("sup_precision", "Precision Works UK", "ctry_uk", 94.0),
        ],
    )

    # 7. Products
    conn.executemany(
        f"INSERT INTO {p}products VALUES (?, 'tenant_a', ?, ?, ?, ?, ?)",
        [
            ("prod_pro_laptop", "ProBook Ultra 16", "cat_laptops", "sup_intellect", 1999.00, 1200.00),
            ("prod_air_laptop", "AirLite Slim 14", "cat_laptops", "sup_intellect", 1299.00, 780.00),
            ("prod_wireless_anc", "Nova Pro ANC Headset", "cat_audio", "sup_soundwave", 349.00, 160.00),
            ("prod_mech_keyboard", "Apex Studio Keyboard", "cat_accessories", "sup_precision", 179.00, 85.00),
            ("prod_4k_display", "VisionPro 4K HDR Monitor", "cat_computers", "sup_hypertech", 699.00, 420.00),
        ],
    )

    # 8. Tags
    conn.executemany(
        f"INSERT INTO {p}tags VALUES (?, 'tenant_a', ?)",
        [
            ("tag_flagship", "Flagship Tier"),
            ("tag_eco", "Eco-Certified"),
            ("tag_enterprise", "Enterprise Ready"),
            ("tag_bestseller", "Bestseller"),
        ],
    )

    # 9. Product Tags (M:N)
    conn.executemany(
        f"INSERT INTO {p}product_tags VALUES (?, 'tenant_a', ?, ?)",
        [
            ("pt_1", "prod_pro_laptop", "tag_flagship"),
            ("pt_2", "prod_pro_laptop", "tag_enterprise"),
            ("pt_3", "prod_air_laptop", "tag_bestseller"),
            ("pt_4", "prod_air_laptop", "tag_eco"),
            ("pt_5", "prod_wireless_anc", "tag_flagship"),
            ("pt_6", "prod_mech_keyboard", "tag_bestseller"),
            ("pt_7", "prod_4k_display", "tag_enterprise"),
        ],
    )

    # 10. Inventory
    conn.executemany(
        f"INSERT INTO {p}inventory VALUES (?, 'tenant_a', ?, ?, ?, ?)",
        [
            ("inv_1", "wh_ny", "prod_pro_laptop", 450, 100),
            ("inv_2", "wh_ny", "prod_wireless_anc", 850, 200),
            ("inv_3", "wh_sf", "prod_air_laptop", 620, 120),
            ("inv_4", "wh_sf", "prod_4k_display", 310, 80),
            ("inv_5", "wh_ber", "prod_pro_laptop", 280, 75),
            ("inv_6", "wh_ber", "prod_mech_keyboard", 920, 250),
            ("inv_7", "wh_tok", "prod_wireless_anc", 540, 150),
            ("inv_8", "wh_tok", "prod_4k_display", 410, 90),
        ],
    )

    # 11. Departments
    conn.executemany(
        f"INSERT INTO {p}departments VALUES (?, 'tenant_a', ?)",
        [
            ("dept_enterprise", "Global Enterprise Sales"),
            ("dept_partners", "Strategic Alliances"),
            ("dept_solutions", "Client Engineering"),
        ],
    )

    # 12. Employees (Self-referential manager)
    conn.executemany(
        f"INSERT INTO {p}employees VALUES (?, 'tenant_a', ?, ?, ?, ?)",
        [
            ("emp_sarah", "Sarah Chen (VP Global Sales)", "dept_enterprise", None, 220000),
            ("emp_david", "David Miller (Director)", "dept_enterprise", "emp_sarah", 155000),
            ("emp_alex", "Alex Rivera (Lead Rep)", "dept_enterprise", "emp_david", 105000),
            ("emp_elena", "Elena Rostova (Senior Rep)", "dept_enterprise", "emp_david", 98000),
            ("emp_marcus", "Marcus Johnson (Solutions Lead)", "dept_solutions", "emp_sarah", 125000),
        ],
    )

    # 13. Customers
    conn.executemany(
        f"INSERT INTO {p}customers VALUES (?, 'tenant_a', ?, ?, ?)",
        [
            ("cust_apex", "Apex Global Systems", "ctry_us", "Platinum"),
            ("cust_quantum", "Quantum AI Technologies", "ctry_us", "Platinum"),
            ("cust_nordic", "Nordic Media Network", "ctry_de", "Gold"),
            ("cust_tokyo", "Tokyo Robotics Corp", "ctry_jp", "Platinum"),
            ("cust_solaris", "Solaris Energy International", "ctry_uk", "Gold"),
            ("cust_aurora", "Aurora BioPharma", "ctry_ca", "Silver"),
        ],
    )

    # 14. Loyalty Tiers
    conn.executemany(
        f"INSERT INTO {p}loyalty_tiers VALUES (?, 'tenant_a', ?, ?)",
        [
            ("tier_plat", "Platinum Elite", 15),
            ("tier_gold", "Gold Priority", 10),
            ("tier_silver", "Silver Standard", 5),
        ],
    )

    # 15. Customer Loyalty (M:N)
    conn.executemany(
        f"INSERT INTO {p}customer_loyalty VALUES (?, 'tenant_a', ?, ?, ?)",
        [
            ("cl_1", "cust_apex", "tier_plat", 52000),
            ("cl_2", "cust_quantum", "tier_plat", 48500),
            ("cl_3", "cust_nordic", "tier_gold", 26000),
            ("cl_4", "cust_tokyo", "tier_plat", 61000),
            ("cl_5", "cust_solaris", "tier_gold", 19500),
            ("cl_6", "cust_aurora", "tier_silver", 8400),
        ],
    )

    # 16. Orders (Multi-year for forecasting and variance: 2022, 2023, 2024)
    conn.executemany(
        f"INSERT INTO {p}orders VALUES (?, 'tenant_a', ?, ?, ?, ?, ?, ?)",
        [
            ("ord_1", "cust_apex", "emp_alex", "2022-04-10", "2022", "COMPLETED", 54000),
            ("ord_2", "cust_quantum", "emp_elena", "2022-10-18", "2022", "COMPLETED", 46000),
            ("ord_3", "cust_nordic", "emp_alex", "2023-02-14", "2023", "COMPLETED", 72000),
            ("ord_4", "cust_tokyo", "emp_elena", "2023-07-20", "2023", "COMPLETED", 94000),
            ("ord_5", "cust_solaris", "emp_alex", "2023-11-28", "2023", "COMPLETED", 68000),
            ("ord_6", "cust_apex", "emp_alex", "2024-02-10", "2024", "COMPLETED", 112000),
            ("ord_7", "cust_quantum", "emp_elena", "2024-05-15", "2024", "COMPLETED", 138000),
            ("ord_8", "cust_tokyo", "emp_elena", "2024-08-22", "2024", "COMPLETED", 164000),
            ("ord_9", "cust_aurora", "emp_alex", "2024-11-05", "2024", "COMPLETED", 48000),
        ],
    )

    # 17. Order Items
    conn.executemany(
        f"INSERT INTO {p}order_items VALUES (?, 'tenant_a', ?, ?, ?, ?, ?, ?)",
        [
            ("oi_1", "ord_1", "prod_pro_laptop", 20, 1999.00, 39980.00, 15980.00),
            ("oi_2", "ord_1", "prod_4k_display", 20, 699.00, 13980.00, 5580.00),
            ("oi_3", "ord_2", "prod_air_laptop", 30, 1299.00, 38970.00, 15570.00),
            ("oi_4", "ord_2", "prod_wireless_anc", 20, 349.00, 6980.00, 3780.00),
            ("oi_5", "ord_3", "prod_pro_laptop", 30, 1999.00, 59970.00, 23970.00),
            ("oi_6", "ord_3", "prod_mech_keyboard", 65, 179.00, 11635.00, 6110.00),
            ("oi_7", "ord_4", "prod_pro_laptop", 40, 1999.00, 79960.00, 31960.00),
            ("oi_8", "ord_4", "prod_4k_display", 20, 699.00, 13980.00, 5580.00),
            ("oi_9", "ord_5", "prod_air_laptop", 45, 1299.00, 58455.00, 23355.00),
            ("oi_10", "ord_6", "prod_pro_laptop", 50, 1999.00, 99950.00, 39950.00),
            ("oi_11", "ord_6", "prod_wireless_anc", 34, 349.00, 11866.00, 6426.00),
            ("oi_12", "ord_7", "prod_pro_laptop", 60, 1999.00, 119940.00, 47940.00),
            ("oi_13", "ord_7", "prod_4k_display", 25, 699.00, 17475.00, 6975.00),
            ("oi_14", "ord_8", "prod_pro_laptop", 70, 1999.00, 139930.00, 55930.00),
            ("oi_15", "ord_8", "prod_wireless_anc", 68, 349.00, 23732.00, 12852.00),
            ("oi_16", "ord_9", "prod_air_laptop", 35, 1299.00, 45465.00, 18165.00),
        ],
    )

    # 18. Promotions
    conn.executemany(
        f"INSERT INTO {p}promotions VALUES (?, 'tenant_a', ?, ?)",
        [
            ("promo_accelerate", "ACCELERATE24", 15),
            ("promo_enterprise", "ENTERPRISE_PRIME", 20),
            ("promo_cloudsummit", "CLOUDSUMMIT", 10),
        ],
    )

    # 19. Order Promotions (M:N)
    conn.executemany(
        f"INSERT INTO {p}order_promotions VALUES (?, 'tenant_a', ?, ?, ?)",
        [
            ("op_1", "ord_1", "promo_accelerate", 8100.00),
            ("op_2", "ord_3", "promo_enterprise", 14400.00),
            ("op_3", "ord_6", "promo_accelerate", 16800.00),
            ("op_4", "ord_8", "promo_enterprise", 32800.00),
        ],
    )

    # 20. Shipments
    conn.executemany(
        f"INSERT INTO {p}shipments VALUES (?, 'tenant_a', ?, ?, ?, ?, ?)",
        [
            ("ship_1", "ord_1", "wh_ny", "DHL Express Global", 620.00, 2),
            ("ship_2", "ord_2", "wh_sf", "FedEx Priority Freight", 540.00, 3),
            ("ship_3", "ord_3", "wh_ber", "DHL Express Global", 780.00, 2),
            ("ship_4", "ord_4", "wh_tok", "Nippon Express Air", 950.00, 1),
            ("ship_5", "ord_6", "wh_ny", "FedEx Priority Freight", 1100.00, 2),
            ("ship_6", "ord_7", "wh_sf", "DHL Express Global", 1250.00, 2),
            ("ship_7", "ord_8", "wh_tok", "Nippon Express Air", 1420.00, 1),
        ],
    )

    # 21. Customer Reviews
    conn.executemany(
        f"INSERT INTO {p}customer_reviews VALUES (?, 'tenant_a', ?, ?, ?, ?)",
        [
            ("rev_1", "prod_pro_laptop", "cust_apex", 5, "Unbeatable compile times and battery"),
            ("rev_2", "prod_pro_laptop", "cust_quantum", 5, "Flawless for on-prem AI workloads"),
            ("rev_3", "prod_wireless_anc", "cust_nordic", 4, "Studio quality ANC audio"),
            ("rev_4", "prod_4k_display", "cust_tokyo", 5, "Crisp text and zero glare"),
            ("rev_5", "prod_mech_keyboard", "cust_solaris", 5, "Exceptional key switches"),
        ],
    )


def _build_catalog_definition() -> dict:
    tables = {
        "nexus_continents": ("continent", ["id", "tenant_id", "continent_name"]),
        "nexus_regions": ("region", ["id", "tenant_id", "region_name", "continent_id"]),
        "nexus_countries": ("country", ["id", "tenant_id", "country_name", "region_id"]),
        "nexus_warehouses": ("warehouse", ["id", "tenant_id", "warehouse_name", "country_id", "capacity_sqft"]),
        "nexus_categories": ("category", ["id", "tenant_id", "category_name", "parent_category_id"]),
        "nexus_suppliers": ("supplier", ["id", "tenant_id", "supplier_name", "country_id", "reliability_score"]),
        "nexus_products": ("product", ["id", "tenant_id", "product_name", "category_id", "supplier_id", "list_price", "cost_price"]),
        "nexus_tags": ("tag", ["id", "tenant_id", "tag_name"]),
        "nexus_product_tags": ("product_tag", ["id", "tenant_id", "product_id", "tag_id"]),
        "nexus_inventory": ("inventory_item", ["id", "tenant_id", "warehouse_id", "product_id", "stock_quantity", "reorder_point"]),
        "nexus_departments": ("department", ["id", "tenant_id", "department_name"]),
        "nexus_employees": ("employee", ["id", "tenant_id", "employee_name", "department_id", "manager_id", "salary"]),
        "nexus_customers": ("customer", ["id", "tenant_id", "customer_name", "country_id", "customer_tier"]),
        "nexus_loyalty_tiers": ("loyalty_tier", ["id", "tenant_id", "tier_name", "discount_rate"]),
        "nexus_customer_loyalty": ("customer_loyalty_item", ["id", "tenant_id", "customer_id", "tier_id", "points_balance"]),
        "nexus_orders": ("order", ["id", "tenant_id", "customer_id", "sales_rep_id", "order_date", "order_year", "status", "total_amount"]),
        "nexus_order_items": ("order_item", ["id", "tenant_id", "order_id", "product_id", "quantity", "unit_price", "line_total", "profit_margin"]),
        "nexus_promotions": ("promotion", ["id", "tenant_id", "promo_code", "discount_percent"]),
        "nexus_order_promotions": ("order_promotion", ["id", "tenant_id", "order_id", "promotion_id", "discount_applied"]),
        "nexus_shipments": ("shipment", ["id", "tenant_id", "order_id", "warehouse_id", "carrier", "shipping_cost", "delivery_days"]),
        "nexus_customer_reviews": ("review", ["id", "tenant_id", "product_id", "customer_id", "rating", "review_title"]),
    }

    numeric_cols = {
        "capacity_sqft", "reliability_score", "list_price", "cost_price",
        "stock_quantity", "reorder_point", "salary", "discount_rate",
        "points_balance", "total_amount", "quantity", "unit_price",
        "line_total", "profit_margin", "discount_percent", "discount_applied",
        "shipping_cost", "delivery_days", "rating"
    }

    table_defs = {}
    for table, (grain, names) in tables.items():
        cols = {
            col: ("DECIMAL" if col in numeric_cols else "INTEGER" if col in ("quantity", "delivery_days", "rating", "capacity_sqft") else "STRING")
            for col in names
        }
        table_defs[table] = {
            "table_name": table,
            "grain": grain,
            "tenant_column": "tenant_id",
            "primary_key": ["id"],
            "columns": cols,
        }

    # Approved Directed Join Edges across the entire relational graph
    edges = [
        # Multi-hop hierarchy: Continents <- Regions <- Countries
        ("nexus_regions", "nexus_continents", "continent_id", "id", "MANY_TO_ONE"),
        ("nexus_countries", "nexus_regions", "region_id", "id", "MANY_TO_ONE"),
        # Countries <- Customers, Warehouses, Suppliers
        ("nexus_customers", "nexus_countries", "country_id", "id", "MANY_TO_ONE"),
        ("nexus_warehouses", "nexus_countries", "country_id", "id", "MANY_TO_ONE"),
        ("nexus_suppliers", "nexus_countries", "country_id", "id", "MANY_TO_ONE"),
        # Products <- Categories, Suppliers
        ("nexus_products", "nexus_categories", "category_id", "id", "MANY_TO_ONE"),
        ("nexus_products", "nexus_suppliers", "supplier_id", "id", "MANY_TO_ONE"),
        # Category Self-Join
        ("nexus_categories", "nexus_categories", "parent_category_id", "id", "MANY_TO_ONE"),
        # Orders <- Customers, Employees (Sales Reps)
        ("nexus_orders", "nexus_customers", "customer_id", "id", "MANY_TO_ONE"),
        ("nexus_orders", "nexus_employees", "sales_rep_id", "id", "MANY_TO_ONE"),
        # Employees <- Departments, Manager Self-Join
        ("nexus_employees", "nexus_departments", "department_id", "id", "MANY_TO_ONE"),
        ("nexus_employees", "nexus_employees", "manager_id", "id", "MANY_TO_ONE"),
        # Order Items <- Orders, Products
        ("nexus_order_items", "nexus_orders", "order_id", "id", "MANY_TO_ONE"),
        ("nexus_order_items", "nexus_products", "product_id", "id", "MANY_TO_ONE"),
        # Inventory <- Warehouses, Products
        ("nexus_inventory", "nexus_warehouses", "warehouse_id", "id", "MANY_TO_ONE"),
        ("nexus_inventory", "nexus_products", "product_id", "id", "MANY_TO_ONE"),
        # M:N Product Tags
        ("nexus_products", "nexus_product_tags", "id", "product_id", "ONE_TO_MANY"),
        ("nexus_product_tags", "nexus_tags", "tag_id", "id", "MANY_TO_ONE"),
        # M:N Order Promotions
        ("nexus_orders", "nexus_order_promotions", "id", "order_id", "ONE_TO_MANY"),
        ("nexus_order_promotions", "nexus_promotions", "promotion_id", "id", "MANY_TO_ONE"),
        # M:N Customer Loyalty
        ("nexus_customers", "nexus_customer_loyalty", "id", "customer_id", "ONE_TO_MANY"),
        ("nexus_customer_loyalty", "nexus_loyalty_tiers", "tier_id", "id", "MANY_TO_ONE"),
        # Shipments <- Orders, Warehouses
        ("nexus_shipments", "nexus_orders", "order_id", "id", "MANY_TO_ONE"),
        ("nexus_shipments", "nexus_warehouses", "warehouse_id", "id", "MANY_TO_ONE"),
        # Customer Reviews <- Products, Customers
        ("nexus_customer_reviews", "nexus_products", "product_id", "id", "MANY_TO_ONE"),
        ("nexus_customer_reviews", "nexus_customers", "customer_id", "id", "MANY_TO_ONE"),
    ]

    joins = [
        dict(from_table=a, to_table=b, from_column=ac, to_column=bc, cardinality=card, join_type="INNER")
        for a, b, ac, bc, card in edges
    ]

    dim_specs = {
        "continent_name": ("nexus_continents", "continent_name"),
        "region_name": ("nexus_regions", "region_name"),
        "country_name": ("nexus_countries", "country_name"),
        "warehouse_name": ("nexus_warehouses", "warehouse_name"),
        "category_name": ("nexus_categories", "category_name"),
        "supplier_name": ("nexus_suppliers", "supplier_name"),
        "product_name": ("nexus_products", "product_name"),
        "tag_name": ("nexus_tags", "tag_name"),
        "department_name": ("nexus_departments", "department_name"),
        "employee_name": ("nexus_employees", "employee_name"),
        "customer_name": ("nexus_customers", "customer_name"),
        "customer_tier": ("nexus_customers", "customer_tier"),
        "tier_name": ("nexus_loyalty_tiers", "tier_name"),
        "order_year": ("nexus_orders", "order_year"),
        "order_date": ("nexus_orders", "order_date"),
        "status": ("nexus_orders", "status"),
        "promo_code": ("nexus_promotions", "promo_code"),
        "carrier": ("nexus_shipments", "carrier"),
    }

    dimensions = {
        k: {"id": k, "name": k.replace("_", " ").title(), "table": t, "column": c, "data_type": "STRING"}
        for k, (t, c) in dim_specs.items()
    }
    allowed = list(dimensions)

    measures = {
        "total_revenue": {
            "id": "total_revenue",
            "name": "Total Revenue",
            "description": "Aggregated order revenue in USD",
            "table": "nexus_orders",
            "column": "total_amount",
            "aggregation": "SUM",
            "base_grain": "order",
            "unit": "USD",
            "allowed_dimensions": allowed,
            "allowed_families": ["TOTAL", "AVERAGE", "TREND", "COMPARISON", "CONTRIBUTION", "FORECAST", "ANOMALY", "SCENARIO"],
            "allocation_policy": "EXPLICIT_EQUAL_SPLIT",
        },
        "order_count": {
            "id": "order_count",
            "name": "Order Count",
            "description": "Distinct count of completed orders",
            "table": "nexus_orders",
            "column": "id",
            "aggregation": "COUNT_DISTINCT",
            "base_grain": "order",
            "unit": "Orders",
            "allowed_dimensions": allowed,
            "allowed_families": ["TOTAL", "AVERAGE", "TREND", "COMPARISON"],
        },
        "line_item_revenue": {
            "id": "line_item_revenue",
            "name": "Line Item Revenue",
            "description": "Revenue aggregated from individual order items",
            "table": "nexus_order_items",
            "column": "line_total",
            "aggregation": "SUM",
            "base_grain": "order_item",
            "unit": "USD",
            "allowed_dimensions": allowed,
            "allowed_families": ["TOTAL", "AVERAGE", "TREND", "COMPARISON", "CONTRIBUTION"],
            "allocation_policy": "EXPLICIT_EQUAL_SPLIT",
        },
        "profit_margin": {
            "id": "profit_margin",
            "name": "Gross Profit Margin",
            "description": "Total gross profit from sold order items",
            "table": "nexus_order_items",
            "column": "profit_margin",
            "aggregation": "SUM",
            "base_grain": "order_item",
            "unit": "USD",
            "allowed_dimensions": allowed,
            "allowed_families": ["TOTAL", "AVERAGE", "TREND", "COMPARISON", "CONTRIBUTION"],
            "allocation_policy": "EXPLICIT_EQUAL_SPLIT",
        },
        "units_sold": {
            "id": "units_sold",
            "name": "Units Sold",
            "description": "Total product volume sold",
            "table": "nexus_order_items",
            "column": "quantity",
            "aggregation": "SUM",
            "base_grain": "order_item",
            "unit": "Units",
            "allowed_dimensions": allowed,
            "allowed_families": ["TOTAL", "AVERAGE", "TREND", "COMPARISON"],
            "allocation_policy": "EXPLICIT_EQUAL_SPLIT",
        },
        "inventory_stock": {
            "id": "inventory_stock",
            "name": "Inventory Units on Hand",
            "description": "Total physical stock across warehouses",
            "table": "nexus_inventory",
            "column": "stock_quantity",
            "aggregation": "SUM",
            "base_grain": "inventory_item",
            "unit": "Units",
            "allowed_dimensions": allowed,
            "allowed_families": ["TOTAL", "AVERAGE", "COMPARISON"],
        },
        "shipping_spend": {
            "id": "shipping_spend",
            "name": "Shipping Expenditure",
            "description": "Total logistics and carrier shipping costs",
            "table": "nexus_shipments",
            "column": "shipping_cost",
            "aggregation": "SUM",
            "base_grain": "shipment",
            "unit": "USD",
            "allowed_dimensions": allowed,
            "allowed_families": ["TOTAL", "AVERAGE", "TREND", "COMPARISON"],
        },
        "salary_expense": {
            "id": "salary_expense",
            "name": "Total Salary Expense",
            "description": "Total annual compensation by department or role",
            "table": "nexus_employees",
            "column": "salary",
            "aggregation": "SUM",
            "base_grain": "employee",
            "unit": "USD",
            "allowed_dimensions": allowed,
            "allowed_families": ["TOTAL", "AVERAGE", "COMPARISON"],
        },
        "average_rating": {
            "id": "average_rating",
            "name": "Average Customer Rating",
            "description": "Average verified review score (1 to 5)",
            "table": "nexus_customer_reviews",
            "column": "rating",
            "aggregation": "AVG",
            "base_grain": "review",
            "unit": "Stars",
            "allowed_dimensions": allowed,
            "allowed_families": ["TOTAL", "AVERAGE", "COMPARISON"],
        },
        "average_order_discount": {
            "id": "average_order_discount",
            "name": "Average Order Discount",
            "description": "Average promotional discount applied per order",
            "table": "nexus_order_promotions",
            "column": "discount_applied",
            "aggregation": "AVG",
            "base_grain": "order_promotion",
            "unit": "USD",
            "allowed_dimensions": allowed,
            "allowed_families": ["TOTAL", "AVERAGE", "COMPARISON"],
        },
        "average_order_value": {
            "id": "average_order_value",
            "name": "Average Order Value",
            "description": "Average total order spend in USD",
            "table": "nexus_orders",
            "column": "total_amount",
            "aggregation": "AVG",
            "base_grain": "order",
            "unit": "USD",
            "allowed_dimensions": allowed,
            "allowed_families": ["TOTAL", "AVERAGE", "COMPARISON"],
        },
        "average_salary": {
            "id": "average_salary",
            "name": "Average Salary",
            "description": "Average annual compensation by department or role",
            "table": "nexus_employees",
            "column": "salary",
            "aggregation": "AVG",
            "base_grain": "employee",
            "unit": "USD",
            "allowed_dimensions": allowed,
            "allowed_families": ["TOTAL", "AVERAGE", "COMPARISON"],
        },
    }

    time_series_policies = {
        "total_revenue": {
            "metric": "total_revenue",
            "period_dimension": "order_year",
            "frequency": "YEAR",
            "min_history_periods": 3,
            "max_forecast_horizon": 3,
        }
    }

    return {
        "release_id": "enterprise_nexus_v1",
        "epoch": 1,
        "source_id": SOURCE_ID,
        "tables": table_defs,
        "measures": measures,
        "dimensions": dimensions,
        "joins": joins,
        "default_period_dimension": "order_year",
        "time_series_policies": time_series_policies,
    }
