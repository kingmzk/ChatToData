"""Tests for LangGraph StateGraph Target Architecture in Talk2Data."""
from __future__ import annotations
import json
import pytest

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from talk2data.domain.models import ApiActor
from talk2data.graph.workflow import create_talk2data_graph
from talk2data.graph.summary import DeterministicClaimGuardrail


@pytest.fixture
def graph_services(test_setup):
    """Provide standard test services dictionary from test_setup fixture."""
    from talk2data.compiler.compiler import GovernedQueryCompiler
    from talk2data.execution.kernel import DeterministicAnalysisKernel
    from talk2data.intent.parser import IntentParser
    from talk2data.intent.validator import IntentValidator
    from talk2data.presentation.fallback import DeterministicPresentationFallback
    from talk2data.security.disclosure import GovernedDisclosureService
    from talk2data.semantic.resolver import SemanticResolver
    from talk2data.lifecycle.audit import TerminalAuditRecorder

    return {
        "security": test_setup["security"],
        "catalogs": test_setup["catalogs"],
        "gateway": test_setup["gateway"],
        "parser": IntentParser(),
        "validator": IntentValidator(),
        "resolver": SemanticResolver(),
        "compiler": GovernedQueryCompiler(),
        "kernel": DeterministicAnalysisKernel(),
        "disclosure": GovernedDisclosureService(),
        "presentation": DeterministicPresentationFallback(),
        "audit": TerminalAuditRecorder(),
        "source_id": test_setup["source_id"],
        "actor_a": test_setup["actor_a"],
    }


def test_graph_valid_total_query(graph_services):
    """Test full execution path for a valid TOTAL sales_revenue query."""
    actor = graph_services["actor_a"]
    source_id = graph_services["source_id"]
    graph = create_talk2data_graph(checkpointer=MemorySaver())
    thread_id = "thread_turn_101"
    
    proposed_intent = json.dumps({
        "measure": "sales_revenue",
        "family": "TOTAL",
        "dimensions": [],
        "filters": [],
        "current_period": "2024",
    })
    
    initial_state = {
        "conversation_id": "conv_1",
        "turn_id": "turn_101",
        "tenant_id": actor.tenant_id,
        "subject_id": actor.subject_id,
        "source_id": source_id,
        "question": "What is total sales revenue in 2024?",
        "proposed_llm_json": proposed_intent,
        "thread_id": thread_id,
        "graph_status": "RUNNING",
    }
    
    config = {
        "configurable": {
            "thread_id": thread_id,
            "services": graph_services,
            "actor": actor,
        }
    }
    
    result = graph.invoke(initial_state, config)
    state = graph.get_state(config)
    
    assert state.next == ()  # Reached END
    vals = state.values
    assert vals.get("graph_status") == "READY_FOR_PUBLICATION"
    assert vals.get("validation_status") == "VALID"
    
    candidate = vals.get("publication_candidate")
    assert candidate is not None
    assert candidate["status"] == "READY_FOR_PUBLICATION"
    assert candidate["turn_id"] == "turn_101"
    assert candidate["protected_evidence_reference"] is not None
    assert candidate["evidence_digest"] is not None
    assert len(candidate["deterministic_presentation"]["kpi_cards"]) > 0
    assert vals.get("verified_summary") is not None


def test_graph_clarification_interrupt_and_resume(graph_services):
    """Test LangGraph interrupt() pause and Command(resume=...) on the same thread_id."""
    actor = graph_services["actor_a"]
    source_id = graph_services["source_id"]
    graph = create_talk2data_graph(checkpointer=MemorySaver())
    thread_id = "thread_turn_202"
    
    # Missing periods for comparison triggers CLARIFY
    proposed_intent = json.dumps({
        "measure": "sales_revenue",
        "family": "COMPARISON",
        "dimensions": ["country"],
        "filters": [],
        "current_period": None,
        "comparison_period": None,
    })
    
    initial_state = {
        "conversation_id": "conv_2",
        "turn_id": "turn_202",
        "tenant_id": actor.tenant_id,
        "subject_id": actor.subject_id,
        "source_id": source_id,
        "question": "Compare sales revenue by region",
        "proposed_llm_json": proposed_intent,
        "thread_id": thread_id,
        "graph_status": "RUNNING",
    }
    
    config = {
        "configurable": {
            "thread_id": thread_id,
            "services": graph_services,
            "actor": actor,
        }
    }
    
    # 1. Invoke initial query -> hits interrupt() in clarification_preparation_node
    graph.invoke(initial_state, config)
    state = graph.get_state(config)
    
    assert "clarification_preparation_node" in state.next
    assert len(state.tasks) > 0
    assert len(state.tasks[0].interrupts) > 0
    
    payload = state.tasks[0].interrupts[0].value
    assert payload["type"] == "ANALYTICAL_CLARIFICATION"
    assert payload["turn_id"] == "turn_202"
    assert len(payload["choices"]) > 0
    
    # First available choice
    selected_choice_id = payload["choices"][0]["choice_id"]
    
    # 2. Worker simulates resume via Command(resume=...) using same thread_id
    resume_command = Command(resume={"choice_id": selected_choice_id})
    graph.invoke(resume_command, config)
    
    resumed_state = graph.get_state(config)
    assert resumed_state.next == ()  # Completed to END
    res_vals = resumed_state.values
    assert res_vals.get("graph_status") == "READY_FOR_PUBLICATION"
    assert res_vals.get("publication_candidate") is not None


def test_graph_decline_greeting(graph_services):
    """Test that non-analytical greetings terminate cleanly at decline_result_node."""
    actor = graph_services["actor_a"]
    source_id = graph_services["source_id"]
    graph = create_talk2data_graph(checkpointer=MemorySaver())
    thread_id = "thread_turn_303"
    
    initial_state = {
        "conversation_id": "conv_3",
        "turn_id": "turn_303",
        "tenant_id": actor.tenant_id,
        "subject_id": actor.subject_id,
        "source_id": source_id,
        "question": "Hello! How are you today?",
        "thread_id": thread_id,
        "graph_status": "RUNNING",
    }
    
    config = {
        "configurable": {
            "thread_id": thread_id,
            "services": graph_services,
            "actor": actor,
        }
    }
    
    graph.invoke(initial_state, config)
    state = graph.get_state(config)
    
    assert state.next == ()
    assert state.values.get("graph_status") == "DECLINED"
    assert "Greeting" in (state.values.get("decline_reason") or "")


def test_graph_provider_failure_routing(graph_services):
    """Test that provider timeouts route to failure_result_node with FAILED status."""
    actor = graph_services["actor_a"]
    source_id = graph_services["source_id"]
    graph = create_talk2data_graph(checkpointer=MemorySaver())
    thread_id = "thread_turn_404"
    
    initial_state = {
        "conversation_id": "conv_4",
        "turn_id": "turn_404",
        "tenant_id": actor.tenant_id,
        "subject_id": actor.subject_id,
        "source_id": source_id,
        "question": "Show revenue in 2024",
        "proposed_llm_json": json.dumps({"provider_failure": "Upstream Gemini timeout 504"}),
        "thread_id": thread_id,
        "graph_status": "RUNNING",
    }
    
    config = {
        "configurable": {
            "thread_id": thread_id,
            "services": graph_services,
            "actor": actor,
        }
    }
    
    graph.invoke(initial_state, config)
    state = graph.get_state(config)
    
    assert state.next == ()
    assert state.values.get("graph_status") == "FAILED"
    assert state.values.get("is_provider_failure") is True


def test_checkpoint_state_privacy_hygiene(graph_services):
    """CRITICAL TEST: Verify raw database rows NEVER land in checkpoint state."""
    actor = graph_services["actor_a"]
    source_id = graph_services["source_id"]
    graph = create_talk2data_graph(checkpointer=MemorySaver())
    thread_id = "thread_turn_505"
    
    proposed_intent = json.dumps({
        "measure": "sales_revenue",
        "family": "TOTAL",
        "dimensions": ["country"],
        "filters": [],
        "current_period": "2024",
    })
    
    initial_state = {
        "conversation_id": "conv_5",
        "turn_id": "turn_505",
        "tenant_id": actor.tenant_id,
        "subject_id": actor.subject_id,
        "source_id": source_id,
        "question": "Show sales revenue by region in 2024",
        "proposed_llm_json": proposed_intent,
        "thread_id": thread_id,
        "graph_status": "RUNNING",
    }
    
    config = {
        "configurable": {
            "thread_id": thread_id,
            "services": graph_services,
            "actor": actor,
        }
    }
    
    graph.invoke(initial_state, config)
    state = graph.get_state(config)
    vals = state.values
    
    # Check that forbidden fields are completely absent
    assert "rows" not in vals
    assert "raw_rows" not in vals
    assert "database_credentials" not in vals
    assert "connection" not in vals
    
    # Check safe display facts
    safe_facts = vals.get("safe_display_facts", [])
    assert len(safe_facts) > 0
    for fact in safe_facts:
        assert "fact_id" in fact
        assert "display_value" in fact
        if fact.get("is_suppressed"):
            assert fact.get("value") is None


def test_deterministic_claim_guardrail():
    """Test placeholder verification and prose coverage rejection of hallucinations."""
    safe_facts = [
        {
            "fact_id": "fact_rev_2024",
            "value": "1200",
            "display_value": "1,200 USD",
            "is_suppressed": False,
        },
        {
            "fact_id": "fact_rev_2023",
            "value": "900",
            "display_value": "900 USD",
            "is_suppressed": False,
        },
        {
            "fact_id": "fact_suppressed",
            "value": None,
            "display_value": None,
            "is_suppressed": True,
        },
    ]
    periods = ["2024", "2023"]
    
    # 1. Valid narrative template
    valid_template = {
        "summary_template": "In {{period}}, total revenue was {{total_rev}} compared to {{prior_rev}}.",
        "bindings": {
            "period": "period_2024",
            "total_rev": "fact_rev_2024",
            "prior_rev": "fact_rev_2023",
        },
    }
    ok, text, reason = DeterministicClaimGuardrail.verify_and_render(
        valid_template, safe_facts, periods
    )
    assert ok is True
    assert "1,200 USD" in text
    assert "900 USD" in text
    
    # 2. Template referencing suppressed fact -> REJECT
    suppressed_template = {
        "summary_template": "Hidden value was {{secret_fact}}.",
        "bindings": {"secret_fact": "fact_suppressed"},
    }
    ok, text, reason = DeterministicClaimGuardrail.verify_and_render(
        suppressed_template, safe_facts, periods
    )
    assert ok is False
    assert "suppressed" in reason.lower()
    
    # 3. Template containing ungrounded / hallucinated number -> REJECT by prose coverage
    hallucinated_template = {
        "summary_template": "In {{period}}, total revenue was {{total_rev}} and profit margin was 95%.",
        "bindings": {
            "period": "period_2024",
            "total_rev": "fact_rev_2024",
        },
    }
    ok, text, reason = DeterministicClaimGuardrail.verify_and_render(
        hallucinated_template, safe_facts, periods
    )
    assert ok is False
    assert "ungrounded numeric expression" in reason.lower()


def test_manager_with_graph_enabled(test_setup):
    """Test ConversationLifecycleManager executing transparently via LangGraph."""
    manager = test_setup["manager"]
    manager.use_graph = True
    actor = test_setup["actor_a"]
    source_id = test_setup["source_id"]
    
    proposed_intent = json.dumps({
        "measure": "sales_revenue",
        "family": "TOTAL",
        "dimensions": [],
        "filters": [],
        "current_period": "2024",
    })
    
    turn, answer = manager.submit_turn(
        actor=actor,
        conversation_id="conv_graph_mgr_1",
        source_id=source_id,
        question="What is total sales revenue in 2024?",
        proposed_llm_json=proposed_intent,
        turn_id="turn_mgr_1",
    )
    
    assert turn.status.value == "ANSWERED"
    assert answer is not None
    assert len(answer.kpi_cards) > 0
    assert turn.pinned_release_id is not None


def test_manager_with_graph_clarification(test_setup):
    """Test ConversationLifecycleManager clarification pause and resume via LangGraph."""
    manager = test_setup["manager"]
    manager.use_graph = True
    actor = test_setup["actor_a"]
    source_id = test_setup["source_id"]
    
    # Missing periods triggers clarification
    proposed_intent = json.dumps({
        "measure": "sales_revenue",
        "family": "COMPARISON",
        "dimensions": ["country"],
        "filters": [],
        "current_period": None,
        "comparison_period": None,
    })
    
    turn, answer = manager.submit_turn(
        actor=actor,
        conversation_id="conv_graph_mgr_2",
        source_id=source_id,
        question="Compare sales revenue by country",
        proposed_llm_json=proposed_intent,
        turn_id="turn_mgr_2",
    )
    
    assert turn.status.value == "AWAITING_CLARIFICATION"
    assert answer is None
    assert turn.clarification_request is not None
    
    choice_id = turn.clarification_request.options[0].choice_id
    
    # Resume turn via clarification choice
    turn2, answer2 = manager.submit_clarification_choice(
        actor=actor,
        turn_id="turn_mgr_2",
        choice_id=choice_id,
    )
    
    assert turn2.status.value == "ANSWERED"
    assert answer2 is not None
    assert len(answer2.table_rows) > 0
    assert answer2.table_columns[0] == "country"

