import json
import pytest
pytest.importorskip('deepeval')
from deepeval.test_case import LLMTestCase
from talk2data.eval.deep_metrics import ExactFactsMetric, IntentMetric, GovernanceMetric
from talk2data.eval.cases import fact


def score(metric,actual,expected):
    return metric.measure(LLMTestCase(input='synthetic',actual_output=json.dumps(actual),expected_output=json.dumps(expected)))


@pytest.mark.parametrize('mutation', ['wrong_value','wrong_region','wrong_period','wrong_unit','extra_fact','nonfinite','result_type','diagnostic'])
def test_exact_metric_rejects_corrupted_science_or_scope(mutation):
    original=dict(fact('FORECAST',200,'2025','North'),result_type='PREDICTED',diagnostics={'step':'1'})
    observed=dict(original)
    actual={'facts':[observed]}
    expected={'facts':[original]}
    assert score(ExactFactsMetric(),actual,expected)==1
    if mutation=='wrong_value': observed['value']='201'
    if mutation=='wrong_region': observed['dimension_values']={'region':'South'}
    if mutation=='wrong_period': observed['period']='2026'
    if mutation=='wrong_unit': observed['unit']='EUR'
    if mutation=='extra_fact': actual['facts'].append(dict(observed))
    if mutation=='nonfinite': observed['value']='NaN'
    if mutation=='result_type': observed['result_type']='OBSERVED'
    if mutation=='diagnostic': observed['diagnostics']={'step':'2'}
    assert score(ExactFactsMetric(),actual,expected)==0


def test_intent_metric_detects_join_filter_error():
    expected={'intent':{'filters':[{'dimension':'segment','operator':'=','values':['Business']}]}}
    assert score(IntentMetric(),{'intent':{}},expected)==0


def test_governance_metric_rejects_suppressed_diagnostic_leak():
    actual={'status':'ANSWERED','integrity':True,'suppressed':True,'facts':[{'value':None,'is_suppressed':True,'diagnostics':{'suppression_status':'SUPPRESSED'},'prediction_interval':None}]}
    assert score(GovernanceMetric(),actual,{'suppressed':True})==1
    actual['facts'][0]['diagnostics']['hidden_amount']='180'
    assert score(GovernanceMetric(),actual,{'suppressed':True})==0


def test_governance_metric_rejects_invalid_seal_and_declined_facts():
    assert score(GovernanceMetric(),{'status':'ANSWERED','integrity':False,'facts':[]},{})==0
    assert score(GovernanceMetric(),{'status':'DECLINED','facts':[{'value':1}]},{})==0


def test_full_synthetic_deep_eval_suite(tmp_path, monkeypatch):
    monkeypatch.setenv('LANGSMITH_TRACING','false')
    from talk2data.eval.run_deep import run
    report=run(output=str(tmp_path/'report.json'))
    assert report['planned']==19
    assert report['passed']==19
    assert report['failed']==report['blocked']==0
    assert all(r['mode']=='fixture' and r['model'] is None for r in report['results'])
