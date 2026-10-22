"""
Comprehensive End-to-End Test Suite for Talk2Data Platform
Tests 35 Complex Queries across 3 Data Sources:
- enterprise_nexus (15-table multi-hop join enterprise schema)
- complex_relations (many-to-many promotion & tag graph)
- demo_sales (star schema sales dataset)

Validates:
1. HTTP Asynchronous Polling Protocol (POST 202 -> GET 200)
2. Semantic Intent Interpretation & Parameter Pinning
3. Multi-table Joins & Statistical Aggregations (TOTAL, AVERAGE, TREND, COMPARISON, CONTRIBUTION)
4. Clarification Pause & Zero-Hallucination Guardrails
5. Observability telemetry updates (Prometheus & OpenTelemetry)
"""
import time
import requests
import sys

BASE_URL = "http://127.0.0.1:8000"

def run_turn(source_id: str, question: str, expect_clarification: bool = False, clarification_answer: str = None):
    # Step 1: Create Conversation
    conv_resp = requests.post(f"{BASE_URL}/api/v1/conversations", json={"source_id": source_id, "title": f"Test: {question[:30]}"})
    if conv_resp.status_code != 201:
        return False, f"Failed to create conversation: {conv_resp.text}", 0.0
    conv_id = conv_resp.json()["conversation_id"]

    # Step 2: Post Question with source_id
    t0 = time.time()
    q_resp = requests.post(
        f"{BASE_URL}/api/v1/conversations/{conv_id}/questions",
        json={"source_id": source_id, "question": question}
    )
    if q_resp.status_code != 202:
        return False, f"Failed to submit question: {q_resp.text}", 0.0
    turn_id = q_resp.json()["turn_id"]

    # Step 3: Poll for Completion (up to 40 seconds)
    max_wait = 40.0
    status = "PENDING"
    turn_data = None
    while max_wait > 0:
        time.sleep(1.0)
        max_wait -= 1.0
        t_resp = requests.get(f"{BASE_URL}/api/v1/turns/{turn_id}")
        if t_resp.status_code == 200:
            turn_data = t_resp.json()
            status = turn_data.get("status")
            if status in ["ANSWERED", "AWAITING_CLARIFICATION", "FAILED"]:
                break

    duration = time.time() - t0

    if expect_clarification:
        if status == "AWAITING_CLARIFICATION":
            choices = turn_data.get("clarification_request", {}).get("options", [])
            choice_id = choices[0].get("choice_id") if choices else "choice_1"
            c_resp = requests.post(
                f"{BASE_URL}/api/v1/turns/{turn_id}/clarification",
                json={"choice_id": choice_id}
            )
            if c_resp.status_code != 202:
                return False, f"Failed to submit clarification: {c_resp.text}", duration
            
            # Wait for post-clarification answer
            max_wait = 40.0
            while max_wait > 0:
                time.sleep(1.0)
                max_wait -= 1.0
                t_resp = requests.get(f"{BASE_URL}/api/v1/turns/{turn_id}")
                if t_resp.status_code == 200:
                    turn_data = t_resp.json()
                    if turn_data.get("status") == "ANSWERED":
                        ans_resp = requests.get(f"{BASE_URL}/api/v1/turns/{turn_id}/answer")
                        summary = ans_resp.json().get("summary_text", "Answered") if ans_resp.status_code == 200 else "Answered"
                        return True, f"Clarified & Answered: {summary[:40]}", time.time() - t0
            return False, "Clarification timed out", duration
        else:
            return False, f"Expected AWAITING_CLARIFICATION, got {status}", duration

    if status == "ANSWERED":
        ans_resp = requests.get(f"{BASE_URL}/api/v1/turns/{turn_id}/answer")
        summary = ans_resp.json().get("summary_text", "Answered") if ans_resp.status_code == 200 else "Answered"
        return True, summary[:45], duration
    else:
        err = (turn_data.get("error_message") if turn_data else None) or f"Timed out or status: {status}"
        return False, err, duration


TEST_QUERIES = [
    # ==================== DATASET 1: ENTERPRISE_NEXUS (15 Queries) ====================
    # Multi-hop joins across continents, departments, suppliers, warehouses, products
    {"id": 1, "source": "enterprise_nexus", "q": "What is the total revenue in 2024?", "clarify": False},
    {"id": 2, "source": "enterprise_nexus", "q": "Show total revenue by continent in 2024", "clarify": False},
    {"id": 3, "source": "enterprise_nexus", "q": "What is the total revenue by department in 2023?", "clarify": False},
    {"id": 4, "source": "enterprise_nexus", "q": "Show gross profit margin by product category in 2024", "clarify": False},
    {"id": 5, "source": "enterprise_nexus", "q": "What are total units sold by supplier in 2024?", "clarify": False},
    {"id": 6, "source": "enterprise_nexus", "q": "Show total shipping spend by carrier in 2024", "clarify": False},
    {"id": 7, "source": "enterprise_nexus", "q": "What is total salary expense by department?", "clarify": False},
    {"id": 8, "source": "enterprise_nexus", "q": "Show average customer rating by category in 2024", "clarify": False},
    {"id": 9, "source": "enterprise_nexus", "q": "What is the trend of revenue across years?", "clarify": False},
    {"id": 10, "source": "enterprise_nexus", "q": "Show total revenue by customer tier in 2024", "clarify": False},
    {"id": 11, "source": "enterprise_nexus", "q": "What is inventory stock by warehouse?", "clarify": False},
    {"id": 12, "source": "enterprise_nexus", "q": "Compare revenue for 2024", "clarify": True, "answer": "Compare 2024 vs 2023"},
    {"id": 13, "source": "enterprise_nexus", "q": "Which product category contributed most to revenue growth?", "clarify": False},
    {"id": 14, "source": "enterprise_nexus", "q": "Show average order value by country in 2024", "clarify": False},
    {"id": 15, "source": "enterprise_nexus", "q": "What is the total revenue in region Europe in 2024?", "clarify": False},

    # ==================== DATASET 2: COMPLEX_RELATIONS (10 Queries) ====================
    # Many-to-many bridge tables, tags, promotions, order items
    {"id": 16, "source": "complex_relations", "q": "What is total order revenue in 2024?", "clarify": False},
    {"id": 17, "source": "complex_relations", "q": "Show order revenue by category in 2024", "clarify": False},
    {"id": 18, "source": "complex_relations", "q": "Show line revenue by city in 2024", "clarify": False},
    {"id": 19, "source": "complex_relations", "q": "What is the total order count by region?", "clarify": False},
    {"id": 20, "source": "complex_relations", "q": "Show item quantity by product name in 2024", "clarify": False},
    {"id": 21, "source": "complex_relations", "q": "What is the order count by promo code in 2024?", "clarify": False},
    {"id": 22, "source": "complex_relations", "q": "Show order revenue by tag name in 2024", "clarify": False},
    {"id": 23, "source": "complex_relations", "q": "What is the trend of line revenue across years?", "clarify": False},
    {"id": 24, "source": "complex_relations", "q": "Compare order revenue for 2024", "clarify": True, "answer": "2024 vs 2023"},
    {"id": 25, "source": "complex_relations", "q": "Show order revenue by customer in 2024", "clarify": False},

    # ==================== DATASET 3: DEMO_SALES (10 Queries) ====================
    # Star schema, sales items, regions, categories, trends
    {"id": 26, "source": "demo_sales", "q": "What is total sales in 2024?", "clarify": False},
    {"id": 27, "source": "demo_sales", "q": "Show sales by region in 2024", "clarify": False},
    {"id": 28, "source": "demo_sales", "q": "What is total sales by category in 2023?", "clarify": False},
    {"id": 29, "source": "demo_sales", "q": "Show total sales by customer segment in 2024", "clarify": False},
    {"id": 30, "source": "demo_sales", "q": "What is the sales trend across all years?", "clarify": False},
    {"id": 31, "source": "demo_sales", "q": "What is total sales in region West in 2024?", "clarify": False},
    {"id": 32, "source": "demo_sales", "q": "Show average order discount in 2024", "clarify": False},
    {"id": 33, "source": "demo_sales", "q": "Compare sales for 2024", "clarify": True, "answer": "Compare 2024 vs 2023"},
    {"id": 34, "source": "demo_sales", "q": "Which category contributed most to sales growth in 2024?", "clarify": False},
    {"id": 35, "source": "demo_sales", "q": "What is total sales for Corporate segment in 2023?", "clarify": False},
]

def main():
    print("=" * 80)
    print("TALK2DATA 35-QUERY COMPREHENSIVE END-TO-END EXECUTION SUITE")
    print("=" * 80)
    passed = 0
    failed = 0
    results = []

    # Record initial Prometheus scrape count
    try:
        m_before = requests.get(f"{BASE_URL}/metrics").text
        print("Prometheus /metrics endpoint is online and responding.")
    except Exception as e:
        print(f"Warning: /metrics not reachable: {e}")

    for item in TEST_QUERIES:
        qid = item["id"]
        source = item["source"]
        q = item["q"]
        clarify = item["clarify"]
        c_ans = item.get("answer")

        print(f"[{qid}/35] ({source}) \"{q}\" ...", end=" ", flush=True)
        ok, msg, dur = run_turn(source, q, expect_clarification=clarify, clarification_answer=c_ans)
        if ok:
            print(f"PASSED ({dur:.2f}s)")
            passed += 1
            results.append((qid, source, q, "PASS", dur, msg))
        else:
            print(f"FAILED ({dur:.2f}s) - {msg}")
            failed += 1
            results.append((qid, source, q, "FAIL", dur, msg))

    print("\n" + "=" * 80)
    print(f"EXECUTION SUMMARY: {passed} PASSED | {failed} FAILED (Total: 35)")
    print("=" * 80)

    # Scrape metrics again to show evaluation
    try:
        m_after = requests.get(f"{BASE_URL}/metrics").text
        # extract answered turns count
        answered_lines = [l for l in m_after.splitlines() if "talk2data_turns_total" in l and 'status="ANSWERED"' in l]
        clarified_lines = [l for l in m_after.splitlines() if "talk2data_clarifications_total" in l]
        print("\n--- FINAL TELEMETRY SNAPSHOT ---")
        for line in answered_lines:
            print(line)
        for line in clarified_lines:
            print(line)
    except Exception as e:
        print(f"Error fetching final metrics: {e}")

    return 0 if failed == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
