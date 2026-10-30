"""Panel commands cross real session/CSRF boundaries, without provider writes."""

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from app.main import create_app
from app.panel_access import PanelAccessStore
from app.panel_security import COOKIE_NAME
from app.settings import Settings
from test_api_security import InjectedDatabase, invoke_asgi


PRIVATE = 'PRIVATE_SERVICE_VALUE_MUST_NOT_APPEAR'


class FakeService:
    def __init__(self):
        self.calls = []
        self.failure = None

    def result(self, command, session, payload):
        from app.services.panel_agenda import PanelMutationResult
        self.calls.append((command, session, payload))
        if self.failure:
            raise self.failure
        return PanelMutationResult({'operation_id': str(uuid4()), 'state': 'pending' if command == 'preview' else 'succeeded',
            'message': 'Confira os dados.', 'mutation_state': 'not_applied' if command == 'preview' else 'applied'})

    def preview(self, session, **payload):
        return self.result('preview', session, payload)

    def confirm(self, session, operation):
        return self.result('confirm', session, operation)

    def cancel(self, session, operation):
        return self.result('cancel', session, operation)

    def status(self, session, operation):
        return self.result('status', session, operation)


class PanelAgendaHttpTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = PanelAccessStore(Path(self.directory.name) / 'access.sqlite3')
        self.sid, pending = self.store.begin()
        self.store.authorize(pending.pairing_code)
        self.session = self.store.get(self.sid)
        self.service = FakeService()
        self.app = create_app(config=Settings(_env_file=None, api_auth_token=PRIVATE, whatsapp_agent_enabled=False),
                              db=InjectedDatabase(), panel_access_store_factory=lambda: self.store,
                              panel_agenda_service_factory=lambda: self.service)
        self.operation = str(uuid4())

    def request(self, path='/agenda/preview', *, method='POST', body=None, raw=None, sid=True, csrf=True,
                extra=(), query=b''):
        headers = [(b'host', b'127.0.0.1:8000')]
        if method == 'POST':
            headers += [(b'origin', b'http://127.0.0.1:8000'), (b'content-type', b'application/json')]
        if sid:
            headers.append((b'cookie', f'{COOKIE_NAME}={self.sid}'.encode()))
        if csrf:
            headers.append((b'x-panel-csrf', self.session.csrf_token.encode()))
        replaced = {key for key, _ in extra}
        headers = [item for item in headers if item[0] not in replaced] + list(extra)
        data = raw if raw is not None else json.dumps({} if body is None else body).encode()
        result = asyncio.run(invoke_asgi(self.app, '/painel/api' + path, method=method,
                                        body=data, headers=headers, query=query))
        self.assertEqual(result[1]['cache-control'], 'no-store')
        self.assertNotIn(PRIVATE, str(result))
        return result

    def preview(self, action='create'):
        body = {'request_id': str(uuid4()), 'action': action}
        if action != 'create':
            body['event_id'] = 'OpaquePanelEventID'
        if action in {'create', 'reschedule'}:
            body['event'] = {'start': '2030-06-18T09:00', 'end': '2030-06-18T09:30'}
            if action == 'create':
                body['event'].update(title='Entrevista fictícia', description='Preparação')
        elif action == 'edit':
            body['event'] = {'description': '', 'location': 'Sala virtual'}
        return body

    def test_all_previews_are_bound_to_authenticated_session(self):
        for action in ('create', 'edit', 'reschedule', 'delete'):
            with self.subTest(action=action):
                body = self.preview(action)
                status, _, result = self.request(body=body)
                self.assertEqual(status, 200)
                self.assertEqual(self.service.calls[-1], ('preview', self.session.id_hash, body))
                self.assertEqual(result['data']['state'], 'pending')

    def test_mutations_require_session_before_reading_payload(self):
        self.assertEqual(self.request(body=self.preview(), sid=False)[0], 401)
        self.assertEqual(self.service.calls, [])

    def test_csrf_required_for_preview_confirmation_and_cancellation(self):
        for path in ('/agenda/preview', f'/agenda/{self.operation}/confirm', f'/agenda/{self.operation}/cancel'):
            self.assertEqual(self.request(path, body=self.preview(), csrf=False)[0], 403)
        self.assertFalse(self.service.calls)

    def test_foreign_origin_or_tunnel_never_calls_executor(self):
        for headers in ([(b'origin', b'http://evil.invalid')], [(b'x-forwarded-host', b'evil.invalid')],
                        [(b'sec-fetch-site', b'cross-site')]):
            self.assertEqual(self.request(body=self.preview(), extra=headers)[0], 403)
        self.assertFalse(self.service.calls)

    def test_service_key_cannot_replace_browser_session(self):
        self.assertEqual(self.request(body=self.preview(), sid=False,
            extra=[(b'authorization', ('Bearer ' + PRIVATE).encode())])[0], 401)

    def test_confirm_and_cancel_accept_only_opaque_id_and_empty_body(self):
        for command in ('confirm', 'cancel'):
            path = f'/agenda/{self.operation}/{command}'
            self.assertEqual(self.request(path)[0], 200)
            self.assertEqual(self.service.calls[-1], (command, self.session.id_hash, self.operation))
            before = len(self.service.calls)
            self.assertEqual(self.request(path, body={'event_id': 'another-event'})[0], 422)
            self.assertEqual(before, len(self.service.calls))

    def test_status_uses_session_but_not_csrf(self):
        self.assertEqual(self.request(f'/agenda/{self.operation}', method='GET', csrf=False)[0], 200)
        self.assertEqual(self.service.calls[-1], ('status', self.session.id_hash, self.operation))
        self.assertEqual(self.request(f'/agenda/{self.operation}', method='GET', sid=False, csrf=False)[0], 401)

    def test_invalid_operation_ids_and_queries_never_reach_service(self):
        for path in ('/agenda/invalid', '/agenda/invalid/confirm'):
            self.assertEqual(self.request(path, method='GET' if path.endswith('invalid') else 'POST')[0], 422)
        self.assertEqual(self.request(body=self.preview(), query=b'calendar_id=private')[0], 422)
        self.assertFalse(self.service.calls)

    def test_malformed_duplicate_and_oversized_bodies_are_rejected(self):
        for body in (b'[]', b'null', b'{', b'{"action":"delete","action":"create"}', b'{"x":NaN}',
                     b'{"x":"' + b'a' * 24000 + b'"}'):
            self.assertEqual(self.request(raw=body)[0], 422)
        self.assertFalse(self.service.calls)

    def test_content_type_and_duplicate_content_type_rejected(self):
        for headers in ([(b'content-type', b'text/plain')],
                        [(b'content-type', b'application/json'), (b'content-type', b'application/json')]):
            self.assertEqual(self.request(body=self.preview(), extra=headers)[0], 422)
        self.assertFalse(self.service.calls)

    def test_preview_keys_actions_and_values_strict(self):
        bodies = [dict(self.preview(), action='tick'), dict(self.preview(), event_id='another'),
                  dict(self.preview(), calendar_id='private'), dict(self.preview(), request_id='not-a-uuid'),
                  dict(self.preview(), event={'title': True, 'start': 'x', 'end': 'y'}),
                  dict(self.preview('edit'), event={}), dict(self.preview('reschedule'), event={'start': 'x'}),
                  dict(self.preview('delete'), event={}), dict(self.preview('edit'), event={'description': '\ud800'})]
        for body in bodies:
            self.assertEqual(self.request(body=body)[0], 422)
        self.assertFalse(self.service.calls)

    def test_create_location_is_not_silently_ignored(self):
        body = self.preview()
        body['event']['location'] = 'Sala'
        self.assertEqual(self.request(body=body)[0], 422)
        self.assertFalse(self.service.calls)

    def test_unknown_error_after_confirmation_never_claims_not_applied(self):
        self.service.failure = RuntimeError(PRIVATE)
        status, _, result = self.request(f'/agenda/{self.operation}/confirm')
        self.assertEqual(status, 503)
        self.assertEqual(result['mutation_state'], 'unknown')
        self.assertFalse(result['error']['retryable'])

    def test_unexpected_preview_error_is_sanitized(self):
        self.service.failure = RuntimeError(PRIVATE)
        self.assertEqual(self.request(body=self.preview())[0], 503)

    def test_revoked_session_cannot_confirm(self):
        self.store.revoke(self.sid)
        self.assertEqual(self.request(f'/agenda/{self.operation}/confirm')[0], 401)
        self.assertFalse(self.service.calls)
