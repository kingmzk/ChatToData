"""FastAPI Application factory for Talk2Data enterprise analytics service."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Optional
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from fastapi.middleware.cors import CORSMiddleware
from talk2data.api.routes import router
from talk2data.catalog.store import SemanticCatalogStore
from talk2data.context.followup import FollowUpService
from talk2data.execution.gateway import GenericSourceGateway
from talk2data.export.export import SecureExportService
from talk2data.jobs.worker import DurableJobWorker
from talk2data.lifecycle.session import ConversationLifecycleManager
from talk2data.security.auth import SecurityRuntime
from talk2data.storage.repository import PersistentApplicationStore


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
        from talk2data.runtime import load_runtime
        security, catalogs, configured_gateway = load_runtime()
    # Initialize components if not provided
    sec = security or SecurityRuntime()
    cat_store = catalogs or SemanticCatalogStore()
    app_store = store or PersistentApplicationStore()

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
    app.state.run_embedded_worker = manager is not None
    app.state.security_runtime = sec
    app.state.catalog_store = cat_store
    app.state.store = app_store
    app.state.manager = mgr
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

    # Mount API routes
    app.include_router(router)

    # Serve static assets and decoupled React UI
    frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"
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
