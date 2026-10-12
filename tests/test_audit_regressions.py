"""Regression coverage for failures identified by the implementation audit."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from talk2data.api.app import create_app
from talk2data.domain.models import TurnStatus
from talk2data.intent.parser import IntentParser
from talk2data.intent.schema import AnalysisFamily, StructuredIntent
from talk2data.jobs.worker import DurableJobWorker
from talk2data.lifecycle.session import ConversationLifecycleManager
from talk2data.security.auth import AuthorizationError
from talk2data.storage.repository import PersistentApplicationStore, LeaseFencingError
from talk2data.science.forecast import GovernedForecastEngine, ForecastingEligibilityError
from talk2data.catalog.schema import TimeSeriesPolicy


def configured_worker(setup, store, manager=None):
    return DurableJobWorker(store, manager or setup['manager'], setup['security'])


def test_question_calls_real_interpreter_boundary(test_setup):
    class Interpreter:
        def interpret(self, vocabulary, question):
            assert question == 'Revenue in 2024?'
            assert 'sales_revenue' in vocabulary.measures
            return json.dumps({'measure': 'sales_revenue', 'family': 'TOTAL', 'current_period': '2024'})
    manager = test_setup['manager']
    manager.intent_assist = Interpreter()
    turn, answer = manager.submit_turn(test_setup['actor_a'], 'conv_nl', 'finance_dw', 'Revenue in 2024?')
    assert turn.status == TurnStatus.ANSWERED
    assert '150,000' in answer.summary_text


def test_atomic_submission_across_independent_connections(tmp_path, test_setup):
    actor = test_setup['actor_a']
    path = str(tmp_path / 'jobs.db')
    first = PersistentApplicationStore(path)
    conversation = first.create_conversation(actor)
    second = PersistentApplicationStore(path)
    def submit(store):
        return store.submit_question_atomic(actor, conversation, 'finance_dw', 'Revenue?', 'same-key', '{}')
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(submit, [first, second]))
    assert results[0][0]['job_id'] == results[1][0]['job_id']
    assert results[0][1]['turn_id'] == results[1][1]['turn_id']
    assert sum(result[2] for result in results) == 1
    assert first.conn.execute('SELECT COUNT(*) FROM turns').fetchone()[0] == 1
    with pytest.raises(ValueError):
        first.submit_question_atomic(actor, conversation, 'finance_dw', 'Other?', 'same-key', '{}')


def test_clarification_survives_restart_and_keeps_original_release(tmp_path, test_setup):
    actor, catalogs = test_setup['actor_a'], test_setup['catalogs']
    store = PersistentApplicationStore(str(tmp_path / 'restart.db'))
    conversation = store.create_conversation(actor)
    job, row, _ = store.submit_question_atomic(actor, conversation, 'finance_dw', 'Show sales growth.', 'question',
        json.dumps({'measure': 'sales_revenue', 'family': 'COMPARISON'}))
    configured_worker(test_setup, store).process_next_job()
    row = store.get_turn(actor, row['turn_id'])
    assert row['status'] == 'AWAITING_CLARIFICATION'
    original_release = row['pinned_release_id']
    choice = row['clarification_request']['options'][0]['choice_id']
    catalogs.register_catalog(replace(catalogs.get_catalog('finance_dw'), release_id='new_release', epoch=2))
    restarted = PersistentApplicationStore(str(tmp_path / 'restart.db'))
    manager = ConversationLifecycleManager(test_setup['security'], catalogs, test_setup['gateway'])
    restarted.submit_job_idempotent(actor, row['turn_id'], 'SUBMIT_CLARIFICATION', 'choice', {'choice_id': choice})
    configured_worker(test_setup, restarted, manager).process_next_job()
    result = restarted.get_turn(actor, row['turn_id'])
    assert result['status'] == 'ANSWERED'
    assert result['pinned_release_id'] == original_release
    assert restarted.get_analytical_context(actor, conversation)['measure'] == 'sales_revenue'
    assert restarted.get_published_answer(actor, row['turn_id'])['version'] == 2


def test_distinct_entity_threshold_applies_in_actual_pipeline(test_setup):
    catalogs = test_setup['catalogs']
    catalog = catalogs.get_catalog('finance_dw')
    measures = dict(catalog.measures)
    measures['sales_revenue'] = replace(measures['sales_revenue'], protected_entity_column='customer_id', min_entities=3)
    catalogs.register_catalog(replace(catalog, release_id="protected_release", measures=measures))
    turn, answer = test_setup['manager'].submit_turn(test_setup['actor_a'], 'privacy', 'finance_dw', 'Revenue in 2024?',
        proposed_llm_json=json.dumps({'measure': 'sales_revenue', 'family': 'TOTAL', 'current_period': '2024'}))
    assert turn.status == TurnStatus.ANSWERED
    assert answer.is_suppressed
    assert '150,000' not in answer.summary_text
    assert all(f.value is None for f in test_setup['manager'].get_evidence(turn.turn_id).facts)


def test_answer_permission_revocation_in_api(test_setup):
    store = PersistentApplicationStore()
    app = create_app(store, test_setup['manager'], test_setup['security'], test_setup['catalogs'])
    client = TestClient(app)
    conversation = client.post('/api/v1/conversations', json={}).json()['conversation_id']
    result = client.post(f'/api/v1/conversations/{conversation}/questions', json={'source_id': 'finance_dw',
        'question': 'Revenue in 2024?', 'proposed_llm_json': json.dumps({'measure': 'sales_revenue', 'family': 'TOTAL', 'current_period': '2024'})})
    turn = result.json()['turn_id']
    assert client.get(f'/api/v1/turns/{turn}/answer').status_code == 200
    test_setup['security'].revoke_tenant_source('tenant_a', 'finance_dw')
    assert client.get(f'/api/v1/turns/{turn}/answer').status_code == 403
    assert client.get(f'/api/v1/turns/{turn}/export/csv').status_code == 403


def test_cancelled_job_cannot_overwrite_terminal_state(test_setup):
    store = PersistentApplicationStore()
    actor = test_setup['actor_a']
    conversation = store.create_conversation(actor)
    job, row, _ = store.submit_question_atomic(actor, conversation, 'finance_dw', 'Revenue?', 'q', '{}')
    claimed = store.claim_next_job('worker')
    turn = store.restore_execution_state(actor, row['turn_id'], test_setup['manager'])
    assert store.cancel_job_and_turn(actor, turn.turn_id)
    turn.status = TurnStatus.ANSWERED
    with pytest.raises(LeaseFencingError):
        store.save_turn(turn, actor, job['job_id'], 'worker', claimed['fencing_token'])
    with pytest.raises(LeaseFencingError):
        store.fail_job(job['job_id'], 'late failure', 'worker', claimed['fencing_token'])
    assert store.get_turn(actor, turn.turn_id)['status'] == 'CANCELLED'


@pytest.mark.parametrize('frequency,periods', [('MONTH',['2024-01','2024-02','2024-04','2024-05']),
                                            ('QUARTER',['2023-Q1','2023-Q2','2023-Q4','2024-Q1'])])
def test_nonannual_gaps_are_rejected(frequency, periods):
    policy = TimeSeriesPolicy('revenue', 'period', frequency=frequency)
    with pytest.raises(ForecastingEligibilityError, match='Missing historical period'):
        GovernedForecastEngine().forecast('Revenue', 'USD', [(p, Decimal(i)) for i,p in enumerate(periods)], 1, policy)


def test_month_projection_and_explicit_zero_fill():
    policy = TimeSeriesPolicy('revenue', 'period', frequency='MONTH', allow_missing_as_zero=True)
    series = [('2024-09',Decimal('1')),('2024-10',Decimal('2')),('2024-12',Decimal('4'))]
    result = GovernedForecastEngine().forecast('Revenue', 'USD', series, 1, policy)
    assert result.facts[0].period == '2025-01'
    assert result.training_end == '2024-12'


@pytest.mark.parametrize('payload', [{'family':'UNKNOWN'}, {'horizon':'two'}, {'sql':'SELECT 1'}, {'bounds':[1]}])
def test_untrusted_intent_schema_rejects_invalid_types(payload):
    assert not IntentParser().parse_with_bounded_retry(json.dumps(payload)).success


def test_filter_id_resolves_actual_catalog_column(test_setup):
    catalog = test_setup['catalogs'].get_catalog('finance_dw')
    dimensions = dict(catalog.dimensions)
    dimensions['nation'] = replace(dimensions['country'], id='nation')
    catalog = replace(catalog, dimensions=dimensions)
    from talk2data.intent.schema import FilterSpec
    intent = StructuredIntent(measure='sales_revenue', family=AnalysisFamily.TOTAL,
        current_period='2024', filters=(FilterSpec('nation','IN',('US',)),))
    manager = test_setup['manager']
    pin = test_setup['catalogs'].pin('tenant_a', 'finance_dw')
    plan = manager.resolver.resolve(intent, catalog, pin, 'alias', 'tenant_a')
    package = manager.compiler.compile(plan)
    rows = test_setup['gateway'].execute_package(package)
    assert Decimal(rows[0]['metric_value']) == Decimal('80000')


def test_runtime_config_is_shared_and_source_is_read_only(tmp_path, monkeypatch):
    from talk2data.demo import initialize
    from talk2data.runtime import load_runtime
    config = initialize(tmp_path / 'demo')
    monkeypatch.setenv('TALK2DATA_RUNTIME_CONFIG', str(config))
    monkeypatch.setenv('TALK2DATA_ENV', 'development')
    sec, catalogs, gateway = load_runtime()
    assert catalogs.get_catalog('demo_sales').release_id == 'demo_v1'
    with pytest.raises(sqlite3.OperationalError):
        gateway._conn.execute('DELETE FROM sales')
    app = create_app(store=PersistentApplicationStore())
    assert not app.state.run_embedded_worker
    assert app.state.catalog_store.get_catalog('demo_sales').release_id == 'demo_v1'


def test_unsupported_cross_query_dataset_is_disabled(test_setup):
    catalog = test_setup['catalogs'].get_catalog('finance_dw')
    measures = dict(catalog.measures)
    measures['sales_revenue'] = replace(measures['sales_revenue'], requires_repeated_query_protection=True)
    test_setup['catalogs'].register_catalog(replace(catalog, release_id="protected_release", measures=measures))
    turn, answer = test_setup['manager'].submit_turn(test_setup['actor_a'], 'cross_query', 'finance_dw', 'Revenue?',
        proposed_llm_json=json.dumps({'measure':'sales_revenue','family':'TOTAL'}))
    assert turn.status == TurnStatus.DECLINED
    assert answer is None


def test_semantic_release_cannot_be_mutated(test_setup):
    catalogs = test_setup['catalogs']
    catalog = catalogs.get_catalog('finance_dw')
    with pytest.raises(ValueError, match='immutable'):
        catalogs.register_catalog(replace(catalog, epoch=99))
    catalog.measures.clear()
    assert catalogs.get_catalog('finance_dw').measures


def test_standalone_worker_uses_shared_runtime(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys
    from talk2data.demo import initialize
    config = initialize(tmp_path / 'demo')
    monkeypatch.setenv('TALK2DATA_RUNTIME_CONFIG', str(config))
    monkeypatch.setenv('TALK2DATA_ENV', 'development')
    path = str(tmp_path / 'app.sqlite')
    store = PersistentApplicationStore(path)
    app = create_app(store=store)
    client = TestClient(app)
    conversation = client.post('/api/v1/conversations', json={}).json()['conversation_id']
    response = client.post(f'/api/v1/conversations/{conversation}/questions', json={
        'source_id': 'demo_sales', 'question': 'Revenue in 2024?',
        'proposed_llm_json': json.dumps({'measure': 'revenue', 'family': 'TOTAL', 'current_period': '2024'})})
    assert response.status_code == 202
    turn_id = response.json()['turn_id']
    assert response.json()['status'] == 'SUBMITTED'
    result = subprocess.run([sys.executable, '-m', 'talk2data.jobs.daemon', '--db-path', path, '--max-jobs', '1'],
        capture_output=True, text=True, timeout=15, env=os.environ.copy())
    assert result.returncode == 0, result.stderr
    assert client.get(f'/api/v1/turns/{turn_id}').json()['status'] == 'ANSWERED'
    assert '180' in client.get(f'/api/v1/turns/{turn_id}/answer').json()['summary_text']


def test_grouped_comparison_keeps_each_category(test_setup):
    manager = test_setup['manager']
    turn, answer = manager.submit_turn(test_setup['actor_a'], 'grouped', 'finance_dw', 'Compare revenue by country in 2024 and 2023',
        proposed_llm_json=json.dumps({'measure':'sales_revenue','family':'COMPARISON','dimensions':['country'],'current_period':'2024','comparison_period':'2023'}))
    assert turn.status == TurnStatus.ANSWERED
    delta = {f.dimension_values['country']: f.value for f in manager.get_evidence(turn.turn_id).facts if f.calculation_type == 'DELTA'}
    assert delta == {'US':Decimal('10000'), 'Germany':Decimal('40000')}
    assert len(answer.table_rows) == 2
    assert not answer.kpi_cards
    assert "by category" in answer.summary_text


def test_average_does_not_relabel_sum(test_setup):
    turn, answer = test_setup['manager'].submit_turn(test_setup['actor_a'], 'average', 'finance_dw', 'Average revenue?',
        proposed_llm_json=json.dumps({'measure':'sales_revenue','family':'AVERAGE'}))
    assert turn.status == TurnStatus.DECLINED
    assert answer is None


def test_comparison_period_dimension_is_not_a_category(test_setup):
    turn, answer = test_setup['manager'].submit_turn(test_setup['actor_a'], 'period_comparison', 'finance_dw', 'Compare revenue',
        proposed_llm_json=json.dumps({'measure':'sales_revenue','family':'COMPARISON','dimensions':['period'],'current_period':'2024','comparison_period':'2023'}))
    assert turn.status == TurnStatus.ANSWERED
    assert any(card.delta == '+50,000.00' and card.growth_percent == '+50.00%' for card in answer.kpi_cards)


def test_missing_category_chart_value_is_null():
    from talk2data.presentation.charts import ChartGenerator
    from talk2data.domain.facts import ProtectedFact
    fact = ProtectedFact('missing','Revenue','COMPARISON',None,unit='USD',dimension_values={'country':'Missing'})
    chart = ChartGenerator().generate_charts([fact])[0]
    assert chart['values'] == [None]
    assert 'unavailable' in chart['tooltips'][0]
