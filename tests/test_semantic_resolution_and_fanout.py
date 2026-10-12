"""Tests for Semantic Resolution, Approved Joins, Revenue Allocation, and Fan-Out Prevention.

Covers:
1. Parent-level totals with mixed child counts and 0-child parents
2. Rejection of implicit revenue allocation (equal division is not assumed)
3. Approved item-level revenue metric with mixed categories within one order and unequal item values
4. Child grouping combined with filtering
5. Parents without children (excluded from item grain vs grouped under Unallocated)
6. Explicit catalog allocation policy with UNALLOCATED grouping
7. Child filters via EXISTS semi-join avoiding artificial scaling
8. Rejection of unapproved joins
"""
import pytest
from decimal import Decimal
from talk2data.domain.models import TurnStatus
from talk2data.intent.schema import AnalysisFamily, StructuredIntent
from talk2data.semantic.resolver import SemanticResolutionError


def test_parent_level_totals_with_mixed_child_counts(test_setup):
    """Parent-level total sales in 2022 where ord_8 has 0 children ($20k) and ord_9 has 5 children ($50k).
    
    True parent total = 20,000 + 50,000 = 70,000.00.
    Must not drop the zero-child order and must not multiply the 5-child order.
    """
    manager = test_setup["manager"]
    actor_a = test_setup["actor_a"]
    source_id = test_setup["source_id"]

    turn, answer = manager.submit_turn(
        actor=actor_a,
        conversation_id="conv_fanout_parent_total",
        source_id=source_id,
        question="What was total sales in 2022?",
        proposed_llm_json='{"measure": "sales_revenue", "family": "TOTAL", "current_period": "2022"}',
    )

    assert turn.status == TurnStatus.ANSWERED
    assert answer is not None
    kpi_clean = answer.kpi_cards[0].value.replace(",", "")
    assert Decimal(kpi_clean) == Decimal("70000.00")


def test_implicit_revenue_allocation_is_rejected_without_policy(test_setup):
    """Equal division across child items is NOT automatically assumed.
    
    Attempting to group order-grain 'sales_revenue' by child dimension 'category'
    without an explicit catalog allocation policy must be DECLINED with a clear explanation.
    """
    manager = test_setup["manager"]
    actor_a = test_setup["actor_a"]
    source_id = test_setup["source_id"]

    turn, answer = manager.submit_turn(
        actor=actor_a,
        conversation_id="conv_reject_implicit_allocation",
        source_id=source_id,
        question="Sales revenue by category in 2024.",
        proposed_llm_json='{"measure": "sales_revenue", "family": "TOTAL", "dimensions": ["category"], "current_period": "2024"}',
    )

    assert turn.status == TurnStatus.DECLINED
    assert answer is None
    assert "cannot be aggregated across child dimension" in turn.decline_reason
    assert "allocation policy" in turn.decline_reason.lower()


def test_approved_item_level_revenue_with_mixed_categories_and_unequal_values(test_setup):
    """Approved item-level metric (item_sales_revenue) calculates exact amounts without division.
    
    Seed Data for 2021 (ord_mixed, total = 100k):
    - Enterprise Server (Hardware): $80,000.00
    - Database License (Software): $20,000.00
    Mixed categories within one order and unequal item values are preserved exactly.
    """
    manager = test_setup["manager"]
    actor_a = test_setup["actor_a"]
    source_id = test_setup["source_id"]

    turn, answer = manager.submit_turn(
        actor=actor_a,
        conversation_id="conv_item_level_revenue",
        source_id=source_id,
        question="Item sales revenue by category in 2021.",
        proposed_llm_json='{"measure": "item_sales_revenue", "family": "TOTAL", "dimensions": ["category"], "current_period": "2021"}',
    )

    assert turn.status == TurnStatus.ANSWERED, f"Turn status {turn.status}, err: {turn.error_message}, decline: {turn.decline_reason}"
    assert answer is not None

    rows = {r["category"]: Decimal(r["Value"].replace(",", "")) for r in answer.table_rows}
    assert rows["Hardware"] == Decimal("80000.00")
    assert rows["Software"] == Decimal("20000.00")
    assert sum(rows.values()) == Decimal("100000.00")


def test_child_grouping_combined_with_child_filter(test_setup):
    """Child grouping combined with filtering (Hardware category in 2021)."""
    manager = test_setup["manager"]
    actor_a = test_setup["actor_a"]
    source_id = test_setup["source_id"]

    turn, answer = manager.submit_turn(
        actor=actor_a,
        conversation_id="conv_item_grouping_and_filter",
        source_id=source_id,
        question="Item sales revenue by category in 2021 for Hardware.",
        proposed_llm_json='{"measure": "item_sales_revenue", "family": "TOTAL", "dimensions": ["category"], "current_period": "2021", "filters": [{"dimension": "category", "operator": "=", "values": ["Hardware"]}]}',
    )

    assert turn.status == TurnStatus.ANSWERED
    assert answer is not None
    rows = {r["category"]: Decimal(r["Value"].replace(",", "")) for r in answer.table_rows}
    assert len(rows) == 1
    assert rows["Hardware"] == Decimal("80000.00")


def test_explicit_catalog_allocation_policy_with_unallocated_group(test_setup):
    """Explicit catalog allocation policy (EXPLICIT_EQUAL_SPLIT) with UNALLOCATED handling.
    
    Seed Data (2022):
    - ord_8 ($20k): 0 line items -> placed into 'Unallocated' group
    - ord_9 ($50k): 5 items in 'Electronics' -> placed into 'Electronics' group
    Total = $70,000.00.
    """
    manager = test_setup["manager"]
    actor_a = test_setup["actor_a"]
    source_id = test_setup["source_id"]

    turn, answer = manager.submit_turn(
        actor=actor_a,
        conversation_id="conv_explicit_allocation_unallocated",
        source_id=source_id,
        question="Allocated sales revenue by category in 2022.",
        proposed_llm_json='{"measure": "sales_revenue_allocated", "family": "TOTAL", "dimensions": ["category"], "current_period": "2022"}',
    )

    assert turn.status == TurnStatus.ANSWERED, f"err: {turn.error_message}, decline: {turn.decline_reason}"
    assert answer is not None

    rows = {r["category"]: Decimal(r["Value"].replace(",", "")) for r in answer.table_rows}
    assert rows["Electronics"] == Decimal("50000.00")
    assert rows["Unallocated"] == Decimal("20000.00")
    assert sum(rows.values()) == Decimal("70000.00")

    # Scope disclosure must be present in assumptions or summary text
    assert any("unallocated" in a.lower() or "allocated across child items" in a.lower() for a in answer.assumptions) or \
           "unallocated" in answer.summary_text.lower() or "allocated across child items" in answer.summary_text.lower()


def test_child_filter_preserves_parent_order_total(test_setup):
    """Child filter without child dimension grouping uses EXISTS semi-join.
    
    In 2024, ord_2 has category 'Hardware' with amount = 70,000.00.
    An EXISTS semi-join ensures the parent order total is exactly $70,000.00
    without row division or fan-out inflation.
    """
    manager = test_setup["manager"]
    actor_a = test_setup["actor_a"]
    source_id = test_setup["source_id"]

    turn, answer = manager.submit_turn(
        actor=actor_a,
        conversation_id="conv_fanout_child_filter",
        source_id=source_id,
        question="What was total sales in 2024 for orders with Hardware?",
        proposed_llm_json='{"measure": "sales_revenue", "family": "TOTAL", "current_period": "2024", "filters": [{"dimension": "category", "operator": "=", "values": ["Hardware"]}]}',
    )

    assert turn.status == TurnStatus.ANSWERED
    assert answer is not None
    kpi_val = answer.kpi_cards[0].value.replace(",", "")
    assert Decimal(kpi_val) == Decimal("70000.00")


def test_unapproved_join_rejected(test_setup):
    """Attempting to resolve a dimension without an approved join path is rejected."""
    catalogs = test_setup["catalogs"]
    resolver = test_setup["manager"].resolver
    pin = catalogs.pin("tenant_a", "finance_dw")
    cat = catalogs.get_catalog("finance_dw", pin.release_id)

    intent = StructuredIntent(
        measure="employee_salary",
        family=AnalysisFamily.TOTAL,
        dimensions=("country",),  # No join path between salaries and customers!
    )

    with pytest.raises(SemanticResolutionError, match="No approved join path"):
        resolver.resolve(
            intent=intent,
            catalog=cat,
            pin=pin,
            turn_id="turn_unapproved_join",
            tenant_id="tenant_a",
        )
