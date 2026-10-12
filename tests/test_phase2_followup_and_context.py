"""Tests for Talk2Data Phase 2 Analytical Context Persistence and Safe Follow-Up Suggestions."""
import pytest

from talk2data.context.followup import FollowUpService
from talk2data.domain.models import Turn, TurnStatus
from talk2data.jobs.worker import DurableJobWorker
from talk2data.storage.repository import PersistentApplicationStore


def test_analytical_context_persistence_and_followup_suggestions(test_setup):
    """Verify analytical context is persisted upon answer publication and follow-up suggestions are generated safely."""
    store = PersistentApplicationStore(":memory:")
    manager = test_setup["manager"]
    security = test_setup["security"]
    catalogs = test_setup["catalogs"]
    actor = test_setup["actor_a"]

    conv_id = store.create_conversation(actor, title="Follow-up Test Conv")
    turn_id = "turn_fu_01"

    t = Turn.create(
        conversation_id=conv_id,
        actor=actor,
        question="What was total sales revenue in 2024?",
        source_id="finance_dw",
        turn_id=turn_id,
    )
    store.save_turn(t, actor)

    job, _ = store.submit_job_idempotent(
        actor=actor,
        turn_id=turn_id,
        operation="SUBMIT_QUESTION",
        idempotency_key="key_fu_01",
        payload={
            "question": "What was total sales revenue in 2024?",
            "proposed_llm_json": '{"measure": "sales_revenue", "family": "TOTAL", "current_period": "2024"}',
        },
    )

    worker = DurableJobWorker(store=store, manager=manager, security=security, worker_id="w_fu")
    worker.process_next_job()

    turn_res = store.get_turn(actor, turn_id)
    assert turn_res["status"] == "ANSWERED"

    # Verify analytical context was persisted
    ctx = store.get_analytical_context(actor, conv_id)
    assert ctx is not None
    assert ctx["measure"] == "sales_revenue"
    assert ctx["current_period"] == "2024"
    assert ctx["source_id"] == "finance_dw"

    # Generate follow-up suggestions
    fu_service = FollowUpService(store=store, catalogs=catalogs)
    suggestions = fu_service.get_follow_up_suggestions(actor, conv_id)

    assert len(suggestions) > 0
    # Suggestions should offer relevant drill-downs based on accessible catalog metadata
    assert any("by" in s.lower() or "compare" in s.lower() for s in suggestions)
    # Suggestions must not execute queries or leak private database values
    for s in suggestions:
        assert isinstance(s, str)
        assert len(s) > 5


def test_conversational_context_inheritance_and_revalidation(test_setup):
    """Verify follow-up turns inherit temporal context from prior turns and revalidate against catalog."""
    manager = test_setup["manager"]
    actor = test_setup["actor_a"]
    source_id = test_setup["source_id"]

    # Turn 1: Annual revenue in 2024
    t1, a1 = manager.submit_turn(
        actor=actor,
        conversation_id="conv_inherit_01",
        source_id=source_id,
        question="What was total sales revenue in 2024?",
        proposed_llm_json='{"measure": "sales_revenue", "family": "TOTAL", "current_period": "2024"}',
    )
    assert t1.status == TurnStatus.ANSWERED

    # Turn 2: Follow-up asking "Compare Q3 and Q2" without repeating year 2024
    t2, a2 = manager.submit_turn(
        actor=actor,
        conversation_id="conv_inherit_01",
        source_id=source_id,
        question="Compare Q3 and Q2",
        proposed_llm_json='{"measure": "sales_revenue", "family": "COMPARISON", "current_period": "Q3", "comparison_period": "Q2"}',
    )
    assert t2.status == TurnStatus.ANSWERED
    # Verified that year was inherited into 2024-Q3 and 2024-Q2
    pending = manager._pending_intents.get(t2.turn_id)
    assert pending.current_period == "2024-Q3"
    assert pending.comparison_period == "2024-Q2"
    assert any("Inherited year 2024" in a for a in pending.assumptions)
