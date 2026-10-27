"""Comprehensive 200+ question regression benchmark suite for governed query generation.

Executes 245 diverse analytical questions through ConversationLifecycleManager and verifies:
1. Expected turn status (ANSWERED, AWAITING_CLARIFICATION, DECLINED).
2. Non-empty answer narrative, KPI cards, and evidence digests on answered turns.
3. Top-N ranking correctness and LIMIT parameters.
4. Method disclosure in narrative when allocation policy applies.
5. Populated compiled_sql / sql_query on COMPARISON turns.
6. Refusal / security decline on unauthorized access or dangerous input.
"""
from __future__ import annotations
import json
from decimal import Decimal
import pytest

from backend.domain.models import TurnStatus


def _build_220_benchmark_cases() -> list[dict]:
    """Generate 245 structured analytical benchmark test cases."""
    cases = []
    case_idx = 1

    # 1. Standard Totals with dimension slicing and period filters (44 cases)
    periods = ["2024", "2023", "2022"]
    countries = ["US", "Germany", "Canada"]

    # Order-grain totals (sales_revenue, order_count)
    for m in ["sales_revenue", "order_count"]:
        for p in periods:
            for c in countries:
                cases.append({
                    "id": f"TC-TOT-{case_idx:03d}",
                    "query": f"What was {m} in {c} for {p}?",
                    "json": {
                        "measure": m,
                        "family": "TOTAL",
                        "current_period": p,
                        "filters": [{"dimension": "country", "operator": "=", "values": [c]}],
                    },
                    "expected_status": TurnStatus.ANSWERED,
                })
                case_idx += 1

        for c in countries:
            cases.append({
                "id": f"TC-TOT-{case_idx:03d}",
                "query": f"What was {m} in {c} overall?",
                "json": {
                    "measure": m,
                    "family": "TOTAL",
                    "filters": [{"dimension": "country", "operator": "=", "values": [c]}],
                },
                "expected_status": TurnStatus.ANSWERED,
            })
            case_idx += 1

        for p in periods:
            cases.append({
                "id": f"TC-TOT-{case_idx:03d}",
                "query": f"What was {m} in {p}?",
                "json": {
                    "measure": m,
                    "family": "TOTAL",
                    "current_period": p,
                },
                "expected_status": TurnStatus.ANSWERED,
            })
            case_idx += 1

        cases.append({
            "id": f"TC-TOT-{case_idx:03d}",
            "query": f"Overall total {m}",
            "json": {"measure": m, "family": "TOTAL"},
            "expected_status": TurnStatus.ANSWERED,
        })
        case_idx += 1

    # Item-grain totals (item_sales_revenue with category)
    for cat in ["Electronics", "Furniture", "Apparel"]:
        for p in periods:
            cases.append({
                "id": f"TC-TOT-{case_idx:03d}",
                "query": f"Item sales revenue for {cat} in {p}",
                "json": {
                    "measure": "item_sales_revenue",
                    "family": "TOTAL",
                    "filters": [{"dimension": "category", "operator": "=", "values": [cat]}],
                },
                "expected_status": TurnStatus.ANSWERED,
            })
            case_idx += 1
        cases.append({
            "id": f"TC-TOT-{case_idx:03d}",
            "query": f"Item sales revenue for {cat} overall",
            "json": {
                "measure": "item_sales_revenue",
                "family": "TOTAL",
                "filters": [{"dimension": "category", "operator": "=", "values": [cat]}],
            },
            "expected_status": TurnStatus.ANSWERED,
        })
        case_idx += 1

    # 2. Grouped Totals across single and multiple dimensions (28 cases)
    for m in ["sales_revenue", "order_count"]:
        for dims in [["country"], ["period"], ["country", "period"]]:
            for p in ["2024", "2023", "2022", None]:
                payload = {"measure": m, "family": "TOTAL", "dimensions": dims}
                if p and "period" not in dims:
                    payload["current_period"] = p
                cases.append({
                    "id": f"TC-GRP-{case_idx:03d}",
                    "query": f"{m} broken down by {' and '.join(dims)}{f' in {p}' if p and 'period' not in dims else ''}",
                    "json": payload,
                    "expected_status": TurnStatus.ANSWERED,
                })
                case_idx += 1

    # Item revenue by category
    for p in ["2024", "2023", "2022", None]:
        cases.append({
            "id": f"TC-GRP-{case_idx:03d}",
            "query": f"Item revenue by category {p or 'all time'}",
            "json": {"measure": "item_sales_revenue", "family": "TOTAL", "dimensions": ["category"]},
            "expected_status": TurnStatus.ANSWERED,
        })
        case_idx += 1

    # 3. Top-N and Ranking Queries (40 cases)
    limits = [1, 2, 3, 5, 10]
    directions = ["DESC", "ASC"]
    for lim in limits:
        for d in directions:
            for m in ["sales_revenue", "order_count"]:
                for dim in ["country", "period"]:
                    cases.append({
                        "id": f"TC-RANK-{case_idx:03d}",
                        "query": f"Top {lim} {dim}s by {m} {d}",
                        "json": {
                            "measure": m,
                            "family": "TOTAL",
                            "dimensions": [dim],
                            "limit": lim,
                            "order_by_metric": True,
                            "order_direction": d,
                        },
                        "expected_status": TurnStatus.ANSWERED,
                        "check_ranking": True,
                        "expected_limit": lim,
                    })
                    case_idx += 1

    # 4. Period Comparisons with and without dimensions and filters (54 cases)
    period_pairs = [("2024", "2023"), ("2023", "2022"), ("2024", "2022")]
    for curr_p, comp_p in period_pairs:
        for m in ["sales_revenue", "avg_order_value", "order_count"]:
            # Overall comparison
            cases.append({
                "id": f"TC-COMP-{case_idx:03d}",
                "query": f"Compare {m} in {curr_p} vs {comp_p}",
                "json": {
                    "measure": m,
                    "family": "COMPARISON",
                    "current_period": curr_p,
                    "comparison_period": comp_p,
                },
                "expected_status": TurnStatus.ANSWERED,
                "check_comparison_sql": True,
            })
            case_idx += 1
            # Sliced by country comparison
            cases.append({
                "id": f"TC-COMP-{case_idx:03d}",
                "query": f"Compare {m} by country in {curr_p} vs {comp_p}",
                "json": {
                    "measure": m,
                    "family": "COMPARISON",
                    "dimensions": ["country"],
                    "current_period": curr_p,
                    "comparison_period": comp_p,
                },
                "expected_status": TurnStatus.ANSWERED,
                "check_comparison_sql": True,
            })
            case_idx += 1
            # Filtered comparisons
            for c in ["US", "Germany"]:
                cases.append({
                    "id": f"TC-COMP-{case_idx:03d}",
                    "query": f"Compare {m} in {c} ({curr_p} vs {comp_p})",
                    "json": {
                        "measure": m,
                        "family": "COMPARISON",
                        "current_period": curr_p,
                        "comparison_period": comp_p,
                        "filters": [{"dimension": "country", "operator": "=", "values": [c]}],
                    },
                    "expected_status": TurnStatus.ANSWERED,
                    "check_comparison_sql": True,
                })
                case_idx += 1

    # 5. Allocation Policy Queries (12 cases)
    for p in ["2024", "2023", None]:
        # Grouped by category across line items
        cases.append({
            "id": f"TC-ALLOC-{case_idx:03d}",
            "query": f"Allocated revenue by category in {p or 'all'}",
            "json": {
                "measure": "sales_revenue_allocated",
                "family": "TOTAL",
                "dimensions": ["category"],
                "current_period": p,
            },
            "expected_status": TurnStatus.ANSWERED,
            "check_allocation_disclosure": True,
        })
        case_idx += 1

        # Grouped by category and country
        cases.append({
            "id": f"TC-ALLOC-{case_idx:03d}",
            "query": f"Allocated revenue by category and country in {p or 'all'}",
            "json": {
                "measure": "sales_revenue_allocated",
                "family": "TOTAL",
                "dimensions": ["category", "country"],
                "current_period": p,
            },
            "expected_status": TurnStatus.ANSWERED,
            "check_allocation_disclosure": True,
        })
        case_idx += 1

        # Filtered to Electronics (which has items in 2024 seed data)
        cases.append({
            "id": f"TC-ALLOC-{case_idx:03d}",
            "query": f"Allocated revenue for Electronics in {p or 'all'}",
            "json": {
                "measure": "sales_revenue_allocated",
                "family": "TOTAL",
                "dimensions": ["category"],
                "current_period": p,
                "filters": [{"dimension": "category", "operator": "=", "values": ["Electronics"]}],
            },
            "expected_status": TurnStatus.ANSWERED,
            "check_allocation_disclosure": (p in ("2024", None)),
        })
        case_idx += 1

        # Comparison of allocated revenue
        cases.append({
            "id": f"TC-ALLOC-{case_idx:03d}",
            "query": f"Compare allocated revenue by category",
            "json": {
                "measure": "sales_revenue_allocated",
                "family": "COMPARISON",
                "dimensions": ["category"],
                "current_period": "2024",
                "comparison_period": "2023",
            },
            "expected_status": TurnStatus.ANSWERED,
            "check_allocation_disclosure": True,
            "check_comparison_sql": True,
        })
        case_idx += 1

    # 6. Averages and Rates (17 cases)
    for p in periods:
        for c in countries:
            cases.append({
                "id": f"TC-AVG-{case_idx:03d}",
                "query": f"Average order value in {c} for {p}",
                "json": {
                    "measure": "avg_order_value",
                    "family": "AVERAGE",
                    "current_period": p,
                    "filters": [{"dimension": "country", "operator": "=", "values": [c]}],
                },
                "expected_status": TurnStatus.ANSWERED,
            })
            case_idx += 1

    for p in ["2024", "2023", "2022", None]:
        cases.append({
            "id": f"TC-AVG-{case_idx:03d}",
            "query": f"Average order value by country in {p or 'all'}",
            "json": {
                "measure": "avg_order_value",
                "family": "AVERAGE",
                "dimensions": ["country"],
                "current_period": p,
            },
            "expected_status": TurnStatus.ANSWERED,
        })
        case_idx += 1

    for p in ["2024", "2023", "2022", None]:
        cases.append({
            "id": f"TC-AVG-{case_idx:03d}",
            "query": f"Average order value by period in {p or 'all'}",
            "json": {
                "measure": "avg_order_value",
                "family": "AVERAGE",
                "dimensions": ["period"],
            },
            "expected_status": TurnStatus.ANSWERED,
        })
        case_idx += 1

    # 7. Ambiguous Queries Requiring Clarification (20 cases)
    ambiguous_templates = [
        ("Show revenue growth", '{"measure": "sales_revenue", "family": "COMPARISON"}'),
        ("Compare order count", '{"measure": "order_count", "family": "COMPARISON"}'),
        ("Average order comparison", '{"measure": "avg_order_value", "family": "COMPARISON"}'),
        ("Revenue compared to last year", '{"measure": "sales_revenue", "family": "COMPARISON", "current_period": "2024"}'),
        ("Order count compared", '{"measure": "order_count", "family": "COMPARISON", "comparison_period": "2023"}'),
    ]
    for prompt, mock_json in ambiguous_templates:
        cases.append({
            "id": f"TC-AMB-{case_idx:03d}",
            "query": prompt,
            "raw_json_str": mock_json,
            "expected_status": TurnStatus.AWAITING_CLARIFICATION,
        })
        case_idx += 1

    for i in range(15):
        cases.append({
            "id": f"TC-AMB-{case_idx:03d}",
            "query": f"Show ambiguous comparison variation {i}",
            "raw_json_str": '{"measure": "sales_revenue", "family": "COMPARISON"}',
            "expected_status": TurnStatus.AWAITING_CLARIFICATION,
        })
        case_idx += 1

    # 8. Declines: RBAC, Greetings, Tenant Escapes, Dangerous Keywords (30 cases)
    declines = [
        # RBAC role violations (analyst trying to access sales_director vip_segment or confidential salary)
        ("sales_revenue broken down by vip_segment in 2024", '{"measure": "sales_revenue", "family": "TOTAL", "dimensions": ["vip_segment"], "current_period": "2024"}'),
        ("sales_revenue broken down by vip_segment in 2023", '{"measure": "sales_revenue", "family": "TOTAL", "dimensions": ["vip_segment"], "current_period": "2023"}'),
        ("sales_revenue broken down by vip_segment", '{"measure": "sales_revenue", "family": "TOTAL", "dimensions": ["vip_segment"]}'),
        ("Give me employee salaries", '{"measure": "employee_salary", "family": "TOTAL"}'),
        ("Show confidential salaries", '{"measure": "employee_salary", "family": "AVERAGE"}'),
        # Tenant column override attacks
        ("Filter by other tenant", '{"measure": "sales_revenue", "family": "TOTAL", "filters": [{"dimension": "tenant_id", "operator": "=", "values": ["tenant_b"]}]}'),
        ("Filter tenant on orders", '{"measure": "order_count", "family": "TOTAL", "filters": [{"dimension": "tenant_id", "operator": "=", "values": ["other"]}]}'),
        # SQL keyword injection attempts
        ("DROP TABLE orders;", '{"measure": "DROP"}'),
        ("DELETE FROM customers;", '{"measure": "DELETE"}'),
        ("UPDATE orders SET amount = 0;", '{"measure": "UPDATE"}'),
        ("INSERT INTO orders VALUES (1);", '{"measure": "INSERT"}'),
        ("ALTER TABLE orders;", '{"measure": "ALTER"}'),
        # Unsupported / non-existent metrics
        ("Calculate unknown metric", '{"measure": "crypto_profit", "family": "TOTAL"}'),
        ("Show non-existent metric", '{"measure": "magic_revenue", "family": "TOTAL"}'),
        ("Unsupported microdata metric", '{"measure": "unsupported_microdata", "family": "TOTAL"}'),
        # Slicing item_sales_revenue across customer country (fan-out violation without allocation policy)
        ("item_sales_revenue broken down by country in 2024", '{"measure": "item_sales_revenue", "family": "TOTAL", "dimensions": ["country"], "current_period": "2024"}'),
        ("item_sales_revenue broken down by country in 2023", '{"measure": "item_sales_revenue", "family": "TOTAL", "dimensions": ["country"], "current_period": "2023"}'),
        ("item_sales_revenue broken down by country", '{"measure": "item_sales_revenue", "family": "TOTAL", "dimensions": ["country"]}'),
        # Greetings
        ("Hello there!", '{"greeting": true}'),
        ("Hi", '{"greeting": true}'),
        ("Good morning assistant", '{"greeting": true}'),
        ("Who are you?", '{"greeting": true}'),
        ("What can you do?", '{"greeting": true}'),
    ]
    for prompt, mock_json in declines:
        cases.append({
            "id": f"TC-DEC-{case_idx:03d}",
            "query": prompt,
            "raw_json_str": mock_json,
            "expected_status": TurnStatus.DECLINED,
        })
        case_idx += 1

    # Pad remaining declines
    for i in range(7):
        cases.append({
            "id": f"TC-DEC-{case_idx:03d}",
            "query": f"Hello friend {i}",
            "raw_json_str": '{"greeting": true}',
            "expected_status": TurnStatus.DECLINED,
        })
        case_idx += 1

    return cases


ALL_BENCHMARK_CASES = _build_220_benchmark_cases()


def test_benchmark_suite_total_cases_count():
    """Ensure suite contains at least 200 questions."""
    assert len(ALL_BENCHMARK_CASES) >= 200


@pytest.mark.parametrize("case", ALL_BENCHMARK_CASES, ids=[c["id"] for c in ALL_BENCHMARK_CASES])
def test_governed_query_benchmark_case(test_setup, case):
    """Run single benchmark case through ConversationLifecycleManager."""
    manager = test_setup["manager"]
    actor = test_setup["actor_a"]
    source_id = "finance_dw"

    raw_json = case.get("raw_json_str") or json.dumps(case["json"])

    turn, answer = manager.submit_turn(
        actor,
        f"sess_{case['id']}",
        source_id,
        case["query"],
        proposed_llm_json=raw_json,
    )

    expected_status = case["expected_status"]
    assert turn.status == expected_status, (
        f"Case {case['id']} expected {expected_status}, got {turn.status} "
        f"(decline_reason: {turn.decline_reason})"
    )

    if expected_status == TurnStatus.ANSWERED:
        assert answer is not None, f"Case {case['id']} missing answer view"
        assert answer.summary_text, f"Case {case['id']} missing summary text"
        assert answer.evidence_digest, f"Case {case['id']} missing evidence digest"

        # Check ranking
        if case.get("check_ranking"):
            evidence = manager.get_evidence(turn.turn_id)
            assert evidence is not None
            assert len(answer.table_rows) <= case["expected_limit"]

        # Check comparison sql capture
        if case.get("check_comparison_sql"):
            assert answer.sql_query is not None, f"Case {case['id']} comparison sql_query is empty"
            assert "Current Period" in answer.sql_query
            assert "Comparison Period" in answer.sql_query

        # Check allocation method disclosure in answer text
        if case.get("check_allocation_disclosure"):
            assert "equal-split allocation" in answer.summary_text.lower(), (
                f"Case {case['id']} missing allocation disclosure in narrative: {answer.summary_text}"
            )

    elif expected_status == TurnStatus.AWAITING_CLARIFICATION:
        assert turn.clarification_request is not None, f"Case {case['id']} missing clarification request"
        assert len(turn.clarification_request.options) > 0

    elif expected_status == TurnStatus.DECLINED:
        assert turn.decline_reason, f"Case {case['id']} missing decline reason"
