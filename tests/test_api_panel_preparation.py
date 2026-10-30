"""Preparation contracts cross the real panel session/CSRF boundary, without network."""

import asyncio
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from fastapi import APIRouter, FastAPI

from app.api_agenda_summary import register_agenda_summary_routes
from app.api_panel_preparation import register_panel_preparation_routes
from app.api_security import RequestCorrelationMiddleware
from app.llm import ModelError, ModelReply
from app.panel_access import PanelAccessStore
from app.panel_security import COOKIE_NAME, PanelAccessError, panel_access_error_handler
from app.panel_web import PanelHeadersMiddleware
from app.services.availability import AvailabilityCollectionError
from app.services.summary_context import SummaryCollectionError
from app.settings import Settings
from test_agenda_summary_api import Model
from test_availability_api import OccupancyGateway, REQUEST as AVAILABILITY
from test_briefing_api import BriefingGateway, EVENT_ID
from test_panel_security import invoke
from test_summary_context_api import Gateway


PRIVATE = 'PRIVATE_PANEL_PREPARATION_DIAGNOSTIC'
SUMMARY = {'period': 'week', 'date': '2030-06-19'}
BRIEFING = {'event_id': EVENT_ID, 'user_context': 'Quero revisar meus exemplos de projetos.'}
CASES = (('/agenda/summary', SUMMARY), ('/agenda/availability', AVAILABILITY), ('/preparation/briefing', BRIEFING))


class PanelPreparationTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = PanelAccessStore(Path(self.directory.name) / 'access.sqlite3')
        self.sid, pending = self.store.begin()
        self.store.authorize(pending.pairing_code)
        self.session = self.store.get(self.sid)
        self.summary = Gateway()
        self.availability = OccupancyGateway()
        self.briefing = BriefingGateway()
        self.model = Model()
        self.model_factory = Mock(return_value=self.model)
        self.app = FastAPI()
        self.app.state.settings = Settings(_env_file=None, panel_enabled=True, api_auth_token=None)
        self.app.state.panel_access_store_factory = lambda: self.store
        self.app.state.summary_calendar_gateway_factory = self.summary.factory
        self.app.state.summary_model_factory = self.model_factory
        self.app.state.availability_gateway_factory = self.availability.factory
        self.app.state.briefing_gateway_factory = self.briefing.factory
        self.app.add_middleware(PanelHeadersMiddleware)
        self.app.add_middleware(RequestCorrelationMiddleware)
        self.app.add_exception_handler(PanelAccessError, panel_access_error_handler)
        register_panel_preparation_routes(self.app)

    def request(self, path, value=None, *, raw=None, method='POST', sid=True, csrf=True,
                extra=(), query=b'', client='127.0.0.1'):
        headers = [(b'host', b'127.0.0.1:8000'), (b'content-type', b'application/json'),
                   (b'origin', b'http://127.0.0.1:8000')]
        if sid:
            headers.append((b'cookie', f'{COOKIE_NAME}={self.sid}'.encode('ascii')))
        if csrf:
            headers.append((b'x-panel-csrf', self.session.csrf_token.encode('ascii')))
        replaced = {name.lower() for name, _ in extra}
        headers = [item for item in headers if item[0] not in replaced] + list(extra)
        body = raw if raw is not None else json.dumps(value).encode('utf-8')
        result = asyncio.run(invoke(self.app, '/painel/api' + path, method=method,
                                   headers=headers, body=body, query=query, client=client))
        text = json.dumps(result, ensure_ascii=False)
        for hidden in (PRIVATE, 'private-calendar@example.invalid', 'private-calendar-name',
                       'private-version', 'https://private.invalid/link'):
            self.assertNotIn(hidden, text)
        self.assertEqual(result[1]['cache-control'], 'no-store')
        self.assertEqual(result[1]['vary'], 'Cookie')
        return result

    def assert_unused(self):
        self.assertEqual((self.summary.opened, self.availability.opened, self.briefing.opened), (0, 0, 0))
        self.model_factory.assert_not_called()
        self.assertEqual(self.model.calls, [])

    def test_session_precedes_json_and_all_provider_factories(self):
        for path, _ in CASES:
            self.assertEqual(self.request(path, raw=b'PRIVATE INVALID JSON', sid=False)[0], 401)
        self.assert_unused()

    def test_csrf_missing_wrong_or_duplicate_blocks_all_resources(self):
        for path, body in CASES:
            self.assertEqual(self.request(path, body, csrf=False)[0], 403)
            self.assertEqual(self.request(path, body, extra=[(b'x-panel-csrf', b'wrong')])[0], 403)
            self.assertEqual(self.request(path, body, extra=[(b'x-panel-csrf', self.session.csrf_token.encode()),
                                                            (b'x-panel-csrf', self.session.csrf_token.encode())])[0], 403)
        self.assert_unused()

    def test_foreign_origin_forwarding_and_nonlocal_client_are_denied(self):
        for path, body in CASES:
            for extra in ([(b'origin', b'https://evil.invalid')], [(b'sec-fetch-site', b'cross-site')],
                          [(b'x-forwarded-for', b'127.0.0.1')]):
                self.assertEqual(self.request(path, body, extra=extra)[0], 403)
            self.assertEqual(self.request(path, body, client='203.0.113.7')[0], 403)
        self.assert_unused()

    def test_revoked_or_pending_session_cannot_consume_model(self):
        self.store.revoke(self.sid)
        for path, body in CASES:
            self.assertEqual(self.request(path, body)[0], 401)
        self.sid, _ = self.store.begin()
        for path, body in CASES:
            self.assertEqual(self.request(path, body)[0], 401)
        self.assert_unused()

    def test_service_key_is_not_a_replacement_for_panel_session(self):
        for path, body in CASES:
            self.assertEqual(self.request(path, body, sid=False,
                             extra=[(b'authorization', b'Bearer PRIVATE_SERVICE_KEY')])[0], 401)
        self.assert_unused()

    def test_summary_uses_existing_facts_and_friendly_organization(self):
        status, _, envelope = self.request('/agenda/summary', SUMMARY)
        self.assertEqual(status, 200)
        self.assertEqual(envelope['action'], 'summarize_agenda')
        self.assertEqual(envelope['mutation_state'], 'not_applicable')
        result = envelope['data']
        self.assertEqual(result['totals']['events'], 1)
        self.assertEqual(result['summary']['mode'], 'ai_organized')
        self.assertFalse(result['execution_allowed'])
        self.assertIn('Entrevista fictícia', result['summary']['text'])
        self.assertNotIn('Consulta UTC', result['summary']['text'])
        self.assertNotIn('America/Recife', result['summary']['text'])
        self.assertNotIn('2030-06-17T', result['summary']['text'])
        self.assertEqual(len(self.model.calls), 1)
        model_input = json.dumps(self.model.calls, ensure_ascii=False)
        for hidden in ('Entrevista fictícia', 'PrivateEventMixedID', 'private-calendar@example.invalid'):
            self.assertNotIn(hidden, model_input)

    def test_empty_summary_does_not_create_or_claim_ai(self):
        self.summary.events = ()
        status, _, envelope = self.request('/agenda/summary', SUMMARY)
        self.assertEqual(status, 200)
        self.assertEqual(envelope['data']['totals']['events'], 0)
        self.assertEqual(envelope['data']['summary'], {
            'text': envelope['data']['summary']['text'], 'mode': 'factual', 'reason': 'empty_period'})
        self.model_factory.assert_not_called()

    def test_unavailable_model_uses_facts_without_retry(self):
        self.model.failure = ModelError('llm_rate_limited')
        status, _, envelope = self.request('/agenda/summary', SUMMARY)
        self.assertEqual(status, 200)
        self.assertEqual(envelope['data']['summary']['mode'], 'factual')
        self.assertEqual(envelope['data']['summary']['reason'], 'llm_rate_limited')
        self.assertEqual(envelope['data']['totals']['events'], 1)
        self.assertEqual(len(self.model.calls), 1)

    def test_untrusted_model_text_is_not_displayed(self):
        self.model.reply = ModelReply(text=PRIVATE, model='fake-model')
        status, _, envelope = self.request('/agenda/summary', SUMMARY)
        self.assertEqual(status, 200)
        self.assertEqual(envelope['data']['summary']['mode'], 'factual')
        self.assertEqual(envelope['data']['summary']['reason'], 'llm_invalid_response')

    def test_calendar_failure_is_not_an_empty_factual_summary(self):
        self.summary.failure = SummaryCollectionError()
        status, _, envelope = self.request('/agenda/summary', SUMMARY)
        self.assertEqual(status, 502)
        self.assertIsNone(envelope['data'])
        self.assertEqual(envelope['error']['code'], 'agenda_collection_failed')
        self.model_factory.assert_not_called()

    def test_availability_reuses_calculation_and_never_reserves(self):
        status, _, envelope = self.request('/agenda/availability', AVAILABILITY)
        self.assertEqual(status, 200)
        self.assertEqual(envelope['action'], 'find_free_slots')
        data = envelope['data']
        self.assertEqual(data['count'], 3)
        self.assertEqual(data['slots'][0]['start'], '2030-06-19T10:00:00-03:00')
        self.assertFalse(data['reservation_created'])
        self.assertFalse(data['execution_allowed'])
        self.assertEqual(self.availability.opened, self.availability.closed)
        self.model_factory.assert_not_called()

    def test_availability_failure_never_claims_a_free_slot(self):
        self.availability.failure = AvailabilityCollectionError()
        status, _, envelope = self.request('/agenda/availability', AVAILABILITY)
        self.assertEqual(status, 502)
        self.assertIsNone(envelope['data'])
        self.model_factory.assert_not_called()

    def test_briefing_includes_facts_notes_and_generic_checklist(self):
        status, _, envelope = self.request('/preparation/briefing', BRIEFING)
        self.assertEqual(status, 200)
        data = envelope['data']
        self.assertEqual(data['status'], 'ready')
        self.assertEqual(data['mode'], 'factual_template')
        self.assertTrue(data['checklist'])
        self.assertIn('Entrevista fictícia', data['text'])
        self.assertFalse(data['provided_context']['verified_externally'])
        self.assertFalse(data['execution_allowed'])
        self.assertFalse(data['reservation_created'])
        self.model_factory.assert_not_called()

    def test_changed_event_asks_for_current_selection(self):
        self.briefing.details = replace(self.briefing.details, title='Título atualizado')
        status, _, envelope = self.request('/preparation/briefing', BRIEFING)
        self.assertEqual(status, 200)
        self.assertEqual(envelope['data']['status'], 'needs_clarification')
        self.assertIsNone(envelope['data']['facts'])

    def test_briefing_without_event_is_clarification_not_provider_read(self):
        status, _, envelope = self.request('/preparation/briefing', {})
        self.assertEqual(status, 200)
        self.assertEqual(envelope['data']['status'], 'needs_clarification')
        self.assertEqual(self.briefing.opened, 0)

    def test_raw_provider_errors_are_sanitized(self):
        self.summary.failure = self.availability.failure = self.briefing.failure = RuntimeError(PRIVATE)
        for path, body in CASES:
            status, _, envelope = self.request(path, body)
            self.assertEqual(status, 502)
            self.assertIsNone(envelope['data'])
        self.model_factory.assert_not_called()

    def test_duplicate_extra_nonobject_and_oversized_body_are_rejected(self):
        cases = [('/agenda/summary', b'{"period":"week","period":"month"}'),
                 ('/agenda/availability', b'{"date_from":"2030-06-19","date_from":"2030-06-20"}'),
                 ('/preparation/briefing', b'{"event_id":"a","event_id":"b"}')]
        for path, raw in cases:
            self.assertEqual(self.request(path, raw=raw)[0], 422)
        for path, body in CASES:
            for raw in (b'[]', b'null', b'bad-json', b'{"x":NaN}', b'x' * 8193, b'\xff'):
                self.assertEqual(self.request(path, raw=raw)[0], 422)
            self.assertEqual(self.request(path, {**body, 'calendar_id': 'other-agenda'})[0], 422)
            self.assertEqual(self.request(path, body, query=b'calendar_id=other')[0], 422)
        self.assert_unused()

    def test_wrong_or_duplicate_content_types_are_rejected(self):
        for path, body in CASES:
            for extra in ([(b'content-type', b'text/plain')],
                          [(b'content-type', b'application/json'), (b'content-type', b'application/json')]):
                self.assertEqual(self.request(path, body, extra=extra)[0], 422)
        self.assert_unused()

    def test_panel_names_do_not_replace_v1_routes_or_default_presentation(self):
        previous = APIRouter(prefix='/api/v1')
        register_agenda_summary_routes(previous)
        self.app.include_router(previous)
        self.assertEqual(previous.routes[0].name, 'api_v1_agenda_summary')
        # FastAPI may defer included routers instead of flattening app.routes.
        paths = self.app.openapi()['paths']
        service_id = paths['/api/v1/agenda/summary']['post']['operationId']
        panel_id = paths['/painel/api/agenda/summary']['post']['operationId']
        self.assertTrue(service_id.startswith('api_v1_agenda_summary_'))
        self.assertTrue(panel_id.startswith('panel_agenda_summary_'))
        self.assertNotEqual(service_id, panel_id)
