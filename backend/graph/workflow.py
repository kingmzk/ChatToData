"""LangGraph StateGraph assembly for Talk2Data Governed Analytical Workflow."""
from __future__ import annotations
from typing import Optional, Any

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

from backend.graph.state import GraphState
from backend.graph.nodes import (
    access_context_node,
    catalog_projection_node,
    intent_interpretation_node,
    structured_intent_parser_node,
    context_resolution_node,
    governance_validator_node,
    clarification_preparation_node,
    clarification_resume_node,
    semantic_resolution_node,
    governed_analytics_node,
    deterministic_presentation_node,
    optional_executive_summary_node,
    deterministic_claim_guardrail_node,
    publication_candidate_node,
    failure_result_node,
    decline_result_node,
)


def _route_after_access(state: GraphState) -> str:
    if state.get("access_status") == "DECLINED":
        return "decline_result_node"
    return "catalog_projection_node"


def _route_after_parser(state: GraphState) -> str:
    status = state.get("validation_status")
    if status == "FAILED":
        return "failure_result_node"
    if status == "DECLINE":
        return "decline_result_node"
    return "context_resolution_node"


def _route_after_validator(state: GraphState) -> str:
    status = state.get("validation_status")
    if status == "CLARIFY":
        return "clarification_preparation_node"
    if status == "DECLINE":
        return "decline_result_node"
    if status == "FAILED":
        return "failure_result_node"
    return "semantic_resolution_node"


def _route_after_semantic(state: GraphState) -> str:
    status = state.get("validation_status")
    if status == "DECLINE":
        return "decline_result_node"
    if status == "FAILED":
        return "failure_result_node"
    return "governed_analytics_node"


def _route_after_analytics(state: GraphState) -> str:
    status = state.get("validation_status")
    if status == "FAILED":
        return "failure_result_node"
    return "deterministic_presentation_node"


def create_talk2data_graph(checkpointer: Optional[BaseCheckpointSaver] = None) -> Any:
    """Build and compile the governed Talk2Data StateGraph.
    
    If no checkpointer is provided, defaults to MemorySaver for testing and development.
    In production, a persistent checkpointer (e.g. PostgresSaver / SqliteSaver) is supplied.
    """
    builder = StateGraph(GraphState)
    
    # 1. Register discrete nodes
    builder.add_node("access_context_node", access_context_node)
    builder.add_node("catalog_projection_node", catalog_projection_node)
    builder.add_node("intent_interpretation_node", intent_interpretation_node)
    builder.add_node("structured_intent_parser_node", structured_intent_parser_node)
    builder.add_node("context_resolution_node", context_resolution_node)
    builder.add_node("governance_validator_node", governance_validator_node)
    
    # Clarification nodes
    builder.add_node("clarification_preparation_node", clarification_preparation_node)
    builder.add_node("clarification_resume_node", clarification_resume_node)
    
    # Analytical nodes
    builder.add_node("semantic_resolution_node", semantic_resolution_node)
    builder.add_node("governed_analytics_node", governed_analytics_node)
    builder.add_node("deterministic_presentation_node", deterministic_presentation_node)
    builder.add_node("optional_executive_summary_node", optional_executive_summary_node)
    builder.add_node("deterministic_claim_guardrail_node", deterministic_claim_guardrail_node)
    builder.add_node("publication_candidate_node", publication_candidate_node)
    
    # Terminal exit nodes
    builder.add_node("failure_result_node", failure_result_node)
    builder.add_node("decline_result_node", decline_result_node)
    
    # 2. Define workflow edges
    builder.add_edge(START, "access_context_node")
    
    builder.add_conditional_edges(
        "access_context_node",
        _route_after_access,
        {
            "decline_result_node": "decline_result_node",
            "catalog_projection_node": "catalog_projection_node",
        },
    )
    
    builder.add_edge("catalog_projection_node", "intent_interpretation_node")
    builder.add_edge("intent_interpretation_node", "structured_intent_parser_node")
    
    builder.add_conditional_edges(
        "structured_intent_parser_node",
        _route_after_parser,
        {
            "failure_result_node": "failure_result_node",
            "decline_result_node": "decline_result_node",
            "context_resolution_node": "context_resolution_node",
        },
    )
    
    builder.add_edge("context_resolution_node", "governance_validator_node")
    
    builder.add_conditional_edges(
        "governance_validator_node",
        _route_after_validator,
        {
            "clarification_preparation_node": "clarification_preparation_node",
            "decline_result_node": "decline_result_node",
            "failure_result_node": "failure_result_node",
            "semantic_resolution_node": "semantic_resolution_node",
        },
    )
    
    # Clarification loop
    builder.add_edge("clarification_preparation_node", "clarification_resume_node")
    builder.add_edge("clarification_resume_node", "governance_validator_node")
    
    builder.add_conditional_edges(
        "semantic_resolution_node",
        _route_after_semantic,
        {
            "decline_result_node": "decline_result_node",
            "failure_result_node": "failure_result_node",
            "governed_analytics_node": "governed_analytics_node",
        },
    )
    
    builder.add_conditional_edges(
        "governed_analytics_node",
        _route_after_analytics,
        {
            "failure_result_node": "failure_result_node",
            "deterministic_presentation_node": "deterministic_presentation_node",
        },
    )
    
    builder.add_edge("deterministic_presentation_node", "optional_executive_summary_node")
    builder.add_edge("optional_executive_summary_node", "deterministic_claim_guardrail_node")
    builder.add_edge("deterministic_claim_guardrail_node", "publication_candidate_node")
    
    # Terminal edges
    builder.add_edge("publication_candidate_node", END)
    builder.add_edge("failure_result_node", END)
    builder.add_edge("decline_result_node", END)
    
    saver = checkpointer if checkpointer is not None else MemorySaver()
    return builder.compile(checkpointer=saver)
