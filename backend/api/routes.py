"""FastAPI router providing authenticated, authorized Talk2Data endpoints."""
from __future__ import annotations
import re
import uuid
from pathlib import Path
from typing import Any, Optional
from fastapi import BackgroundTasks, APIRouter, Depends, Header, HTTPException, Request, Response, status, UploadFile, File, Form
from pydantic import BaseModel, Field

from backend.api.auth import get_current_actor
from backend.catalog.extractor import introspect_sqlite_database
from backend.domain.models import ApiActor, Turn, TurnStatus
from backend.export.export import SecureExportService
from backend.jobs.worker import DurableJobWorker
from backend.security.auth import AuthorizationError
from backend.storage.repository import PersistentApplicationStore


router = APIRouter(prefix="/api/v1", tags=["Talk2Data API"])


class CreateConversationRequest(BaseModel):
    title: Optional[str] = Field("New Analysis", description="Title of conversation")


class SubmitQuestionRequest(BaseModel):
    source_id: str = Field(..., description="Target semantic catalog source ID")
    question: str = Field(..., description="Natural language analytics question")
    proposed_llm_json: Optional[str] = Field(None, description="Optional intent JSON")


class SubmitClarificationRequest(BaseModel):
    choice_id: str = Field(..., description="Selected clarification choice ID")


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
def create_conversation(
    req: CreateConversationRequest,
    actor: ApiActor = Depends(get_current_actor),
    request: Request = None,
):
    """Create a new conversation scoped to the authenticated tenant and actor."""
    store: PersistentApplicationStore = request.app.state.store
    conv_id = store.create_conversation(actor, title=req.title or "New Analysis")
    return {"conversation_id": conv_id, "title": req.title, "tenant_id": actor.tenant_id}


@router.get("/conversations")
def list_conversations(
    actor: ApiActor = Depends(get_current_actor),
    request: Request = None,
):
    """List all conversations owned by the caller."""
    store: PersistentApplicationStore = request.app.state.store
    return store.list_conversations(actor)


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: str,
    actor: ApiActor = Depends(get_current_actor),
    request: Request = None,
):
    """Retrieve conversation details and turn history."""
    store: PersistentApplicationStore = request.app.state.store
    try:
        conv = store.get_conversation(actor, conversation_id)
        turns = store.list_turns_for_conversation(actor, conversation_id)
        conv["turns"] = turns
        return conv
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    except AuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.post("/conversations/{conversation_id}/questions", status_code=status.HTTP_202_ACCEPTED)
def submit_question(
    conversation_id: str,
    req: SubmitQuestionRequest,
    background_tasks: BackgroundTasks,
    actor: ApiActor = Depends(get_current_actor),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    request: Request = None,
):
    """Idempotently submit a natural language question returning 202 Accepted with durable job status."""
    store: PersistentApplicationStore = request.app.state.store
    try:
        # Verify conversation ownership
        store.get_conversation(actor, conversation_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    except AuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

    ikey = idempotency_key or f"idem_{uuid.uuid4().hex}"
    if req.proposed_llm_json and __import__("os").getenv("TALK2DATA_ENV") == "production":
        raise HTTPException(400, "Proposed intent is only supported for development verification")
    try:
        request.app.state.security_runtime.check_source_access(actor, req.source_id)
        job, original, is_new = store.submit_question_atomic(actor, conversation_id, req.source_id, req.question, ikey, req.proposed_llm_json)
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    turn_id = original["turn_id"]

    # Process job synchronously or via worker pool
    worker: DurableJobWorker = request.app.state.worker
    if request.app.state.run_embedded_worker:
        background_tasks.add_task(worker.process_next_job)

    updated_turn = store.get_turn(actor, turn_id)
    return {
        "job_id": job["job_id"],
        "turn_id": turn_id,
        "status": updated_turn["status"],
        "is_new_submission": is_new,
    }


@router.get("/turns/{turn_id}")
def get_turn_status(
    turn_id: str,
    actor: ApiActor = Depends(get_current_actor),
    request: Request = None,
):
    """Poll turn execution status, error messages, and clarification options."""
    store: PersistentApplicationStore = request.app.state.store
    try:
        return store.get_turn(actor, turn_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Turn not found.")
    except AuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.post("/turns/{turn_id}/clarification", status_code=status.HTTP_202_ACCEPTED)
def submit_clarification(
    turn_id: str,
    req: SubmitClarificationRequest,
    background_tasks: BackgroundTasks,
    actor: ApiActor = Depends(get_current_actor),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    request: Request = None,
):
    """Resume execution with structured clarification choice."""
    store: PersistentApplicationStore = request.app.state.store
    try:
        turn = store.get_turn(actor, turn_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Turn not found.")
    except AuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

    if turn["status"] != "AWAITING_CLARIFICATION":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Turn is in status '{turn['status']}', not awaiting clarification.",
        )

    ikey = idempotency_key or f"clarif_{turn_id}_{req.choice_id}"
    job, _ = store.submit_job_idempotent(
        actor=actor,
        turn_id=turn_id,
        operation="SUBMIT_CLARIFICATION",
        idempotency_key=ikey,
        payload={"choice_id": req.choice_id, "actor": {"roles": list(actor.roles), "permissions": list(actor.permissions)}},
    )

    worker: DurableJobWorker = request.app.state.worker
    if request.app.state.run_embedded_worker:
        background_tasks.add_task(worker.process_next_job)

    updated_turn = store.get_turn(actor, turn_id)
    return {
        "job_id": job["job_id"],
        "turn_id": turn_id,
        "status": updated_turn["status"],
    }


@router.post("/turns/{turn_id}/cancel")
def cancel_turn(
    turn_id: str,
    actor: ApiActor = Depends(get_current_actor),
    request: Request = None,
):
    """Cancel in-flight turn execution."""
    store: PersistentApplicationStore = request.app.state.store
    try:
        success = store.cancel_job_and_turn(actor, turn_id)
        return {"turn_id": turn_id, "cancelled": success}
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Turn not found.")
    except AuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.get("/turns/{turn_id}/answer")
def get_answer(
    turn_id: str,
    actor: ApiActor = Depends(get_current_actor),
    request: Request = None,
):
    """Retrieve published answer view with KPI cards, tables, and charts."""
    store: PersistentApplicationStore = request.app.state.store
    try:
        return store.get_published_answer(actor, turn_id)
    except KeyError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except AuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.get("/turns/{turn_id}/export/xlsx")
def export_xlsx(
    turn_id: str,
    actor: ApiActor = Depends(get_current_actor),
    request: Request = None,
):
    """Export published dataset to XLSX workbook with formula injection protection."""
    export_service: SecureExportService = request.app.state.export_service
    try:
        data = export_service.export_xlsx(actor, turn_id)
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=talk2data_export_{turn_id}.xlsx"},
        )
    except KeyError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except AuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.get("/turns/{turn_id}/export/csv")
def export_csv(
    turn_id: str,
    actor: ApiActor = Depends(get_current_actor),
    request: Request = None,
):
    """Export published dataset to CSV with formula injection protection."""
    export_service: SecureExportService = request.app.state.export_service
    try:
        csv_text = export_service.export_csv(actor, turn_id)
        return Response(
            content=csv_text,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=talk2data_export_{turn_id}.csv"},
        )
    except KeyError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except AuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.get("/conversations/{conversation_id}/follow-ups")
def get_follow_ups(
    conversation_id: str,
    actor: ApiActor = Depends(get_current_actor),
    request: Request = None,
):
    """Generate safe follow-up suggestions from persisted analytical context."""
    followup_service = getattr(request.app.state, "followup_service", None)
    if not followup_service:
        return {"suggestions": []}
    try:
        suggestions = followup_service.get_follow_up_suggestions(actor, conversation_id)
        return {"suggestions": suggestions}
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    except AuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.get("/sources")
def accessible_sources(actor: ApiActor = Depends(get_current_actor), request: Request = None):
    security = request.app.state.security_runtime
    catalogs = request.app.state.catalog_store
    result = []
    for source_id in catalogs._active_releases:
        try:
            security.check_source_access(actor, source_id)
            result.append({"source_id": source_id})
        except AuthorizationError:
            continue
    return result


@router.get("/sources/{source_id}/catalog")
def get_source_catalog(
    source_id: str,
    actor: ApiActor = Depends(get_current_actor),
    request: Request = None,
):
    """Retrieve caller-authorized catalog schema (measures, dimensions, models)."""
    security = request.app.state.security_runtime
    catalogs = request.app.state.catalog_store
    security.check_source_access(actor, source_id)
    try:
        cat = catalogs.get_catalog(source_id)
        pin = catalogs.pin(actor.tenant_id, source_id)
        vocab = catalogs.get_accessible_vocabulary(actor, pin)
        
        measures = [
            {
                "id": m.id,
                "name": m.name,
                "table": m.table,
                "column": m.column,
                "description": m.description,
                "unit": m.unit,
                "aggregation": m.aggregation.value if hasattr(m.aggregation, "value") else str(m.aggregation),
                "allowed_families": [f.value if hasattr(f, "value") else str(f) for f in m.allowed_families],
                "allowed_dimensions": list(m.allowed_dimensions),
            }
            for m in vocab.measures.values()
        ]
        dimensions = [
            {
                "id": d.id,
                "name": d.name,
                "table": d.table,
                "column": d.column,
                "data_type": d.data_type.value if hasattr(d.data_type, "value") else str(d.data_type),
            }
            for d in vocab.dimensions.values()
        ]
        scenario_models = [
            {
                "model_id": sm.model_id,
                "name": sm.name,
                "description": sm.description,
                "output_metric": sm.output_metric,
                "drivers": [
                    {
                        "name": dr.name,
                        "unit": dr.unit,
                        "min_value": str(dr.min_value),
                        "max_value": str(dr.max_value),
                        "default_value": str(dr.default_value),
                    }
                    for dr in sm.drivers.values()
                ],
            }
            for sm in cat.scenario_models.values()
        ]
        time_series_policies = [
            {
                "metric": p.metric,
                "frequency": p.frequency,
                "period_dimension": p.period_dimension,
                "max_forecast_horizon": p.max_forecast_horizon,
            }
            for p in cat.time_series_policies.values()
        ]
        tables = [
            {
                "name": t.table_name,
                "primary_key": list(t.primary_key),
                "tenant_column": t.tenant_column,
                "grain": t.grain,
            }
            for t in cat.tables.values()
        ]
        joins = [
            {
                "from_table": j.from_table,
                "to_table": j.to_table,
                "from_column": j.from_column,
                "to_column": j.to_column,
                "cardinality": j.cardinality.value if hasattr(j.cardinality, "value") else str(j.cardinality),
                "join_type": j.join_type.value if hasattr(j.join_type, "value") else str(j.join_type),
            }
            for j in cat.joins
        ]
        return {
            "source_id": source_id,
            "release_id": cat.release_id,
            "epoch": cat.epoch,
            "tables": tables,
            "joins": joins,
            "measures": measures,
            "dimensions": dimensions,
            "scenario_models": scenario_models,
            "time_series_policies": time_series_policies,
        }
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/sources/upload", status_code=status.HTTP_201_CREATED)
async def upload_source_database(
    file: UploadFile = File(...),
    source_name: Optional[str] = Form(None),
    actor: ApiActor = Depends(get_current_actor),
    request: Request = None,
):
    """Upload a SQLite database, auto-extract schema and relationships, and register to catalog."""
    filename = file.filename or "uploaded.sqlite"
    if not any(filename.lower().endswith(ext) for ext in (".sqlite", ".db", ".sqlite3")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only SQLite database files (.sqlite, .db, .sqlite3) are currently supported.",
        )

    uploads_dir = Path("browser-demo/uploads")
    uploads_dir.mkdir(parents=True, exist_ok=True)

    clean_base = re.sub(r"[^a-zA-Z0-9_]", "_", Path(filename).stem)
    source_id = source_name.strip() if source_name and source_name.strip() else clean_base
    source_id = re.sub(r"[^a-zA-Z0-9_]", "_", source_id).lower()

    saved_path = uploads_dir / f"{source_id}.sqlite"
    content = await file.read()
    saved_path.write_bytes(content)

    try:
        catalog, manifest = introspect_sqlite_database(
            db_path=saved_path,
            source_id=source_id,
            default_tenant_id=actor.tenant_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Schema introspection failed: {exc}")

    # Register in catalog store
    cat_store = request.app.state.catalog_store
    cat_store.register_catalog(catalog)

    # Register physical path in gateway
    gateway = getattr(request.app.state, "gateway", None)
    if not gateway and hasattr(request.app.state, "manager"):
        gateway = getattr(request.app.state.manager, "gateway", None)
    if gateway:
        gateway.register_source(source_id, saved_path)

    # Register tenant permissions
    sec = request.app.state.security_runtime
    sec.register_tenant_source(actor.tenant_id, source_id)

    return {
        "status": "success",
        "source_id": source_id,
        "manifest": manifest,
    }


