"""Tests for Turn Lifecycle State Machine and Privacy-Preserving Audit Trail."""
from talk2data.domain.models import TurnStatus


def test_turn_lifecycle_and_audit_digests(test_setup):
    """Verify that end-to-end execution generates complete audit trail without raw question or rows."""
    manager = test_setup["manager"]
    actor_a = test_setup["actor_a"]
    source_id = test_setup["source_id"]

    turn, answer = manager.submit_turn(
        actor=actor_a,
        conversation_id="conv_audit_1",
        source_id=source_id,
        question="What was total sales in 2024?",
        proposed_llm_json='{"measure": "sales_revenue", "family": "TOTAL", "current_period": "2024"}',
    )

    assert turn.status == TurnStatus.ANSWERED
    events = manager.audit.get_turn_events(turn.turn_id)

    event_types = [e.event_type for e in events]
    assert "question_submitted" in event_types
    assert "analysis_started" in event_types
    assert "evidence_sealed" in event_types
    assert "transient_memory_erased" in event_types
    assert "terminal_closure" in event_types

    # Ensure raw question text is never stored in audit events
    for e in events:
        assert turn.question_text not in str(e.metadata)
        assert "raw_rows" not in e.metadata
        assert "raw_sql" not in e.metadata
        assert len(e.payload_digest) == 64  # Valid SHA-256 hex digest
