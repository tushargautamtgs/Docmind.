"""Local plumbing/validator tests; these do not validate real model behavior."""
import json
import os
import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.services.answer_service import generate_answer
from app.services.calculation_plan_service import CalculationPlan, extraction_schema, route_question
from app.services.calculation_executor import execute_calculation_plan
from test_answers import amount, chunk

FIXTURES = json.loads((Path(__file__).parent / 'fixtures/observed_failures.json').read_text())


class Diagnostics(unittest.TestCase):
    def test_observed_routes(self):
        for case in FIXTURES:
            self.assertEqual(route_question(case['question']), case['route'])

    def test_observed_usage_wording_validates(self):
        q = FIXTURES[0]['question']
        doc = '10 sick leave days per calendar year'
        plan = CalculationPlan.model_validate(dict(kind='remaining_balance',
            entitlement=amount(10, doc), used=amount(3, 'I used 3 sick leaves this year.', 'question')))
        result = execute_calculation_plan(plan, q, [chunk(doc)])
        self.assertEqual(result['result'], '7')
        self.assertIn('Assumes no other usage', result['answer'])

    def test_schema_requires_routed_inputs(self):
        schema = extraction_schema('remaining_balance')
        self.assertTrue({'entitlement', 'used'} <= set(schema['required']))
        self.assertEqual(schema['properties']['kind']['const'], 'remaining_balance')

    def test_real_observed_extraction_omission_is_not_missing_evidence(self):
        # Exact structured output captured from real qwen3:1.7b on 2026-10-29.
        raw = (Path(__file__).parent / 'fixtures/real_sick_extraction.json').read_text()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'diagnostics.jsonl'
            with patch.dict(os.environ, DOCMIND_DIAGNOSTICS_PATH=str(path)), patch(
                    'app.services.calculation_plan_service.httpx.Client') as client:
                response = Mock(); response.json.return_value = {'message': {'content': raw}}
                client.return_value.__enter__.return_value.post.return_value = response
                answer = generate_answer(FIXTURES[0]['question'], [chunk('10 sick leave days per calendar year')])
            self.assertTrue(answer.startswith('Invalid model response:'))
            entries = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len({e['request_id'] for e in entries}), 1)
            events = {e['event']: e for e in entries}
            self.assertEqual(events['retrieved_chunks']['chunk_ids'], [1])
            self.assertEqual(events['raw_structured_extraction']['raw'], raw)
            self.assertIsNone(events['parsed_extraction']['parsed']['used'])
            self.assertIn('omitted', events['validation_failure']['detail'])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_malformed_extraction_records_parse_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'diagnostics.jsonl'
            with patch.dict(os.environ, DOCMIND_DIAGNOSTICS_PATH=str(path)), patch(
                    'app.services.calculation_plan_service.httpx.Client') as client:
                response = Mock(); response.json.return_value = {'message': {'content': '{broken'}}
                client.return_value.__enter__.return_value.post.return_value = response
                answer = generate_answer(FIXTURES[0]['question'], [chunk('private text')])
            self.assertNotIn('private text', answer)
            events = {e['event']: e for e in map(json.loads, path.read_text().splitlines())}
            self.assertIsNone(events['parsed_extraction']['parsed'])
            self.assertIn('detail', events['validation_failure'])


    def test_recorded_real_results_expose_unresolved_direct_failures(self):
        records = json.loads((Path(__file__).parent / 'fixtures/real_model_observed_results.json').read_text())
        answers = {r['case_id']: r['answer'] for r in records if r['event'] == 'case_result'}
        outcomes = []
        for case in FIXTURES:
            answer = answers[case['id']]
            passed = all(re.search(p, answer, re.I | re.S) for p in case['required_patterns'])
            passed = passed and not any(re.search(p, answer, re.I | re.S) for p in case['forbidden_patterns'])
            outcomes.append(bool(passed))
        self.assertEqual(outcomes, [True, False, False])
