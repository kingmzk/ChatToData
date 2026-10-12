"""Tests for Talk2Data Phase 2 API, Authentication, and Persistent Application State."""
import os
import pytest
from fastapi.testclient import TestClient

from talk2data.api.app import create_app
from talk2data.storage.repository import PersistentApplicationStore


@pytest.fixture
def app_client(test_setup):
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


def test_api_health_check(app_client):
    """Verify healthz endpoint."""
    client, _, _ = app_client
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_dev_vs_prod_authentication(app_client):
    """Verify distinction between development and production authentication."""
    client, _, _ = app_client

    # 1. Development mode allows dev headers
    os.environ["TALK2DATA_ENV"] = "development"
    res_dev = client.post(
        "/api/v1/conversations",
        headers={"X-Tenant-Id": "tenant_a", "X-Subject-Id": "user_alice"},
        json={"title": "Dev Conv"},
    )
    assert res_dev.status_code == 201
    assert res_dev.json()["tenant_id"] == "tenant_a"

    # 2. Production mode without secret configured explicitly disables production mode with 503
    os.environ["TALK2DATA_ENV"] = "production"
    os.environ.pop("TALK2DATA_AUTH_SECRET", None)
    try:
        res_no_sec = client.post("/api/v1/conversations", json={"title": "Disabled Prod"})
        assert res_no_sec.status_code == 503
        assert "TALK2DATA_AUTH_SECRET is not configured" in res_no_sec.json()["detail"]

        # Configure secret
        test_secret = "test-hmac-secret-987654"
        os.environ["TALK2DATA_AUTH_SECRET"] = test_secret

        # Client-supplied identity headers strictly rejected in production
        res_spoof = client.post(
            "/api/v1/conversations",
            headers={"X-Tenant-Id": "tenant_b", "X-Subject-Id": "user_bob"},
            json={"title": "Spoofed Conv"},
        )
        assert res_spoof.status_code == 400
        assert "Client-supplied identity headers are forbidden" in res_spoof.json()["detail"]

        # Missing bearer token rejected
        res_no_token = client.post("/api/v1/conversations", json={"title": "No Token Conv"})
        assert res_no_token.status_code == 401

        # Tampered or forged bearer token rejected
        res_tampered = client.post(
            "/api/v1/conversations",
            headers={"Authorization": "Bearer forged_payload.forged_signature"},
            json={"title": "Tampered Conv"},
        )
        assert res_tampered.status_code == 401
        assert "Cryptographic verification failed" in res_tampered.json()["detail"]

        # Valid cryptographically signed HMAC token accepted
        from talk2data.api.auth import create_production_token
        valid_token = create_production_token("tenant_a", "user_alice", secret=test_secret)
        res_prod = client.post(
            "/api/v1/conversations",
            headers={"Authorization": f"Bearer {valid_token}"},
            json={"title": "Prod Conv"},
        )
        assert res_prod.status_code == 201
        assert res_prod.json()["tenant_id"] == "tenant_a"

    finally:
        os.environ["TALK2DATA_ENV"] = "development"
        os.environ.pop("TALK2DATA_AUTH_SECRET", None)


def test_conversation_and_turn_lifecycle_via_api(app_client):
    """Test full asynchronous conversation lifecycle via HTTP API returning 202 Accepted."""
    client, store, setup = app_client

    # 1. Create conversation
    res_conv = client.post(
        "/api/v1/conversations",
        headers={"X-Tenant-Id": "tenant_a", "X-Subject-Id": "user_alice"},
        json={"title": "2024 Revenue Analysis"},
    )
    assert res_conv.status_code == 201
    conv_id = res_conv.json()["conversation_id"]

    # 2. Submit question -> returns 202 Accepted
    res_q = client.post(
        f"/api/v1/conversations/{conv_id}/questions",
        headers={
            "X-Tenant-Id": "tenant_a",
            "X-Subject-Id": "user_alice",
            "Idempotency-Key": "key_q1_test",
        },
        json={
            "source_id": "finance_dw",
            "question": "What was total sales revenue in 2024?",
            "proposed_llm_json": '{"measure": "sales_revenue", "family": "TOTAL", "current_period": "2024"}',
        },
    )
    assert res_q.status_code == 202
    q_data = res_q.json()
    assert "job_id" in q_data
    assert "turn_id" in q_data
    turn_id = q_data["turn_id"]

    # 3. Poll turn status
    res_status = client.get(
        f"/api/v1/turns/{turn_id}",
        headers={"X-Tenant-Id": "tenant_a", "X-Subject-Id": "user_alice"},
    )
    assert res_status.status_code == 200
    assert res_status.json()["status"] == "ANSWERED", f"Turn status: {res_status.json()['status']}, err: {res_status.json().get('error_message')}"

    # 4. Retrieve published answer
    res_ans = client.get(
        f"/api/v1/turns/{turn_id}/answer",
        headers={"X-Tenant-Id": "tenant_a", "X-Subject-Id": "user_alice"},
    )
    assert res_ans.status_code == 200
    ans_data = res_ans.json()
    assert "150,000.00" in ans_data["summary_text"]
    assert len(ans_data["kpi_cards"]) > 0


def test_unauthorized_cross_tenant_and_same_tenant_access_rejected(app_client):
    """Ensure cross-tenant and same-tenant access is rejected at API level."""
    client, store, _ = app_client

    # Alice creates conversation in Tenant A
    res = client.post(
        "/api/v1/conversations",
        headers={"X-Tenant-Id": "tenant_a", "X-Subject-Id": "user_alice"},
        json={"title": "Alice Conv"},
    )
    conv_id = res.json()["conversation_id"]

    # Bob (Tenant B) tries to read Alice's conversation -> 403 Forbidden
    res_bob = client.get(
        f"/api/v1/conversations/{conv_id}",
        headers={"X-Tenant-Id": "tenant_b", "X-Subject-Id": "user_bob"},
    )
    assert res_bob.status_code == 403

    # Charlie (same Tenant A, different user) tries to read Alice's conversation -> 403 Forbidden
    res_charlie = client.get(
        f"/api/v1/conversations/{conv_id}",
        headers={"X-Tenant-Id": "tenant_a", "X-Subject-Id": "user_charlie"},
    )
    assert res_charlie.status_code == 403
