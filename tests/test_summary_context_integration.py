"""Coleta composta: API/CLI, domínio, gateway e SDK com HTTP Google fictício."""

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

from app.collect_summary_context import main
from app.google_transport import SingleAttemptHttp
from app.main import create_app
from app.services.summary_context import SummaryContextService, SummaryCollectionError
from app.settings import Settings
from app.summary_calendar import GoogleSummaryCalendarGateway
from test_api_security import InjectedDatabase, TOKEN, invoke_asgi
from test_summary_calendar import SummaryHttpFake, event


class SummaryContextIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.backend = SummaryHttpFake()
        for target, options in [
            ('app.google_transport.requests.Session', {'side_effect': self.backend.session}),
            ('app.summary_calendar.resolve_calendar', {'side_effect': lambda _service: deepcopy(self.backend.calendar)}),
            ('app.google_calendar.get_credentials', {'side_effect': AssertionError('OAuth real proibido.')})]:
            patcher = patch(target, **options)
            patcher.start()
            self.addCleanup(patcher.stop)
        http = AuthorizedHttp(AnonymousCredentials(), http=SingleAttemptHttp(timeout=20), max_refresh_attempts=0)
        self.sdk = build('calendar', 'v3', http=http, cache_discovery=False, static_discovery=True)
        self.addCleanup(self.sdk.close)
        self.gateway = GoogleSummaryCalendarGateway(self.sdk)
        self.clock = lambda: datetime(2030, 6, 19, 12, tzinfo=timezone.utc)

        @contextmanager
        def factory():
            yield self.gateway
        self.factory = factory

    def test_full_paginated_source_counts_beyond_the_display_limit(self):
        items = [event(f'context-occurrence-{index:02d}') for index in range(12)]
        self.backend.pages = [{'items': items[:7], 'nextPageToken': 'page-2'}, {'items': items[7:]}]
        result = SummaryContextService(self.factory, clock=self.clock).build(period='month', reference_date='2030-06-19')
        self.assertEqual(result['totals']['events'], 12)
        self.assertEqual(result['details']['omitted'], 2)
        self.assertEqual(len(result['events']), 10)
        self.assertEqual(sum(day['event_count'] for day in result['days']), 12)
        self.assertEqual(self.backend.page_number, 2)
        self.assertTrue(all(call['method'] == 'GET' for call in self.backend.calls))

    def test_late_conflicting_occurrence_prevents_prefix_success(self):
        original = event('same-exact-occurrence')
        changed = event('same-exact-occurrence', summary='changed after first page')
        self.backend.pages = [{'items': [original], 'nextPageToken': 'later'}, {'items': [changed]}]
        with self.assertRaises(SummaryCollectionError):
            SummaryContextService(self.factory, clock=self.clock).build(period='week', reference_date='2030-06-19')
        self.assertEqual(self.backend.page_number, 2)

    def test_authenticated_api_composes_sdk_result_without_provider_identifiers(self):
        self.backend.pages = [{'items': [event('private-occurrence-id')]}]
        app = create_app(config=Settings(_env_file=None, api_auth_token=TOKEN, app_env='test'),
            db=InjectedDatabase(), summary_calendar_gateway_factory=self.factory)
        status, _, value = asyncio.run(invoke_asgi(app, '/api/v1/agenda/context',
            query=b'period=week&date=2030-06-19', headers=((b'authorization', f'Bearer {TOKEN}'.encode()),)))
        self.assertEqual(status, 200)
        self.assertEqual(value['data']['events'][0]['ref'], 'E1')
        text = json.dumps(value)
        self.assertNotIn('private-occurrence-id', text)
        self.assertNotIn(self.backend.calendar['id'], text)
        self.assertEqual(value['data']['totals']['events'], 1)

    def test_cli_counts_only_preserves_factual_totals_from_sdk_without_titles(self):
        self.backend.pages = [{'items': [event('private-1', summary='PRIVATE TITLE')]}]
        output = StringIO()
        with redirect_stdout(output):
            code = main(['--period', 'month', '--date', '2030-06-19', '--counts-only'],
                gateway_factory=self.factory, clock=self.clock)
        self.assertEqual(code, 0)
        text = output.getvalue()
        self.assertNotIn('PRIVATE TITLE', text)
        self.assertNotIn('private-1', text)
        self.assertEqual(json.loads(text)['context']['totals']['events'], 1)
