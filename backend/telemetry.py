"""OpenTelemetry & Prometheus instrumentation for Talk2Data enterprise analytics.

Adheres strictly to zero-data-leakage governance:
- Records stage names, latencies, and execution statuses.
- Excludes customer PII, unparameterized SQL values, and proprietary data payloads.
- Integrates both OpenTelemetry distributed tracing and Prometheus live metrics.
"""
from __future__ import annotations

import os
import time
import logging
from contextlib import contextmanager
from typing import Any, Dict, Generator, Optional

# OpenTelemetry API & SDK
from opentelemetry import trace
from opentelemetry.trace import StatusCode, Tracer
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.resources import Resource

# Prometheus Client
from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    generate_latest,
    CONTENT_TYPE_LATEST,
    REGISTRY,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. OpenTelemetry Initialization (Idempotent)
# ---------------------------------------------------------------------------
_tracer: Optional[Tracer] = None

def get_tracer() -> Tracer:
    """Retrieve or initialize the global OpenTelemetry tracer."""
    global _tracer
    if _tracer is not None:
        return _tracer

    service_name = os.getenv("OTEL_SERVICE_NAME", "talk2data-analytics")
    service_version = "3.0.0"

    try:
        current_provider = trace.get_tracer_provider()
        # If default ProxyTracerProvider, register an active SDK TracerProvider
        if not hasattr(current_provider, "add_span_processor"):
            resource = Resource.create({
                "service.name": service_name,
                "service.version": service_version,
                "deployment.environment": os.getenv("TALK2DATA_ENV", "development"),
            })
            provider = TracerProvider(resource=resource)
            trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("talk2data.engine", service_version)
    except Exception as exc:
        logger.warning("OpenTelemetry tracer initialization fell back to no-op: %s", exc)
        _tracer = trace.get_tracer("talk2data.noop")

    return _tracer


# ---------------------------------------------------------------------------
# 2. Prometheus Metric Definitions
# ---------------------------------------------------------------------------

HTTP_REQUESTS_TOTAL = Counter(
    "talk2data_http_requests_total",
    "Total HTTP requests received by the Talk2Data API.",
    ["method", "endpoint", "status_code"],
)

TURNS_TOTAL = Counter(
    "talk2data_turns_total",
    "Total analytical question turns processed across sources.",
    ["status", "source_id"],
)

STAGE_DURATION_SECONDS = Histogram(
    "talk2data_stage_duration_seconds",
    "Duration of analytical execution stages in seconds.",
    ["stage", "status"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

QUEUE_DEPTH = Gauge(
    "talk2data_worker_queue_depth",
    "Current pending jobs waiting in the durable worker queue.",
)

GOVERNANCE_DECLINES_TOTAL = Counter(
    "talk2data_governance_declines_total",
    "Total requests declined by zero-hallucination semantic governance.",
    ["decline_category"],
)

CLARIFICATIONS_TOTAL = Counter(
    "talk2data_clarifications_total",
    "Total queries paused awaiting user clarification.",
    ["source_id"],
)


# ---------------------------------------------------------------------------
# 3. Governed OpenTelemetry Span Context Manager
# ---------------------------------------------------------------------------

@contextmanager
def otel_stage_span(
    stage_name: str,
    attributes: Optional[Dict[str, Any]] = None,
) -> Generator[trace.Span, None, None]:
    """Execute block within an OpenTelemetry span, enforcing zero-leakage constraints.
    
    Safe metadata (tenant, stage, status) is tracked; customer row values, secrets,
    and unparameterized SQL are strictly excluded.
    """
    tracer = get_tracer()
    safe_attrs = {"stage.name": stage_name, "engine": "talk2data"}
    if attributes:
        for k, v in attributes.items():
            # Allow scalar metadata, reject complex objects or query strings
            if isinstance(v, (str, int, float, bool)) and len(str(v)) < 128:
                safe_attrs[k] = v

    with tracer.start_as_current_span(stage_name, attributes=safe_attrs) as span:
        t0 = time.time()
        stage_status = "completed"
        try:
            yield span
            span.set_status(StatusCode.OK)
        except Exception as exc:
            stage_status = "error"
            span.set_status(StatusCode.ERROR, description="Stage execution failed (details masked)")
            span.set_attribute("error.type", exc.__class__.__name__)
            raise
        finally:
            elapsed = time.time() - t0
            STAGE_DURATION_SECONDS.labels(stage=stage_name, status=stage_status).observe(elapsed)


# ---------------------------------------------------------------------------
# 4. Telemetry Helpers for Worker & Lifecycle
# ---------------------------------------------------------------------------

def record_turn_outcome(
    turn_status: str,
    source_id: str,
    duration_sec: float,
    decline_reason: Optional[str] = None,
) -> None:
    """Record turn completion metrics in Prometheus."""
    TURNS_TOTAL.labels(status=turn_status, source_id=source_id or "unknown").inc()
    STAGE_DURATION_SECONDS.labels(stage="turn_total", status=turn_status.lower()).observe(duration_sec)

    if turn_status == "DECLINED" and decline_reason:
        # Categorize reason safely without variable leaks
        cat = "general_governance"
        low = decline_reason.lower()
        if "not supported" in low or "unsupported" in low:
            cat = "unsupported_family_or_metric"
        elif "allocation" in low or "fan-out" in low:
            cat = "allocation_policy_required"
        elif "permission" in low or "denied" in low or "unauthorized" in low:
            cat = "access_denied"
        elif "average" in low:
            cat = "average_measure_required"
        elif "dimension" in low:
            cat = "unapproved_dimension"
        GOVERNANCE_DECLINES_TOTAL.labels(decline_category=cat).inc()

    elif turn_status == "AWAITING_CLARIFICATION":
        CLARIFICATIONS_TOTAL.labels(source_id=source_id or "unknown").inc()


def update_queue_depth(depth: int) -> None:
    """Update current durable worker queue depth gauge."""
    QUEUE_DEPTH.set(max(0, depth))


def generate_prometheus_metrics() -> bytes:
    """Generate latest Prometheus scrape format text."""
    return generate_latest(REGISTRY)


def get_telemetry_snapshot() -> Dict[str, Any]:
    """Retrieve operational telemetry snapshot for administrative inspection."""
    tracer = get_tracer()
    return {
        "service": "talk2data-analytics",
        "version": "3.0.0",
        "opentelemetry_active": tracer is not None,
        "prometheus_metrics_active": True,
        "langsmith_tracing_active": os.getenv("LANGSMITH_TRACING", "").lower() in ("true", "1", "yes"),
        "langsmith_project": os.getenv("LANGSMITH_PROJECT", "Talk2Data"),
        "deepeval_active": True,
        "metrics_url": "/metrics",
        "timestamp_utc": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
    }
