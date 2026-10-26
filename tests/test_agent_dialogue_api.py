"""Fronteira HTTP da conversa com autenticação, persistência e falhas controladas."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, get_ident
import unittest
from unittest.mock import Mock
from uuid import uuid4

from app.api_conversations import conversation_owner_key
from app.conversation_store import ConversationStore, QueryMemory
from app.dialogue_state import PendingRequest
from app.llm import ModelError
from app.main import create_app
from app.services.agent_dialogue import DialogueResult
from app.services.agent_queries import AgentQueriesService
from app.services.agenda_tools import AgendaReadTools
from app.agent_query_contract import QueryPlan
from app.settings import Settings
from test_api_queries import QueryFactory, QueryGateway, event
from test_api_security import InjectedDatabase, TOKEN, WRONG_TOKEN, invoke_asgi

ENDPOINT = '/api/v1/agent/dialogue'
DRAFT = PendingRequest({'action': 'create', 'title': 'Título fictício privado'}, ('start', 'end'))

def question():
    return DialogueResult('needs_clarification', 'create', 'Informe início e término.', {
        'reason': 'missing_information', 'question': 'Informe início e término.',
        'missing_fields': ['start', 'end'], 'invalid_fields': [], 'event': None,
        'candidates': [], 'parameters': None, 'execution_allowed': False,
    })

class Service:
    def __init__(self):
        self.calls = []
        self.result = question()
        self.memory = None
        self.error = None
        self.barrier = None

    def respond(self, message, memory):
        self.calls.append((message, memory, get_ident()))
        if self.barrier:
            self.barrier.wait(timeout=5)
        if self.error:
            raise self.error
        return self.result, (self.memory if self.memory is not None else replace(memory, pending_request=DRAFT))

class DialogueApiTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'private-context.sqlite3'
        self.store = ConversationStore(self.path)
        self.service = Service()
        self.store_factory = Mock(return_value=self.store)
        self.factory = Mock(return_value=self.service)
        self.app = self.application()

    def application(self, *, token=TOKEN, factory='configured'):
        config = Settings(_env_file=None, app_env='test', api_auth_token=token,
                          openai_api_key=None, conversation_database_path=self.path)
        return create_app(config, InjectedDatabase(), conversation_store_factory=self.store_factory,
                          agent_dialogue_service_factory=self.factory if factory == 'configured' else factory)

    def headers(self, token=TOKEN):
        return [(b'authorization', f'Bearer {token}'.encode()), (b'content-type', b'application/json'),
                (b'x-request-id', b'dialogue-fixture')]

    def call(self, body=None, *, headers=None, app=None, raw=None, query=b''):
        if raw is None:
            raw = json.dumps(body).encode()
        response = asyncio.run(invoke_asgi(app or self.app, ENDPOINT, method='POST',
            body=raw, headers=self.headers() if headers is None else headers, query=query))
        encoded = json.dumps(response)
        for value in (TOKEN, WRONG_TOKEN, str(self.path), 'private-provider-detail'):
            self.assertNotIn(value, encoded)
        self.assertEqual(response[1]['cache-control'], 'no-store')
        self.assertEqual(response[1]['x-request-id'], 'dialogue-fixture' if headers is None else response[1]['x-request-id'])
        return response

    def create(self, app=None):
        return self.store.create(conversation_owner_key((app or self.app).state.settings))

    def snapshot(self, cid):
        return self.store.load(conversation_owner_key(self.app.state.settings), cid)

    def send(self, cid, **options):
        return self.call({'message': 'Prepare uma entrevista fictícia.', 'conversation_id': cid}, **options)

    def test_authentication_precedes_body_store_and_model(self):
        for headers in ([], self.headers(WRONG_TOKEN)):
            response = self.call(headers=headers, raw=b'invalid')
            self.assertEqual(response[0], 401)
        self.store_factory.assert_not_called()
        self.factory.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_missing_api_key_does_not_open_context(self):
        response = self.call(raw=b'invalid', app=self.application(token=None))
        self.assertEqual(response[0], 503)
        self.store_factory.assert_not_called()

    def test_body_requires_exactly_message_and_canonical_conversation(self):
        for body in (None, [], {}, {'message': 'x'}, {'conversation_id': str(uuid4())},
                     {'message': 'x', 'conversation_id': None},
                     {'message': 'x', 'conversation_id': 'invalid'},
                     {'message': 'x', 'conversation_id': str(uuid4()).upper()}):
            self.assertEqual(self.call(body)[0], 422)
        self.store_factory.assert_not_called()
        self.factory.assert_not_called()

    def test_confirmation_and_execution_fields_cannot_enter_the_contract(self):
        for name in ('confirmed', 'execute', 'confirmation_token', 'operation_id', 'user_id', 'calendar_id'):
            body = {'message': 'x', 'conversation_id': str(uuid4()), name: True}
            self.assertEqual(self.call(body)[0], 422)
        self.store_factory.assert_not_called()

    def test_duplicate_keys_bad_utf8_constants_and_oversize_rejected_before_store(self):
        cid = str(uuid4())
        for raw in (b'\xff', b'{}' + b' ' * (24 * 1024),
                    ('{"message":"x","message":"y","conversation_id":"' + cid + '"}').encode(),
                    ('{"message":NaN,"conversation_id":"' + cid + '"}').encode(),
                    json.dumps({'message': '\ud800', 'conversation_id': cid}).encode(),
                    json.dumps({'message': 'x' * 4001, 'conversation_id': cid}).encode()):
            self.assertEqual(self.call(raw=raw)[0], 422)
        self.store_factory.assert_not_called()

    def test_query_and_content_type_rejected_before_store(self):
        self.assertEqual(self.send(str(uuid4()), query=b'execute=true')[0], 422)
        headers = self.headers()
        for modified in (headers[:1], headers + [(b'content-type', b'application/json')],
                         [headers[0], (b'content-type', b'text/plain')]):
            self.assertEqual(self.send(str(uuid4()), headers=modified)[0], 422)
        self.store_factory.assert_not_called()

    def test_unknown_context_is_rejected_before_model_construction(self):
        response = self.send(str(uuid4()))
        self.assertEqual((response[0], response[2]['error']['code']), (404, 'conversation_not_found'))
        self.factory.assert_not_called()

    def test_question_is_persisted_without_authorizing_mutations_or_echoing_id(self):
        cid = self.create()
        response = self.send(cid)
        self.assertEqual(response[0], 200)
        self.assertEqual(response[2]['status'], 'needs_clarification')
        self.assertEqual(response[2]['mutation_state'], 'not_applied')
        self.assertIsNone(response[2]['operation_id'])
        self.assertFalse(response[2]['data']['execution_allowed'])
        self.assertNotIn(cid, json.dumps(response))
        self.assertNotIn(DRAFT.fields['title'], json.dumps(response, ensure_ascii=False))
        self.assertEqual(self.snapshot(cid).memory.pending_request, DRAFT)
        self.assertEqual(self.snapshot(cid).revision, 1)

    def test_second_turn_receives_only_persisted_same_conversation_draft(self):
        first, second = self.create(), self.create()
        self.send(first)
        self.send(first)
        self.send(second)
        self.assertEqual([call[1].pending_request for call in self.service.calls], [None, DRAFT, None])

    def test_new_application_and_store_can_resume_draft(self):
        cid = self.create()
        self.send(cid)
        restarted = self.application()
        self.store_factory.return_value = ConversationStore(self.path)
        self.send(cid, app=restarted)
        self.assertEqual(self.service.calls[-1][1].pending_request, DRAFT)

    def test_key_rotation_cannot_access_old_conversation(self):
        cid = self.create()
        rotated = self.application(token=WRONG_TOKEN)
        response = self.send(cid, app=rotated, headers=self.headers(WRONG_TOKEN))
        self.assertEqual(response[0], 404)
        self.factory.assert_not_called()

    def test_expired_context_never_invokes_model(self):
        now = [datetime(2030, 6, 17, tzinfo=timezone.utc)]
        self.store = ConversationStore(self.path, clock=lambda: now[0])
        self.store_factory.return_value = self.store
        cid = self.create()
        now[0] += timedelta(seconds=1800)
        self.assertEqual(self.send(cid)[0], 404)
        self.factory.assert_not_called()

    def test_model_timeout_keeps_original_revision_and_memory(self):
        cid = self.create()
        self.service.error = ModelError('llm_timeout')
        self.assertEqual(self.send(cid)[0], 504)
        self.assertEqual(self.snapshot(cid).revision, 0)
        self.assertEqual(self.snapshot(cid).memory, QueryMemory())

    def test_calendar_failure_does_not_save_even_valid_changed_memory(self):
        cid = self.create()
        self.service.result = DialogueResult('failed', 'create', 'Falha controlada.', None, 502, 'operation_failed')
        self.assertEqual(self.send(cid)[0], 502)
        self.assertEqual(self.snapshot(cid).revision, 0)

    def test_result_cannot_smuggle_extra_data_or_execution_permission(self):
        cid = self.create()
        for changes in ({'execution_allowed': True}, {'private-provider-detail': 'unsafe'}):
            self.service.result = replace(question(), data={**question().data, **changes})
            response = self.send(cid)
            self.assertEqual((response[0], response[2]['error']['code']), (502, 'llm_invalid_response'))
            self.assertEqual(self.snapshot(cid).revision, 0)

    def test_invalid_new_memory_is_rejected_before_save(self):
        cid = self.create()
        self.service.memory = QueryMemory(pending_request={'unsafe': 'private-provider-detail'})
        self.assertEqual(self.send(cid)[0], 502)
        self.assertEqual(self.snapshot(cid).revision, 0)

    def test_cas_concurrency_returns_409_without_model_retry(self):
        cid = self.create()
        self.service.barrier = Barrier(2)
        payload = json.dumps({'message': 'Continuar.', 'conversation_id': cid}).encode()
        async def simultaneous():
            return await asyncio.gather(*[invoke_asgi(self.app, ENDPOINT, method='POST', headers=self.headers(), body=payload) for _ in range(2)])
        responses = asyncio.run(simultaneous())
        self.assertEqual(sorted(response[0] for response in responses), [200, 409])
        self.assertEqual(len(self.service.calls), 2)
        self.assertEqual(self.snapshot(cid).revision, 1)

    def test_service_runs_on_worker(self):
        cid = self.create()
        loop_thread = get_ident()
        self.send(cid)
        self.assertNotEqual(self.service.calls[0][2], loop_thread)

    def test_default_service_without_model_key_preserves_context(self):
        app = self.application(factory=None)
        cid = self.create(app)
        response = self.send(cid, app=app)
        self.assertEqual((response[0], response[2]['error']['code']), (503, 'llm_not_configured'))
        self.assertEqual(self.snapshot(cid).revision, 0)

    def test_storage_errors_are_safe_and_do_not_invoke_model(self):
        self.store_factory.side_effect = OSError('private-provider-detail')
        response = self.send(str(uuid4()))
        self.assertEqual((response[0], response[2]['error']['code']), (503, 'conversation_unavailable'))
        self.factory.assert_not_called()

    def test_read_queries_keep_pending_draft_on_list_get_and_absence(self):
        gateway = QueryGateway()
        model = Mock()
        service = AgentQueriesService(model, AgendaReadTools(QueryFactory(gateway)))
        memory = QueryMemory(pending_request=DRAFT)
        for plan, message in (
            (QueryPlan('list_events', {'date': '2030-06-17', 'period': 'day', 'time_zone': 'America/Recife'}, 'fixture-model'), 'Agenda'),
            (QueryPlan('get_event', {'event_id': 'OpaqueMixedID', 'time_zone': 'America/Recife'}, 'fixture-model'), 'OpaqueMixedID'),
        ):
            model.plan_query.return_value = plan
            result, next_memory = service.query_with_context(message, memory)
            self.assertEqual(result.status, 'succeeded')
            self.assertEqual(next_memory.pending_request, DRAFT)
        gateway.lookup = None
        _, next_memory = service.query_with_context('OpaqueMixedID', memory)
        self.assertEqual(next_memory.pending_request, DRAFT)

    def test_openapi_declares_auth_on_new_endpoint(self):
        security = self.app.openapi()['paths'][ENDPOINT]['post']['security']
        self.assertIn({'ApiKeyBearer': []}, security)

if __name__ == '__main__':
    unittest.main()
