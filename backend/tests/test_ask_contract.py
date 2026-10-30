"""Exercise the actual FastAPI /ask route with controlled retrieval and model output."""
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from app.main import app
from app.services.calculation_plan_service import CalculationPlan, route_question
from test_answers import balance


class AskContract(unittest.TestCase):
    def test_calculation_response_and_retrieved_passages(self):
        q, docs, data = balance()
        identifier = 'd4ba513297b842c5a74283f1184057a7'
        docs[0].update(document_id=identifier, score=0.8)
        docs.append(dict(chunk_id=2, page=2, document_id=identifier,
                         score=0.2, text='An unrelated retrieved passage.'))
        with patch('app.api.documents.search_document', return_value=docs), patch(
            'app.services.answer_service.create_calculation_plan', return_value=CalculationPlan.model_validate(data)
        ), TestClient(app) as client:
            response = client.post(f'/documents/{identifier}/ask', json={'question': q})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {'document_id', 'question', 'answer', 'retrieved_sources'})
        self.assertEqual(body['document_id'], identifier)
        self.assertEqual(body['question'], q)
        self.assertEqual(len(body['retrieved_sources']), 2)
        self.assertEqual(set(body['retrieved_sources'][0]), {'document_id', 'chunk_id', 'page', 'score', 'text'})
        self.assertIn('= 7 days', body['answer'])
        self.assertNotIn('Chunk 2', body['answer'])

    def test_direct_answer_through_route(self):
        identifier = 'd4ba513297b842c5a74283f1184057a7'
        docs = [dict(chunk_id=5, page=2, document_id=identifier, score=0.9,
                     text='Lost or stolen devices must be reported immediately.')]
        with patch('app.api.documents.search_document', return_value=docs), patch(
            'app.services.answer_service.create_calculation_plan'
        ) as extraction, patch('app.services.answer_service.httpx.Client') as model, TestClient(app) as client:
            response = Mock()
            response.json.return_value = {'message': {'content': 'Report immediately. The replacement timeline is unspecified. [Page 2, Chunk 5]'}}
            model.return_value.__enter__.return_value.post.return_value = response
            result = client.post(f'/documents/{identifier}/ask', json={'question': 'When does my stolen laptop replacement arrive?'})
        self.assertEqual(result.status_code, 200)
        self.assertIn('unspecified', result.json()['answer'])
        extraction.assert_not_called()

    def test_permission_is_direct_even_for_reimbursement(self):
        self.assertEqual(route_question('Can I claim actual internet cost of INR 850 monthly?'), 'direct')
        self.assertEqual(route_question('Does my remaining leave balance permit taking all 8 days?'), 'direct')

    def test_search_errors_do_not_expose_contents(self):
        identifier = 'd4ba513297b842c5a74283f1184057a7'
        with patch('app.api.documents.search_document', side_effect=ValueError('PRIVATE document and question')), TestClient(app) as client:
            response = client.post(f'/documents/{identifier}/ask', json={'question': 'A question'})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('PRIVATE', response.text)


    def test_invalid_question_is_not_echoed_in_error(self):
        identifier = 'd4ba513297b842c5a74283f1184057a7'
        with TestClient(app) as client:
            response = client.post(f'/documents/{identifier}/ask', json={'question': 'PRIVATE' * 300})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('PRIVATE', response.text)
