"""ASGI autenticado com o serviço real e snapshots de calendário fictícios."""

import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

from app.main import create_app
from app.services.agenda import AgendaEvent
from app.services.summary_context import (
    SummaryCalendarBinding, SummaryCalendarSnapshot, SummaryCollectionError, SummaryLimitError, SummarySetupError,
)
from app.settings import Settings
from test_api_security import InjectedDatabase, TOKEN, WRONG_TOKEN, invoke_asgi


URL = '/api/v1/agenda/context'
HEADERS = ((b'authorization', f'Bearer {TOKEN}'.encode()), (b'x-request-id', b'context-test-001'))
PRIVATE = 'PRIVATE_CONTEXT_DIAGNOSTIC_DO_NOT_ECHO'


class Gateway:
    def __init__(self):
        self.selected = SummaryCalendarBinding('private-calendar@example.invalid', 'America/Recife')
        self.events = (AgendaEvent('PrivateEventMixedID', 'Entrevista fictícia',
            '2030-06-17T10:00:00-03:00', '2030-06-17T10:30:00-03:00', False,
            'America/Recife', 'confirmed', 'https://private.invalid/link'),)
        self.opened = 0
        self.closed = 0
        self.periods = []
        self.failure = None
        self.incomplete = False

    @contextmanager
    def factory(self):
        self.opened += 1
        try:
            yield self
        finally:
            self.closed += 1

    def binding(self):
        if self.failure:
            raise self.failure
        return self.selected

    def list_events(self, period, *, expected_binding):
        self.periods.append(period)
        if expected_binding != self.selected:
            raise AssertionError('Vínculo incorreto.')
        return SummaryCalendarSnapshot(self.selected, period, self.events, not self.incomplete, 1, len(self.events))


class SummaryContextApiTests(unittest.TestCase):
    def setUp(self):
        self.gateway = Gateway()
        self.app = create_app(config=Settings(_env_file=None, api_auth_token=TOKEN, app_env='test'),
            db=InjectedDatabase(), summary_calendar_gateway_factory=self.gateway.factory)

    def request(self, query=b'period=week&date=2030-06-19', *, headers=HEADERS):
        result = asyncio.run(invoke_asgi(self.app, URL, headers=headers, query=query))
        text = json.dumps(result, ensure_ascii=False)
        for hidden in (TOKEN, WRONG_TOKEN, PRIVATE, 'private-calendar@example.invalid', 'PrivateEventMixedID', 'https://private.invalid/link'):
            self.assertNotIn(hidden, text)
        return result

    def test_week_success_is_read_only_and_correlated(self):
        status, headers, value = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(headers['cache-control'], 'no-store')
        self.assertEqual(headers['x-request-id'], 'context-test-001')
        self.assertEqual(value['action'], 'collect_agenda_context')
        self.assertEqual(value['mutation_state'], 'not_applicable')
        self.assertIsNone(value['operation_id'])
        self.assertFalse(value['data']['execution_allowed'])
        self.assertEqual(value['data']['totals']['events'], 1)
        self.assertEqual(value['data']['period']['start'][:10], '2030-06-17')
        self.assertEqual(self.gateway.opened, self.gateway.closed)

    def test_month_uses_full_civil_month(self):
        status, _, value = self.request(b'period=month&date=2030-06-19')
        self.assertEqual(status, 200)
        self.assertEqual(value['data']['period']['start'][:10], '2030-06-01')
        self.assertEqual(value['data']['period']['end'][:10], '2030-07-01')
        self.assertEqual(len(value['data']['days']), 30)

    def test_missing_auth_does_not_open_gateway_even_for_invalid_input(self):
        self.assertEqual(self.request(b'period=bad', headers=())[0], 401)
        self.assertEqual(self.gateway.opened, 0)

    def test_wrong_or_duplicate_authorization_is_rejected(self):
        for headers in [((b'authorization', f'Bearer {WRONG_TOKEN}'.encode()),), HEADERS + (HEADERS[0],)]:
            self.assertEqual(self.request(headers=headers)[0], 401)
        self.assertEqual(self.gateway.opened, 0)

    def test_invalid_correlation_fails_before_gateway(self):
        self.assertEqual(self.request(headers=(HEADERS[0], (b'x-request-id', b'bad\nvalue')))[0], 422)
        self.assertEqual(self.gateway.opened, 0)

    def test_missing_or_unsupported_period_is_invalid_before_gateway(self):
        for query in [b'', b'date=2030-06-19', b'period=day', b'period=Week', b'period=']:
            with self.subTest(query=query):
                status, _, value = self.request(query)
                self.assertEqual(status, 422)
                self.assertEqual(value['error']['code'], 'invalid_request')
        self.assertEqual(self.gateway.opened, 0)

    def test_invalid_date_cannot_open_google(self):
        for day in ['2030-02-30', '20300619', '2030-6-19', '', PRIVATE, '9999-12-31']:
            with self.subTest(day=day):
                status, _, value = self.request(('period=month&date=' + day).encode())
                self.assertEqual(status, 422)
                self.assertIn('date', value['error']['fields'])
        self.assertEqual(self.gateway.opened, 0)

    def test_unknown_or_duplicate_query_is_rejected(self):
        for query in [b'period=week&period=month', b'period=week&date=2030-06-19&date=2030-06-20',
                      b'period=week&time_zone=UTC', b'period=week&calendar_id=primary',
                      b'period=week&%70eriod=month', ('period=week&' + PRIVATE + '=bad').encode()]:
            with self.subTest(query=query):
                self.assertEqual(self.request(query)[0], 422)
        self.assertEqual(self.gateway.opened, 0)

    def test_oversized_query_is_rejected(self):
        self.assertEqual(self.request(b'period=week&date=' + b'x' * 1025)[0], 422)
        self.assertEqual(self.gateway.opened, 0)

    def test_no_date_uses_actual_calendar_time_zone(self):
        self.gateway.events = ()
        self.gateway.selected = replace(self.gateway.selected, time_zone='Pacific/Kiritimati')
        status, _, value = self.request(b'period=week')
        self.assertEqual(status, 200)
        self.assertEqual(value['data']['period']['time_zone'], 'Pacific/Kiritimati')
        self.assertEqual(value['data']['totals']['events'], 0)
        self.assertTrue(value['data']['snapshot']['complete'])

    def test_setup_failure_is_503_without_private_error(self):
        self.gateway.failure = SummarySetupError()
        status, _, value = self.request()
        self.assertEqual(status, 503)
        self.assertEqual(value['error']['code'], 'calendar_setup_unavailable')
        self.assertIsNone(value['data'])

    def test_unexpected_or_collection_error_is_not_empty_success(self):
        for error in [RuntimeError(PRIVATE), SummaryCollectionError()]:
            self.gateway.failure = error
            status, _, value = self.request()
            self.assertEqual(status, 502)
            self.assertIsNone(value['data'])

    def test_limit_exceeded_is_distinct_without_partial_data(self):
        self.gateway.failure = SummaryLimitError()
        status, _, value = self.request()
        self.assertEqual(status, 503)
        self.assertEqual(value['error']['code'], 'agenda_context_limit_exceeded')
        self.assertIsNone(value['data'])
        self.assertFalse(value['error']['retryable'])

    def test_incomplete_snapshot_is_failure_not_empty(self):
        self.gateway.incomplete = True
        self.gateway.events = ()
        status, _, value = self.request()
        self.assertEqual(status, 502)
        self.assertIsNone(value['data'])

    def test_current_calendar_factory_is_not_reused_silently(self):
        def forbidden():
            raise AssertionError('Fronteira antiga não deve abrir.')
        self.app.state.calendar_gateway_factory = forbidden
        self.assertEqual(self.request()[0], 200)

    def test_default_gateway_is_lazy_and_replaceable(self):
        self.app.state.summary_calendar_gateway_factory = None
        with patch('app.api_summary_context.default_summary_calendar_gateway', self.gateway.factory):
            self.assertEqual(self.request()[0], 200)

    def test_no_llm_is_constructed(self):
        with patch('app.language_model.configured_language_model', side_effect=AssertionError('Sem IA nesta coleta.')):
            self.assertEqual(self.request()[0], 200)

    def test_openapi_documents_only_supported_query_parameters(self):
        route = self.app.openapi()['paths'][URL]['get']
        parameters = {value['name']: value for value in route['parameters']}
        self.assertEqual(set(parameters), {'period', 'date'})
        self.assertEqual(parameters['period']['schema']['enum'], ['week', 'month'])
        self.assertTrue(route['security'])
