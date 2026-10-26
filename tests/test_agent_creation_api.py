"""Criação explícita do pedido preparado por ASGI, sem provedores reais."""
import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from app.agent_creation_store import AgentCreationStore
from app.api_agent_creation import default_agent_creation_store
from app.api_conversations import conversation_owner_key
from app.conversation_store import ConversationStore, QueryMemory
from app.dialogue_state import PendingRequest
from app.main import create_app
from app.services.confirmations import ConfirmationService
from app.services.confirmation_snapshot import ConfirmationSnapshot
from app.services.interviews import CreatedInterview, ReconciledCreation
from app.settings import Settings, PROJECT_ROOT
from test_api_security import InjectedDatabase, TOKEN, WRONG_TOKEN, invoke_asgi

URL = '/api/v1/agent/create'
PARAMETERS = {'title': 'Entrevista fictícia da API', 'start': '2030-06-17T10:00:00-03:00',
              'end': '2030-06-17T10:30:00-03:00', 'time_zone': 'America/Recife', 'description': ''}
NOW = datetime(2030, 6, 17, 12, tzinfo=timezone.utc)

class Gateway:
    def __init__(self):
        self.calendar = 'PRIVATE_CALENDAR_FIXTURE'
        self.writes = []
        self.reads = []
        self.created = None
        self.error = None
        self.reconcile_status = 'unregistered'
        self.snapshot_hook = None
    def get_confirmation_snapshot(self, event_id, *, time_zone):
        self.reads.append(('snapshot', event_id))
        if self.snapshot_hook:
            self.snapshot_hook()
        return ConfirmationSnapshot(self.calendar, 'Genius Testes', None, None)
    def create_event(self, draft, *, operation_id, expected_calendar_id=None):
        self.writes.append((draft, operation_id, expected_calendar_id))
        if self.error:
            raise self.error
        self.created = CreatedInterview('FixtureCreatedEvent', 'https://example.invalid/event', 'Genius Testes')
        self.reconcile_status = 'found'
        return self.created
    def reconcile_creation(self, draft, *, operation_id, expected_calendar_id):
        self.reads.append(('reconcile', operation_id))
        created = replace(self.created, reused=True, recovered=True) if self.reconcile_status == 'found' else None
        return ReconciledCreation(self.reconcile_status, created)

class AgentCreationApiTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.context_path = Path(temp.name) / 'context.sqlite3'
        self.ledger_path = Path(temp.name) / 'executions.sqlite3'
        self.context = ConversationStore(self.context_path, clock=lambda: NOW)
        self.ledger = AgentCreationStore(self.ledger_path)
        self.gateway = Gateway()
        @contextmanager
        def gateway_factory():
            yield self.gateway
        self.gateway_factory = gateway_factory
        self.context_factory = Mock(return_value=self.context)
        self.ledger_factory = Mock(return_value=self.ledger)
        self.app = self.application()

    def application(self, token=TOKEN):
        return create_app(Settings(_env_file=None, api_auth_token=token, openai_api_key=None,
                    conversation_database_path=self.context_path, agent_creation_database_path=self.ledger_path),
                    InjectedDatabase(), calendar_gateway_factory=self.gateway_factory,
                    conversation_store_factory=self.context_factory,
                    agent_creation_store_factory=self.ledger_factory)

    def headers(self, token=TOKEN):
        return [(b'authorization', f'Bearer {token}'.encode()), (b'content-type', b'application/json'),
                (b'x-request-id', b'agent-create-test')]

    def request(self, body=None, *, raw=None, query=b'', headers=None, app=None):
        response = asyncio.run(invoke_asgi(app or self.app, URL, method='POST',
            body=json.dumps(body).encode() if raw is None else raw, query=query,
            headers=self.headers() if headers is None else headers))
        text = json.dumps(response)
        for value in (TOKEN, WRONG_TOKEN, self.gateway.calendar, str(self.context_path), str(self.ledger_path), 'PRIVATE_PROVIDER_ERROR'):
            self.assertNotIn(value, text)
        self.assertEqual(response[1]['cache-control'], 'no-store')
        return response

    def draft(self, fields=None, awaiting=()):
        owner = conversation_owner_key(self.app.state.settings)
        cid = self.context.create(owner)
        self.context.save(owner, cid, 0, QueryMemory(pending_request=PendingRequest(
            {'action': 'create', **PARAMETERS} if fields is None else fields, awaiting)))
        return cid

    def snapshot(self, cid):
        return self.context.load(conversation_owner_key(self.app.state.settings), cid)

    def test_authentication_precedes_input_and_factories(self):
        for headers in ([], self.headers(WRONG_TOKEN)):
            self.assertEqual(self.request(raw=b'invalid', headers=headers)[0], 401)
        self.context_factory.assert_not_called()
        self.ledger_factory.assert_not_called()
        self.assertEqual(self.gateway.writes, [])

    def test_unconfigured_api_does_not_open_execution_store(self):
        self.assertEqual(self.request(raw=b'invalid', app=self.application(None))[0], 503)
        self.context_factory.assert_not_called()
        self.ledger_factory.assert_not_called()

    def test_only_canonical_conversation_uuid_is_accepted(self):
        cid = str(uuid4())
        for value in (None, True, 4, {}, [], '', 'bad', cid.upper(), cid.replace('-', '')):
            self.assertEqual(self.request({'conversation_id': value})[0], 422)
        self.context_factory.assert_not_called()

    def test_caller_cannot_supply_event_operation_or_authorization_fields(self):
        for name in ('event', 'parameters', 'operation_id', 'confirmed', 'proposal_id', 'execute', 'user_id', 'calendar_id'):
            self.assertEqual(self.request({'conversation_id': str(uuid4()), name: 'unsafe'})[0], 422)
        self.context_factory.assert_not_called()

    def test_raw_json_type_limit_query_and_content_type_are_strict(self):
        cid = str(uuid4())
        for raw in (b'', b'{}', b'[]', b'null', b'\xff', b' ' * 1025,
                    ('{"conversation_id":"' + cid + '","conversation_id":"' + cid + '"}').encode(),
                    b'{"conversation_id":NaN}'):
            self.assertEqual(self.request(raw=raw)[0], 422)
        self.assertEqual(self.request({'conversation_id': cid}, query=b'execute=true')[0], 422)
        self.assertEqual(self.request({'conversation_id': cid}, headers=self.headers()[:1])[0], 422)
        self.context_factory.assert_not_called()

    def test_unknown_conversation_precedes_service_factory(self):
        service = Mock()
        self.app.state.agent_creation_service_factory = service
        self.assertEqual(self.request({'conversation_id': str(uuid4())})[0], 404)
        service.assert_not_called()
        self.ledger_factory.assert_not_called()

    def test_wrong_key_cannot_execute_another_conversation(self):
        cid = self.draft()
        self.assertEqual(self.request({'conversation_id': cid}, app=self.application(WRONG_TOKEN),
                                     headers=self.headers(WRONG_TOKEN))[0], 404)
        self.assertEqual(self.gateway.writes, [])

    def test_complete_draft_executes_without_llm_or_extra_generic_confirmation(self):
        cid = self.draft()
        self.app.state.agent_dialogue_service_factory = Mock(side_effect=AssertionError('No LLM'))
        response = self.request({'conversation_id': cid})
        self.assertEqual((response[0], response[2]['status'], response[2]['mutation_state']), (201, 'succeeded', 'confirmed'))
        self.assertEqual(response[2]['data']['event_id'], 'FixtureCreatedEvent')
        self.assertTrue(response[2]['operation_id'].startswith('agent-create-'))
        self.assertEqual(len(self.gateway.writes), 1)
        draft, operation_id, calendar_id = self.gateway.writes[0]
        self.assertEqual(draft.title, PARAMETERS['title'])
        self.assertEqual(draft.description, '')
        self.assertEqual(operation_id, response[2]['operation_id'])
        self.assertEqual(calendar_id, self.gateway.calendar)
        self.app.state.agent_dialogue_service_factory.assert_not_called()

    def test_repeat_and_new_application_reconcile_without_another_write(self):
        cid = self.draft()
        first = self.request({'conversation_id': cid})
        self.ledger_factory.return_value = AgentCreationStore(self.ledger_path)
        second = self.request({'conversation_id': cid}, app=self.application())
        self.assertEqual(second[0], 200)
        self.assertEqual(first[2]['operation_id'], second[2]['operation_id'])
        self.assertTrue(second[2]['data']['reused'])
        self.assertEqual(len(self.gateway.writes), 1)

    def test_missing_invalid_or_wrong_action_draft_does_not_write(self):
        for fields, awaiting in (({'action': 'create', 'title': 'Missing times'}, ('start', 'end')),
                                 ({'action': 'list', 'date': '2030-06-17', 'time_zone': 'UTC'}, ()),
                                 ({'action': 'create', **PARAMETERS, 'end': PARAMETERS['start']}, ())):
            cid = self.draft(fields, awaiting)
            self.assertIn(self.request({'conversation_id': cid})[0], (409, 422))
        self.assertEqual(self.gateway.writes, [])

    def test_active_confirmation_must_be_approved_and_consumed_once(self):
        cid = self.draft()
        owner = conversation_owner_key(self.app.state.settings)
        service = ConfirmationService(self.gateway_factory, clock=lambda: NOW)
        snapshot = self.snapshot(cid)
        _, memory = service.preview(snapshot.memory)
        self.context.save(owner, cid, snapshot.revision, memory)
        self.assertEqual(self.request({'conversation_id': cid})[0], 409)
        self.assertEqual(self.gateway.writes, [])
        snapshot = self.snapshot(cid)
        _, memory = service.approve(snapshot.memory, snapshot.memory.confirmation.proposal_id, 'confirmo')
        self.context.save(owner, cid, snapshot.revision, memory)
        self.assertIn(self.request({'conversation_id': cid})[0], (200, 201))
        self.assertEqual(self.snapshot(cid).memory.confirmation.state, 'consumed')
        self.assertEqual(self.request({'conversation_id': cid})[0], 200)
        self.assertEqual(len(self.gateway.writes), 1)

    def test_uncertain_result_repeats_only_read_and_preserves_operation_id(self):
        cid = self.draft()
        self.gateway.error = OSError('PRIVATE_PROVIDER_ERROR')
        first = self.request({'conversation_id': cid})
        self.assertEqual(first[0], 502)
        self.assertEqual(first[2]['mutation_state'], 'unknown')
        self.gateway.error = None
        self.gateway.reconcile_status = 'absent'
        second = self.request({'conversation_id': cid})
        self.assertIn(second[0], (409, 502))
        self.assertEqual(second[2]['operation_id'], first[2]['operation_id'])
        self.assertEqual(len(self.gateway.writes), 1)

    def test_unknown_can_be_confirmed_by_read_only_reconciliation(self):
        cid = self.draft()
        self.gateway.error = OSError('PRIVATE_PROVIDER_ERROR')
        self.request({'conversation_id': cid})
        self.gateway.error = None
        self.gateway.created = CreatedInterview('FixtureCreatedEvent', 'https://example.invalid/event', 'Genius Testes')
        self.gateway.reconcile_status = 'found'
        recovered = self.request({'conversation_id': cid})
        self.assertEqual(recovered[0], 200)
        self.assertTrue(recovered[2]['data']['recovered'])
        self.assertEqual(len(self.gateway.writes), 1)

    def test_context_changed_during_calendar_read_prevents_write(self):
        cid = self.draft()
        def change():
            snapshot = self.snapshot(cid)
            changed = PendingRequest({'action': 'create', **PARAMETERS, 'title': 'Mudou antes da execução'})
            self.context.save(conversation_owner_key(self.app.state.settings), cid, snapshot.revision,
                              replace(snapshot.memory, pending_request=changed))
        self.gateway.snapshot_hook = change
        response = self.request({'conversation_id': cid})
        self.assertIn(response[0], (409, 422))
        self.assertEqual(self.gateway.writes, [])

    def test_relative_execution_database_path_uses_project_root(self):
        config = Settings(_env_file=None, agent_creation_database_path=Path('data/private-operations.sqlite3'))
        with patch('app.api_agent_creation.AgentCreationStore') as constructor:
            default_agent_creation_store(config)
        constructor.assert_called_once_with(PROJECT_ROOT / 'data/private-operations.sqlite3')

    def test_simultaneous_commands_issue_at_most_one_write(self):
        cid = self.draft()
        barrier = Barrier(2)
        self.gateway.snapshot_hook = lambda: barrier.wait(timeout=5)
        payload = json.dumps({'conversation_id': cid}).encode()
        async def simultaneous():
            return await asyncio.gather(*[invoke_asgi(self.app, URL, method='POST',
                body=payload, headers=self.headers()) for _ in range(2)])
        responses = asyncio.run(simultaneous())
        # A reconciliação concorrente pode confirmar antes do recibo da escrita;
        # nesse caso ambos reutilizam a confirmação e respondem 200.
        confirmed = [item for item in responses if item[0] in (200, 201)
                     and item[2]['status'] == 'succeeded'
                     and item[2]['mutation_state'] == 'confirmed']
        self.assertTrue(confirmed, [item[0] for item in responses])
        self.assertEqual(len(self.gateway.writes), 1)
        for response in confirmed:
            self.assertEqual(response[2]['data']['event_id'], 'FixtureCreatedEvent')
            self.assertEqual(response[2]['operation_id'], self.gateway.writes[0][1])

    def test_execution_route_retains_bearer_security(self):
        self.assertIn({'ApiKeyBearer': []}, self.app.openapi()['paths'][URL]['post']['security'])

    def test_invalid_result_after_service_execution_remains_unknown(self):
        cid = self.draft()
        calls = []
        class InvalidService:
            def execute(self, owner, conversation):
                calls.append(conversation)
                return {'status': 'succeeded', 'private': 'PRIVATE_PROVIDER_ERROR'}
        self.app.state.agent_creation_service_factory = InvalidService
        response = self.request({'conversation_id': cid})
        self.assertEqual(calls, [cid])
        self.assertEqual(response[0], 502)
        self.assertEqual(response[2]['mutation_state'], 'unknown')
        self.assertEqual(response[2]['error']['code'], 'operation_not_confirmed')
        self.assertFalse(response[2]['error']['retryable'])

if __name__ == '__main__':
    unittest.main()
