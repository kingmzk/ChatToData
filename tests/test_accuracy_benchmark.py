"""Tests for Accuracy Evaluation and Benchmarking Suite.

Asserts separated reporting across:
1. Numerical answer accuracy (evaluated on answered numerical questions)
2. Clarification accuracy (evaluated on ambiguous questions)
3. Refusal accuracy (evaluated on greetings, attacks, unauthorized metrics)
4. Answer coverage breakdown (distinguishing direct answered from awaiting clarification)
5. Clear identification of mock vs live LLM
6. Fixture limitation statement (acknowledging controlled fixture suite vs general accuracy)
"""
import pytest
from talk2data.eval.benchmark import AccuracyBenchmarkSuite


def test_accuracy_benchmark_suite_execution(test_setup):
    """Run full accuracy benchmark and verify separated dimensions."""
    manager = test_setup["manager"]
    actor_a = test_setup["actor_a"]
    source_id = test_setup["source_id"]

    bench = AccuracyBenchmarkSuite()
    report = bench.run_benchmark(manager=manager, actor=actor_a, source_id=source_id)

    # 1. Verification of metadata and fixture scope
    assert report.total_cases == 11
    assert "controlled fixture suite" in report.fixture_description.lower()
    assert report.mock_intent_cases == 11
    assert report.live_llm_cases == 0

    # 2. Numerical Answer Accuracy (4 cases: Total Sales, Country, Avg Order, Order Count)
    assert report.numerical_cases_count == 4
    assert report.numerical_cases_correct == 4
    assert report.numerical_accuracy_pct == 100.0

    # 3. Clarification Accuracy (3 cases: Growth, Metric disambiguation, Breakdown)
    assert report.clarification_opportunities == 3
    assert report.clarification_correct == 3
    assert report.clarification_accuracy_pct == 100.0

    # 4. Refusal Accuracy (4 cases: Greeting, Cross-tenant hack, Unknown metric, Unauthorized salary)
    assert report.refusal_cases_count == 4
    assert report.refusal_cases_correct == 4
    assert report.refusal_accuracy_pct == 100.0

    # 5. Answer Coverage Breakdown (distinguishing direct answered vs awaiting clarification)
    # Total supported questions = 7 (4 answered directly + 3 awaiting clarification)
    assert report.supported_questions_count == 7
    assert report.direct_answered_count == 4
    assert report.direct_answered_coverage_pct == round((4 / 7) * 100.0, 2)  # 57.14%
    assert report.awaiting_clarification_count == 3
    assert report.awaiting_clarification_coverage_pct == round((3 / 7) * 100.0, 2)  # 42.86%
    assert report.unnecessary_declines_count == 0
    assert report.unnecessary_declines_pct == 0.0

    # 6. Combined Supported Question Handling
    assert report.supported_handled_correctly == 7
    assert report.supported_question_accuracy_pct == 100.0
    assert len(report.failures) == 0
