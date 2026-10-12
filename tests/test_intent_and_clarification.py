"""Tests for Intent Parsing, Bounded Retry, and Clarification Lifecycle Flow."""
import pytest
from talk2data.domain.models import TurnStatus
from talk2data.intent.parser import IntentParser


def test_malformed_json_markdown_fences_cleaned(test_setup):
    """Parser automatically handles markdown code fences and trailing commas."""
    parser = IntentParser(max_retries=2)
    raw_markdown = """```json
    {
      "measure": "sales_revenue",
      "family": "TOTAL",
      "current_period": "2024",
    }
    ```"""
    result = parser.parse_with_bounded_retry(raw_markdown)
    assert result.success is True
    assert result.intent.measure == "sales_revenue"
    assert result.intent.current_period == "2024"


def test_malformed_json_bounded_retry(test_setup):
    """Parser invokes retry function when JSON is malformed and succeeds within budget."""
    parser = IntentParser(max_retries=2)
    bad_json = "NOT VALID JSON"

    def mock_retry(error_msg: str, attempt: int) -> str:
        # On retry attempt 1, return valid JSON
        return '{"measure": "sales_revenue", "family": "TOTAL"}'

    result = parser.parse_with_bounded_retry(bad_json, retry_fn=mock_retry)
    assert result.success is True
    assert result.retry_count == 1
    assert result.intent.measure == "sales_revenue"


def test_clarification_flow_halts_and_resumes(test_setup):
    """Clarification pauses execution; user choice resumes validation into ANSWERED state."""
    manager = test_setup["manager"]
    actor_a = test_setup["actor_a"]
    source_id = test_setup["source_id"]

    # 1. Submit ambiguous query (COMPARISON family without periods)
    turn, answer = manager.submit_turn(
        actor=actor_a,
        conversation_id="conv_clar_1",
        source_id=source_id,
        question="Show sales growth.",
        proposed_llm_json='{"measure": "sales_revenue", "family": "COMPARISON"}',
    )

    # Must transition to AWAITING_CLARIFICATION and HALT immediately
    assert turn.status == TurnStatus.AWAITING_CLARIFICATION
    assert answer is None
    assert turn.clarification_request is not None
    assert turn.clarification_request.ambiguity_code == "comparison-period-required"
    assert len(turn.clarification_request.options) >= 2

    # 2. User submits clarification choice
    choice_id = turn.clarification_request.options[0].choice_id  # 'Compare 2024 with 2023'
    turn_resumed, final_answer = manager.submit_clarification_choice(
        actor=actor_a,
        turn_id=turn.turn_id,
        choice_id=choice_id,
    )

    # Must resume validation and complete execution
    assert turn_resumed.status == TurnStatus.ANSWERED
    assert final_answer is not None
    assert "2024" in final_answer.summary_text
    assert "2023" in final_answer.summary_text


def test_decline_terminates_turn_immediately(test_setup):
    """Unsupported requests transition to DECLINED and terminate immediately."""
    manager = test_setup["manager"]
    actor_a = test_setup["actor_a"]
    source_id = test_setup["source_id"]

    turn, answer = manager.submit_turn(
        actor=actor_a,
        conversation_id="conv_dec_1",
        source_id=source_id,
        question="Show customer churn rate for 2024.",
        proposed_llm_json='{"measure": "churn_rate", "family": "TOTAL"}',
    )

    assert turn.status == TurnStatus.DECLINED
    assert answer is None
    assert "unsupported metric" in turn.decline_reason.lower()


def test_greeting_fast_path_declined(test_setup):
    """Non-analytical greetings are cleanly declined with helpful prompt."""
    manager = test_setup["manager"]
    actor_a = test_setup["actor_a"]
    source_id = test_setup["source_id"]

    turn, answer = manager.submit_turn(
        actor=actor_a,
        conversation_id="conv_greet_1",
        source_id=source_id,
        question="Hello, good morning!",
        proposed_llm_json='{}',
    )

    assert turn.status == TurnStatus.DECLINED
    assert answer is None
    assert "greeting" in turn.decline_reason.lower()


def test_gemini_intent_assist_prompt_scoping(test_setup):
    """Ensure GeminiIntentAssist generates prompts containing ONLY accessible metadata and no credentials."""
    from talk2data.intent.gemini_assist import GeminiIntentAssist
    catalogs = test_setup["catalogs"]
    actor_a = test_setup["actor_a"]
    pin = catalogs.pin(actor_a.tenant_id, "finance_dw")
    vocab = catalogs.get_accessible_vocabulary(actor_a, pin)

    assist = GeminiIntentAssist()
    prompt = assist.build_catalog_prompt(vocab, "Show total sales by country in 2024")

    # Prompt contains accessible measures and dimensions
    assert "sales_revenue" in prompt
    assert "country" in prompt
    assert "Show total sales by country in 2024" in prompt

    # Prompt contains NO database credentials or connection strings
    assert "password" not in prompt.lower()
    assert "sqlite" not in prompt.lower()
    assert "SELECT " not in prompt


def test_configurable_model_and_env():
    """Verify model selection is configurable via parameter and environment variable."""
    import os
    from talk2data.intent.gemini_assist import GeminiIntentAssist

    # Default model is gemini-3.5-flash
    assist_default = GeminiIntentAssist()
    assert assist_default.model_name in ("gemini-3.5-flash", os.getenv("TALK2DATA_MODEL", "gemini-3.5-flash"))


    # Explicit constructor override
    assist_custom = GeminiIntentAssist(model_name="gemini-1.5-pro")
    assert assist_custom.model_name == "gemini-1.5-pro"

    # Environment variable override
    os.environ["TALK2DATA_MODEL"] = "gemini-2.5-pro"
    assist_env = GeminiIntentAssist()
    assert assist_env.model_name == "gemini-2.5-pro"
    os.environ.pop("TALK2DATA_MODEL", None)



def test_json_cleanup_preserves_semantics_without_inventing_meaning():
    """Verify parser strips syntax noise (fences, trailing commas) without mutating business fields."""
    parser = IntentParser()
    raw = '```json\n{"measure": "sales_revenue", "family": "TOTAL", "current_period": "2024",}\n```'
    res = parser.parse_with_bounded_retry(raw)
    assert res.success is True
    assert res.intent.measure == "sales_revenue"
    assert res.intent.family.value == "TOTAL"
    assert res.intent.current_period == "2024"
    assert res.intent.dimensions == ()
    assert res.intent.filters == ()


def test_repaired_json_revalidated_against_strict_schema(test_setup):
    """Repaired or retried JSON that contains an unauthorized metric is rejected by strict validator."""
    manager = test_setup["manager"]
    actor_a = test_setup["actor_a"]
    source_id = test_setup["source_id"]

    # Retry function returns syntactically valid JSON, but with an unsupported metric
    def mock_retry_hallucinated(err, attempt):
        return '{"measure": "hallucinated_unreal_metric", "family": "TOTAL"}'

    turn, answer = manager.submit_turn(
        actor=actor_a,
        conversation_id="conv_repair_strict",
        source_id=source_id,
        question="Show total revenue.",
        proposed_llm_json="MALFORMED JSON",
        retry_fn=mock_retry_hallucinated,
    )

    # Syntactic repair succeeded, but strict validator rejected it deterministically
    assert turn.status == TurnStatus.DECLINED
    assert answer is None
    assert "unsupported metric" in turn.decline_reason.lower()

