"""Privacy-safe LangGraph State definitions for Talk2Data.

CRITICAL ARCHITECTURAL RULE:
Graph boundaries must also be privacy boundaries.
Raw source rows, unsuppressed facts, database credentials, and transient query
diagnostics MUST NEVER enter checkpointed graph state.
"""
from __future__ import annotations
from typing import Any, Optional, TypedDict


class ClarificationChoice(TypedDict, total=False):
    choice_id: str
    label: str
    target_type: str
    resolved_value: Any


class ClarificationInterruptPayload(TypedDict, total=False):
    type: str  # "ANALYTICAL_CLARIFICATION"
    turn_id: str
    clarification_version: int
    prompt: str
    choices: list[dict[str, Any]]
    ambiguity_code: Optional[str]


class SafeDisplayFact(TypedDict, total=False):
    fact_id: str
    measure: str
    display_value: str
    unit: Optional[str]
    period: Optional[str]
    dimensions: dict[str, Any]
    calculation_type: str
    result_type: str
    is_suppressed: bool
    suppression_reason: Optional[str]


class PublicationCandidate(TypedDict, total=False):
    status: str  # "READY_FOR_PUBLICATION"
    conversation_id: str
    turn_id: str
    job_id: str
    thread_id: str
    semantic_release_id: str
    protected_evidence_reference: str
    evidence_digest: str
    deterministic_presentation: dict[str, Any]
    chart_specifications: list[dict[str, Any]]
    verified_optional_summary: Optional[dict[str, Any]]
    graph_completed_at: str


class GraphState(TypedDict, total=False):
    """Authoritative, privacy-safe state schema for Talk2Data StateGraph.
    
    Contains only what is strictly required to orchestrate and resume analytical progress.
    """
    # Identifiers & Thread Mapping
    conversation_id: str
    turn_id: str
    job_id: str
    tenant_id: str
    subject_id: str
    source_id: str
    question: str
    semantic_release_id: Optional[str]
    thread_id: str
    fencing_token: Optional[int]
    
    # Authorized Context & Vocabulary
    access_status: str  # "AUTHORIZED", "UNAUTHORIZED", "CANCELLED"
    authorized_source_id: Optional[str]
    accessible_vocabulary: Optional[dict[str, Any]]
    
    # Intent & Interpretation
    proposed_llm_json: Optional[str]
    raw_model_input: Optional[str]
    structured_intent: Optional[dict[str, Any]]
    
    # Governance & Validation
    validation_status: str  # "PENDING", "VALID", "CLARIFY", "DECLINE", "FAILED"
    decline_reason: Optional[str]
    error_message: Optional[str]
    is_provider_failure: bool
    
    # Human-In-The-Loop Clarification
    clarification_request: Optional[dict[str, Any]]
    clarification_response: Optional[dict[str, Any]]
    
    # Semantic Plan (resolved metadata, not physical execution)
    semantic_plan: Optional[dict[str, Any]]
    
    # Coarse-Grained Governed Analytics Output (Protected Facts Only)
    analysis_status: Optional[str]  # "PROTECTED_AND_SEALED"
    protected_evidence_reference: Optional[str]
    evidence_digest: Optional[str]
    safe_display_facts: Optional[list[dict[str, Any]]]
    
    # Presentation & Narrative
    deterministic_presentation: Optional[dict[str, Any]]
    chart_specifications: Optional[list[dict[str, Any]]]
    summary_template: Optional[dict[str, Any]]
    verified_summary: Optional[str]
    follow_up_questions: Optional[list[dict[str, Any]]]
    
    # Lifecycle & Candidate Result
    graph_status: str  # "RUNNING", "INTERRUPTED", "READY_FOR_PUBLICATION", "DECLINED", "FAILED"
    publication_candidate: Optional[PublicationCandidate]
    completed_at: Optional[str]
