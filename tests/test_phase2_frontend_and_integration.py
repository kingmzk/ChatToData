"""Tests for Talk2Data Phase 2 Frontend Integration and End-to-End API Workflows."""
from fastapi.testclient import TestClient
import pytest

from talk2data.api.app import create_app
from talk2data.storage.repository import PersistentApplicationStore


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


def test_frontend_ui_serving_and_elements(api_client):
    """Verify that the frontend UI is served at root with all required states and controls."""
    client, _, _ = api_client
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    html = res.text

    # Brand and Header
    assert "Talk2Data" in html
    assert "Enterprise Conversational Analytics" in html

    # Core UI components
    assert 'id="turns-container"' in html
    assert 'id="question-input"' in html
    assert 'id="btn-submit"' in html
    assert 'id="conv-list"' in html

    # States & Action controls
    assert "Processing analysis" in html
    assert "clarification-options" in html
    assert "Export XLSX" in html
    assert "Export CSV" in html
    assert "kpi-grid" in html


def test_end_to_end_clarification_and_export_workflow(api_client):
    """Verify full asynchronous lifecycle: question -> clarification -> answer -> exports."""
    client, store, _ = api_client
    headers = {"X-Tenant-Id": "tenant_a", "X-Subject-Id": "user_alice"}

    # 1. Create conversation
    res_c = client.post("/api/v1/conversations", headers=headers, json={"title": "E2E Test"})
    assert res_c.status_code == 201
    conv_id = res_c.json()["conversation_id"]

    # 2. Submit ambiguous question triggering clarification
    res_q = client.post(
        f"/api/v1/conversations/{conv_id}/questions",
        headers=headers,
        json={
            "source_id": "finance_dw",
            "question": "Show sales growth.",
            "proposed_llm_json": '{"measure": "sales_revenue", "family": "COMPARISON"}',
        },
    )
    assert res_q.status_code == 202
    turn_id = res_q.json()["turn_id"]

    # 3. Poll turn status -> AWAITING_CLARIFICATION
    res_poll = client.get(f"/api/v1/turns/{turn_id}", headers=headers)
    assert res_poll.status_code == 200
    turn_data = res_poll.json()
    assert turn_data["status"] == "AWAITING_CLARIFICATION"
    assert turn_data["clarification_request_json"] is not None

    import json
    clarif = json.loads(turn_data["clarification_request_json"])
    choice_id = clarif["options"][0]["choice_id"]

    # 4. Submit clarification choice
    res_choice = client.post(
        f"/api/v1/turns/{turn_id}/clarification",
        headers=headers,
        json={"choice_id": choice_id},
    )
    assert res_choice.status_code == 202

    # 5. Poll turn status -> ANSWERED
    res_poll2 = client.get(f"/api/v1/turns/{turn_id}", headers=headers)
    assert res_poll2.status_code == 200
    assert res_poll2.json()["status"] == "ANSWERED"

    # 6. Retrieve published answer
    res_ans = client.get(f"/api/v1/turns/{turn_id}/answer", headers=headers)
    assert res_ans.status_code == 200
    ans_body = res_ans.json()
    assert "summary_text" in ans_body
    assert "kpi_cards" in ans_body
    assert "table_rows" in ans_body

    # 7. Export to XLSX
    res_xlsx = client.get(f"/api/v1/turns/{turn_id}/export/xlsx", headers=headers)
    assert res_xlsx.status_code == 200
    assert "spreadsheetml" in res_xlsx.headers["content-type"]
    assert len(res_xlsx.content) > 1000

    # 8. Export to CSV
    res_csv = client.get(f"/api/v1/turns/{turn_id}/export/csv", headers=headers)
    assert res_csv.status_code == 200
    assert "text/csv" in res_csv.headers["content-type"]
    assert len(res_csv.text) > 10

    # 9. Follow-up suggestions
    res_fu = client.get(f"/api/v1/conversations/{conv_id}/follow-ups", headers=headers)
    assert res_fu.status_code == 200
    assert "suggestions" in res_fu.json()
    assert len(res_fu.json()["suggestions"]) > 0


def test_turn_cancellation_workflow(api_client):
    """Verify cancellation endpoint updates turn and prevents publication."""
    client, store, _ = api_client
    headers = {"X-Tenant-Id": "tenant_a", "X-Subject-Id": "user_alice"}

    # 1. Create conversation
    res_c = client.post("/api/v1/conversations", headers=headers, json={"title": "Cancel Test"})
    conv_id = res_c.json()["conversation_id"]

    # 2. Submit question awaiting clarification so it pauses
    res_q = client.post(
        f"/api/v1/conversations/{conv_id}/questions",
        headers=headers,
        json={
            "source_id": "finance_dw",
            "question": "Show sales growth.",
            "proposed_llm_json": '{"measure": "sales_revenue", "family": "COMPARISON"}',
        },
    )
    turn_id = res_q.json()["turn_id"]

    # 3. Cancel turn
    res_cancel = client.post(f"/api/v1/turns/{turn_id}/cancel", headers=headers)
    assert res_cancel.status_code == 200
    assert res_cancel.json()["cancelled"] is True

    # 4. Verify turn status is CANCELLED
    res_poll = client.get(f"/api/v1/turns/{turn_id}", headers=headers)
    assert res_poll.status_code == 200
    assert res_poll.json()["status"] == "CANCELLED"
