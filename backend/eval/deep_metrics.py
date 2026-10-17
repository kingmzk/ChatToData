"""DeepEval metrics for exact governed analytics, without an LLM judge.

Numbers and disclosure decisions have objective oracles; a judge model must not
override them. Inputs are normalized JSON snapshots of synthetic eval turns.
"""
import json
import os
from decimal import Decimal

os.environ.setdefault('DEEPEVAL_TELEMETRY_OPT_OUT', 'YES')
from deepeval.metrics import BaseMetric


class ContractMetric(BaseMetric):
    threshold = 1.0
    async_mode = False
    strict_mode = True

    def measure(self, test_case, *args, **kwargs):
        self.error = None
        try:
            actual = json.loads(test_case.actual_output)
            expected = json.loads(test_case.expected_output)
            problems = self.check(actual, expected)
        except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
            problems = ['Invalid evaluation contract: ' + type(exc).__name__]
        self.score = 0.0 if problems else 1.0
        self.success = not problems
        self.reason = '; '.join(problems) if problems else 'Contract satisfied'
        return self.score

    async def a_measure(self, test_case, *args, **kwargs):
        return self.measure(test_case)

    def is_successful(self):
        return self.success

    @property
    def __name__(self):
        return type(self).__name__


class OutcomeMetric(ContractMetric):
    def check(self, actual, expected):
        return [] if actual['status'] == expected['status'] else ['Unexpected turn status']


class IntentMetric(ContractMetric):
    def check(self, actual, expected):
        intent = actual.get('intent', {})
        problems = []
        for key, value in expected.get('intent', {}).items():
            observed = intent.get(key)
            if key in ('dimensions', 'filters'):
                canonical = lambda v: sorted(json.dumps(x, sort_keys=True) for x in (v or []))
                match = canonical(observed) == canonical(value)
            else:
                match = observed == value
            if not match:
                problems.append('Intent mismatch: ' + key)
        return problems


class ExactFactsMetric(ContractMetric):
    def check(self, actual, expected):
        wanted = expected.get('facts')
        if wanted is None:
            return []
        # Compare complete selected fact sets, including group, period and units.
        types = {f['calculation_type'] for f in wanted}
        observed = [f for f in actual['facts'] if f['calculation_type'] in types]
        if len(observed) != len(wanted):
            return ['Missing or extra analytical facts']
        problems = []
        remaining = list(observed)
        for fact in wanted:
            keys = ('calculation_type', 'period', 'dimension_values', 'unit')
            matches = [f for f in remaining if all(f.get(k) == fact.get(k) for k in keys)]
            if len(matches) != 1:
                problems.append('Fact scope mismatch')
                continue
            found = matches[0]
            remaining.remove(found)
            if fact.get('result_type') and found.get('result_type') != fact['result_type']:
                problems.append('Scientific result type mismatch')
            for key,value in fact.get('diagnostics',{}).items():
                if found.get('diagnostics',{}).get(key) != value:
                    problems.append('Scientific diagnostic mismatch')
            if found['value'] is None or not Decimal(str(found['value'])).is_finite():
                problems.append('Missing or non-finite analytical value')
            elif abs(Decimal(str(found['value'])) - Decimal(str(fact['value']))) > Decimal(str(fact.get('tolerance', '0'))):
                problems.append('Analytical value mismatch')
        return problems


class GovernanceMetric(ContractMetric):
    def check(self, actual, expected):
        problems = []
        if actual['status'] == 'ANSWERED' and not actual.get('integrity'):
            problems.append('Evidence seal invalid or missing')
        if actual['status'] in ('DECLINED', 'AWAITING_CLARIFICATION', 'UNAUTHORIZED') and actual['facts']:
            problems.append('Unexecuted turn exposed facts')
        if expected.get('suppressed'):
            if not actual.get('suppressed') or not actual['facts']:
                problems.append('Expected disclosure suppression')
            for fact in actual['facts']:
                if fact['value'] is not None or not fact['is_suppressed'] or fact.get('diagnostics') not in ({}, {'suppression_status':'SUPPRESSED'}) or fact.get('prediction_interval'):
                    problems.append('Suppressed fact leaked details')
        return problems


def metrics():
    return [OutcomeMetric(), IntentMetric(), ExactFactsMetric(), GovernanceMetric()]
