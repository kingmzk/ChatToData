"""FastAPI Application factory for Talk2Data enterprise analytics service."""
from __future__ import annotations
import os
import time
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from fastapi.middleware.cors import CORSMiddleware
from backend.api.routes import router
from backend.telemetry import (
    generate_prometheus_metrics,
    HTTP_REQUESTS_TOTAL,
    STAGE_DURATION_SECONDS,
)
from backend.catalog.store import SemanticCatalogStore
from backend.context.followup import FollowUpService
from backend.execution.gateway import GenericSourceGateway
from backend.export.export import SecureExportService
from backend.jobs.worker import DurableJobWorker
from backend.lifecycle.session import ConversationLifecycleManager
from backend.security.auth import SecurityRuntime
from backend.storage.repository import PersistentApplicationStore
from backend.config import get_db_path


def create_app(
    store: Optional[PersistentApplicationStore] = None,
    manager: Optional[ConversationLifecycleManager] = None,
    security: Optional[SecurityRuntime] = None,
    catalogs: Optional[SemanticCatalogStore] = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Talk2Data Enterprise Analytics",
        description="Governed, verified conversational data analytics API",
        version="3.0.0",
    )

    # API and daemon load identical catalog, permission and read-only source configuration.
    configured_gateway = None
    if manager is None and security is None and catalogs is None:
        from backend.runtime import load_runtime
        security, catalogs, configured_gateway = load_runtime()
    # Initialize components if not provided
    sec = security or SecurityRuntime()
    cat_store = catalogs or SemanticCatalogStore()
    app_store = store or PersistentApplicationStore(get_db_path())

    if manager is None:
        gateway = configured_gateway or GenericSourceGateway()
        mgr = ConversationLifecycleManager(
            security_runtime=sec,
            catalog_store=cat_store,
            gateway=gateway,
        )
    else:
        mgr = manager

    worker = DurableJobWorker(store=app_store, manager=mgr, security=sec)
    export_svc = SecureExportService(store=app_store, security=sec, catalogs=cat_store)
    followup_svc = FollowUpService(store=app_store, catalogs=cat_store)

    # Attach state
    app_store.security = sec
    app_store.catalogs = cat_store
    embedded_env = os.getenv("TALK2DATA_EMBEDDED_WORKER")
    if embedded_env is not None:
        app.state.run_embedded_worker = embedded_env.lower() in ("true", "1", "yes")
    elif store is None:
        # Default for uvicorn standalone startup: run embedded worker so turns execute immediately
        app.state.run_embedded_worker = True
    else:
        # Custom store injected (e.g. standalone worker test without embedded execution)
        app.state.run_embedded_worker = manager is not None
    app.state.security_runtime = sec
    app.state.catalog_store = cat_store
    app.state.store = app_store
    app.state.manager = mgr
    app.state.gateway = mgr.gateway
    app.state.worker = worker
    app.state.export_service = export_svc
    app.state.followup_service = followup_svc

    # Decoupled React frontend support: Enable CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def opentelemetry_metrics_middleware(request: Request, call_next):
        start_time = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start_time

        path = request.url.path
        if path.startswith("/api/v1/turns/"):
            normalized_path = "/api/v1/turns/{id}"
        elif path.startswith("/api/v1/conversations/"):
            normalized_path = "/api/v1/conversations/{id}"
        elif path.startswith("/api/v1/jobs/"):
            normalized_path = "/api/v1/jobs/{id}"
        else:
            normalized_path = path

        try:
            HTTP_REQUESTS_TOTAL.labels(
                method=request.method,
                endpoint=normalized_path,
                status_code=str(response.status_code),
            ).inc()
            STAGE_DURATION_SECONDS.labels(
                stage=f"http_{request.method.lower()}",
                status=str(response.status_code),
            ).observe(duration)
        except Exception:
            pass
        return response

    @app.get("/metrics", tags=["System"], include_in_schema=False)
    def prometheus_metrics():
        """Prometheus metrics scrape endpoint for OpenTelemetry / monitoring agents."""
        return Response(
            content=generate_prometheus_metrics(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    # Mount API routes
    app.include_router(router)

    # Serve static assets and decoupled React UI
    current_path = Path(__file__).resolve()
    frontend_dist = current_path.parent.parent.parent / "frontend" / "dist"
    for parent in current_path.parents:
        candidate = parent / "frontend" / "dist"
        if candidate.exists():
            frontend_dist = candidate
            break

    static_dir = Path(__file__).parent / "static"

    if frontend_dist.exists() and (frontend_dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="react-assets")

    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def serve_ui():
        if (frontend_dist / "index.html").exists():
            return HTMLResponse(content=(frontend_dist / "index.html").read_text(encoding="utf-8"))
        if (static_dir / "index.html").exists():
            return HTMLResponse(content=(static_dir / "index.html").read_text(encoding="utf-8"))
        return HTMLResponse(content="<h1>Talk2Data Enterprise Analytics</h1><p>Separate React application available in frontend/</p>")

    @app.get("/healthz", tags=["System"])
    def health_check():
        return {"status": "ok", "phase": "Governed Analytics", "privacy": "basic suppression; cross-query protected datasets disabled"}

    return app
