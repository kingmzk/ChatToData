"""Tests verifying separation of provider execution failures from semantic ambiguity.

Covers:
1. Gemini 503 / network exception -> TurnStatus.FAILED (NOT clarification)
2. Malformed non-JSON response exhausted retries -> TurnStatus.FAILED (NOT clarification)
3. Genuinely ambiguous business question -> TurnStatus.AWAITING_CLARIFICATION
4. Safe failure status prevents asking users to clarify clear questions due to provider downtime
"""
import pytest
from talk2data.domain.models import TurnStatus


def test_provider_503_or_network_failure_transitions_to_failed(test_setup):
    """Gemini 503 or network failure is an execution failure and must NOT trigger clarification."""
    manager = test_setup["manager"]
    actor_a = test_setup["actor_a"]
    source_id = test_setup["source_id"]

    # Clear, unambiguous user question
    clear_question = "What was total sales in 2024?"

    # Model provider fails with 503 Unavailable
    provider_error_json = '{"provider_failure": "503 UNAVAILABLE: Model is currently experiencing high demand."}'

    turn, answer = manager.submit_turn(
        actor=actor_a,
        conversation_id="conv_provider_fail_1",
        source_id=source_id,
        question=clear_question,
        proposed_llm_json=provider_error_json,
    )

    # Must be FAILED, not AWAITING_CLARIFICATION
    assert turn.status == TurnStatus.FAILED
    assert turn.clarification_request is None
    assert answer is None
    assert "Provider/Execution failure" in (turn.error_message or "")
    assert "503 UNAVAILABLE" in (turn.error_message or "")


def test_malformed_json_exhausted_retries_transitions_to_failed(test_setup):
    """Unparseable model output after retries transitions to FAILED, never clarification."""
    manager = test_setup["manager"]
    actor_a = test_setup["actor_a"]
    source_id = test_setup["source_id"]

    clear_question = "What was total sales in 2024?"
    malformed_output = "I cannot fulfill this request as JSON. Here is plain text."

    turn, answer = manager.submit_turn(
        actor=actor_a,
        conversation_id="conv_malformed_exhausted",
        source_id=source_id,
        question=clear_question,
        proposed_llm_json=malformed_output,
    )

    assert turn.status == TurnStatus.FAILED
    assert turn.clarification_request is None
    assert answer is None
    assert "Provider/Execution failure" in (turn.error_message or "")


def test_semantic_ambiguity_triggers_clarification_distinct_from_failure(test_setup):
    """Genuine ambiguity (missing comparison period) triggers AWAITING_CLARIFICATION."""
    manager = test_setup["manager"]
    actor_a = test_setup["actor_a"]
    source_id = test_setup["source_id"]

    # Ambiguous question without periods
    turn, answer = manager.submit_turn(
        actor=actor_a,
        conversation_id="conv_genuine_ambiguity",
        source_id=source_id,
        question="Compare sales periods.",
        proposed_llm_json='{"measure": "sales_revenue", "family": "COMPARISON"}',
    )

    assert turn.status == TurnStatus.AWAITING_CLARIFICATION
    assert turn.clarification_request is not None
    assert turn.clarification_request.ambiguity_code == "comparison-period-required"
    assert answer is None
