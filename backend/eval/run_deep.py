"""Run exact synthetic DeepEval contracts; live calls and LangSmith are explicit.

Usage: python -m talk2data.eval.run_deep [--live --model MODEL] [--langsmith]
"""
import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from dataclasses import asdict
from backend.eval.cases import cases


def run(live=False, model=None, limit=None, publish=False, output='eval-results/deep-eval.json', selected=None):
    os.environ.setdefault('DEEPEVAL_TELEMETRY_OPT_OUT', 'YES')
    from dotenv import load_dotenv
    load_dotenv()
    # Ordinary regression/eval runs do not upload anything even if .env enables app tracing.
    os.environ['LANGSMITH_TRACING'] = 'true' if publish else 'false'
    from deepeval.test_case import LLMTestCase
    from backend.eval.deep_metrics import metrics
    from backend.demo import initialize
    from backend.join_demo import add_join_demo
    from backend.runtime import load_runtime
    from backend.domain.models import ApiActor
    from backend.security.auth import AuthorizationError
    from backend.lifecycle.session import ConversationLifecycleManager
    from backend.intent.gemini_assist import GeminiIntentAssist

    rows = []
    planned = [c for c in cases() if not selected or c['name'] in selected][:limit]
    if not planned:
        raise ValueError('No matching evaluation cases')
    with tempfile.TemporaryDirectory(prefix='talk2data-eval-') as directory:
        config = initialize(directory)
        add_join_demo(directory)
        raw = json.loads(config.read_text(encoding='utf-8'))
        protected = dict(raw['catalogs'][0]['measures']['revenue'], id='protected_revenue', name='Protected Revenue', min_entities=6)
        raw['catalogs'][0]['measures']['protected_revenue'] = protected
        config.write_text(json.dumps(raw),encoding='utf-8')
        previous = os.environ.get('TALK2DATA_RUNTIME_CONFIG')
        os.environ['TALK2DATA_RUNTIME_CONFIG'] = str(config)
        try:
            security, catalogs, gateway = load_runtime()
            class CapturingAssist(GeminiIntentAssist):
                result = None
                def interpret(self, *args, **kwargs):
                    self.result = super().interpret(*args, max_retries=0, **kwargs)
                    return self.result
            assist = CapturingAssist(model_name=model) if live else None
            manager = ConversationLifecycleManager(security,catalogs,gateway,intent_assist=assist)
            for case in planned:
                started = time.monotonic()
                if assist:
                    assist.result = None
                actor = ApiActor(case.get('tenant','tenant_a'),'eval_user')
                answer = evidence = None
                try:
                    turn,answer = manager.submit_turn(actor,case['name'],case['source'],case['question'],
                        proposed_llm_json=None if live else json.dumps(case['intent']))
                    if case.get('clarify') and turn.clarification_request:
                        choice = turn.clarification_request.options[0].choice_id
                        turn,answer = manager.submit_clarification_choice(actor,turn.turn_id,choice)
                    status = turn.status.value
                    evidence = manager.get_evidence(turn.turn_id)
                except AuthorizationError:
                    status = 'UNAUTHORIZED'
                try:
                    intent = json.loads(assist.result or '{}') if live else case['intent']
                    if not isinstance(intent, dict):
                        intent = {}
                except (ValueError, TypeError):
                    intent = {}
                if intent.get('provider_failure'):
                    rows.append({'case':case['name'],'outcome':'BLOCKED','reason':'Provider unavailable or quota exhausted','model':assist.model_name})
                    # Stop rather than burning quota on an unavailable provider.
                    break
                actual = {'status':status,'intent':intent,'facts':[asdict(f) for f in evidence.facts] if evidence else [],
                          'integrity':evidence.verify_integrity() if evidence else False,
                          'suppressed':bool(answer and answer.is_suppressed)}
                test = LLMTestCase(input=case['question'],actual_output=json.dumps(actual,default=str),
                                   expected_output=json.dumps(case['expected']))
                scores = []
                for metric in metrics():
                    metric.measure(test)
                    scores.append({'metric':metric.__name__,'score':metric.score,'reason':metric.reason})
                passed = all(m['score']==1 for m in scores)
                row = {'case':case['name'],'outcome':'PASS' if passed else 'FAIL','mode':'live' if live else 'fixture',
                       'model':assist.model_name if live else None,'seconds':round(time.monotonic()-started,3),'metrics':scores,'actual':actual}
                if publish:
                    try:
                        publish_scores(case['name'],scores,live,row.get('model'))
                        row['langsmith'] = 'submitted'
                    except Exception:
                        row['langsmith'] = 'unavailable'
                        print('LangSmith score submission unavailable; local result retained',flush=True)
                rows.append(row)
                print(case['name'],row['outcome'],flush=True)
                if not passed:
                    print(json.dumps({'scores':scores,'actual':actual},default=str),flush=True)
        finally:
            gateway._conn.close()
            if previous is None:
                os.environ.pop('TALK2DATA_RUNTIME_CONFIG',None)
            else:
                os.environ['TALK2DATA_RUNTIME_CONFIG'] = previous
    report = {'mode':'live' if live else 'fixture','model':model if live else None,'planned':len(planned),
              'passed':sum(r['outcome']=='PASS' for r in rows),'failed':sum(r['outcome']=='FAIL' for r in rows),
              'blocked':sum(r['outcome']=='BLOCKED' for r in rows),'completed':len(rows),'results':rows,
              'scope':'Curated synthetic cases; fixture mode does not measure natural-language accuracy.'}
    path=Path(output);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,default=str,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='results'}),flush=True)
    return report


def publish_scores(name, scores, live, model):
    """Upload case IDs and scores only; synthetic payloads remain local."""
    from langsmith import Client, trace
    client=Client()
    with trace('synthetic_eval',inputs={'case':name},client=client,
               project_name=os.getenv('LANGSMITH_PROJECT','Talk2Data'),
               metadata={'mode':'live' if live else 'fixture','model':model}) as span:
        span.end(outputs={'scores':{s['metric']:s['score'] for s in scores}})
    client.flush()
    project = client.read_project(project_name=os.getenv('LANGSMITH_PROJECT','Talk2Data'))
    for score in scores:
        client.create_feedback(span.id,key=score['metric'],score=score['score'],comment=score['reason'],session_id=project.id)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',action='store_true');parser.add_argument('--model')
    parser.add_argument('--limit',type=int);parser.add_argument('--langsmith',action='store_true')
    parser.add_argument('--output',default='eval-results/deep-eval.json')
    parser.add_argument('--cases',help='Comma-separated case IDs for a bounded targeted run')
    args=parser.parse_args()
    report=run(args.live,args.model,args.limit,args.langsmith,args.output,args.cases.split(',') if args.cases else None)
    raise SystemExit(1 if report['failed'] else 2 if report['blocked'] else 0)
