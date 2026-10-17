"""Integration coverage using a deterministic SQLite source with real FK constraints."""
import json
from decimal import Decimal

import pytest

from backend.complex_join_demo import SOURCE_ID, add_complex_join_demo
from backend.catalog.extractor import introspect_sqlite_database
from backend.demo import initialize
from backend.domain.models import ApiActor, TurnStatus
from backend.lifecycle.session import ConversationLifecycleManager
from backend.runtime import load_runtime


@pytest.fixture
def complex_environment(tmp_path, monkeypatch):
    config_path = initialize(tmp_path)
    add_complex_join_demo(tmp_path)
    monkeypatch.setenv("TALK2DATA_RUNTIME_CONFIG", str(config_path))
    monkeypatch.setenv("TALK2DATA_ENV", "development")
    security, catalogs, gateway = load_runtime()
    manager = ConversationLifecycleManager(security, catalogs, gateway)
    actor = ApiActor("tenant_a", "complex_join_tester")
    return manager, actor


def run_query(environment, conversation_id, question, intent):
    manager, actor = environment
    turn, answer = manager.submit_turn(
        actor=actor,
        conversation_id=conversation_id,
        source_id=SOURCE_ID,
        question=question,
        proposed_llm_json=json.dumps(intent),
    )
    assert turn.status == TurnStatus.ANSWERED, (turn.error_message, turn.decline_reason)
    assert answer is not None
    return answer


def row_values(answer, *dimensions):
    return {
        tuple(row[name] for name in dimensions): Decimal(row["Value"].replace(",", ""))
        for row in answer.table_rows
    }


def test_three_hop_foreign_key_join_through_customer_city_region(complex_environment):
    answer = run_query(
        complex_environment,
        "three_hop_region",
        "Order revenue by region for 2024",
        {"measure": "order_revenue", "family": "TOTAL", "dimensions": ["region_name"], "current_period": "2024"},
    )
    rows = row_values(answer, "region_name")
    assert rows == {("North America",): Decimal("400.00"), ("Europe",): Decimal("200.00")}


def test_many_to_many_count_by_tag_uses_junction(complex_environment):
    answer = run_query(
        complex_environment,
        "orders_by_tag",
        "Distinct order count by tag for 2024",
        {"measure": "order_count", "family": "TOTAL", "dimensions": ["tag_name"], "current_period": "2024"},
    )
    rows = row_values(answer, "tag_name")
    assert rows == {("Tech",): Decimal("2"), ("Urgent",): Decimal("1"), ("Wholesale",): Decimal("1")}


def test_two_many_to_many_paths_join_at_once_without_duplicate_order_counts(complex_environment):
    answer = run_query(
        complex_environment,
        "orders_by_tag_promo",
        "Distinct order count by tag and promotion for 2024",
        {"measure": "order_count", "family": "TOTAL", "dimensions": ["tag_name", "promo_code"], "current_period": "2024"},
    )
    rows = row_values(answer, "tag_name", "promo_code")
    assert rows == {
        ("Tech", "BLACKFRIDAY"): Decimal("1"),
        ("Urgent", "BLACKFRIDAY"): Decimal("1"),
        ("Tech", "SUMMER"): Decimal("1"),
        ("Wholesale", "BLACKFRIDAY"): Decimal("1"),
    }


def test_junction_attributes_and_reviewer_relation(complex_environment):
    answer = run_query(
        complex_environment,
        "orders_by_reviewer",
        "Distinct order count by tag reviewer for 2024",
        {"measure": "order_count", "family": "TOTAL", "dimensions": ["assigned_by"], "current_period": "2024"},
    )
    assert row_values(answer, "assigned_by") == {("Alice",): Decimal("1"), ("Bob",): Decimal("2")}

    priorities = run_query(
        complex_environment,
        "orders_by_priority",
        "Distinct order count by junction priority for 2024",
        {"measure": "order_count", "family": "TOTAL", "dimensions": ["priority"], "current_period": "2024"},
    )
    assert row_values(priorities, "priority") == {
        ("HIGH",): Decimal("1"), ("CRITICAL",): Decimal("1"),
        ("LOW",): Decimal("1"), ("MEDIUM",): Decimal("1"),
    }


def test_revenue_filter_by_many_to_many_tag_has_no_fanout(complex_environment):
    answer = run_query(
        complex_environment,
        "tech_orders_revenue",
        "Revenue for orders tagged Tech in 2024",
        {"measure": "order_revenue", "family": "TOTAL", "current_period": "2024",
         "filters": [{"dimension": "tag_name", "operator": "=", "values": ["Tech"]}]},
    )
    assert Decimal(answer.kpi_cards[0].value.replace(",", "")) == Decimal("300.00")


def test_unallocated_order_revenue_grouping_by_tag_is_declined(complex_environment):
    manager, actor = complex_environment
    turn, answer = manager.submit_turn(
        actor=actor,
        conversation_id="unallocated_revenue_by_tag",
        source_id=SOURCE_ID,
        question="Order revenue by tag for 2024",
        proposed_llm_json=json.dumps({
            "measure": "order_revenue", "family": "TOTAL", "dimensions": ["tag_name"], "current_period": "2024"
        }),
    )
    assert turn.status == TurnStatus.DECLINED
    assert answer is None
    assert "allocation policy" in turn.decline_reason.lower()


def test_multi_hop_order_item_to_product_category_revenue(complex_environment):
    answer = run_query(
        complex_environment,
        "line_revenue_by_category",
        "Line revenue by product category for 2024",
        {"measure": "line_revenue", "family": "TOTAL", "dimensions": ["category_name"], "current_period": "2024"},
    )
    assert row_values(answer, "category_name") == {
        ("Hardware",): Decimal("380.00"), ("Software",): Decimal("220.00")
    }


def test_database_introspection_discovers_all_explicit_foreign_keys(tmp_path):
    initialize(tmp_path)
    db_path = add_complex_join_demo(tmp_path)
    catalog, manifest = introspect_sqlite_database(db_path, SOURCE_ID)
    explicit = {
        (edge.from_table, edge.to_table, edge.from_column, edge.to_column)
        for edge in catalog.joins
    }
    expected = {
        ("m2m_cities", "m2m_regions", "region_id", "id"),
        ("m2m_customers", "m2m_cities", "city_id", "id"),
        ("m2m_orders", "m2m_customers", "customer_id", "id"),
        ("m2m_products", "m2m_categories", "category_id", "id"),
        ("m2m_order_items", "m2m_orders", "order_id", "id"),
        ("m2m_order_items", "m2m_products", "product_id", "id"),
        ("m2m_order_tags", "m2m_orders", "order_id", "id"),
        ("m2m_order_tags", "m2m_tags", "tag_id", "id"),
        ("m2m_order_promotions", "m2m_orders", "order_id", "id"),
        ("m2m_order_promotions", "m2m_promotions", "promotion_id", "id"),
    }
    assert expected <= explicit
    assert manifest["summary"]["total_tables"] == 12  # 11 join tables plus the base sales table
    assert catalog.source_id == SOURCE_ID
