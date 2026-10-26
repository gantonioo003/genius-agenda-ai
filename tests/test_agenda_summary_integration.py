"""Resumo composto com adaptador Gemini real e transporte HTTP em memória."""

import asyncio
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

from google.auth.credentials import AnonymousCredentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
import requests

from app.gemini_llm import GeminiModel
from app.google_transport import SingleAttemptHttp
from app.main import create_app
from app.services.agenda_summary import AgendaSummaryService
from app.services.summary_context import SummaryContextService
from app.settings import Settings
from app.summary_calendar import GoogleSummaryCalendarGateway
from test_agenda_summary_api import HEADERS, PLAN, URL
from test_api_security import InjectedDatabase, invoke_asgi
from test_gemini_llm import KEY, MODEL, Response, Session, envelope
from test_summary_calendar import SummaryHttpFake, event
from test_summary_context_api import Gateway


class AgendaSummaryTransportTests(unittest.TestCase):
    def setUp(self):
        self.gateway = Gateway()
        self.response = Response(envelope([{'text': PLAN}]))
        self.session = Session(self.response)
        self.model = GeminiModel(KEY, MODEL, session_factory=lambda: self.session)
        self.clock = lambda: datetime(2030, 6, 19, 12, tzinfo=timezone.utc)

    def service(self):
        return AgendaSummaryService(SummaryContextService(self.gateway.factory, clock=self.clock), lambda: self.model)

    def test_existing_gemini_adapter_generates_one_plan_without_tools_or_titles(self):
        result = self.service().summarize(period='week', reference_date='2030-06-19')
        self.assertEqual(result['summary']['mode'], 'ai_organized')
        self.assertEqual(len(self.session.calls), 1)
        options = self.session.calls[0][1]
        payload = options['json']
        self.assertNotIn('tools', payload)
        self.assertNotIn('Entrevista fictícia', json.dumps(payload, ensure_ascii=False))
        self.assertNotIn('PrivateEventMixedID', json.dumps(payload))
        self.assertNotIn(KEY, json.dumps(result))
        self.assertIn('Entrevista fictícia', result['summary']['text'])
        self.assertFalse(options['allow_redirects'])
        self.assertTrue(self.session.closed)

    def test_existing_adapter_timeout_is_one_attempt_and_factual_fallback(self):
        self.session.error = requests.Timeout('PRIVATE PROVIDER EXCEPTION')
        result = self.service().summarize(period='week', reference_date='2030-06-19')
        self.assertEqual(result['summary']['mode'], 'factual')
        self.assertEqual(result['summary']['reason'], 'llm_timeout')
        self.assertEqual(len(self.session.calls), 1)
        self.assertNotIn('PRIVATE PROVIDER EXCEPTION', json.dumps(result))

    def test_provider_text_cannot_replace_verified_count_or_date(self):
        self.session.response = Response(envelope([{'text': '{"layout":"chronological","highlights":[],"events":99999}'}]))
        result = self.service().summarize(period='week', reference_date='2030-06-19')
        self.assertEqual(result['summary']['reason'], 'llm_invalid_response')
        self.assertEqual(result['totals']['events'], 1)
        self.assertNotIn('99999', result['summary']['text'])

    def test_api_default_gemini_factory_uses_existing_private_configuration(self):
        from test_api_security import TOKEN
        config = Settings(_env_file=None, api_auth_token=TOKEN, app_env='test',
            llm_provider='gemini', gemini_api_key=KEY, gemini_model=MODEL)
        app = create_app(config=config, db=InjectedDatabase(), summary_calendar_gateway_factory=self.gateway.factory)
        with patch('app.gemini_llm.requests.Session', return_value=self.session):
            status, _, value = asyncio.run(invoke_asgi(app, URL, method='POST', headers=HEADERS,
                body=b'{"period":"week","date":"2030-06-19"}'))
        self.assertEqual(status, 200)
        self.assertEqual(value['data']['summary']['mode'], 'ai_organized')
        self.assertNotIn(KEY, json.dumps(value))
        self.assertEqual(len(self.session.calls), 1)

    def test_full_google_sdk_to_summary_to_gemini_with_both_boundaries_replaced(self):
        backend = SummaryHttpFake()
        backend.pages = [{'items': [event('private-google-occurrence')]}]
        with patch('app.google_transport.requests.Session', side_effect=backend.session), \
                patch('app.summary_calendar.resolve_calendar', side_effect=lambda _service: deepcopy(backend.calendar)), \
                patch('app.google_calendar.get_credentials', side_effect=AssertionError('OAuth real proibido.')):
            transport = SingleAttemptHttp(timeout=20)
            http = AuthorizedHttp(AnonymousCredentials(), http=transport, max_refresh_attempts=0)
            sdk = build('calendar', 'v3', http=http, cache_discovery=False, static_discovery=True)
            try:
                @contextmanager
                def factory():
                    yield GoogleSummaryCalendarGateway(sdk)
                service = AgendaSummaryService(SummaryContextService(factory, clock=self.clock), lambda: self.model)
                result = service.summarize(period='week', reference_date='2030-06-19')
            finally:
                sdk.close()
        self.assertEqual(result['summary']['mode'], 'ai_organized')
        self.assertEqual(result['totals']['events'], 1)
        self.assertEqual(len(self.session.calls), 1)
        self.assertTrue(all(call['method'] == 'GET' for call in backend.calls))
        self.assertNotIn('private-google-occurrence', json.dumps(result))
