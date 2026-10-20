"""Discrete, deterministic and bounded LLM workflow nodes for LangGraph StateGraph.

Principles:
- Principle 1: LLMs only where language uncertainty exists (intent interpretation, optional narrative template).
- Principle 2: Authoritative operations remain deterministic Python (validation, resolution, SQL AST, math, privacy, evidence sealing, guardrails).
- Principle 4: Privacy boundary: raw rows and unsuppressed facts never cross into checkpointed graph state.
"""
from __future__ import annotations
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import logging
import re
import uuid
from typing import Any, Optional

from langgraph.types import interrupt
from langchain_core.runnables import RunnableConfig

from backend.catalog.pinning import SemanticSnapshotPin
from backend.catalog.schema import SemanticCatalog
from backend.compiler.compiler import GovernedQueryCompiler
from backend.domain.facts import SealedEvidencePackage, ProtectedFact
from backend.domain.models import ApiActor, ClarificationOption, ClarificationRequest
from backend.execution.calendar import CalendarBoundaryResolver
from backend.execution.gateway import GenericSourceGateway
from backend.execution.kernel import DeterministicAnalysisKernel
from backend.graph.state import (
    GraphState,
    PublicationCandidate,
    ClarificationInterruptPayload,
)
from backend.graph.summary import DeterministicClaimGuardrail
from backend.intent.parser import IntentParser
from backend.intent.schema import AnalysisFamily, StructuredIntent
from backend.intent.validator import IntentValidator
from backend.presentation.charts import ChartGenerator
from backend.presentation.fallback import DeterministicPresentationFallback
from backend.security.auth import SecurityRuntime, AuthorizationError
from backend.security.disclosure import GovernedDisclosureService
from backend.semantic.plan import ResolvedSemanticPlan
from backend.semantic.resolver import SemanticResolver, SemanticResolutionError

logger = logging.getLogger(__name__)


def _get_services(config: RunnableConfig) -> dict[str, Any]:
    """Retrieve runtime injected dependencies from config."""
    cfg = config.get("configurable", {})
    return cfg.get("services", {})


def _get_actor(config: RunnableConfig, state: GraphState) -> ApiActor:
    """Retrieve or construct the authenticated ApiActor."""
    cfg = config.get("configurable", {})
    actor = cfg.get("actor")
    if actor and isinstance(actor, ApiActor):
        return actor
    tenant_id = state.get("tenant_id", "default_tenant")
    subject_id = state.get("subject_id", "default_subject")
    return ApiActor(tenant_id=tenant_id, subject_id=subject_id, roles=("analyst",))


# =====================================================================
# 8.1 access_context_node
# =====================================================================
def access_context_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Establish a trusted authorization context and verify baseline security."""
    services = _get_services(config)
    security: SecurityRuntime = services.get("security", SecurityRuntime())
    actor = _get_actor(config, state)
    
    question = state.get("question", "")
    source_id = state.get("source_id", "")
    
    # 1. Source authorization check
    try:
        security.check_source_access(actor, source_id)
    except Exception as exc:
        return {
            "access_status": "DECLINED",
            "validation_status": "DECLINE",
            "decline_reason": f"Source access denied: {exc}",
            "graph_status": "DECLINED",
        }
    
    # 2. Cross-tenant injection check
    if security.detect_cross_tenant_injection(question):
        return {
            "access_status": "DECLINED",
            "validation_status": "DECLINE",
            "decline_reason": "Security policy violation: cross-tenant access or restriction bypass attempt.",
            "graph_status": "DECLINED",
        }
    
    # 3. Fast-path greeting / non-analytical check
    for pat in IntentValidator.GREETING_PATTERNS:
        if pat.match(question.strip()):
            return {
                "access_status": "DECLINED",
                "validation_status": "DECLINE",
                "decline_reason": "Greeting / non-analytical query. Please submit a data analytics question.",
                "graph_status": "DECLINED",
            }
            
    return {
        "access_status": "AUTHORIZED",
        "authorized_source_id": source_id,
        "graph_status": "RUNNING",
    }


# =====================================================================
# 8.2 catalog_projection_node
# =====================================================================
def catalog_projection_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Build the caller-specific approved analytical vocabulary and pin semantic release."""
    services = _get_services(config)
    catalogs = services.get("catalogs")
    actor = _get_actor(config, state)
    source_id = state["source_id"]
    
    pinned_release = state.get("semantic_release_id")
    if pinned_release and catalogs:
        existing_cat = catalogs.get_catalog(source_id, pinned_release)
        pin = SemanticSnapshotPin(pinned_release, existing_cat.epoch, source_id)
    elif catalogs:
        pin = catalogs.pin(actor.tenant_id, source_id)
    else:
        pin = SemanticSnapshotPin("default_release", 1, source_id)
        
    vocab = catalogs.get_accessible_vocabulary(actor, pin) if catalogs else None
    
    return {
        "semantic_release_id": pin.release_id,
        "accessible_vocabulary": vocab,
    }


# =====================================================================
# 8.3 intent_interpretation_node (LLM Call 1)
# =====================================================================
def intent_interpretation_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Gemini-powered intent interpretation converting question to structured schema."""
    if state.get("proposed_llm_json") is not None:
        return {"raw_model_input": state["proposed_llm_json"]}
        
    services = _get_services(config)
    assist = services.get("intent_assist")
    vocab = state.get("accessible_vocabulary")
    question = state.get("question", "")
    
    if assist:
        raw_output = assist.interpret(vocab, question)
    else:
        from backend.intent.gemini_assist import GeminiIntentAssist
        raw_output = GeminiIntentAssist().interpret(vocab, question)
        
    return {"raw_model_input": raw_output}


# =====================================================================
# 8.4 structured_intent_parser_node
# =====================================================================
def structured_intent_parser_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Parse raw model output with bounded retry, isolating provider and parse failures."""
    raw_input = state.get("raw_model_input", "")
    services = _get_services(config)
    parser: IntentParser = services.get("parser", IntentParser())
    
    retry_fn = services.get("retry_fn")
    parse_res = parser.parse_with_bounded_retry(raw_input, retry_fn=retry_fn)
    
    if not parse_res.success:
        if getattr(parse_res, "is_provider_failure", False) or getattr(parse_res, "is_malformed", False):
            return {
                "validation_status": "FAILED",
                "is_provider_failure": True,
                "error_message": f"Provider/Execution failure: {parse_res.error}",
                "graph_status": "FAILED",
            }
        else:
            return {
                "validation_status": "DECLINE",
                "decline_reason": f"Model output parsing failed: {parse_res.error}",
                "graph_status": "DECLINED",
            }
            
    intent = parse_res.intent or StructuredIntent()
    return {
        "structured_intent": intent,
        "validation_status": "PENDING",
    }


# =====================================================================
# 8.5 context_resolution_node
# =====================================================================
def context_resolution_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Resolve supported conversational references and relative time expressions deterministically."""
    intent = state.get("structured_intent")
    if not intent or not isinstance(intent, StructuredIntent):
        return {}
        
    question = state.get("question", "")
    services = _get_services(config)
    catalogs = services.get("catalogs")
    reference_dt = services.get("reference_datetime") or datetime.now(timezone.utc)
    
    source_id = state.get("source_id")
    release_id = state.get("semantic_release_id")
    cat_tz = "UTC"
    fiscal_start = 1
    if catalogs and source_id and release_id:
        c = catalogs.get_catalog(source_id, release_id)
        cat_tz = getattr(c, "timezone", "UTC")
        fiscal_start = getattr(c, "fiscal_year_start_month", 1)
        
    # Relative date 'last month'
    if "last month" in question.lower() and (not intent.current_period or intent.current_period == "last month"):
        range_info = CalendarBoundaryResolver.resolve_last_month(reference_dt, tz_name=cat_tz)
        assumptions = list(intent.assumptions) + [f"Resolved 'last month' to {range_info.description}."]
        intent = replace(intent, current_period=range_info.period_code, assumptions=tuple(assumptions))
        
    # Relative quarter 'last quarter'
    if ("last quarter" in question.lower() or "last fiscal quarter" in question.lower()) and not intent.current_period:
        range_info = CalendarBoundaryResolver.resolve_fiscal_quarter(
            reference_dt, tz_name=cat_tz, fiscal_year_start_month=fiscal_start, relative_offset=-1
        )
        assumptions = list(intent.assumptions) + [f"Resolved relative quarter to {range_info.description}."]
        intent = replace(intent, current_period=range_info.period_code, assumptions=tuple(assumptions))
        
    # Context resolution from previous turns (if manager or store provided)
    conv_history = services.get("conversation_history", [])
    if intent.family == AnalysisFamily.COMPARISON and conv_history:
        if intent.current_period in ("Q3", "Q2", "Q1", "Q4") or (
            "q3" in question.lower() and "q2" in question.lower() and not re.search(r"\b20\d\d\b", question)
        ):
            inherited_year = None
            for pt in reversed(conv_history):
                pt_intent = pt.get("intent")
                if pt_intent and getattr(pt_intent, "current_period", None) and re.match(r"^\d{4}$", pt_intent.current_period):
                    inherited_year = pt_intent.current_period
                    break
            if inherited_year:
                curr_q = intent.current_period or "Q3"
                comp_q = intent.comparison_period or "Q2"
                assumptions = list(intent.assumptions) + [f"Inherited year {inherited_year} from prior conversation context."]
                intent = StructuredIntent(
                    measure=intent.measure or (pt_intent.measure if pt_intent else None),
                    family=intent.family,
                    dimensions=intent.dimensions,
                    filters=intent.filters,
                    current_period=f"{inherited_year}-{curr_q}",
                    comparison_period=f"{inherited_year}-{comp_q}",
                    ambiguity_code=None,
                    assumptions=tuple(assumptions),
                )
                
    return {"structured_intent": intent}


# =====================================================================
# 8.6 governance_validator_node
# =====================================================================
def governance_validator_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Deterministic validation gate checking measures, dimensions, families, and bounds."""
    intent = state.get("structured_intent")
    services = _get_services(config)
    validator: IntentValidator = services.get("validator", IntentValidator())
    catalogs = services.get("catalogs")
    vocab = state.get("accessible_vocabulary")
    actor = _get_actor(config, state)
    
    source_id = state.get("source_id", "")
    release_id = state.get("semantic_release_id") or None
    if catalogs and source_id:
        if release_id:
            catalog = catalogs.get_catalog(source_id, release_id)
            pin = SemanticSnapshotPin(release_id, catalog.epoch, source_id)
        else:
            pin = catalogs.pin(actor.tenant_id, source_id)
            catalog = catalogs.get_catalog(pin.source_id, pin.release_id)
            release_id = pin.release_id
        if vocab is None:
            vocab = catalogs.get_accessible_vocabulary(actor, pin)
    else:
        catalog = None
    
    val_res = validator.validate_intent(
        intent=intent,
        vocabulary=vocab,
        user_question=state.get("question", ""),
        turn_id=state.get("turn_id", ""),
        tenant_id=actor.tenant_id,
        subject_id=actor.subject_id,
        catalog=catalog,
    )
    
    if val_res.status == "CLARIFY":
        req = val_res.clarification_request
        return {
            "validation_status": "CLARIFY",
            "clarification_request": req,
            "graph_status": "RUNNING",
        }
    elif val_res.status == "DECLINE":
        return {
            "validation_status": "DECLINE",
            "decline_reason": val_res.decline_reason,
            "graph_status": "DECLINED",
        }
    else:
        return {
            "validation_status": "VALID",
            "structured_intent": val_res.validated_intent,
            "graph_status": "RUNNING",
        }


# =====================================================================
# 9. Clarification nodes with interrupt()
# =====================================================================
def clarification_preparation_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Prepare serializable clarification payload and execute LangGraph interrupt().
    
    When interrupt() is called, LangGraph pauses graph execution and snapshots thread state.
    The Durable Worker receives the payload, marks turn AWAITING_CLARIFICATION, releases
    its lease, and exits. When resumed via Command(resume=...), interrupt() returns the user choice.
    """
    req: ClarificationRequest = state.get("clarification_request")
    if not req:
        return {"validation_status": "DECLINE", "decline_reason": "Missing clarification details."}
        
    choices = [
        {
            "choice_id": opt.choice_id,
            "label": opt.label,
            "target_type": opt.target_type,
            "resolved_value": opt.resolved_value,
        }
        for opt in req.options
    ]
    
    interrupt_payload: ClarificationInterruptPayload = {
        "type": "ANALYTICAL_CLARIFICATION",
        "turn_id": state.get("turn_id", ""),
        "clarification_version": 1,
        "prompt": req.question_prompt,
        "choices": choices,
        "ambiguity_code": req.ambiguity_code,
    }
    
    # LangGraph interrupt: Pauses execution until resumed
    resume_val = interrupt(interrupt_payload)
    
    return {"clarification_response": resume_val}


def clarification_resume_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Apply server-approved choice to intent and prepare for deterministic revalidation."""
    response = state.get("clarification_response")
    choice_id = response.get("choice_id") if isinstance(response, dict) else str(response)
    
    req: ClarificationRequest = state.get("clarification_request")
    if not req:
        return {"validation_status": "DECLINE", "decline_reason": "No active clarification to resume."}
        
    selected_option = req.resolve(choice_id)
    
    current_intent: StructuredIntent = state.get("structured_intent")
    target_type = getattr(selected_option, "target_type", "")
    resolved_val = getattr(selected_option, "resolved_value", None)
    
    # Deterministic intent patching (0 LLM calls!)
    new_curr_p = current_intent.current_period
    new_comp_p = current_intent.comparison_period
    new_measure = current_intent.measure
    new_dims = list(current_intent.dimensions)
    new_horizon = current_intent.horizon
    new_scenario_model = current_intent.scenario_model
    new_driver = current_intent.adjustable_driver
    
    if target_type == "period":
        new_curr_p = str(resolved_val)
    elif target_type == "period_comparison":
        if isinstance(resolved_val, dict):
            new_curr_p = resolved_val.get("current")
            new_comp_p = resolved_val.get("comparison")
        elif isinstance(resolved_val, (tuple, list)) and len(resolved_val) == 2:
            new_curr_p, new_comp_p = str(resolved_val[0]), str(resolved_val[1])
        else:
            new_comp_p = str(resolved_val)
    elif target_type == "comparison_period":
        if isinstance(resolved_val, (tuple, list)) and len(resolved_val) == 2:
            new_curr_p, new_comp_p = str(resolved_val[0]), str(resolved_val[1])
        else:
            new_comp_p = str(resolved_val)
    elif target_type == "metric":
        new_measure = str(resolved_val)
    elif target_type == "dimension":
        if str(resolved_val) not in new_dims:
            new_dims.append(str(resolved_val))
    elif target_type in ("horizon", "forecast_horizon"):
        new_horizon = int(resolved_val)
    elif target_type == "scenario_model":
        new_scenario_model = str(resolved_val)
    elif target_type == "adjustable_driver":
        new_driver = str(resolved_val)
        
    updated_intent = replace(
        current_intent,
        measure=new_measure,
        dimensions=tuple(new_dims),
        current_period=new_curr_p,
        comparison_period=new_comp_p,
        horizon=new_horizon,
        scenario_model=new_scenario_model,
        adjustable_driver=new_driver,
        ambiguity_code=None,
    )
    
    return {
        "structured_intent": updated_intent,
        "clarification_request": None,
        "validation_status": "PENDING",
    }


# =====================================================================
# 10. semantic_resolution_node
# =====================================================================
def semantic_resolution_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Resolve validated intent to an approved physical semantic plan with join edges."""
    intent: StructuredIntent = state["structured_intent"]
    services = _get_services(config)
    resolver: SemanticResolver = services.get("resolver", SemanticResolver())
    catalogs = services.get("catalogs")
    actor = _get_actor(config, state)
    
    source_id = state["source_id"]
    release_id = state["semantic_release_id"]
    catalog: SemanticCatalog = catalogs.get_catalog(source_id, release_id)
    pin = SemanticSnapshotPin(release_id, catalog.epoch, source_id)
    
    try:
        plan: ResolvedSemanticPlan = resolver.resolve(
            intent=intent,
            catalog=catalog,
            pin=pin,
            turn_id=state.get("turn_id", ""),
            tenant_id=actor.tenant_id,
        )
        
        # Verify measure, dimension, and disclosure access dynamically
        security: SecurityRuntime = services.get("security", SecurityRuntime())
        security.check_measure_access(actor, plan.measure)
        for dim in plan.dimensions:
            security.check_dimension_access(actor, dim)
        for pred in plan.filters:
            security.check_dimension_access(actor, catalog.dimensions[pred.dimension])
            
        if catalog.disclosure_policy:
            from backend.security.disclosure import DatasetDisclosurePolicy
            policy = DatasetDisclosurePolicy(**catalog.disclosure_policy)
            if policy.requires_repeated_query_protection:
                raise SemanticResolutionError("Dataset requires unsupported cross-query protection")
            allowed_roles = [r for r in actor.roles if not policy.allowed_roles or r in policy.allowed_roles]
            if not allowed_roles:
                raise AuthorizationError("No role is allowed by dataset disclosure policy")
            policy.validate_request(
                allowed_roles[0], plan.measure.id,
                [d.id for d in plan.dimensions] + [f.dimension for f in plan.filters],
                plan.family.value
            )
            plan = replace(plan, measure=replace(
                plan.measure,
                protected_entity_column=policy.protected_entity,
                min_entities=policy.min_group_threshold
            ))
            
        if plan.measure.requires_repeated_query_protection:
            raise SemanticResolutionError("Dataset requires cross-query privacy protection; operation disabled.")
            
        return {"semantic_plan": plan}
        
    except (SemanticResolutionError, AuthorizationError) as exc:
        return {
            "validation_status": "DECLINE",
            "decline_reason": str(exc),
            "graph_status": "DECLINED",
        }
    except Exception as exc:
        return {
            "validation_status": "FAILED",
            "error_message": f"Semantic resolution error: {exc}",
            "graph_status": "FAILED",
        }


# =====================================================================
# 11. governed_analytics_node (CRITICAL PRIVACY BOUNDARY)
# =====================================================================
def governed_analytics_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Execute SQL, calculations, privacy suppression, and evidence sealing in ONE node.
    
    Raw query rows and unsuppressed numbers remain local stack variables and NEVER cross
    into checkpointed state. Only privacy-protected display facts and sealed digests are returned.
    """
    plan: ResolvedSemanticPlan = state.get("semantic_plan")
    if not plan:
        return {"validation_status": "FAILED", "error_message": "Missing semantic plan"}
        
    services = _get_services(config)
    compiler: GovernedQueryCompiler = services.get("compiler", GovernedQueryCompiler())
    gateway: GenericSourceGateway = services.get("gateway")
    kernel: DeterministicAnalysisKernel = services.get("kernel", DeterministicAnalysisKernel())
    disclosure: GovernedDisclosureService = services.get("disclosure", GovernedDisclosureService())
    catalogs = services.get("catalogs")
    actor = _get_actor(config, state)
    
    source_id = state["source_id"]
    release_id = state["semantic_release_id"]
    catalog: SemanticCatalog = catalogs.get_catalog(source_id, release_id)
    pin = SemanticSnapshotPin(release_id, catalog.epoch, source_id)
    turn_id = state.get("turn_id", "turn")
    
    try:
        audit = services.get("audit")
        if audit:
            audit.record_event(
                tenant_id=actor.tenant_id,
                subject_id=actor.subject_id,
                turn_id=turn_id,
                event_type="analysis_started",
                payload_digest=hashlib.sha256(turn_id.encode("utf-8")).hexdigest(),
            )

        # Step 1: SQL Compilation & Step 2: Read-Only DB Execution
        if plan.family == AnalysisFamily.COMPARISON and plan.comparison_period:
            pkg_curr = compiler.compile(plan)
            rows_curr = gateway.execute_package(pkg_curr, source_id=source_id)
            curr_facts = kernel.analyze(plan=plan, rows=rows_curr, pin=pin)
            
            comp_plan = replace(plan, current_period=plan.comparison_period, comparison_period=None)
            pkg_comp = compiler.compile(comp_plan)
            rows_comp = gateway.execute_package(pkg_comp, source_id=source_id)
            comp_facts = kernel.analyze(plan=comp_plan, rows=rows_comp, pin=pin)
            
            if plan.dimensions:
                current_by_key = {tuple(sorted(f.dimension_values.items())): f for f in curr_facts if f.dimension_values}
                previous_by_key = {tuple(sorted(f.dimension_values.items())): f for f in comp_facts if f.dimension_values}
                raw_facts = []
                for key in sorted(set(current_by_key) | set(previous_by_key)):
                    cur = current_by_key.get(key)
                    prv = previous_by_key.get(key)
                    if cur is None:
                        cur = replace(prv, value=None, period=plan.current_period, completeness_status="LIMITED_SCOPE")
                    if prv is None:
                        prv = replace(cur, value=None, period=plan.comparison_period, completeness_status="LIMITED_SCOPE")
                    raw_facts.extend(replace(f, dimension_values=dict(key)) for f in kernel.compute_comparison(cur, prv, pin))
            else:
                raw_facts = kernel.compute_comparison(curr_facts[0], comp_facts[0], pin)
            combined_rows = rows_curr + rows_comp
            
        elif plan.family == AnalysisFamily.CONTRIBUTION and plan.comparison_period:
            pkg_curr = compiler.compile(plan)
            rows_curr = gateway.execute_package(pkg_curr, source_id=source_id)
            curr_facts = kernel.analyze(plan=plan, rows=rows_curr, pin=pin)
            
            comp_plan = replace(plan, current_period=plan.comparison_period, comparison_period=None)
            pkg_comp = compiler.compile(comp_plan)
            rows_comp = gateway.execute_package(pkg_comp, source_id=source_id)
            comp_facts = kernel.analyze(plan=comp_plan, rows=rows_comp, pin=pin)
            
            raw_facts = kernel.compute_growth_contribution(curr_facts, comp_facts, pin)
            combined_rows = rows_curr + rows_comp
            
        else:
            pkg = compiler.compile(plan)
            combined_rows = gateway.execute_package(pkg, source_id=source_id)
            raw_facts = kernel.analyze(plan=plan, rows=combined_rows, pin=pin)
            compiled_sql = pkg.sql
            
        services["_last_compiled_sql"] = locals().get("compiled_sql") or getattr(locals().get("pkg"), "sql", None)
        raw_facts = [replace(f, fact_id=f"{turn_id}_fact_{idx}") for idx, f in enumerate(raw_facts)]
        
        # Step 4: Privacy & Disclosure Protection
        if not plan.measure.protected_entity_column:
            if getattr(plan.measure, "sensitivity", None) in ("CONFIDENTIAL", "RESTRICTED"):
                counts = {f.fact_id: 0 for f in raw_facts}
            else:
                counts = {}
        else:
            c_list = [int(r.get("protected_entity_count", 0)) for r in combined_rows]
            minimum = min(c_list, default=0)
            counts = {f.fact_id: minimum for f in raw_facts}
                
        protected_facts = disclosure.inspect_and_protect(
            facts=raw_facts,
            tenant_id=actor.tenant_id,
            distinct_entity_counts=counts,
            min_entities=plan.measure.min_entities,
        )
        
        # Step 5: Evidence Sealing
        pkg_id = f"pkg_{uuid.uuid4().hex[:12]}"
        sealed_evidence = SealedEvidencePackage.seal(
            package_id=pkg_id,
            turn_id=turn_id,
            tenant_id=actor.tenant_id,
            semantic_release_id=pin.release_id,
            facts=protected_facts,
        )
        
        # Memory buffer disposal
        gateway.close_without_values()
        
        # Cache sealed evidence in services for publication
        services["_last_sealed_evidence"] = sealed_evidence
        
        # Step 6: Safe Display Facts (Clean projection with zero raw values)
        safe_display_facts = [
            {
                "fact_id": f.fact_id,
                "measure": f.metric,
                "metric": f.metric,
                "value": str(f.value) if f.value is not None else None,
                "display_value": f"{f.value} {f.unit}".strip() if f.value is not None and f.unit else str(f.value),
                "unit": f.unit,
                "period": f.period,
                "dimensions": f.dimension_values or {},
                "calculation_type": f.calculation_type,
                "result_type": f.result_type,
                "is_suppressed": f.is_suppressed,
                "suppression_reason": f.suppression_reason,
            }
            for f in protected_facts
        ]
        
        return {
            "analysis_status": "PROTECTED_AND_SEALED",
            "protected_evidence_reference": sealed_evidence.package_id,
            "evidence_digest": sealed_evidence.package_digest,
            "safe_display_facts": safe_display_facts,
            "compiled_sql": services.get("_last_compiled_sql"),
        }
        
    except Exception as exc:
        gateway.close_without_values()
        logger.exception("Governed analytics execution failed")
        return {
            "validation_status": "FAILED",
            "error_message": str(exc),
            "graph_status": "FAILED",
        }


# =====================================================================
# 13. deterministic_presentation_node
# =====================================================================
def deterministic_presentation_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Build deterministic UI result: KPI cards, table views, chart JSON, suppression markers."""
    services = _get_services(config)
    sealed_evidence: SealedEvidencePackage = services.get("_last_sealed_evidence")
    fallback: DeterministicPresentationFallback = services.get("presentation", DeterministicPresentationFallback())
    
    if not sealed_evidence:
        return {"deterministic_presentation": {}}
        
    compiled_sql = services.get("_last_compiled_sql")
    rendered_view = fallback.render(sealed_evidence, sql_query=compiled_sql)
    charts = ChartGenerator().generate_charts(list(sealed_evidence.facts))
    services["_last_rendered_view"] = rendered_view
    
    presentation = {
        "summary_text": rendered_view.summary_text,
        "kpi_cards": [
            {
                "title": k.title,
                "value": k.value,
                "unit": k.unit,
                "period": k.period,
                "delta": k.delta,
                "growth_percent": k.growth_percent,
            }
            for k in rendered_view.kpi_cards
        ],
        "table_columns": rendered_view.table_columns,
        "table_rows": rendered_view.table_rows,
        "assumptions": rendered_view.assumptions,
        "limitations": rendered_view.limitations,
        "is_suppressed": rendered_view.is_suppressed,
        "evidence_digest": rendered_view.evidence_digest,
        "sql_query": rendered_view.sql_query,
    }
    
    return {
        "deterministic_presentation": presentation,
        "chart_specifications": charts,
    }


# =====================================================================
# 14. optional_executive_summary_node (LLM Call 2)
# =====================================================================
def optional_executive_summary_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Generate an optional narrative template with fact placeholders."""
    facts = state.get("safe_display_facts", [])
    if not facts:
        return {"summary_template": None}
        
    unsuppressed = [f for f in facts if not f.get("is_suppressed")]
    if not unsuppressed:
        return {"summary_template": None}
        
    # Build a deterministic placeholder template structure
    # In production, Gemini is called here with strict schema to propose bindings.
    bindings = {}
    template_parts = []
    
    primary_fact = unsuppressed[0]
    p_id = primary_fact["fact_id"]
    bindings["primary_metric"] = p_id
    
    period = primary_fact.get("period")
    if period:
        bindings["period"] = f"period_{period}"
        template_parts.append("For {{period}}, {{primary_metric}} was observed.")
    else:
        template_parts.append("{{primary_metric}} was observed.")
        
    if len(unsuppressed) >= 2 and unsuppressed[0].get("dimensions") and unsuppressed[1].get("dimensions"):
        bindings["fact_a"] = unsuppressed[0]["fact_id"]
        bindings["fact_b"] = unsuppressed[1]["fact_id"]
        template_parts.append("Specifically, {{fact_a}} compared with {{fact_b}}.")
        
    template_str = " ".join(template_parts)
    
    summary_template = {
        "summary_template": template_str,
        "bindings": bindings,
        "follow_up_questions": [
            {"action_id": "breakdown_by_segment"},
            {"action_id": "compare_previous_period"},
        ],
    }
    
    return {"summary_template": summary_template}


# =====================================================================
# 16. deterministic_claim_guardrail_node
# =====================================================================
def deterministic_claim_guardrail_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Verify placeholder bindings and enforce prose coverage against safe display facts."""
    template = state.get("summary_template")
    if not template:
        return {"verified_summary": None}
        
    safe_facts = state.get("safe_display_facts", [])
    periods = [str(f.get("period")) for f in safe_facts if f.get("period")]
    
    is_valid, rendered_text, rejection_reason = DeterministicClaimGuardrail.verify_and_render(
        summary_template=template,
        safe_display_facts=safe_facts,
        periods=periods,
    )
    
    if not is_valid:
        logger.warning("Executive summary discarded by claim guardrail: %s", rejection_reason)
        return {"verified_summary": None}
        
    return {"verified_summary": rendered_text}


# =====================================================================
# 17. publication_candidate_node
# =====================================================================
def publication_candidate_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Package the publication candidate for the Durable Worker publication gate.
    
    NOTE: LangGraph DOES NOT publish to the application database.
    It returns a candidate; the Durable Worker verifies ownership and fencing.
    """
    candidate: PublicationCandidate = {
        "status": "READY_FOR_PUBLICATION",
        "conversation_id": state.get("conversation_id", ""),
        "turn_id": state.get("turn_id", ""),
        "job_id": state.get("job_id", ""),
        "thread_id": state.get("thread_id", ""),
        "semantic_release_id": state.get("semantic_release_id", ""),
        "protected_evidence_reference": state.get("protected_evidence_reference", ""),
        "evidence_digest": state.get("evidence_digest", ""),
        "deterministic_presentation": state.get("deterministic_presentation", {}),
        "chart_specifications": state.get("chart_specifications", []),
        "verified_optional_summary": {
            "summary": state.get("verified_summary"),
        } if state.get("verified_summary") else None,
        "graph_completed_at": datetime.now(timezone.utc).isoformat(),
    }
    
    return {
        "graph_status": "READY_FOR_PUBLICATION",
        "publication_candidate": candidate,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


# =====================================================================
# Terminal Nodes
# =====================================================================
def failure_result_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Terminal node for provider or runtime failures."""
    return {"graph_status": "FAILED"}


def decline_result_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Terminal node for governance declines."""
    return {"graph_status": "DECLINED"}
