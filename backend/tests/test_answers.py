"""Deterministic regression fixtures: no embedding downloads or Ollama required."""
import json
import unittest
from unittest.mock import patch, Mock

from pydantic import ValidationError
from app.services.answer_service import generate_answer
from app.services.calculation_plan_service import CalculationPlan, route_question
from app.services.calculation_executor import execute_calculation_plan, normalize_number, CalculationFailure


def chunk(text, id=1):
    return {'chunk_id': id, 'page': 1, 'text': text}


def amount(value, quote, source='document', unit='days', period='annual', token=None, id=1):
    return dict(value=str(value), source=source, chunk_id=id if source == 'document' else None,
                quote=quote, number_text=token or str(value), unit=unit, period=period)


def balance():
    doc = 'Annual sick leave entitlement is 10 days.'
    q = 'I used 3 days of annual sick leave. How many days remain?'
    return q, [chunk(doc)], dict(kind='remaining_balance', entitlement=amount(10, doc),
        used=amount(3, 'I used 3 days of annual sick leave.', 'question'))


def reimbursement(actual=850, cap=1000, category='Internet', period='monthly'):
    doc = f'{category} cap is INR {cap} {period}.'
    quote = f'Actual {category} cost is INR {actual} {period}.'
    return quote + ' How much can I claim?', [chunk(doc)], dict(kind='capped_reimbursement', categories=[
        dict(category=category, actual=amount(actual, quote, 'question', 'INR', period),
             cap=amount(cap, doc, unit='INR', period=period))])


class Answers(unittest.TestCase):
    def answer(self, fixture):
        q, docs, data = fixture
        with patch('app.services.answer_service.create_calculation_plan', return_value=CalculationPlan.model_validate(data)):
            return generate_answer(q, docs)

    def test_A_balance(self):
        self.assertIn('10 − 3 = 7 days', self.answer(balance()))
        self.assertNotIn('7 = 7', self.answer(balance()))

    def test_E_separate_caps(self):
        categories, docs, quotes = [], [], []
        for name, actual, cap, period in [('Hotel', 3500, 3000, 'per_night'), ('Meal', 450, 600, 'per_day')]:
            phrase = 'per night' if period == 'per_night' else 'per day'
            doc = f'{name} cap is INR {cap} {phrase}.'
            quote = f'Actual {name} cost is INR {actual} {phrase}.'
            docs.append(chunk(doc, len(docs)+1)); quotes.append(quote)
            categories.append(dict(category=name, actual=amount(actual, quote, 'question', 'INR', period),
                cap=amount(cap, doc, unit='INR', period=period)))
        answer = self.answer((' '.join(quotes)+' For one approved day and one approved night, how much reimbursement?', docs,
                             dict(kind='capped_reimbursement', categories=categories)))
        self.assertIn('3000 + 450 = 3450 INR', answer)

    def test_F_G_internet(self):
        self.assertIn('= 850 INR', self.answer(reimbursement()))
        self.assertIn('= 1000 INR', self.answer(reimbursement(1400)))

    def test_direct_B_C_D_H_I_J_L(self):
        cases = [
            ('Can I carry all 8 annual days forward?', 'Up to five annual days may be carried forward.', 'No. The maximum is five annual days.'),
            ('When will my stolen laptop replacement arrive?', 'Report a stolen laptop immediately.', 'Report immediately. The replacement timeline is unspecified in the excerpts.'),
            ('Can I work remotely every weekday?', 'Employees may work remotely two days per week.', 'The arrangement allows two remote days per week.'),
            ('Will I be paid in seven working days?', 'Claims are reviewed in seven working days.', 'Seven working days is the review period; payment is not guaranteed by then.'),
            ('What is the onboarding checklist?', 'Onboarding requires ID and bank details.', 'Provide ID and bank details.'),
            ('What is my salary?', 'Employees receive annual leave.', 'Salary information was not found in the supplied excerpts.'),
            ('What skills are on this resume?', 'Skills: Python, React, SQL.', 'Python, React and SQL.'),
        ]
        for q, doc, expected in cases:
            with self.subTest(q=q), patch('app.services.answer_service.create_calculation_plan') as extraction, patch('app.services.answer_service.httpx.Client') as client:
                response = Mock(); response.json.return_value = {'message': {'content': expected}}
                client.return_value.__enter__.return_value.post.return_value = response
                self.assertEqual(route_question(q), 'direct')
                self.assertEqual(generate_answer(q, [chunk(doc)]), expected)
                extraction.assert_not_called()

    def test_K_invalid_inputs(self):
        for mutate in [lambda d: d['entitlement'].update(quote='Invented entitlement 10 days.'),
                       lambda d: d['used'].update(value='NaN'),
                       lambda d: d['used'].update(value='1e3'),
                       lambda d: d.update(entitlement=d['used'], used=d['entitlement']),
                       lambda d: d['entitlement'].update(number_text='3', value='3'),
                       lambda d: d['used'].update(period='monthly')]:
            q, docs, data = balance(); mutate(data)
            with self.subTest(data=data), self.assertRaises((CalculationFailure, ValidationError)):
                execute_calculation_plan(CalculationPlan.model_validate(data), q, docs)

    def test_wrong_chunk_resolved_duplicate_quotes(self):
        q, docs, data = balance()
        data['entitlement']['chunk_id'] = 999
        docs.append(chunk(docs[0]['text'], 2))
        result = execute_calculation_plan(CalculationPlan.model_validate(data), q, docs)
        self.assertEqual(result['inputs'][0]['chunk_id'], 1)
        self.assertNotIn('Chunk 999', result['answer'])

    def test_number_normalization(self):
        for token, expected in [('five', 5), ('seven', 7), ('1,000', 1000), ('one thousand', 1000)]:
            self.assertEqual(normalize_number(token), expected)
        for token in ['twenty five', 'five-ish', '1,00', 'NaN', '-3', '1e3', 'Infinity']:
            with self.assertRaises(CalculationFailure): normalize_number(token)
        q, docs, data = balance()
        docs[0]['text'] = data['entitlement']['quote'] = 'Annual sick leave entitlement is ten days.'
        data['entitlement']['number_text'] = 'ten'
        self.assertIn('= 7 days', self.answer((q, docs, data)))

    def test_failure_classes(self):
        q, docs, data = balance(); data['used'] = None
        self.assertIn('Invalid model response:', self.answer((q, docs, data)))
        with patch('app.services.answer_service.create_calculation_plan', side_effect=ValueError('bad JSON')):
            self.assertIn('Invalid model response:', generate_answer(q, docs))
        self.assertIn('Unsupported calculation:', generate_answer('Calculate salary divided by hours', docs))

    def test_arbitrary_steps_rejected(self):
        q, docs, data = balance(); data['steps'] = [{'operation': 'subtract'}]
        with self.assertRaises(ValidationError): CalculationPlan.model_validate(data)



class AdditionalValidation(unittest.TestCase):
    def test_handbook_exact_sick_phrase(self):
        q, docs, data = balance()
        docs = [chunk('Employees receive 24 paid annual leave days and 10 sick leave days per calendar year.')]
        data['entitlement']['quote'] = '10 sick leave days per calendar year'
        self.assertEqual(execute_calculation_plan(CalculationPlan.model_validate(data), q, docs)['result'], '7')

    def test_unsupported_word_phrase_cannot_be_truncated(self):
        q, docs, data = balance()
        for phrase, value, token in [('two hundred', '2', 'two'), ('twenty-five', '5', 'five'), ('one thousand', '1', 'one')]:
            with self.subTest(phrase=phrase), self.assertRaises(CalculationFailure):
                docs[0]['text'] = data['entitlement']['quote'] = f'Annual sick leave entitlement is {phrase} days.'
                data['entitlement'].update(value=value, number_text=token)
                execute_calculation_plan(CalculationPlan.model_validate(data), q, docs)

    def test_extraction_http_schema_and_invalid_json(self):
        from app.services.calculation_plan_service import create_calculation_plan
        q, docs, data = balance()
        with patch('app.services.calculation_plan_service.httpx.Client') as client:
            response = Mock(); response.json.return_value = {'message': {'content': json.dumps(data)}}
            client.return_value.__enter__.return_value.post.return_value = response
            plan = create_calculation_plan(q, docs, 'remaining_balance')
            self.assertEqual(execute_calculation_plan(plan, q, docs)['result'], '7')
            payload = client.return_value.__enter__.return_value.post.call_args.kwargs['json']
            self.assertNotIn('steps', payload['format']['properties'])
            response.json.return_value = {'message': {'content': '{broken'}}
            self.assertIn('Invalid model response:', generate_answer(q, docs))

    def test_units_periods_and_unapproved_multiple_nights(self):
        q, docs, data = reimbursement()
        data['categories'][0]['actual']['unit'] = 'USD'
        with self.assertRaises(CalculationFailure):
            execute_calculation_plan(CalculationPlan.model_validate(data), q, docs)
        q, docs, data = reimbursement(period='per_night')
        with self.assertRaises(CalculationFailure):
            execute_calculation_plan(CalculationPlan.model_validate(data), q + ' for two nights', docs)

    def test_missing_retrieval_is_not_document_absence(self):
        self.assertIn('Missing evidence:', generate_answer(balance()[0], []))
