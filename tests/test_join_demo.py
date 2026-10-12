import json
from decimal import Decimal
import pytest
from talk2data.demo import initialize
from talk2data.join_demo import add_join_demo
from talk2data.runtime import load_runtime
from talk2data.domain.models import ApiActor, TurnStatus
from talk2data.lifecycle.session import ConversationLifecycleManager

@pytest.mark.parametrize('intent,expected', [
 ({'measure':'revenue','family':'TOTAL','dimensions':['region'],'current_period':'2024'}, ['700.00','500.00']),
 ({'measure':'revenue','family':'TOTAL','dimensions':['region'],'filters':[{'dimension':'segment','operator':'=','values':['Business']}],'current_period':'2024'}, ['350.00','250.00']),
 ({'measure':'revenue','family':'COMPARISON','dimensions':['region'],'current_period':'2024','comparison_period':'2023'}, ['40.00','25.00']),
 ({'measure':'average_sale','family':'AVERAGE','filters':[{'dimension':'region','operator':'=','values':['North']}],'current_period':'2024'}, ['70.00']),
 ({'measure':'revenue','family':'CONTRIBUTION','dimensions':['region'],'current_period':'2024','comparison_period':'2023'}, ['66.67','33.33']),
])
def test_join_demo_pipeline(tmp_path, monkeypatch, intent, expected):
    config = initialize(tmp_path)
    add_join_demo(tmp_path)
    monkeypatch.setenv('TALK2DATA_RUNTIME_CONFIG', str(config))
    monkeypatch.setenv('TALK2DATA_ENV', 'development')
    security, catalogs, gateway = load_runtime()
    manager = ConversationLifecycleManager(security, catalogs, gateway)
    turn, answer = manager.submit_turn(ApiActor('tenant_a','join_tester'), 'join_test', 'demo_join_sales', 'Synthetic join verification', proposed_llm_json=json.dumps(intent))
    assert turn.status == TurnStatus.ANSWERED
    assert not answer.is_suppressed
    rendered = str(answer)
    for value in expected:
        assert value in rendered
    assert '1000000' not in rendered
    evidence = manager.get_evidence(turn.turn_id)
    assert all(f.value is None or abs(f.value) < Decimal('10000') for f in evidence.facts)
