"""Briefing por ASGI: proteção antes do corpo e composição factual sem rede."""
import asyncio
from contextlib import contextmanager
from dataclasses import replace
import json
from threading import get_ident
import unittest
from unittest.mock import AsyncMock, patch

from starlette.requests import Request

from app.main import create_app
from app.api_briefing import _body
from app.services.agenda import AgendaEvent
from app.services.event_details import EventDetails
from app.services.event_selection import EventLookup
from app.services.interview_briefing import (
    BriefingBinding, BriefingCollectionError, BriefingInputError, BriefingSetupError,
)
from app.services.update_snapshot import UpdateSnapshot
from app.settings import Settings
from test_api_security import InjectedDatabase, TOKEN, WRONG_TOKEN, invoke_asgi


URL = '/api/v1/preparation/briefing'
EVENT_ID = 'MixedOccurrence_20300619'
PRIVATE = 'PRIVATE_BRIEFING_PROVIDER_DIAGNOSTIC'
HEADERS = ((b'authorization', f'Bearer {TOKEN}'.encode()), (b'content-type', b'application/json'),
           (b'x-request-id', b'briefing-test-001'))


class BriefingGateway:
    def __init__(self):
        self.selected = BriefingBinding('private-calendar@example.invalid', 'America/Recife')
        self.details = EventDetails(EVENT_ID, 'Entrevista fictícia', '2030-06-19T09:00:00-03:00',
            '2030-06-19T09:30:00-03:00', False, 'America/Recife', 'confirmed', None,
            'Conversa técnica sobre Python.', 'Videochamada')
        self.lookup_event = self.public()
        self.failure = self.cleanup_error = None
        self.opened = self.closed = 0
        self.calls = []
        self.thread = None

    def public(self):
        return AgendaEvent(**{key: getattr(self.details, key) for key in
            ('event_id', 'title', 'start', 'end', 'all_day', 'time_zone', 'status', 'html_link')})

    @contextmanager
    def factory(self):
        self.opened += 1
        try:
            yield self
        finally:
            self.closed += 1
            if self.cleanup_error:
                raise self.cleanup_error

    def binding(self):
        self.thread = get_ident()
        if self.failure:
            raise self.failure
        return self.selected

    def get_event(self, event_id, *, time_zone):
        self.calls.append(('get_event', event_id, time_zone))
        return EventLookup('private-calendar-name', self.lookup_event)

    def get_update_snapshot(self, event_id, *, time_zone):
        self.calls.append(('get_update_snapshot', event_id, time_zone))
        return UpdateSnapshot(self.selected.calendar_id, 'private-calendar-name', self.details, 'private-version', 'opaque')


class BriefingApiTests(unittest.TestCase):
    def setUp(self):
        self.gateway = BriefingGateway()
        self.app = create_app(config=Settings(_env_file=None, api_auth_token=TOKEN, app_env='test'),
            db=InjectedDatabase(), briefing_gateway_factory=self.gateway.factory)

    def request(self, value=None, *, raw=None, headers=HEADERS, query=b'', app=None):
        body = json.dumps({'event_id': EVENT_ID} if value is None else value).encode() if raw is None else raw
        response = asyncio.run(invoke_asgi(app or self.app, URL, method='POST', headers=headers, body=body, query=query))
        text = json.dumps(response, ensure_ascii=False)
        for private in [TOKEN, WRONG_TOKEN, PRIVATE, 'private-calendar@example.invalid', 'private-calendar-name', 'private-version']:
            self.assertNotIn(private, text)
        return response

    def test_ready_response_is_no_store_factual_and_does_not_authorize_execution(self):
        status, headers, value = self.request({'event_id': EVENT_ID, 'user_context': 'Quero revisar meus exemplos.'})
        self.assertEqual(status, 200)
        self.assertEqual(headers['cache-control'], 'no-store')
        self.assertEqual(headers['x-request-id'], 'briefing-test-001')
        self.assertEqual(set(value), {'api_version', 'request_id', 'operation_id', 'status', 'action',
                         'mutation_state', 'message', 'data', 'error'})
        self.assertEqual((value['status'], value['action'], value['mutation_state']), ('ready', 'prepare_briefing', 'not_applicable'))
        self.assertIsNone(value['operation_id'])
        self.assertIsNone(value['error'])
        data = value['data']
        self.assertEqual(data['status'], 'ready')
        self.assertEqual(data['mode'], 'factual_template')
        self.assertIn('Entrevista fictícia', data['text'])
        self.assertIn('Quero revisar meus exemplos.', data['text'])
        self.assertFalse(data['provided_context']['verified_externally'])
        self.assertLessEqual(len(data['text']), 3500)
        self.assertLessEqual(len(data['checklist']), 6)
        self.assertFalse(data['execution_allowed'])
        self.assertFalse(data['reservation_created'])
        self.assertEqual([call[0] for call in self.gateway.calls], ['get_event', 'get_update_snapshot'])
        self.assertEqual((self.gateway.opened, self.gateway.closed), (1, 1))

    def test_missing_null_or_empty_id_requests_clarification_without_opening_source(self):
        for value in [{}, {'event_id': None}, {'event_id': ''}, {'event_id': '   '}, {'user_context': 'Uma entrevista'}]:
            with self.subTest(value=value):
                status, _, envelope = self.request(value)
                self.assertEqual(status, 200)
                self.assertEqual(envelope['status'], 'needs_clarification')
                self.assertEqual(envelope['data']['status'], 'needs_clarification')
                self.assertTrue(envelope['data']['question'])
                self.assertIsNone(envelope['data']['facts'])
        self.assertEqual(self.gateway.opened, 0)

    def test_absent_or_cancelled_event_is_clarification_without_detail_read(self):
        for event in [None, replace(self.gateway.public(), status='cancelled')]:
            self.gateway.calls.clear()
            self.gateway.lookup_event = event
            status, _, value = self.request()
            self.assertEqual(status, 200)
            self.assertEqual(value['status'], 'needs_clarification')
            self.assertIsNone(value['data']['facts'])
            self.assertEqual([call[0] for call in self.gateway.calls], ['get_event'])

    def test_changed_event_between_reads_does_not_produce_stale_briefing(self):
        self.gateway.details = replace(self.gateway.details, title='Título atualizado')
        status, _, value = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(value['status'], 'needs_clarification')
        self.assertEqual(value['data']['reason'], 'event_changed')
        self.assertIsNone(value['data']['facts'])

    def test_maximum_unicode_context_is_accepted_as_characters_not_bytes(self):
        status, _, value = self.request({'event_id': EVENT_ID, 'user_context': 'á' * 1200})
        self.assertEqual(status, 200)
        self.assertEqual(value['status'], 'ready')
        self.assertIn('user_context', value['data']['provided_context']['truncated_fields'])

    def test_authentication_precedes_even_reading_malformed_body(self):
        with patch('app.api_briefing._body', new_callable=AsyncMock) as body:
            self.assertEqual(self.request(raw=b'PRIVATE BAD JSON', headers=())[0], 401)
            body.assert_not_awaited()
        self.assertEqual(self.gateway.opened, 0)

    def test_wrong_duplicate_or_unconfigured_bearer_never_opens_source(self):
        for headers in [((b'authorization', f'Bearer {WRONG_TOKEN}'.encode()),), HEADERS + (HEADERS[0],)]:
            self.assertEqual(self.request(headers=headers)[0], 401)
        app = create_app(config=Settings(_env_file=None, api_auth_token=None, app_env='test'),
            db=InjectedDatabase(), briefing_gateway_factory=self.gateway.factory)
        self.assertEqual(self.request(app=app)[0], 503)
        self.assertEqual(self.gateway.opened, 0)

    def test_invalid_correlation_fails_before_body_and_source(self):
        self.assertEqual(self.request(headers=HEADERS[:2] + ((b'x-request-id', b'bad\nvalue'),))[0], 422)
        self.assertEqual(self.gateway.opened, 0)

    def test_query_and_invalid_or_duplicate_content_type_are_rejected(self):
        status, _, value = self.request(query=b'event_id=ignored')
        self.assertEqual(status, 422)
        self.assertEqual(value['error']['fields'], ['query'])
        for headers in [(HEADERS[0],), (HEADERS[0], (b'content-type', b'text/plain')),
                        HEADERS + ((b'content-type', b'application/json'),)]:
            self.assertEqual(self.request(headers=headers)[0], 422)
        self.assertEqual(self.gateway.opened, 0)

    def test_malformed_duplicate_nonobject_nan_and_invalid_unicode_are_rejected(self):
        for body in [b'', b'[]', b'null', b'{"event_id":"a","event_id":"b"}', b'bad-json',
                     b'{"user_context":NaN}', b'{"user_context":Infinity}', b'\xff',
                     b'{"event_id":"\\ud800"}', b'{"user_context":"\\ud800"}']:
            with self.subTest(body=body):
                self.assertEqual(self.request(raw=body)[0], 422)
        self.assertEqual(self.gateway.opened, 0)

    def test_invalid_field_types_and_lengths_fail_before_source(self):
        for changes in [{'event_id': True}, {'event_id': []}, {'event_id': 'a' * 1025},
                        {'event_id': 'invalid id'}, {'user_context': None}, {'user_context': 12},
                        {'user_context': 'a' * 1201}]:
            with self.subTest(changes=changes):
                status, _, value = self.request({'event_id': EVENT_ID, **changes})
                self.assertEqual(status, 422)
                self.assertEqual(value['error']['code'], 'invalid_request')
                self.assertTrue(set(value['error']['fields']) <= {'event_id', 'user_context'})
        self.assertEqual(self.gateway.opened, 0)

    def test_extra_prompt_conversation_binding_or_execution_parameters_are_rejected(self):
        for key in ['prompt', 'calendar_id', 'time_zone', 'conversation_id', 'execute', 'confirmed', PRIVATE]:
            self.assertEqual(self.request({'event_id': EVENT_ID, key: 'unsupported'})[0], 422)
        self.assertEqual(self.gateway.opened, 0)

    def test_body_byte_limit_accepts_exact_8192_and_rejects_one_more(self):
        raw = json.dumps({'event_id': EVENT_ID}).encode()
        raw += b' ' * (8192 - len(raw))
        self.assertEqual(self.request(raw=raw)[0], 200)
        self.assertEqual(self.request(raw=raw + b' ')[0], 422)
        self.assertEqual(self.gateway.opened, 1)

    def test_body_timeout_is_two_seconds_and_maps_to_safe_input_error(self):
        async def receive():
            await asyncio.Event().wait()
        request = Request({'type': 'http', 'query_string': b'', 'headers': [(b'content-type', b'application/json')]}, receive)
        original_timeout = asyncio.timeout
        async def check():
            with patch('app.api_briefing.asyncio.timeout', side_effect=lambda seconds: original_timeout(0.001)) as timeout:
                with self.assertRaises(BriefingInputError) as caught:
                    await asyncio.wait_for(_body(request), timeout=0.5)
                self.assertEqual(caught.exception.fields, ('body',))
                timeout.assert_called_once_with(2)
        asyncio.run(check())
        self.assertEqual(self.gateway.opened, 0)

    def test_setup_collection_and_unexpected_errors_have_no_data_or_raw_diagnostic(self):
        for failure, expected, code in [(BriefingSetupError(), 503, 'calendar_setup_unavailable'),
            (BriefingCollectionError(), 502, 'briefing_collection_failed'),
            (RuntimeError(PRIVATE), 502, 'briefing_collection_failed')]:
            self.gateway.failure = failure
            status, _, value = self.request()
            self.assertEqual(status, expected)
            self.assertEqual(value['status'], 'failed')
            self.assertEqual(value['error'], {'code': code, 'retryable': False, 'fields': []})
            self.assertIsNone(value['data'])

    def test_factory_cleanup_and_invalid_internal_result_fail_without_briefing(self):
        self.gateway.cleanup_error = RuntimeError(PRIVATE)
        self.assertEqual(self.request()[0], 502)
        self.app.state.briefing_gateway_factory = lambda: (_ for _ in ()).throw(RuntimeError(PRIVATE))
        self.assertEqual(self.request()[0], 502)
        with patch('app.api_briefing.InterviewBriefingService') as service:
            service.return_value.prepare.return_value = {'status': 'unchecked', 'raw': PRIVATE}
            status, _, value = self.request()
            self.assertEqual(status, 502)
            self.assertIsNone(value['data'])

    def test_default_gateway_is_lazy_without_model_or_sender(self):
        self.app.state.briefing_gateway_factory = None
        with patch('app.api_briefing.default_briefing_gateway', self.gateway.factory), \
                patch('app.language_model.configured_language_model', side_effect=AssertionError('Não há IA.')):
            self.assertEqual(self.request({})[0], 200)
            self.assertEqual(self.gateway.opened, 0)
            self.assertEqual(self.request()[0], 200)
        self.assertEqual(self.gateway.opened, 1)

    def test_work_runs_outside_event_loop_and_openapi_exposes_only_supported_inputs(self):
        current = get_ident()
        self.assertEqual(self.request()[0], 200)
        self.assertNotEqual(self.gateway.thread, current)
        route = self.app.openapi()['paths'][URL]['post']
        schema = route['requestBody']['content']['application/json']['schema']
        self.assertEqual(set(schema['properties']), {'event_id', 'user_context'})
        self.assertFalse(schema['additionalProperties'])
        self.assertTrue(route['security'])
        self.assertEqual(schema['properties']['user_context']['maxLength'], 1200)


if __name__ == '__main__':
    unittest.main()
