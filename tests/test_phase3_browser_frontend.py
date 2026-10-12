"""Tests for Phase 3 Governed Decision Science Frontend DOM, Clarification, and Visual Renderings."""
from decimal import Decimal
import json
import pytest
from fastapi.testclient import TestClient

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


def test_phase3_frontend_dom_elements_and_science_components(api_client):
    """Verify that the frontend UI contains Phase 3 badges, decision science controls, and chart renderers."""
    client, _, _ = api_client
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    html = res.text

    # Phase 3 Badges and Branding
    assert "badge-science" in html or "Decision Science" in html or "FORECAST" in html
    assert "ANOMALY" in html
    assert "SCENARIO" in html
    assert "GOAL_SEEK" in html

    # Visual rendering containers
    assert 'id="turns-container"' in html
    assert 'id="question-input"' in html
    assert 'id="btn-submit"' in html
    assert "kpi-grid" in html
    assert "clarification-options" in html
    assert "chart-card" in html


def test_phase3_frontend_forecast_clarification_and_result_rendering(api_client):
    """Verify end-to-end browser API workflow for Forecast: submission -> clarification -> rendered cards/charts."""
    client, store, setup = api_client
    conn = setup["gateway"]._conn
    conn.execute("DELETE FROM orders WHERE length(period) != 4")
    conn.commit()
    headers = {"X-Tenant-Id": "tenant_a", "X-Subject-Id": "user_alice"}

    # 1. Create conversation
    res_c = client.post("/api/v1/conversations", headers=headers, json={"title": "Decision Science Forecast"})
    assert res_c.status_code == 201
    conv_id = res_c.json()["conversation_id"]

    # 2. Submit forecast question without horizon -> triggers clarification
    res_q = client.post(
        f"/api/v1/conversations/{conv_id}/questions",
        headers=headers,
        json={
            "source_id": "finance_dw",
            "question": "Forecast sales revenue.",
            "proposed_llm_json": json.dumps({
                "measure": "sales_revenue",
                "family": "FORECAST",
                "dimensions": ["period"],
            }),
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

    clarif = json.loads(turn_data["clarification_request_json"])
    assert clarif["ambiguity_code"] == "forecast-horizon-required"
    assert len(clarif["options"]) >= 2
    choice_id = clarif["options"][0]["choice_id"]  # 1 period or 2 periods

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

    # 6. Retrieve rendered answer
    res_ans = client.get(f"/api/v1/turns/{turn_id}/answer", headers=headers)
    assert res_ans.status_code == 200
    ans_body = res_ans.json()
    assert "Projected" in ans_body["summary_text"] and "sales" in ans_body["summary_text"].lower()
    assert len(ans_body["kpi_cards"]) >= 1
    # Prediction interval present
    assert "PI:" in ans_body["kpi_cards"][0]["delta"]
    # Forecast table rendered
    assert "Lower PI" in ans_body["table_columns"]
    assert "Upper PI" in ans_body["table_columns"]

    # 7. Published answer contains schema-validated forecast chart specification
    assert "charts" in ans_body
    assert len(ans_body["charts"]) >= 1
    fc_chart = next(c for c in ans_body["charts"] if c["type"] == "forecast")
    assert fc_chart["title"] is not None
    assert any(v is not None for v in fc_chart["forecast_values"])
    assert any(b is not None for b in fc_chart["lower_bounds"])


def test_phase3_frontend_scenario_and_goalseek_rendering_and_export(api_client):
    """Verify end-to-end browser API workflow for Scenario / Goal Seek: clarification -> answer -> CSV/XLSX export."""
    client, store, setup = api_client
    conn = setup["gateway"]._conn
    conn.execute("DELETE FROM orders WHERE length(period) != 4")
    conn.commit()
    headers = {"X-Tenant-Id": "tenant_a", "X-Subject-Id": "user_alice"}

    # 1. Create conversation
    res_c = client.post("/api/v1/conversations", headers=headers, json={"title": "Goal Seek Simulation"})
    assert res_c.status_code == 201
    conv_id = res_c.json()["conversation_id"]

    # 2. Submit Goal Seek without adjustable driver -> clarification
    res_q = client.post(
        f"/api/v1/conversations/{conv_id}/questions",
        headers=headers,
        json={
            "source_id": "finance_dw",
            "question": "What price is needed for $50,000 revenue?",
            "proposed_llm_json": json.dumps({
                "measure": "sales_revenue",
                "family": "GOAL_SEEK",
                "scenario_model": "sales_simulator",
                "target_value": "50000",
            }),
        },
    )
    assert res_q.status_code == 202
    turn_id = res_q.json()["turn_id"]

    # 3. Poll turn -> AWAITING_CLARIFICATION
    res_poll = client.get(f"/api/v1/turns/{turn_id}", headers=headers)
    assert res_poll.status_code == 200
    turn_data = res_poll.json()
    assert turn_data["status"] == "AWAITING_CLARIFICATION"
    clarif = json.loads(turn_data["clarification_request_json"])
    assert clarif["ambiguity_code"] == "adjustable-driver-required"

    # Select avg_price driver
    opt = next(o for o in clarif["options"] if o["resolved_value"] == "avg_price")
    res_choice = client.post(
        f"/api/v1/turns/{turn_id}/clarification",
        headers=headers,
        json={"choice_id": opt["choice_id"]},
    )
    assert res_choice.status_code == 202

    # 4. Poll turn status -> ANSWERED
    res_poll2 = client.get(f"/api/v1/turns/{turn_id}", headers=headers)
    assert res_poll2.status_code == 200
    assert res_poll2.json()["status"] == "ANSWERED"

    # 5. Verify Answer
    res_ans = client.get(f"/api/v1/turns/{turn_id}/answer", headers=headers)
    assert res_ans.status_code == 200
    ans_body = res_ans.json()
    assert "Goal seek" in ans_body["summary_text"]
    assert any(card["title"] == "Goal Seek: avg_price" for card in ans_body["kpi_cards"])

    # 6. Verify Exports for Phase 3 decision science results
    res_csv = client.get(f"/api/v1/turns/{turn_id}/export/csv", headers=headers)
    assert res_csv.status_code == 200
    assert "text/csv" in res_csv.headers["content-type"]
    assert "Target Metric" in res_csv.text or "sales_revenue" in res_csv.text

    res_xlsx = client.get(f"/api/v1/turns/{turn_id}/export/xlsx", headers=headers)
    assert res_xlsx.status_code == 200
    assert "spreadsheetml" in res_xlsx.headers["content-type"]
    assert len(res_xlsx.content) > 100
