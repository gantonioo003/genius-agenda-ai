"""API de resumos: Bearer, leitura factual e modelo controlado, sem rede."""

import asyncio
import json
from threading import get_ident
import unittest
from unittest.mock import Mock, patch

from app.llm import ModelError, ModelReply
from app.main import create_app
from app.services.summary_context import SummaryCollectionError, SummaryLimitError, SummarySetupError
from app.settings import Settings
from test_api_security import InjectedDatabase, TOKEN, WRONG_TOKEN, invoke_asgi
from test_summary_context_api import Gateway, PRIVATE


URL = '/api/v1/agenda/summary'
HEADERS = ((b'authorization', f'Bearer {TOKEN}'.encode()), (b'content-type', b'application/json'),
           (b'x-request-id', b'summary-test-001'))
PLAN = '{"layout":"chronological","highlights":[]}'


class Model:
    def __init__(self):
        self.calls = []
        self.failure = None
        self.reply = ModelReply(text=PLAN, model='fake-summary-model')
        self.thread = None

    def generate(self, **arguments):
        self.thread = get_ident()
        self.calls.append(arguments)
        if self.failure:
            raise self.failure
        return self.reply


class AgendaSummaryApiTests(unittest.TestCase):
    def setUp(self):
        self.gateway = Gateway()
        self.model = Model()
        self.factory = Mock(return_value=self.model)
        self.config = Settings(_env_file=None, api_auth_token=TOKEN, app_env='test')
        self.app = create_app(config=self.config, db=InjectedDatabase(),
            summary_calendar_gateway_factory=self.gateway.factory, summary_model_factory=self.factory)

    def request(self, body=b'{"period":"week","date":"2030-06-19"}', *, headers=HEADERS, query=b'', method='POST'):
        result = asyncio.run(invoke_asgi(self.app, URL, method=method, headers=headers, body=body, query=query))
        encoded = json.dumps(result, ensure_ascii=False)
        for private in [TOKEN, WRONG_TOKEN, PRIVATE, 'private-calendar@example.invalid', 'PrivateEventMixedID', 'https://private.invalid/link']:
            self.assertNotIn(private, encoded)
        return result

    def assert_unused(self):
        self.assertEqual(self.gateway.opened, 0)
        self.factory.assert_not_called()
        self.assertEqual(self.model.calls, [])

    def test_ai_organized_success_preserves_read_only_envelope_and_facts(self):
        status, headers, value = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(headers['cache-control'], 'no-store')
        self.assertEqual(headers['x-request-id'], 'summary-test-001')
        self.assertEqual(value['action'], 'summarize_agenda')
        self.assertEqual(value['mutation_state'], 'not_applicable')
        self.assertIsNone(value['operation_id'])
        self.assertFalse(value['data']['execution_allowed'])
        self.assertEqual(value['data']['summary']['mode'], 'ai_organized')
        self.assertIsNone(value['data']['summary']['reason'])
        self.assertEqual(value['data']['generation']['model'], 'fake-summary-model')
        self.assertIn('Entrevista fictícia', value['data']['summary']['text'])
        self.assertEqual(value['data']['totals']['events'], 1)
        self.assertLessEqual(len(value['data']['summary']['text']), 3500)
        self.factory.assert_called_once_with()
        self.assertEqual(len(self.model.calls), 1)
        self.assertEqual(self.gateway.closed, 1)

    def test_model_receives_no_private_ids_links_or_event_titles(self):
        self.assertEqual(self.request()[0], 200)
        text = json.dumps(self.model.calls, ensure_ascii=False)
        for private in ['Entrevista fictícia', 'PrivateEventMixedID', 'private-calendar@example.invalid', 'https://private.invalid/link']:
            self.assertNotIn(private, text)
        self.assertLessEqual(len(self.model.calls[0]['message']), self.config.llm_max_input_chars)

    def test_empty_period_does_not_create_model_or_claim_ai(self):
        self.gateway.events = ()
        status, _, value = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(value['data']['summary']['mode'], 'factual')
        self.assertEqual(value['data']['summary']['reason'], 'empty_period')
        self.assertEqual(value['data']['totals']['events'], 0)
        self.factory.assert_not_called()

    def test_missing_wrong_or_duplicate_bearer_prevents_all_side_effects(self):
        for headers in [(), ((b'authorization', f'Bearer {WRONG_TOKEN}'.encode()),), HEADERS + (HEADERS[0],)]:
            self.assertEqual(self.request(body=b'PRIVATE INVALID JSON', headers=headers)[0], 401)
        self.assert_unused()

    def test_invalid_correlation_precedes_body_and_factory(self):
        headers = HEADERS[:2] + ((b'x-request-id', b'bad\nvalue'),)
        self.assertEqual(self.request(headers=headers)[0], 422)
        self.assert_unused()

    def test_query_and_wrong_content_types_are_rejected(self):
        for options in [{'query': b'period=month'}, {'headers': (HEADERS[0],)},
                        {'headers': (HEADERS[0], (b'content-type', b'text/plain'))},
                        {'headers': HEADERS + ((b'content-type', b'application/json'),)}]:
            self.assertEqual(self.request(**options)[0], 422)
        self.assert_unused()

    def test_duplicate_extra_or_nonobject_json_is_rejected(self):
        for body in [b'{"period":"week","period":"month"}', b'[]', b'null', b'{}',
                     b'{"period":"week","conversation_id":"private"}',
                     b'{"period":"week","context":{"events":[]}}',
                     b'{"period":"week","prompt":"invent"}', b'{"period":NaN}', b'bad-json']:
            with self.subTest(body=body):
                status, _, value = self.request(body)
                self.assertEqual(status, 422)
                self.assertEqual(value['error']['code'], 'invalid_request')
        self.assert_unused()

    def test_invalid_period_and_date_do_not_open_calendar(self):
        for value in [{'period': 'day'}, {'period': False}, {'period': 'week', 'date': None},
                      {'period': 'month', 'date': '2030-02-30'}, {'period': 'week', 'date': PRIVATE},
                      {'period': 'month', 'date': '9999-12-31'}]:
            self.assertEqual(self.request(json.dumps(value).encode())[0], 422)
        self.assert_unused()

    def test_oversized_body_and_invalid_utf8_are_rejected(self):
        for body in [b'x' * 4097, b'{"period":"week","date":"\xff"}']:
            self.assertEqual(self.request(body)[0], 422)
        self.assert_unused()

    def test_source_errors_are_not_factual_or_empty_success(self):
        for failure, expected, code in [(SummarySetupError(), 503, 'calendar_setup_unavailable'),
            (SummaryLimitError(), 503, 'agenda_context_limit_exceeded'),
            (SummaryCollectionError(), 502, 'agenda_collection_failed'),
            (RuntimeError(PRIVATE), 502, 'agenda_collection_failed')]:
            self.gateway.failure = failure
            status, _, value = self.request()
            self.assertEqual(status, expected)
            self.assertEqual(value['error']['code'], code)
            self.assertIsNone(value['data'])
        self.factory.assert_not_called()

    def test_incomplete_snapshot_never_reaches_model(self):
        self.gateway.incomplete = True
        self.assertEqual(self.request()[0], 502)
        self.factory.assert_not_called()

    def test_rate_limit_or_timeout_produces_safe_factual_fallback_once(self):
        for code in ['llm_rate_limited', 'llm_timeout']:
            self.model.calls.clear()
            self.model.failure = ModelError(code)
            status, _, value = self.request()
            self.assertEqual(status, 200)
            self.assertEqual(value['data']['summary']['mode'], 'factual')
            self.assertEqual(value['data']['summary']['reason'], code)
            self.assertIsNone(value['data']['generation']['model'])
            self.assertEqual(len(self.model.calls), 1)
            self.assertNotEqual(value['data']['totals']['events'], 0)

    def test_missing_model_configuration_still_serves_verified_facts(self):
        self.factory.side_effect = ModelError('llm_not_configured')
        status, _, value = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(value['data']['summary']['reason'], 'llm_not_configured')
        self.assertEqual(self.model.calls, [])

    def test_untrusted_model_text_or_unknown_highlight_is_never_echoed(self):
        for text in [PRIVATE, '{"layout":"chronological","highlights":["UNKNOWN"]}',
                     '{"layout":"chronological","highlights":[],"text":"' + PRIVATE + '"}']:
            self.model.reply = ModelReply(text=text, model='fake-summary-model')
            status, _, value = self.request()
            self.assertEqual(status, 200)
            self.assertEqual(value['data']['summary']['mode'], 'factual')
            self.assertEqual(value['data']['summary']['reason'], 'llm_invalid_response')
            self.assertIn('Entrevista fictícia', value['data']['summary']['text'])

    def test_raw_factory_exception_is_not_exposed(self):
        self.factory.side_effect = RuntimeError(PRIVATE)
        status, _, value = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(value['data']['summary']['reason'], 'llm_unavailable')

    def test_default_model_factory_is_lazy_and_injected_config_is_used(self):
        self.app.state.summary_model_factory = None
        with patch('app.api_agenda_summary.configured_language_model', return_value=self.model) as configured:
            self.assertEqual(self.request()[0], 200)
            configured.assert_called_once_with(self.config)

    def test_provider_work_runs_outside_the_asgi_event_loop(self):
        main_thread = get_ident()
        self.assertEqual(self.request()[0], 200)
        self.assertNotEqual(self.model.thread, main_thread)

    def test_openapi_documents_only_read_parameters_and_inherits_bearer(self):
        route = self.app.openapi()['paths'][URL]['post']
        schema = route['requestBody']['content']['application/json']['schema']
        self.assertEqual(set(schema['properties']), {'period', 'date'})
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(schema['required'], ['period'])
        self.assertTrue(route['security'])
