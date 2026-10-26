"""API, serviço, leituras legadas e SDK reais sobre HTTP Google fictício."""

import asyncio
from contextlib import contextmanager
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from google.auth.credentials import AnonymousCredentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build

from app.briefing_calendar import GoogleBriefingCalendarGateway
from app.google_transport import SingleAttemptHttp
from app.main import create_app
from app.settings import Settings
from calendar_http_fake import CalendarHttpFake
from test_api_security import InjectedDatabase, TOKEN, invoke_asgi


EVENT_ID = 'briefing-sdk-occurrence'
URL = '/api/v1/preparation/briefing'


class BriefingSdkIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.backend = CalendarHttpFake()
        self.backend.saved[EVENT_ID] = {
            'id': EVENT_ID, 'summary': 'Entrevista SDK fictícia', 'status': 'confirmed', 'etag': '"v1"',
            'start': {'dateTime': '2030-06-19T09:00:00-03:00'},
            'end': {'dateTime': '2030-06-19T09:30:00-03:00'},
            'description': '<p>Discutir testes e APIs.</p>', 'location': 'Sala fictícia'}
        for name, value in (
            ('app.google_transport.requests.Session', {'side_effect': self.backend.session}),
            ('app.briefing_calendar.resolve_calendar', {'side_effect': lambda _: deepcopy(self.backend.calendar)}),
            ('app.google_calendar.resolve_calendar', {'side_effect': lambda _: deepcopy(self.backend.calendar)}),
            ('app.google_calendar.get_credentials', {'side_effect': AssertionError('OAuth real proibido')}),
        ):
            entry = patch(name, **value)
            entry.start()
            self.addCleanup(entry.stop)
        transport = SingleAttemptHttp(timeout=20)
        http = AuthorizedHttp(AnonymousCredentials(), http=transport, max_refresh_attempts=0)
        self.sdk = build('calendar', 'v3', http=http, cache_discovery=False, static_discovery=True)
        self.addCleanup(self.sdk.close)

        @contextmanager
        def factory():
            yield GoogleBriefingCalendarGateway(self.sdk)
        self.app = create_app(config=Settings(_env_file=None, api_auth_token=TOKEN, app_env='test'),
                              db=InjectedDatabase(), briefing_gateway_factory=factory)

    def request(self):
        result = asyncio.run(invoke_asgi(self.app, URL, method='POST',
            headers=[(b'authorization', f'Bearer {TOKEN}'.encode()), (b'content-type', b'application/json')],
            body=json.dumps({'event_id': EVENT_ID}).encode()))
        self.assertTrue(all(call['method'] == 'GET' for call in self.backend.calls))
        self.assertFalse(self.backend._has_written)
        return result

    def test_sdk_read_composes_one_factual_briefing_and_checklist_via_api(self):
        status, headers, envelope = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(envelope['status'], 'ready')
        self.assertEqual(headers['cache-control'], 'no-store')
        result = envelope['data']
        self.assertEqual(result['facts']['event_id'], EVENT_ID)
        self.assertEqual(result['provided_context']['event_description'], 'Discutir testes e APIs.')
        self.assertEqual(len(result['checklist']), 6)
        self.assertEqual(len(self.backend.calls), 2)
        self.assertTrue(all(call['event_id'] == EVENT_ID for call in self.backend.calls))

    def test_sdk_missing_occurrence_returns_question_after_one_get(self):
        del self.backend.saved[EVENT_ID]
        status, _, envelope = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(envelope['data']['reason'], 'event_not_found')
        self.assertIsNone(envelope['data']['facts'])
        self.assertEqual(len(self.backend.calls), 1)

    def test_sdk_incompatible_details_fail_without_partial_briefing(self):
        self.backend.saved[EVENT_ID]['description'] = {'invalid': 'PRIVATE_PROVIDER_FIELD'}
        status, _, envelope = self.request()
        self.assertEqual(status, 502)
        self.assertIsNone(envelope['data'])
        self.assertNotIn('PRIVATE_PROVIDER_FIELD', json.dumps(envelope))
        self.assertEqual(len(self.backend.calls), 2)


if __name__ == '__main__':
    unittest.main()
