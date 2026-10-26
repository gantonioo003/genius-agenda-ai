"""API/CLI/domínio/adaptador/SDK compostos sobre HTTP Google fictício."""

import asyncio
from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
from io import StringIO
import json
import unittest
from unittest.mock import patch

from google.auth.credentials import AnonymousCredentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build

from app.availability_calendar import GoogleAvailabilityCalendarGateway
from app.find_free_slots import main
from app.google_transport import SingleAttemptHttp
from app.main import create_app
from app.services.availability import AvailabilityService
from app.settings import Settings
from test_api_security import InjectedDatabase, TOKEN, invoke_asgi
from test_availability_api import HEADERS, REQUEST, URL
from test_availability_calendar import AvailabilityHttpFake, event
from test_find_free_slots import ARGS


class AvailabilityIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.backend = AvailabilityHttpFake()
        for target, options in [
            ('app.google_transport.requests.Session', {'side_effect': self.backend.session}),
            ('app.availability_calendar.resolve_calendar', {'side_effect': lambda _service: deepcopy(self.backend.calendar)}),
            ('app.google_calendar.get_credentials', {'side_effect': AssertionError('OAuth real proibido.')})]:
            patcher = patch(target, **options)
            patcher.start()
            self.addCleanup(patcher.stop)
        http = AuthorizedHttp(AnonymousCredentials(), http=SingleAttemptHttp(timeout=20), max_refresh_attempts=0)
        self.sdk = build('calendar', 'v3', http=http, cache_discovery=False, static_discovery=True)
        self.addCleanup(self.sdk.close)
        self.gateway = GoogleAvailabilityCalendarGateway(self.sdk)
        self.clock = lambda: datetime(2030, 6, 19, 11, tzinfo=timezone.utc)

        @contextmanager
        def factory():
            yield self.gateway
        self.factory = factory

    def timed(self, identity, start, end, **changes):
        return event(identity, start={'dateTime': '2030-06-19T' + start + ':00-03:00'},
            end={'dateTime': '2030-06-19T' + end + ':00-03:00'}, **changes)

    def test_buffer_fetches_busy_event_outside_requested_window(self):
        self.backend.pages = [{'items': [self.timed('private-neighbor', '08:45', '08:50')]}]
        result = AvailabilityService(self.factory, clock=self.clock).find(**{**REQUEST, 'window_end': '10:00', 'buffer_minutes': 15})
        self.assertEqual(result['slots'][0]['start'], '2030-06-19T09:15:00-03:00')
        self.assertEqual(result['count'], 2)
        query = self.backend.calls[0]['query']
        self.assertEqual(datetime.fromisoformat(query['timeMin']).astimezone(timezone.utc).hour, 11)
        self.assertEqual(datetime.fromisoformat(query['timeMin']).minute, 45)
        self.assertTrue(all(call['method'] == 'GET' for call in self.backend.calls))

    def test_api_consumes_all_pages_and_opaque_tentative_blocks(self):
        self.backend.pages = [
            {'items': [self.timed('private-transparent', '09:00', '12:00', transparency='transparent')], 'nextPageToken': 'later'},
            {'items': [self.timed('private-tentative', '09:00', '10:00', status='tentative')]},
        ]
        app = create_app(config=Settings(_env_file=None, api_auth_token=TOKEN, app_env='test'),
            db=InjectedDatabase(), availability_gateway_factory=self.factory)
        status, _, value = asyncio.run(invoke_asgi(app, URL, method='POST', headers=HEADERS,
            body=json.dumps(REQUEST).encode()))
        self.assertEqual(status, 200)
        self.assertEqual(value['data']['slots'][0]['start'], '2030-06-19T10:00:00-03:00')
        self.assertEqual(self.backend.page_number, 2)
        self.assertNotIn('private-tentative', json.dumps(value))
        self.assertEqual(self.backend.calls[0]['query']['showHiddenInvitations'], 'true')

    def test_late_malformed_occupancy_cannot_become_success(self):
        self.backend.pages = [{'items': [], 'nextPageToken': 'later'},
            {'items': [self.timed('private-malformed', '09:00', '10:00', transparency='unknown')]}]
        app = create_app(config=Settings(_env_file=None, api_auth_token=TOKEN, app_env='test'),
            db=InjectedDatabase(), availability_gateway_factory=self.factory)
        status, _, value = asyncio.run(invoke_asgi(app, URL, method='POST', headers=HEADERS,
            body=json.dumps(REQUEST).encode()))
        self.assertEqual(status, 502)
        self.assertIsNone(value['data'])
        self.assertEqual(self.backend.page_number, 2)

    def test_cli_sdk_composition_has_no_reservation_or_private_busy_fields(self):
        self.backend.pages = [{'items': [self.timed('private-busy', '09:00', '10:00')]}]
        output = StringIO()
        with redirect_stdout(output):
            code = main(ARGS + ['--counts-only'], gateway_factory=self.factory, clock=self.clock)
        self.assertEqual(code, 0)
        text = output.getvalue()
        result = json.loads(text)['result']
        self.assertEqual(result['count'], 3)
        self.assertFalse(result['reservation_created'])
        self.assertNotIn('slots', result)
        self.assertNotIn('private-busy', text)
        self.assertNotIn(self.backend.calendar['id'], text)
