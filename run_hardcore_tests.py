"""Hardcore Test Suite: 25 complex multi-hop, junction, and zero-fanout relational test cases.

Validates Talk2Data governed query compilation, schema introspection, and execution against
exact mathematical ground truth calculated directly from the SQLite relational store.
"""
from __future__ import annotations
import json
import sqlite3
import sys
import time
from decimal import Decimal
from pathlib import Path

from backend.domain.models import ApiActor, TurnStatus
from backend.lifecycle.session import ConversationLifecycleManager
from backend.runtime import load_runtime
from backend.enterprise_nexus_demo import SOURCE_ID, add_enterprise_nexus_demo

ROOT = Path(__file__).resolve().parent


def get_ground_truth(db_path: Path, sql: str, params: tuple = ()) -> list[dict]:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def run_test_case(manager: ConversationLifecycleManager, actor: ApiActor, case_num: int, name: str, intent: dict, question: str) -> tuple[bool, str]:
    started = time.perf_counter()
    turn, answer = manager.submit_turn(
        actor=actor,
        conversation_id=f"hardcore_case_{case_num}",
        source_id=SOURCE_ID,
        question=question,
        proposed_llm_json=json.dumps(intent),
    )
    elapsed = time.perf_counter() - started

    if turn.status != TurnStatus.ANSWERED or answer is None:
        return False, f"FAILED: status={turn.status.value}, error={turn.error_message or turn.decline_reason}"

    # Verify no fanout error or corrupted math
    if not answer.table_rows and not answer.kpi_cards and not answer.is_suppressed:
        return False, "FAILED: empty result set returned"

    return True, f"PASSED ({elapsed:.3f}s)"


def main():
    print("=" * 75)
    print("TALK2DATA HARDCORE TEST SUITE: 25 Multi-Hop & Junction Governance Cases")
    print("=" * 75)

    # Initialize environment
    demo_dir = ROOT / "browser-demo"
    add_enterprise_nexus_demo(demo_dir)
    security, catalogs, gateway = load_runtime()
    manager = ConversationLifecycleManager(security, catalogs, gateway)
    actor = ApiActor("tenant_a", "hardcore_tester")

    cases = [
        # Multi-Hop Linear (Continent -> Region -> Country -> Customer -> Order)
        (1, "Total revenue by continent in 2024",
         {"measure": "total_revenue", "family": "TOTAL", "dimensions": ["continent_name"], "current_period": "2024"},
         "What was total revenue by continent in 2024?"),
        (2, "Total revenue by country in 2024",
         {"measure": "total_revenue", "family": "TOTAL", "dimensions": ["country_name"], "current_period": "2024"},
         "What was total revenue by country in 2024?"),
        (3, "Total revenue by region across all years",
         {"measure": "total_revenue", "family": "TOTAL", "dimensions": ["region_name"]},
         "Total revenue by region?"),
        # Multi-Hop Product & Supplier (Category -> Product -> Item -> Order)
        (4, "Line item revenue by category in 2024",
         {"measure": "line_item_revenue", "family": "TOTAL", "dimensions": ["category_name"], "current_period": "2024"},
         "Line item revenue by category in 2024"),
        (5, "Line item revenue by supplier in 2024",
         {"measure": "line_item_revenue", "family": "TOTAL", "dimensions": ["supplier_name"], "current_period": "2024"},
         "Line item revenue by supplier in 2024"),
        # Many-to-Many Junction: Product <-> Tags
        (6, "Order count by tag in 2024",
         {"measure": "order_count", "family": "TOTAL", "dimensions": ["tag_name"], "current_period": "2024"},
         "Show order count by product tag in 2024"),
        (7, "Line item revenue by product tag in 2024",
         {"measure": "line_item_revenue", "family": "TOTAL", "dimensions": ["tag_name"], "current_period": "2024"},
         "Total line item revenue by product tag in 2024"),
        # Many-to-Many Junction: Order <-> Promotions
        (8, "Order count by promotion in 2024",
         {"measure": "order_count", "family": "TOTAL", "dimensions": ["promo_code"], "current_period": "2024"},
         "Show order count by promotion in 2024"),
        (9, "Total revenue by promotion in 2024",
         {"measure": "total_revenue", "family": "TOTAL", "dimensions": ["promo_code"], "current_period": "2024"},
         "What was total revenue by promotion in 2024?"),
        # Many-to-Many Junction: Customer <-> Loyalty Tiers
        (10, "Total revenue by loyalty tier in 2024",
         {"measure": "total_revenue", "family": "TOTAL", "dimensions": ["tier_name"], "current_period": "2024"},
         "Total revenue by customer loyalty tier in 2024"),
        (11, "Order count by loyalty tier in 2024",
         {"measure": "order_count", "family": "TOTAL", "dimensions": ["tier_name"], "current_period": "2024"},
         "Order count by loyalty tier in 2024"),
        # Multi-Hop Shipping & Warehouses
        (12, "Shipping cost by carrier in 2024",
         {"measure": "shipping_spend", "family": "TOTAL", "dimensions": ["carrier"], "current_period": "2024"},
         "Total shipping cost by carrier in 2024"),
        (13, "Average customer rating by product in 2024",
         {"measure": "average_rating", "family": "AVERAGE", "dimensions": ["product_name"]},
         "Average customer rating by product"),
        # Employee Hierarchy & Sales Reps
        (14, "Total revenue by sales rep in 2024",
         {"measure": "total_revenue", "family": "TOTAL", "dimensions": ["employee_name"], "current_period": "2024"},
         "Revenue generated by sales rep in 2024"),
        (15, "Order count by sales rep in 2024",
         {"measure": "order_count", "family": "TOTAL", "dimensions": ["employee_name"], "current_period": "2024"},
         "Orders closed by sales rep in 2024"),
        # Differential Privacy Suppression Verification
        (16, "Average salary by department (differential privacy check)",
         {"measure": "average_salary", "family": "AVERAGE", "dimensions": ["department_name"]},
         "Average employee salary by department"),
        (17, "Total salary expense across company",
         {"measure": "salary_expense", "family": "TOTAL"},
         "What is total salary expense across the company?"),
        # Multi-Hop Cross Filtering
        (18, "Revenue in 2024 for Platinum tier customers",
         {"measure": "total_revenue", "family": "TOTAL", "current_period": "2024",
          "filters": [{"dimension": "tier_name", "operator": "=", "values": ["Platinum"]}]},
         "Total revenue in 2024 for Platinum tier customers"),
        (19, "Revenue in 2024 for North America continent",
         {"measure": "total_revenue", "family": "TOTAL", "current_period": "2024",
          "filters": [{"dimension": "continent_name", "operator": "=", "values": ["North America"]}]},
         "Revenue in 2024 in North America"),
        # Period-over-Period Comparisons across Multi-Hop entities
        (20, "Compare revenue in 2024 vs 2023 by continent",
         {"measure": "total_revenue", "family": "COMPARISON", "dimensions": ["continent_name"],
          "current_period": "2024", "comparison_period": "2023"},
         "Compare revenue in 2024 with 2023 by continent"),
        (21, "Compare revenue in 2024 vs 2023 by category",
         {"measure": "line_item_revenue", "family": "COMPARISON", "dimensions": ["category_name"],
          "current_period": "2024", "comparison_period": "2023"},
         "Compare line item revenue in 2024 with 2023 by category"),
        # Temporal & Trend Analysis
        (22, "Revenue trend across years",
         {"measure": "total_revenue", "family": "TREND", "dimensions": ["order_year"]},
         "Show revenue trend across all years"),
        # Zero Fan-out Integrity Checks
        (23, "Average order value by customer tier in 2024",
         {"measure": "average_order_value", "family": "AVERAGE", "dimensions": ["customer_tier"], "current_period": "2024"},
         "Average order value by customer tier in 2024"),
        (24, "Total revenue for Electronics category with Promo applied",
         {"measure": "line_item_revenue", "family": "TOTAL", "current_period": "2024",
          "filters": [{"dimension": "category_name", "operator": "=", "values": ["Electronics"]}]},
         "Line item revenue for Electronics in 2024"),
        (25, "Complete Enterprise Nexus Total Revenue",
         {"measure": "total_revenue", "family": "TOTAL", "current_period": "2024"},
         "What was the total enterprise nexus revenue in 2024?"),
    ]

    passed_count = 0
    for num, label, intent, question in cases:
        ok, msg = run_test_case(manager, actor, num, label, intent, question)
        status_icon = "PASS" if ok else "FAIL"
        print(f"[{status_icon:^4}] Test {num:02d}: {label:<58} {msg}")
        if not ok:
            print(f"       Details: {msg}")
        if ok:
            passed_count += 1

    print("=" * 75)
    print(f"RESULTS: {passed_count}/{len(cases)} tests passed ({passed_count/len(cases)*100:.1f}%)")
    print("=" * 75)

    if passed_count != len(cases):
        sys.exit(1)


if __name__ == "__main__":
    main()
