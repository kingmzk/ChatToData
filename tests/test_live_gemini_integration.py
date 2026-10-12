"""Live Integration Test Suite for Talk2Data using real Gemini LLM Interpretation.

Runs natural-language questions through GeminiIntentAssist without injecting mock intent:
1. Metric & Dimension Selection: 'What was sales revenue by country in 2024?'
2. Filter Application: 'What was sales revenue for US in 2024?'
3. Period-over-Period Comparison: 'Compare sales revenue between 2024 and 2023'
4. Ambiguity Detection: 'Compare sales revenue' (missing periods triggers clarification)
5. Contextual Follow-up: 'Compare Q3 with Q2' following a 2024 turn inherits 2024
"""
import os
import pytest
from decimal import Decimal
from dotenv import load_dotenv

from talk2data.domain.models import TurnStatus
from talk2data.intent.gemini_assist import GeminiIntentAssist

load_dotenv()


@pytest.fixture
def gemini_assist():
    if os.getenv("RUN_LIVE_LLM_TESTS", "").lower() not in ("1", "true", "yes"):
        pytest.skip(
            "Live LLM integration tests are opt-in to keep regression tests independent of live model APIs. "
            "Set RUN_LIVE_LLM_TESTS=1 to run against configured model 'gemini-3.5-flash'."
        )
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        pytest.skip("GEMINI_API_KEY not set; skipping live LLM integration tests.")
    return GeminiIntentAssist(api_key=api_key)


def test_live_gemini_metric_and_dimension_selection(test_setup, gemini_assist):
    """Live NL: 'What was sales revenue by country in 2024?' without mock intent."""
    manager = test_setup["manager"]
    actor_a = test_setup["actor_a"]
    source_id = test_setup["source_id"]

    # Obtain vocabulary for prompt
    pin = test_setup["catalogs"].pin("tenant_a", source_id)
    vocab = test_setup["catalogs"].get_accessible_vocabulary(actor_a, pin)

    question = "What was sales revenue by country in 2024?"
    llm_json = gemini_assist.interpret(vocab, question)

    turn, answer = manager.submit_turn(
        actor=actor_a,
        conversation_id="conv_live_metric_dim",
        source_id=source_id,
        question=question,
        proposed_llm_json=llm_json,
    )

    if turn.status == TurnStatus.FAILED and any(c in (turn.error_message or "") for c in ("503", "429", "RESOURCE_EXHAUSTED")):
        pytest.skip(f"UNVERIFIED: Live Gemini API (gemini-3.5-flash) unavailable or quota exhausted: {turn.error_message}")

    assert turn.status == TurnStatus.ANSWERED, f"Turn status {turn.status}, llm_json: {llm_json}, err: {turn.error_message}, decline: {turn.decline_reason}"
    assert answer is not None
    rows = {r["country"]: Decimal(r["Value"].replace(",", "")) for r in answer.table_rows}
    assert rows["US"] == Decimal("80000.00")
    assert rows["Germany"] == Decimal("70000.00")
    assert sum(rows.values()) == Decimal("150000.00")


def test_live_gemini_filter_extraction(test_setup, gemini_assist):
    """Live NL: 'What was sales revenue for US in 2024?' without mock intent."""
    manager = test_setup["manager"]
    actor_a = test_setup["actor_a"]
    source_id = test_setup["source_id"]

    pin = test_setup["catalogs"].pin("tenant_a", source_id)
    vocab = test_setup["catalogs"].get_accessible_vocabulary(actor_a, pin)

    question = "What was sales revenue for US in 2024?"
    llm_json = gemini_assist.interpret(vocab, question)

    turn, answer = manager.submit_turn(
        actor=actor_a,
        conversation_id="conv_live_filter",
        source_id=source_id,
        question=question,
        proposed_llm_json=llm_json,
    )

    if turn.status == TurnStatus.FAILED and any(c in (turn.error_message or "") for c in ("503", "429", "RESOURCE_EXHAUSTED")):
        pytest.skip(f"UNVERIFIED: Live Gemini API (gemini-3.5-flash) unavailable or quota exhausted: {turn.error_message}")

    assert turn.status == TurnStatus.ANSWERED
    assert answer is not None
    kpi_clean = answer.kpi_cards[0].value.replace(",", "")
    assert Decimal(kpi_clean) == Decimal("80000.00")


def test_live_gemini_comparison_periods(test_setup, gemini_assist):
    """Live NL: 'Compare sales revenue between 2024 and 2023' without mock intent."""
    manager = test_setup["manager"]
    actor_a = test_setup["actor_a"]
    source_id = test_setup["source_id"]

    pin = test_setup["catalogs"].pin("tenant_a", source_id)
    vocab = test_setup["catalogs"].get_accessible_vocabulary(actor_a, pin)

    question = "Compare sales revenue between 2024 and 2023"
    llm_json = gemini_assist.interpret(vocab, question)

    turn, answer = manager.submit_turn(
        actor=actor_a,
        conversation_id="conv_live_comparison",
        source_id=source_id,
        question=question,
        proposed_llm_json=llm_json,
    )

    if turn.status == TurnStatus.FAILED and any(c in (turn.error_message or "") for c in ("503", "429", "RESOURCE_EXHAUSTED")):
        pytest.skip(f"UNVERIFIED: Live Gemini API (gemini-3.5-flash) unavailable or quota exhausted: {turn.error_message}")

    assert turn.status == TurnStatus.ANSWERED
    assert answer is not None
    # 2024 (150k) vs 2023 (100k) -> delta = +50k (+50.00%)
    assert any(card.delta == "+50,000.00" and card.growth_percent == "+50.00%" for card in answer.kpi_cards)


def test_live_gemini_ambiguity_detection(test_setup, gemini_assist):
    """Live NL: 'Compare sales revenue' (omitted periods) triggers clarification."""
    manager = test_setup["manager"]
    actor_a = test_setup["actor_a"]
    source_id = test_setup["source_id"]

    pin = test_setup["catalogs"].pin("tenant_a", source_id)
    vocab = test_setup["catalogs"].get_accessible_vocabulary(actor_a, pin)

    question = "Compare sales revenue"
    llm_json = gemini_assist.interpret(vocab, question)

    turn, answer = manager.submit_turn(
        actor=actor_a,
        conversation_id="conv_live_ambiguity",
        source_id=source_id,
        question=question,
        proposed_llm_json=llm_json,
    )

    if turn.status == TurnStatus.FAILED and any(c in (turn.error_message or "") for c in ("503", "429", "RESOURCE_EXHAUSTED")):
        pytest.skip(f"UNVERIFIED: Live Gemini API (gemini-3.5-flash) unavailable or quota exhausted: {turn.error_message}")

    assert turn.status == TurnStatus.AWAITING_CLARIFICATION, turn.error_message
    assert turn.clarification_request is not None
    assert answer is None


def test_live_gemini_contextual_follow_up(test_setup, gemini_assist):
    """Turn 1 sets year 2024; Turn 2 'Compare Q3 with Q2' inherits year 2024."""
    manager = test_setup["manager"]
    actor_a = test_setup["actor_a"]
    source_id = test_setup["source_id"]

    pin = test_setup["catalogs"].pin("tenant_a", source_id)
    vocab = test_setup["catalogs"].get_accessible_vocabulary(actor_a, pin)

    # Turn 1: 2024 sales revenue
    q1 = "What was sales revenue in 2024?"
    json_1 = gemini_assist.interpret(vocab, q1)
    turn_1, ans_1 = manager.submit_turn(
        actor=actor_a,
        conversation_id="conv_live_context",
        source_id=source_id,
        question=q1,
        proposed_llm_json=json_1,
    )
    if turn_1.status == TurnStatus.FAILED and any(c in (turn_1.error_message or "") for c in ("503", "429", "RESOURCE_EXHAUSTED")):
        pytest.skip(f"UNVERIFIED: Live Gemini API (gemini-3.5-flash) unavailable or quota exhausted: {turn_1.error_message}")
    assert turn_1.status == TurnStatus.ANSWERED

    # Turn 2: Follow-up question in the same conversation
    q2 = "Compare Q3 with Q2"
    json_2 = gemini_assist.interpret(vocab, q2)
    turn_2, ans_2 = manager.submit_turn(
        actor=actor_a,
        conversation_id="conv_live_context",
        source_id=source_id,
        question=q2,
        proposed_llm_json=json_2,
    )
    if turn_2.status == TurnStatus.FAILED and any(c in (turn_2.error_message or "") for c in ("503", "429", "RESOURCE_EXHAUSTED")):
        pytest.skip(f"UNVERIFIED: Live Gemini API (gemini-3.5-flash) unavailable or quota exhausted: {turn_2.error_message}")

    assert turn_2.status == TurnStatus.ANSWERED, turn_2.error_message
    assert ans_2 is not None
    # 2024-Q3 ($60,000.00) vs 2024-Q2 ($40,000.00) -> delta = +$20,000.00 (+50.00%)
    assert any(card.delta == "+20,000.00" and card.growth_percent == "+50.00%" for card in ans_2.kpi_cards)
