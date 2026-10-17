"""Conversation lifecycle manager coordinating end-to-end governed analysis execution."""
from __future__ import annotations
from backend.observability import traced_stage
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import re
import uuid
from typing import Any, Callable, Optional

from backend.catalog.pinning import SemanticSnapshotPin
from backend.catalog.store import SemanticCatalogStore
from backend.compiler.compiler import GovernedQueryCompiler
from backend.domain.facts import SealedEvidencePackage
from backend.domain.models import ApiActor, Turn, TurnStatus
from backend.execution.calendar import CalendarBoundaryResolver
from backend.execution.gateway import GenericSourceGateway
from backend.execution.kernel import DeterministicAnalysisKernel
from backend.intent.parser import IntentParser
from backend.intent.schema import AnalysisFamily, StructuredIntent
from backend.intent.validator import IntentValidator
from backend.lifecycle.audit import TerminalAuditRecorder
from backend.presentation.fallback import (
    DeterministicPresentationFallback,
    RenderedAnswerView,
)
from backend.security.auth import SecurityRuntime, AuthorizationError
from backend.security.disclosure import GovernedDisclosureService
from backend.semantic.resolver import SemanticResolver, SemanticResolutionError


class LifecycleExecutionError(Exception):
    """Raised on critical execution pipeline failure."""
    pass


class ConversationLifecycleManager:
    """Orchestrates turn lifecycle, validation gates, clarification pauses, and governed execution."""

    def __init__(
        self,
        security_runtime: SecurityRuntime,
        catalog_store: SemanticCatalogStore,
        gateway: GenericSourceGateway,
        intent_parser: Optional[IntentParser] = None,
        intent_validator: Optional[IntentValidator] = None,
        semantic_resolver: Optional[SemanticResolver] = None,
        query_compiler: Optional[GovernedQueryCompiler] = None,
        analysis_kernel: Optional[DeterministicAnalysisKernel] = None,
        disclosure_service: Optional[GovernedDisclosureService] = None,
        presentation_fallback: Optional[DeterministicPresentationFallback] = None,
        audit_recorder: Optional[TerminalAuditRecorder] = None,
        reference_datetime: Optional[datetime] = None,
        intent_assist: Any = None,
    ):
        self.security = security_runtime
        self.catalogs = catalog_store
        self.gateway = gateway
        self.parser = intent_parser or IntentParser()
        self.validator = intent_validator or IntentValidator()
        self.resolver = semantic_resolver or SemanticResolver()
        self.compiler = query_compiler or GovernedQueryCompiler()
        self.kernel = analysis_kernel or DeterministicAnalysisKernel()
        self.disclosure = disclosure_service or GovernedDisclosureService()
        self.presentation = presentation_fallback or DeterministicPresentationFallback()
        self.audit = audit_recorder or TerminalAuditRecorder()
        self.reference_datetime = reference_datetime
        self.intent_assist = intent_assist

        # In-memory turn, answer, and evidence stores
        self._turns: dict[str, Turn] = {}
        self._answers: dict[str, RenderedAnswerView] = {}
        self._pending_intents: dict[str, StructuredIntent] = {}
        self._evidence_packages: dict[str, SealedEvidencePackage] = {}
        from backend.config import use_langgraph_workflow
        self.use_graph = use_langgraph_workflow()
        self.graph = None

    def get_turn(self, turn_id: str) -> Optional[Turn]:
        return self._turns.get(turn_id)

    def get_evidence(self, turn_id: str) -> Optional[SealedEvidencePackage]:
        return self._evidence_packages.get(turn_id)

    def get_answer(self, actor: ApiActor, turn_id: str) -> Optional[RenderedAnswerView]:
        """Authorized answer retrieval with real-time permission re-verification."""
        turn = self.get_turn(turn_id)
        if not turn:
            return None

        # 1. Re-verify tenant and turn ownership
        self.security.check_turn_ownership(actor, turn)

        # 2. Re-verify source access currently
        if turn.source_id:
            self.security.check_source_access(actor, turn.source_id)

        # 3. Re-verify measure and dimension permissions currently
        if turn.source_id and turn.pinned_release_id:
            pending = self._pending_intents.get(turn_id)
            if pending:
                cat = self.catalogs.get_catalog(turn.source_id, turn.pinned_release_id)
                if pending.measure:
                    m_def = cat.measures.get(pending.measure)
                    if m_def:
                        self.security.check_measure_access(actor, m_def)
                for dim_id in pending.dimensions:
                    d_def = cat.dimensions.get(dim_id)
                    if d_def:
                        self.security.check_dimension_access(actor, d_def)

        return self._answers.get(turn_id)

    def _get_graph_services(self) -> dict[str, Any]:
        return {
            "security": self.security,
            "catalogs": self.catalogs,
            "gateway": self.gateway,
            "parser": self.parser,
            "validator": self.validator,
            "resolver": self.resolver,
            "compiler": self.compiler,
            "kernel": self.kernel,
            "disclosure": self.disclosure,
            "presentation": self.presentation,
            "audit": self.audit,
            "reference_datetime": self.reference_datetime,
            "intent_assist": self.intent_assist,
            "conversation_history": [
                {"turn": t, "intent": self._pending_intents.get(t.turn_id)}
                for t in self._turns.values()
                if t.status == TurnStatus.ANSWERED
            ],
        }

    def _submit_turn_via_graph(
        self,
        actor: ApiActor,
        conversation_id: str,
        source_id: str,
        question: str,
        proposed_llm_json: Optional[str] = None,
        retry_fn: Optional[Callable[[str, int], str]] = None,
        turn_id: Optional[str] = None,
    ) -> tuple[Turn, Optional[RenderedAnswerView]]:
        self.security.check_source_access(actor, source_id)
        conv_turns = [t for t in self._turns.values() if t.conversation_id == conversation_id]
        if conv_turns:
            first_turn = conv_turns[0]
            self.security.check_tenant_access(actor, first_turn.tenant_id)
            if not actor.is_admin and first_turn.subject_id != actor.subject_id:
                raise AuthorizationError(
                    f"Conversation access denied: Actor '{actor.subject_id}' does not own conversation '{conversation_id}'."
                )

        previous = self._turns.get(turn_id) if turn_id else None
        turn = Turn.create(
            conversation_id=conversation_id,
            actor=actor,
            question=question,
            source_id=source_id,
            reference_datetime=previous.reference_timestamp if previous else self.reference_datetime,
            turn_id=turn_id,
        )
        self._turns[turn.turn_id] = turn
        self.audit.record_event(
            tenant_id=actor.tenant_id,
            subject_id=actor.subject_id,
            turn_id=turn.turn_id,
            event_type="question_submitted",
            payload_digest=turn.question_digest,
        )

        thread_id = f"thread_{turn.turn_id}"
        services = self._get_graph_services()
        if retry_fn:
            services["retry_fn"] = retry_fn

        initial_state = {
            "conversation_id": conversation_id,
            "turn_id": turn.turn_id,
            "tenant_id": actor.tenant_id,
            "subject_id": actor.subject_id,
            "source_id": source_id,
            "question": question,
            "proposed_llm_json": proposed_llm_json,
            "thread_id": thread_id,
            "semantic_release_id": previous.pinned_release_id if previous else None,
            "graph_status": "RUNNING",
        }
        config = {
            "configurable": {
                "thread_id": thread_id,
                "services": services,
                "actor": actor,
            }
        }

        self.graph.invoke(initial_state, config)
        graph_state = self.graph.get_state(config)

        if graph_state.next and "clarification_preparation_node" in graph_state.next:
            turn.transition_to(TurnStatus.AWAITING_CLARIFICATION)
            turn.pinned_release_id = graph_state.values.get("semantic_release_id")
            req = graph_state.values.get("clarification_request")
            turn.clarification_request = req
            self._pending_intents[turn.turn_id] = graph_state.values.get("structured_intent")
            if req:
                self.audit.record_event(
                    tenant_id=actor.tenant_id,
                    subject_id=actor.subject_id,
                    turn_id=turn.turn_id,
                    event_type="clarification_requested",
                    payload_digest=hashlib.sha256(req.ambiguity_code.encode("utf-8")).hexdigest(),
                )
            return turn, None

        vals = graph_state.values
        status = vals.get("graph_status")
        if status == "DECLINED" or vals.get("validation_status") == "DECLINE":
            reason = vals.get("decline_reason") or "declined"
            turn.transition_to(TurnStatus.DECLINED, reason=reason)
            self._record_terminal_closure(turn, "DECLINED", reason)
            return turn, None
        elif status == "FAILED" or vals.get("validation_status") == "FAILED":
            error = vals.get("error_message") or "failed"
            turn.transition_to(TurnStatus.FAILED, reason=error)
            self._record_terminal_closure(turn, "FAILED", error)
            return turn, None
        elif status == "READY_FOR_PUBLICATION":
            turn.pinned_release_id = vals.get("semantic_release_id")
            rendered_view = services.get("_last_rendered_view")
            sealed_evidence = services.get("_last_sealed_evidence")
            self._answers[turn.turn_id] = rendered_view
            self._evidence_packages[turn.turn_id] = sealed_evidence
            self._pending_intents[turn.turn_id] = vals.get("structured_intent")

            if sealed_evidence:
                self.audit.record_event(
                    tenant_id=actor.tenant_id,
                    subject_id=actor.subject_id,
                    turn_id=turn.turn_id,
                    event_type="evidence_sealed",
                    payload_digest=sealed_evidence.package_digest,
                )
            self.audit.record_event(
                tenant_id=actor.tenant_id,
                subject_id=actor.subject_id,
                turn_id=turn.turn_id,
                event_type="transient_memory_erased",
                payload_digest="0" * 64,
            )
            turn.transition_to(TurnStatus.COMPOSING)
            turn.transition_to(TurnStatus.ANSWERED)
            self._record_terminal_closure(turn, "ANSWERED", "published_ok")
            return turn, rendered_view
        else:
            turn.transition_to(TurnStatus.FAILED, reason="Unexpected workflow terminal state")
            return turn, None

    def _submit_clarification_via_graph(
        self,
        actor: ApiActor,
        turn_id: str,
        choice_id: str,
    ) -> tuple[Turn, Optional[RenderedAnswerView]]:
        turn = self.get_turn(turn_id)
        if not turn:
            raise LifecycleExecutionError(f"Turn '{turn_id}' not found.")
        self.security.check_turn_ownership(actor, turn)
        if turn.status != TurnStatus.AWAITING_CLARIFICATION or not turn.clarification_request:
            raise LifecycleExecutionError(
                f"Turn '{turn_id}' is not in AWAITING_CLARIFICATION state (status: {turn.status})."
            )
        self.security.check_source_access(actor, turn.source_id)

        self.audit.record_event(
            tenant_id=actor.tenant_id,
            subject_id=actor.subject_id,
            turn_id=turn.turn_id,
            event_type="clarification_responded",
            payload_digest=hashlib.sha256(choice_id.encode("utf-8")).hexdigest(),
        )

        from langgraph.types import Command
        thread_id = f"thread_{turn.turn_id}"
        services = self._get_graph_services()
        config = {
            "configurable": {
                "thread_id": thread_id,
                "services": services,
                "actor": actor,
            }
        }
        curr_state = self.graph.get_state(config)
        if not curr_state.values:
            saved_intent = self._pending_intents.get(turn.turn_id)
            restored_state = {
                "conversation_id": turn.conversation_id,
                "turn_id": turn.turn_id,
                "tenant_id": actor.tenant_id,
                "subject_id": actor.subject_id,
                "source_id": turn.source_id,
                "question": turn.question_text,
                "thread_id": thread_id,
                "semantic_release_id": turn.pinned_release_id,
                "structured_intent": saved_intent,
                "clarification_request": turn.clarification_request,
                "clarification_response": {"choice_id": choice_id},
                "graph_status": "RUNNING",
            }
            self.graph.update_state(config, restored_state, as_node="clarification_preparation_node")
            self.graph.invoke(None, config)
        else:
            self.graph.invoke(Command(resume={"choice_id": choice_id}), config)
        graph_state = self.graph.get_state(config)

        if graph_state.next and "clarification_preparation_node" in graph_state.next:
            turn.transition_to(TurnStatus.AWAITING_CLARIFICATION)
            req = graph_state.values.get("clarification_request")
            turn.clarification_request = req
            self._pending_intents[turn.turn_id] = graph_state.values.get("structured_intent")
            return turn, None

        vals = graph_state.values
        status = vals.get("graph_status")
        if status == "DECLINED" or vals.get("validation_status") == "DECLINE":
            reason = vals.get("decline_reason") or "declined"
            turn.transition_to(TurnStatus.DECLINED, reason=reason)
            self._record_terminal_closure(turn, "DECLINED", reason)
            return turn, None
        elif status == "FAILED" or vals.get("validation_status") == "FAILED":
            error = vals.get("error_message") or "failed"
            turn.transition_to(TurnStatus.FAILED, reason=error)
            self._record_terminal_closure(turn, "FAILED", error)
            return turn, None
        elif status == "READY_FOR_PUBLICATION":
            turn.pinned_release_id = vals.get("semantic_release_id")
            rendered_view = services.get("_last_rendered_view")
            sealed_evidence = services.get("_last_sealed_evidence")
            self._answers[turn.turn_id] = rendered_view
            self._evidence_packages[turn.turn_id] = sealed_evidence
            self._pending_intents[turn.turn_id] = vals.get("structured_intent")
            if sealed_evidence:
                self.audit.record_event(
                    tenant_id=actor.tenant_id,
                    subject_id=actor.subject_id,
                    turn_id=turn.turn_id,
                    event_type="evidence_sealed",
                    payload_digest=sealed_evidence.package_digest,
                )
            self.audit.record_event(
                tenant_id=actor.tenant_id,
                subject_id=actor.subject_id,
                turn_id=turn.turn_id,
                event_type="transient_memory_erased",
                payload_digest="0" * 64,
            )
            turn.transition_to(TurnStatus.COMPOSING)
            turn.transition_to(TurnStatus.ANSWERED)
            self._record_terminal_closure(turn, "ANSWERED", "published_ok")
            return turn, rendered_view
        else:
            turn.transition_to(TurnStatus.FAILED, reason="Unexpected workflow terminal state")
            return turn, None

    @traced_stage('turn')
    def submit_turn(
        self,
        actor: ApiActor,
        conversation_id: str,
        source_id: str,
        question: str,
        proposed_llm_json: Optional[str] = None,
        retry_fn: Optional[Callable[[str, int], str]] = None,
        turn_id: Optional[str] = None,
    ) -> tuple[Turn, Optional[RenderedAnswerView]]:
        """Ingest question and advance turn through governed lifecycle."""
        if getattr(self, "use_graph", False):
            if not getattr(self, "graph", None):
                from backend.graph.workflow import create_talk2data_graph
                self.graph = create_talk2data_graph()
            return self._submit_turn_via_graph(
                actor=actor,
                conversation_id=conversation_id,
                source_id=source_id,
                question=question,
                proposed_llm_json=proposed_llm_json,
                retry_fn=retry_fn,
                turn_id=turn_id,
            )
        # 1. Authorize source access
        self.security.check_source_access(actor, source_id)

        # 2. Authorize conversation ownership if conversation already exists
        conv_turns = [t for t in self._turns.values() if t.conversation_id == conversation_id]
        if conv_turns:
            first_turn = conv_turns[0]
            self.security.check_tenant_access(actor, first_turn.tenant_id)
            if not actor.is_admin and first_turn.subject_id != actor.subject_id:
                from backend.security.auth import AuthorizationError
                raise AuthorizationError(
                    f"Conversation access denied: Actor '{actor.subject_id}' does not own conversation '{conversation_id}'."
                )

        previous = self._turns.get(turn_id) if turn_id else None

        # 3. Allocate Turn entity and record question submitted with captured reference timestamp
        turn = Turn.create(
            conversation_id=conversation_id,
            actor=actor,
            question=question,
            source_id=source_id,
            reference_datetime=previous.reference_timestamp if previous else self.reference_datetime,
            turn_id=turn_id,
        )
        self._turns[turn.turn_id] = turn

        self.audit.record_event(
            tenant_id=actor.tenant_id,
            subject_id=actor.subject_id,
            turn_id=turn.turn_id,
            event_type="question_submitted",
            payload_digest=turn.question_digest,
        )

        # 4. Security Boundary & Injection Check
        if self.security.detect_cross_tenant_injection(question):
            turn.transition_to(
                TurnStatus.DECLINED,
                reason="Security policy violation: cross-tenant access or restriction bypass attempt.",
            )
            self._record_terminal_closure(turn, "DECLINED", "security_policy_violation")
            return turn, None

        # 4. Fast-path check: Greeting / non-analytical input
        for pat in IntentValidator.GREETING_PATTERNS:
            if pat.match(question.strip()):
                turn.transition_to(
                    TurnStatus.DECLINED,
                    reason="Greeting / non-analytical query. Please submit a data analytics question.",
                )
                self._record_terminal_closure(turn, "DECLINED", "non_analytics_input")
                return turn, None

        # 5. Pin active semantic release
        if previous and previous.pinned_release_id:
            existing_catalog = self.catalogs.get_catalog(source_id, previous.pinned_release_id)
            pin = SemanticSnapshotPin(previous.pinned_release_id, existing_catalog.epoch, source_id)
        else:
            pin = self.catalogs.pin(actor.tenant_id, source_id)
        turn.pinned_release_id = pin.release_id
        turn.transition_to(TurnStatus.INTERPRETING)

        # 6. Extract accessible vocabulary for this actor & release
        vocab = self.catalogs.get_accessible_vocabulary(actor, pin)
        cat = self.catalogs.get_catalog(pin.source_id, pin.release_id)

        # 7. Parse model intent (with bounded retry)
        if proposed_llm_json is not None:
            raw_model_input = proposed_llm_json
        else:
            from backend.intent.gemini_assist import GeminiIntentAssist
            assist = self.intent_assist or GeminiIntentAssist()
            raw_model_input = assist.interpret(vocab, question)
        parse_res = self.parser.parse_with_bounded_retry(raw_model_input, retry_fn=retry_fn)
        if not parse_res.success:
            if getattr(parse_res, "is_provider_failure", False) or getattr(parse_res, "is_malformed", False):
                turn.transition_to(
                    TurnStatus.FAILED,
                    reason=f"Provider/Execution failure: {parse_res.error}",
                )
                self._record_terminal_closure(turn, "FAILED", "provider_execution_failure")
                return turn, None
            else:
                turn.transition_to(
                    TurnStatus.DECLINED,
                    reason=f"Model output parsing failed: {parse_res.error}",
                )
                self._record_terminal_closure(turn, "DECLINED", "model_parse_failure")
                return turn, None


        intent = parse_res.intent or StructuredIntent()

        # 8. Conversational Context & Relative Date Resolution
        intent = self._resolve_conversational_and_temporal_context(
            intent=intent,
            question=question,
            conversation_id=conversation_id,
            current_turn_id=turn.turn_id,
        )
        self._pending_intents[turn.turn_id] = intent

        # 9. Deterministic Intent Validation Gate
        val_res = self.validator.validate_intent(
            intent=intent,
            vocabulary=vocab,
            user_question=question,
            turn_id=turn.turn_id,
            tenant_id=actor.tenant_id,
            subject_id=actor.subject_id,
            catalog=cat,
        )

        # Clarification handling: STOP AND WAIT. Do NOT continue to semantic resolution!
        if val_res.status == "CLARIFY":
            turn.transition_to(TurnStatus.AWAITING_CLARIFICATION)
            turn.clarification_request = val_res.clarification_request
            self.audit.record_event(
                tenant_id=actor.tenant_id,
                subject_id=actor.subject_id,
                turn_id=turn.turn_id,
                event_type="clarification_requested",
                payload_digest=hashlib.sha256(
                    val_res.clarification_request.ambiguity_code.encode("utf-8")  # type: ignore
                ).hexdigest(),
            )
            return turn, None

        # Decline handling: STOP AND TERMINATE. Do NOT continue to semantic resolution!
        if val_res.status == "DECLINE":
            turn.transition_to(TurnStatus.DECLINED, reason=val_res.decline_reason)
            self._record_terminal_closure(turn, "DECLINED", val_res.decline_reason or "declined")
            return turn, None

        # 10. Execute Validated Analytical Plan
        validated_intent = val_res.validated_intent
        assert validated_intent is not None

        return self._execute_validated_turn(turn, actor, pin, validated_intent)

    @traced_stage('clarification')
    def submit_clarification_choice(
        self,
        actor: ApiActor,
        turn_id: str,
        choice_id: str,
    ) -> tuple[Turn, Optional[RenderedAnswerView]]:
        """Resume turn validation and execution after user selects a structured clarification choice."""
        if getattr(self, "use_graph", False):
            if not getattr(self, "graph", None):
                from backend.graph.workflow import create_talk2data_graph
                self.graph = create_talk2data_graph()
            return self._submit_clarification_via_graph(
                actor=actor,
                turn_id=turn_id,
                choice_id=choice_id,
            )
        turn = self.get_turn(turn_id)
        if not turn:
            raise LifecycleExecutionError(f"Turn '{turn_id}' not found.")

        self.security.check_turn_ownership(actor, turn)

        if turn.status != TurnStatus.AWAITING_CLARIFICATION or not turn.clarification_request:
            raise LifecycleExecutionError(
                f"Turn '{turn_id}' is not in AWAITING_CLARIFICATION state (status: {turn.status})."
            )

        self.security.check_source_access(actor, turn.source_id)

        # Resolve choice
        selected_option = turn.clarification_request.resolve(choice_id)

        self.audit.record_event(
            tenant_id=actor.tenant_id,
            subject_id=actor.subject_id,
            turn_id=turn.turn_id,
            event_type="clarification_responded",
            payload_digest=hashlib.sha256(choice_id.encode("utf-8")).hexdigest(),
        )

        # Transition back to INTERPRETING to RESUME VALIDATION
        turn.transition_to(TurnStatus.INTERPRETING)

        # Retrieve pinned release and accessible vocabulary
        pin = SemanticSnapshotPin(release_id=turn.pinned_release_id, epoch=self.catalogs.get_catalog(turn.source_id, turn.pinned_release_id).epoch, source_id=turn.source_id)
        vocab = self.catalogs.get_accessible_vocabulary(actor, pin)
        cat = self.catalogs.get_catalog(pin.source_id, pin.release_id)

        # Incorporate user choice into pending intent
        current_intent = self._pending_intents.get(turn_id, StructuredIntent())
        updated_intent = self._apply_choice_to_intent(current_intent, selected_option)
        self._pending_intents[turn_id] = updated_intent

        # Re-run deterministic validation gate
        val_res = self.validator.validate_intent(
            intent=updated_intent,
            vocabulary=vocab,
            user_question=turn.question_text,
            turn_id=turn.turn_id,
            tenant_id=actor.tenant_id,
            subject_id=actor.subject_id,
            catalog=cat,
        )

        if val_res.status == "CLARIFY":
            turn.transition_to(TurnStatus.AWAITING_CLARIFICATION)
            turn.clarification_request = val_res.clarification_request
            return turn, None

        if val_res.status == "DECLINE":
            turn.transition_to(TurnStatus.DECLINED, reason=val_res.decline_reason)
            self._record_terminal_closure(turn, "DECLINED", val_res.decline_reason or "declined")
            return turn, None

        validated_intent = val_res.validated_intent
        assert validated_intent is not None

        return self._execute_validated_turn(turn, actor, pin, validated_intent)

    def _resolve_conversational_and_temporal_context(
        self,
        intent: StructuredIntent,
        question: str,
        conversation_id: str,
        current_turn_id: str,
    ) -> StructuredIntent:
        """Resolve temporal context from previous turns in conversation or relative date expressions."""
        current_turn = self.get_turn(current_turn_id)
        anchor_dt = current_turn.reference_timestamp if current_turn else (self.reference_datetime or datetime.now(timezone.utc))

        # Retrieve catalog timezone & fiscal calendar settings
        cat_tz = "UTC"
        fiscal_start = 1
        if current_turn and current_turn.pinned_release_id and current_turn.source_id:
            c = self.catalogs.get_catalog(current_turn.source_id, current_turn.pinned_release_id)
            cat_tz = getattr(c, "timezone", "UTC")
            fiscal_start = getattr(c, "fiscal_year_start_month", 1)

        # 1. Check relative date 'last month'
        if "last month" in question.lower() and (not intent.current_period or intent.current_period == "last month"):
            range_info = CalendarBoundaryResolver.resolve_last_month(anchor_dt, tz_name=cat_tz)
            assumptions = list(intent.assumptions) + [f"Resolved 'last month' to {range_info.description}."]
            return replace(intent, current_period=range_info.period_code, assumptions=tuple(assumptions))

        # 2. Check relative fiscal quarter: 'last quarter' or 'last fiscal quarter'
        if ("last quarter" in question.lower() or "last fiscal quarter" in question.lower()) and not intent.current_period:
            range_info = CalendarBoundaryResolver.resolve_fiscal_quarter(
                anchor_dt, tz_name=cat_tz, fiscal_year_start_month=fiscal_start, relative_offset=-1
            )
            assumptions = list(intent.assumptions) + [f"Resolved relative quarter to {range_info.description}."]
            return replace(intent, current_period=range_info.period_code, assumptions=tuple(assumptions))

        # 3. Conversation context: 'Compare Q3 with Q2' without year
        prev_turns = [
            t for t in self._turns.values()
            if t.conversation_id == conversation_id and t.turn_id != current_turn_id and t.status == TurnStatus.ANSWERED
        ]

        if intent.family == AnalysisFamily.COMPARISON:
            # Check if periods are quarters without year
            if intent.current_period in ("Q3", "Q2", "Q1", "Q4") or (
                "q3" in question.lower() and "q2" in question.lower() and not re.search(r"\b20\d\d\b", question)
            ):
                inherited_year = None
                for pt in reversed(prev_turns):
                    pt_intent = self._pending_intents.get(pt.turn_id)
                    if pt_intent and pt_intent.current_period and re.match(r"^\d{4}$", pt_intent.current_period):
                        inherited_year = pt_intent.current_period
                        break

                if inherited_year:
                    # Inherit year from previous turn
                    curr_q = intent.current_period or "Q3"
                    comp_q = intent.comparison_period or "Q2"
                    assumptions = list(intent.assumptions) + [f"Inherited year {inherited_year} from prior conversation context."]
                    return StructuredIntent(
                        measure=intent.measure or (pt_intent.measure if pt_intent else None),
                        family=intent.family,
                        dimensions=intent.dimensions,
                        filters=intent.filters,
                        current_period=f"{inherited_year}-{curr_q}",
                        comparison_period=f"{inherited_year}-{comp_q}",
                        ambiguity_code=None,
                        assumptions=tuple(assumptions),
                    )

        return intent

    @traced_stage('governed_execution')
    def _execute_validated_turn(
        self,
        turn: Turn,
        actor: ApiActor,
        pin: SemanticSnapshotPin,
        validated_intent: StructuredIntent,
    ) -> tuple[Turn, Optional[RenderedAnswerView]]:
        """Execute the validated semantic plan, run queries, calculate facts, and render answer."""
        try:
            turn.transition_to(TurnStatus.ANALYZING)
            self.audit.record_event(
                tenant_id=actor.tenant_id,
                subject_id=actor.subject_id,
                turn_id=turn.turn_id,
                event_type="analysis_started",
                payload_digest=hashlib.sha256(turn.turn_id.encode("utf-8")).hexdigest(),
            )

            # Re-verify source access dynamically at execution time
            self.security.check_source_access(actor, pin.source_id)

            # 1. Semantic resolution
            catalog = self.catalogs.get_catalog(pin.source_id, pin.release_id)
            plan = self.resolver.resolve(
                intent=validated_intent,
                catalog=catalog,
                pin=pin,
                turn_id=turn.turn_id,
                tenant_id=actor.tenant_id,
            )

            self._pending_intents[turn.turn_id] = replace(validated_intent,
                dimensions=tuple(d.id for d in plan.dimensions))
            # Re-verify measure and dimension permissions at execution time
            self.security.check_measure_access(actor, plan.measure)
            for dim in plan.dimensions:
                self.security.check_dimension_access(actor, dim)
            for predicate in plan.filters:
                self.security.check_dimension_access(actor, catalog.dimensions[predicate.dimension])

            if catalog.disclosure_policy:
                from backend.security.disclosure import DatasetDisclosurePolicy
                policy = DatasetDisclosurePolicy(**catalog.disclosure_policy)
                if policy.requires_repeated_query_protection:
                    raise SemanticResolutionError("Dataset requires unsupported cross-query protection")
                allowed_roles = [role for role in actor.roles if not policy.allowed_roles or role in policy.allowed_roles]
                if not allowed_roles:
                    raise AuthorizationError("No role is allowed by the dataset disclosure policy")
                policy.validate_request(allowed_roles[0], plan.measure.id,
                    [d.id for d in plan.dimensions] + [f.dimension for f in plan.filters], plan.family.value)
                plan = replace(plan, measure=replace(plan.measure,
                    protected_entity_column=policy.protected_entity, min_entities=policy.min_group_threshold))
            if plan.measure.requires_repeated_query_protection:
                raise SemanticResolutionError("Dataset requires cross-query privacy protection; this operation is disabled until a verified protection mechanism is configured.")
            # Check for unsupported disclosure protections
            if getattr(plan.measure, "requires_unsupported_disclosure_protection", False):
                turn.transition_to(TurnStatus.DECLINED, reason=f"Measure '{plan.measure.name}' requires unsupported disclosure protection.")
                self._record_terminal_closure(turn, "DECLINED", "unsupported_disclosure_protection")
                return turn, None
            for tbl in plan.required_tables:
                if getattr(tbl, "requires_unsupported_disclosure_protection", False):
                    turn.transition_to(TurnStatus.DECLINED, reason=f"Table '{tbl.table_name}' requires unsupported disclosure protection.")
                    self._record_terminal_closure(turn, "DECLINED", "unsupported_disclosure_protection")
                    return turn, None


            # 2. Query compilation and execution
            target_source_id = getattr(plan, "source_id", getattr(turn, "source_id", None))
            if plan.family == AnalysisFamily.COMPARISON and plan.comparison_period:
                # Query current period
                pkg_curr = self.compiler.compile(plan)
                rows_curr = self.gateway.execute_package(pkg_curr, source_id=target_source_id)
                curr_facts = self.kernel.analyze(plan=plan, rows=rows_curr, pin=pin)

                # Query comparison period
                comp_plan = replace(plan, current_period=plan.comparison_period, comparison_period=None)
                pkg_comp = self.compiler.compile(comp_plan)
                rows_comp = self.gateway.execute_package(pkg_comp, source_id=target_source_id)
                comp_facts = self.kernel.analyze(plan=comp_plan, rows=rows_comp, pin=pin)

                if plan.dimensions:
                    current_by_key = {tuple(sorted(f.dimension_values.items())): f for f in curr_facts if f.dimension_values}
                    previous_by_key = {tuple(sorted(f.dimension_values.items())): f for f in comp_facts if f.dimension_values}
                    raw_facts = []
                    for key in sorted(set(current_by_key) | set(previous_by_key)):
                        current = current_by_key.get(key)
                        previous = previous_by_key.get(key)
                        if current is None:
                            current = replace(previous, value=None, period=plan.current_period,
                                completeness_status="LIMITED_SCOPE", assumptions=["No observation in current period; not assumed zero"])
                        if previous is None:
                            previous = replace(current, value=None, period=plan.comparison_period,
                                completeness_status="LIMITED_SCOPE", assumptions=["No observation in comparison period; not assumed zero"])
                        raw_facts.extend(replace(f, dimension_values=dict(key)) for f in self.kernel.compute_comparison(current, previous, pin))
                    if not raw_facts:
                        raise ValueError("No comparable observations available")
                else:
                    raw_facts = self.kernel.compute_comparison(curr_facts[0], comp_facts[0], pin)

            elif plan.family == AnalysisFamily.CONTRIBUTION and plan.comparison_period:
                # Two-period contribution to growth calculation!
                pkg_curr = self.compiler.compile(plan)
                rows_curr = self.gateway.execute_package(pkg_curr, source_id=target_source_id)
                curr_facts = self.kernel.analyze(plan=plan, rows=rows_curr, pin=pin)

                comp_plan = replace(plan, current_period=plan.comparison_period, comparison_period=None)
                pkg_comp = self.compiler.compile(comp_plan)
                rows_comp = self.gateway.execute_package(pkg_comp, source_id=target_source_id)
                comp_facts = self.kernel.analyze(plan=comp_plan, rows=rows_comp, pin=pin)

                raw_facts = self.kernel.compute_growth_contribution(curr_facts, comp_facts, pin)

            else:
                pkg = self.compiler.compile(plan)
                rows = self.gateway.execute_package(pkg, source_id=target_source_id)
                raw_facts = self.kernel.analyze(plan=plan, rows=rows, pin=pin)

            raw_facts = [replace(f, fact_id=f"{turn.turn_id}_fact_{index}") for index, f in enumerate(raw_facts)]

            # 3. Governed disclosure inspection (minimum-group and complementary suppression)
            counts = self._population_counts(plan, rows if "rows" in locals() else rows_curr + rows_comp, raw_facts)
            protected_facts = self.disclosure.inspect_and_protect(
                facts=raw_facts,
                tenant_id=actor.tenant_id,
                distinct_entity_counts=counts,
                min_entities=plan.measure.min_entities,
            )

            # 4. Fingerprint evidence package
            pkg_id = f"pkg_{uuid.uuid4().hex[:12]}"
            sealed_evidence = SealedEvidencePackage.seal(
                package_id=pkg_id,
                turn_id=turn.turn_id,
                tenant_id=actor.tenant_id,
                semantic_release_id=pin.release_id,
                facts=protected_facts,
            )

            self.audit.record_event(
                tenant_id=actor.tenant_id,
                subject_id=actor.subject_id,
                turn_id=turn.turn_id,
                event_type="evidence_sealed",
                payload_digest=sealed_evidence.package_digest,
            )

            # 5. Controlled transient memory buffer disposal
            disposal_digest = self.gateway.close_without_values()
            self.audit.record_event(
                tenant_id=actor.tenant_id,
                subject_id=actor.subject_id,
                turn_id=turn.turn_id,
                event_type="transient_memory_erased",
                payload_digest=disposal_digest,
            )

            # 6. Composition & Presentation Fallback
            turn.transition_to(TurnStatus.COMPOSING)
            rendered_view = self.presentation.render(sealed_evidence)
            self._answers[turn.turn_id] = rendered_view
            self._evidence_packages[turn.turn_id] = sealed_evidence

            # 7. Publication & Terminal Closure
            turn.transition_to(TurnStatus.ANSWERED)
            self._record_terminal_closure(turn, "ANSWERED", "published_ok")

            return turn, rendered_view

        except (SemanticResolutionError, AuthorizationError) as exc:
            self.gateway.close_without_values()
            turn.transition_to(TurnStatus.DECLINED, reason=str(exc))
            self._record_terminal_closure(turn, "DECLINED", str(exc))
            return turn, None

        except Exception as exc:
            self.gateway.close_without_values()
            turn.transition_to(TurnStatus.FAILED, reason=str(exc))
            self._record_terminal_closure(turn, "FAILED", str(exc))
            return turn, None

    def _apply_choice_to_intent(
        self,
        intent: StructuredIntent,
        choice: Any,
    ) -> StructuredIntent:
        """Incorporate a resolved clarification choice into structured intent."""
        target_type = getattr(choice, "target_type", "")
        resolved_val = getattr(choice, "resolved_value", None)

        measure = intent.measure
        family = intent.family
        dims = list(intent.dimensions)
        curr_p = intent.current_period
        comp_p = intent.comparison_period
        horizon = intent.horizon
        scenario_model = intent.scenario_model
        adjustable_driver = intent.adjustable_driver

        if target_type == "metric":
            measure = str(resolved_val)
        elif target_type == "dimension":
            if resolved_val not in dims:
                dims.append(str(resolved_val))
        elif target_type == "period_comparison":
            if isinstance(resolved_val, dict):
                curr_p = resolved_val.get("current")
                comp_p = resolved_val.get("comparison")
        elif target_type == "period":
            curr_p = str(resolved_val)
        elif target_type == "forecast_horizon":
            horizon = int(resolved_val)
        elif target_type == "scenario_model":
            scenario_model = str(resolved_val)
        elif target_type == "adjustable_driver":
            adjustable_driver = str(resolved_val)

        return StructuredIntent(
            measure=measure,
            family=family,
            dimensions=tuple(dims),
            filters=intent.filters,
            current_period=curr_p,
            comparison_period=comp_p,
            ambiguity_code=None,
            assumptions=intent.assumptions,
            horizon=horizon,
            scenario_model=scenario_model,
            adjustments=dict(intent.adjustments),
            target_metric=intent.target_metric,
            target_value=intent.target_value,
            adjustable_driver=adjustable_driver,
            bounds=intent.bounds,
        )

    def _record_terminal_closure(self, turn: Turn, terminal_status: str, reason: str) -> None:
        """Record final state and cleanup in audit logs."""
        digest = hashlib.sha256(f"{turn.turn_id}:{terminal_status}:{reason}".encode("utf-8")).hexdigest()
        self.audit.record_event(
            tenant_id=turn.tenant_id,
            subject_id=turn.subject_id,
            turn_id=turn.turn_id,
            event_type="terminal_closure",
            payload_digest=digest,
            metadata={"status": terminal_status, "reason": reason},
        )

    def _population_counts(self, plan, rows, facts):
        if not plan.measure.protected_entity_column:
            if plan.measure.sensitivity in ("CONFIDENTIAL", "RESTRICTED"):
                return {f.fact_id: 0 for f in facts}
            return {}
        counts = [int(r.get("protected_entity_count", 0)) for r in rows]
        # A conservative lower bound protects derived outputs and comparisons.
        minimum = min(counts, default=0)
        return {f.fact_id: minimum for f in facts}
