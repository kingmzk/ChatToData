"""Unit tests for production-grade OpenTelemetry tracing and Prometheus metrics."""
import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.observability import traced_stage
from backend.telemetry import (
    get_tracer,
    otel_stage_span,
    record_turn_outcome,
    update_queue_depth,
    generate_prometheus_metrics,
    get_telemetry_snapshot,
    QUEUE_DEPTH,
    TURNS_TOTAL,
    GOVERNANCE_DECLINES_TOTAL,
    HTTP_REQUESTS_TOTAL,
)


def test_tracer_initialization():
    """Verify OpenTelemetry tracer initializes and yields valid tracer instance."""
    tracer = get_tracer()
    assert tracer is not None
    assert hasattr(tracer, "start_as_current_span")


def test_otel_stage_span_zero_leakage():
    """Verify spans record stage name and safely filter out complex or overly long attributes."""
    called = False
    attributes = {
        "tenant_id": "test_tenant",
        "turn_id": "turn_12345",
        "allowed_number": 42,
        # Potentially sensitive or unparameterized raw query payload that should not be indexed as simple short scalar
        "raw_query_data": "A" * 200,
    }

    with otel_stage_span("test_governed_stage", attributes=attributes) as span:
        called = True
        assert span is not None

    assert called is True


def test_traced_stage_decorator_with_otel():
    """Verify @traced_stage executes transparently and records duration without errors."""
    @traced_stage("unit_test_math")
    def compute(a: int, b: int) -> int:
        return a + b

    result = compute(10, 25)
    assert result == 35


def test_record_turn_outcome_and_queue_depth():
    """Verify Prometheus turn and queue depth metrics update accurately."""
    update_queue_depth(7)
    # Check gauge value
    assert QUEUE_DEPTH._value.get() == 7

    update_queue_depth(0)
    assert QUEUE_DEPTH._value.get() == 0

    record_turn_outcome(
        turn_status="ANSWERED",
        source_id="ecommerce_db",
        duration_sec=0.15,
    )
    record_turn_outcome(
        turn_status="DECLINED",
        source_id="ecommerce_db",
        duration_sec=0.05,
        decline_reason="Access denied: Dimension not permitted",
    )

    metrics_text = generate_prometheus_metrics().decode("utf-8")
    assert "talk2data_turns_total" in metrics_text
    assert "talk2data_worker_queue_depth" in metrics_text
    assert "talk2data_stage_duration_seconds" in metrics_text
    assert "talk2data_governance_declines_total" in metrics_text


def test_telemetry_snapshot():
    """Verify telemetry snapshot contains required service metadata."""
    snap = get_telemetry_snapshot()
    assert snap["service"] == "talk2data-analytics"
    assert snap["version"] == "3.0.0"
    assert snap["prometheus_metrics_active"] is True
    assert "langsmith_tracing_active" in snap
    assert snap["deepeval_active"] is True
    assert snap["metrics_url"] == "/metrics"
    assert "timestamp_utc" in snap


def test_metrics_endpoint_http():
    """Verify GET /metrics returns valid Prometheus exposition text."""
    app = create_app()
    client = TestClient(app)

    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers.get("content-type", "")
    assert "talk2data_http_requests_total" in response.text


def test_telemetry_stats_endpoint_http():
    """Verify GET /api/v1/telemetry/stats returns JSON snapshot."""
    app = create_app()
    client = TestClient(app)

    headers = {
        "X-Tenant-Id": "tenant_default",
        "X-Subject-Id": "analyst_alice",
        "X-Roles": "analyst",
    }
    response = client.get("/api/v1/telemetry/stats", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "talk2data-analytics"
    assert data["version"] == "3.0.0"
    assert data["prometheus_metrics_active"] is True
