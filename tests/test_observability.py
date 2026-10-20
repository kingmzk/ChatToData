import pytest
from backend.observability import traced_stage


def test_trace_contains_only_stage_status(monkeypatch):
    monkeypatch.setenv('LANGSMITH_TRACING','true')
    captured = {}
    class Span:
        def end(self, **kwargs):
            captured.update(kwargs)
    class Context:
        def __enter__(self):
            return Span()
        def __exit__(self,*args):
            captured['exit'] = args
    def trace(name, **kwargs):
        captured['name'] = name
        captured['config'] = kwargs
        return Context()
    monkeypatch.setattr('langsmith.trace',trace)
    @traced_stage('test_stage')
    def operation(question, key):
        return {'salary':99999,'secret':key}
    result=operation('private question','private key')
    assert result['salary']==99999
    assert captured['config']['inputs']=={}
    assert captured['outputs']=={'status':'completed'}
    assert 'private' not in str(captured)
    assert '99999' not in str(captured)


def test_trace_outage_never_repeats_operation(monkeypatch):
    monkeypatch.setenv('LANGSMITH_TRACING','true')
    def unavailable(*args,**kwargs):
        raise RuntimeError('telemetry failure')
    monkeypatch.setattr('langsmith.trace',unavailable)
    calls=[]
    @traced_stage('query')
    def operation():
        calls.append(1)
        return 12
    assert operation()==12
    assert calls==[1]


def test_application_exception_not_exported(monkeypatch):
    monkeypatch.setenv('LANGSMITH_TRACING','true')
    captured={}
    class Span:
        def end(self,**kwargs):
            captured.update(kwargs)
    class Context:
        def __enter__(self): return Span()
        def __exit__(self,*args): captured['exit']=args
    monkeypatch.setattr('langsmith.trace',lambda *a,**kw:Context())
    @traced_stage('query')
    def operation(): raise ValueError('secret SQL value')
    with pytest.raises(ValueError,match='secret SQL value'):
        operation()
    assert 'secret' not in str(captured)
    assert captured['exit']==(None,None,None)
