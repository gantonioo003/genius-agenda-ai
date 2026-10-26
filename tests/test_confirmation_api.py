"""Confirmações por ASGI: contexto real e serviços somente de leitura fictícios."""
import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import Mock
from uuid import uuid4

from app.api_conversations import conversation_owner_key
from app.confirmation_state import ConfirmationError
from app.conversation_store import ConversationStore, QueryMemory
from app.dialogue_contract import DIALOGUE_TOOLS, DialoguePlan
from app.dialogue_state import PendingRequest
from app.main import create_app
from app.services.confirmations import ConfirmationService
from app.services.confirmation_snapshot import ConfirmationSnapshot
from app.services.agent_dialogue import AgentDialogueService
from app.settings import Settings
from test_api_queries import event
from test_api_security import InjectedDatabase, TOKEN, WRONG_TOKEN, invoke_asgi

BASE = '/api/v1/agent/confirmations/'
NOW = datetime(2030, 6, 17, 12, tzinfo=timezone.utc)
PRIVATE_VERSION = 'PRIVATE_EVENT_ETAG_FIXTURE'
PRIVATE_CALENDAR = 'PRIVATE_CALENDAR_FIXTURE'

class Gateway:
    def __init__(self):
        self.event = event('FixtureEvent')
        self.version = PRIVATE_VERSION
        self.calendar = PRIVATE_CALENDAR
        self.calls = []
        self.error = None
        self.barrier = None
    def get_confirmation_snapshot(self, event_id, *, time_zone):
        self.calls.append((event_id, time_zone))
        if self.barrier:
            self.barrier.wait(timeout=5)
        if self.error:
            raise self.error
        return ConfirmationSnapshot(self.calendar, 'Genius Testes', self.event if event_id else None,
                                    self.version if event_id and self.event else None)

class ConfirmationApiTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'private.sqlite3'
        self.now = NOW
        self.store = ConversationStore(self.path, clock=lambda: self.now)
        self.gateway = Gateway()
        @contextmanager
        def factory():
            yield self.gateway
        self.gateway_factory = factory
        self.service = ConfirmationService(factory, clock=lambda: self.now)
        self.service_factory = Mock(return_value=self.service)
        self.store_factory = Mock(return_value=self.store)
        self.model_factory = Mock(side_effect=AssertionError('No LLM for confirmation'))
        self.app = self.application()

    def application(self, token=TOKEN):
        return create_app(Settings(_env_file=None, api_auth_token=token, openai_api_key=None,
                                  conversation_database_path=self.path), InjectedDatabase(),
                          calendar_gateway_factory=self.gateway_factory,
                          conversation_store_factory=self.store_factory,
                          confirmation_service_factory=self.service_factory,
                          agent_dialogue_service_factory=self.model_factory)

    def headers(self, token=TOKEN):
        return [(b'authorization', f'Bearer {token}'.encode()), (b'content-type', b'application/json'),
                (b'x-request-id', b'confirmation-fixture')]

    def call(self, command, payload=None, *, raw=None, headers=None, query=b'', app=None):
        body = json.dumps(payload).encode() if raw is None else raw
        response = asyncio.run(invoke_asgi(app or self.app, BASE + command, method='POST',
                    body=body, headers=self.headers() if headers is None else headers, query=query))
        text = json.dumps(response)
        for value in (TOKEN, WRONG_TOKEN, PRIVATE_VERSION, PRIVATE_CALENDAR, str(self.path), 'PRIVATE_PROVIDER_ERROR'):
            self.assertNotIn(value, text)
        self.assertEqual(response[1]['cache-control'], 'no-store')
        return response

    def draft(self, fields=None):
        owner = conversation_owner_key(self.app.state.settings)
        cid = self.store.create(owner)
        fields = fields or {'action': 'edit', 'event_id': 'FixtureEvent', 'time_zone': 'America/Recife',
                            'title': 'Novo título fictício'}
        memory = QueryMemory(pending_request=PendingRequest(fields))
        self.store.save(owner, cid, 0, memory)
        return cid

    def snapshot(self, cid):
        return self.store.load(conversation_owner_key(self.app.state.settings), cid)

    def preview(self, cid):
        response = self.call('preview', {'conversation_id': cid})
        self.assertEqual(response[0], 200)
        return response[2]['data']['proposal_id']

    def approve(self, cid, pid, message='confirmo'):
        return self.call('approve', {'conversation_id': cid, 'proposal_id': pid, 'message': message})

    def test_auth_precedes_parsing_and_every_factory(self):
        for command in ('preview', 'approve', 'status', 'revoke'):
            for headers in ([], self.headers(WRONG_TOKEN)):
                self.assertEqual(self.call(command, raw=b'invalid', headers=headers)[0], 401)
        self.store_factory.assert_not_called()
        self.service_factory.assert_not_called()

    def test_unconfigured_api_is_closed(self):
        self.assertEqual(self.call('preview', raw=b'invalid', app=self.application(None))[0], 503)
        self.store_factory.assert_not_called()

    def test_strict_body_uuid_content_type_query_and_limit_precede_store(self):
        cid = str(uuid4())
        for raw in (b'null', b'[]', b'{}', b'\xff', b'{}' + b' ' * 2048,
                    ('{"conversation_id":"' + cid + '","conversation_id":"' + cid + '"}').encode()):
            self.assertEqual(self.call('preview', raw=raw)[0], 422)
        for value in (None, True, '', 'bad', cid.upper()):
            self.assertEqual(self.call('preview', {'conversation_id': value})[0], 422)
        self.assertEqual(self.call('preview', {'conversation_id': cid}, query=b'force=true')[0], 422)
        self.assertEqual(self.call('preview', {'conversation_id': cid}, headers=self.headers()[:1])[0], 422)
        self.store_factory.assert_not_called()

    def test_client_cannot_supply_proposal_or_execute_fields(self):
        for field in ('parameters', 'event_id', 'event_version', 'request_hash', 'execute', 'confirmed', 'approval_message_hash'):
            self.assertEqual(self.call('preview', {'conversation_id': str(uuid4()), field: 'unsafe'})[0], 422)
        self.store_factory.assert_not_called()

    def test_unknown_conversation_precedes_service_and_google(self):
        self.assertEqual(self.call('preview', {'conversation_id': str(uuid4())})[0], 404)
        self.service_factory.assert_not_called()
        self.assertEqual(self.gateway.calls, [])

    def test_preview_returns_exact_target_and_changes_without_provider_version(self):
        cid = self.draft()
        response = self.call('preview', {'conversation_id': cid})
        self.assertEqual(response[0], 200)
        data = response[2]['data']
        self.assertEqual(data['state'], 'pending')
        self.assertEqual(data['event']['event_id'], 'FixtureEvent')
        self.assertEqual(data['parameters']['title'], 'Novo título fictício')
        self.assertFalse(data['execution_allowed'])
        self.assertIsNone(response[2]['operation_id'])
        self.assertEqual(response[2]['mutation_state'], 'not_applied')
        self.assertNotIn('request_hash', data)
        self.model_factory.assert_not_called()

    def test_create_preview_binds_new_event_intent(self):
        cid = self.draft({'action': 'create', 'title': 'Entrevista fictícia',
            'start': '2030-06-17T15:00:00-03:00', 'end': '2030-06-17T15:30:00-03:00',
            'time_zone': 'America/Recife', 'description': ''})
        pid = self.preview(cid)
        self.assertEqual(self.gateway.calls[-1][0], None)
        self.assertIsNone(self.snapshot(cid).memory.confirmation.event_id)
        self.assertEqual(self.approve(cid, pid)[0], 200)

    def test_incomplete_draft_needs_preparation_without_google(self):
        cid = self.draft({'action': 'create', 'title': 'Incomplete'})
        self.assertEqual(self.call('preview', {'conversation_id': cid})[0], 409)
        self.assertEqual(self.gateway.calls, [])

    def test_approval_records_hash_and_timestamp_without_llm(self):
        cid = self.draft()
        pid = self.preview(cid)
        response = self.approve(cid, pid)
        self.assertEqual((response[0], response[2]['status']), (200, 'approved'))
        proposal = self.snapshot(cid).memory.confirmation
        self.assertEqual(proposal.state, 'approved')
        self.assertEqual(len(proposal.approval_message_hash), 64)
        self.assertIsNotNone(proposal.approved_at)
        self.model_factory.assert_not_called()

    def test_only_clear_positive_text_can_approve(self):
        cid = self.draft()
        pid = self.preview(cid)
        for message in ('ok', 'não', 'sim, mas mude o horário', 'confirmo?', 'talvez'):
            self.assertEqual(self.approve(cid, pid, message)[0], 422)
            self.assertEqual(self.snapshot(cid).memory.confirmation.state, 'pending')

    def test_repeated_preview_and_approval_do_not_extend_deadline(self):
        cid = self.draft()
        pid = self.preview(cid)
        self.approve(cid, pid)
        saved = self.snapshot(cid).memory.confirmation
        self.now += timedelta(seconds=20)
        self.assertEqual(self.preview(cid), pid)
        self.assertEqual(self.approve(cid, pid)[0], 200)
        self.assertEqual(self.snapshot(cid).memory.confirmation, saved)

    def test_wrong_proposal_and_wrong_owner_cannot_approve(self):
        cid = self.draft()
        pid = self.preview(cid)
        self.assertEqual(self.approve(cid, str(uuid4()))[0], 404)
        rotated = self.application(WRONG_TOKEN)
        self.assertEqual(self.call('approve', {'conversation_id': cid, 'proposal_id': pid, 'message': 'sim'},
                        app=rotated, headers=self.headers(WRONG_TOKEN))[0], 404)
        self.assertEqual(self.snapshot(cid).memory.confirmation.state, 'pending')

    def test_draft_change_invalidates_approval(self):
        cid = self.draft()
        pid = self.preview(cid)
        self.approve(cid, pid)
        snapshot = self.snapshot(cid)
        fields = {**snapshot.memory.pending_request.fields, 'title': 'Outra alteração'}
        self.store.save(conversation_owner_key(self.app.state.settings), cid, snapshot.revision,
                        replace(snapshot.memory, pending_request=PendingRequest(fields)))
        self.assertEqual(self.approve(cid, pid)[0], 409)

    def test_event_or_calendar_change_rejects_stale_confirmation(self):
        for field in ('version', 'calendar', 'event'):
            with self.subTest(field=field):
                self.gateway = Gateway()
                cid = self.draft()
                pid = self.preview(cid)
                setattr(self.gateway, field, None if field == 'event' else 'changed')
                self.assertEqual(self.approve(cid, pid)[0], 409)
                self.assertEqual(self.snapshot(cid).memory.confirmation.state, 'invalidated')

    def test_expired_proposal_cannot_approve(self):
        cid = self.draft()
        pid = self.preview(cid)
        self.now += timedelta(seconds=300)
        self.assertEqual(self.approve(cid, pid)[0], 409)
        self.assertNotEqual(self.snapshot(cid).memory.confirmation.state, 'approved')

    def test_status_does_not_call_google_or_renew_context_revision(self):
        cid = self.draft()
        pid = self.preview(cid)
        snapshot = self.snapshot(cid)
        calls = len(self.gateway.calls)
        self.now += timedelta(seconds=301)
        response = self.call('status', {'conversation_id': cid, 'proposal_id': pid})
        self.assertEqual(response[0], 200)
        self.assertEqual(response[2]['data']['state'], 'expired')
        self.assertEqual(self.snapshot(cid).revision, snapshot.revision)
        self.assertEqual(len(self.gateway.calls), calls)

    def test_revoke_is_local_and_cannot_approve_later(self):
        cid = self.draft()
        pid = self.preview(cid)
        calls = len(self.gateway.calls)
        self.assertEqual(self.call('revoke', {'conversation_id': cid, 'proposal_id': pid})[0], 200)
        self.assertEqual(len(self.gateway.calls), calls)
        self.assertEqual(self.approve(cid, pid)[0], 409)

    def test_google_error_does_not_replace_pending_confirmation(self):
        cid = self.draft()
        pid = self.preview(cid)
        original = self.snapshot(cid)
        self.gateway.error = OSError('PRIVATE_PROVIDER_ERROR')
        self.assertIn(self.approve(cid, pid)[0], (502, 503))
        self.assertEqual(self.snapshot(cid), original)

    def test_natural_affirmative_uses_bound_confirmation_without_model(self):
        cid = self.draft()
        self.preview(cid)
        response = asyncio.run(invoke_asgi(self.app, '/api/v1/agent/dialogue', method='POST',
                    headers=self.headers(), body=json.dumps({'conversation_id': cid, 'message': 'sim'}).encode()))
        self.assertEqual((response[0], response[2]['status']), (200, 'approved'))
        self.model_factory.assert_not_called()

    def test_successful_new_dialogue_discards_even_identical_old_approval(self):
        cid = self.draft()
        self.preview(cid)
        model = Mock()
        model.plan_dialogue.return_value = DialoguePlan('ask_dialogue', {'reason': 'unclear_answer', 'field': 'request'}, 'fixture-model')
        service = AgentDialogueService(model, self.gateway_factory)
        result, memory = service.respond('Qual dado?', self.snapshot(cid).memory)
        self.assertEqual(result.status, 'needs_clarification')
        self.assertIsNone(memory.confirmation)

    def test_natural_rejection_revokes_without_google_or_llm(self):
        cid = self.draft()
        self.preview(cid)
        calls = len(self.gateway.calls)
        response = asyncio.run(invoke_asgi(self.app, '/api/v1/agent/dialogue', method='POST',
            headers=self.headers(), body=json.dumps({'conversation_id': cid, 'message': 'não confirmo'}).encode()))
        self.assertEqual((response[0], response[2]['status']), (200, 'revoked'))
        self.assertEqual(self.snapshot(cid).memory.confirmation.state, 'revoked')
        self.assertEqual(len(self.gateway.calls), calls)
        self.model_factory.assert_not_called()

    def test_two_approvals_race_through_compare_and_swap(self):
        cid = self.draft()
        pid = self.preview(cid)
        self.gateway.barrier = Barrier(2)
        payload = json.dumps({'conversation_id': cid, 'proposal_id': pid, 'message': 'confirmo'}).encode()
        async def simultaneous():
            return await asyncio.gather(*[invoke_asgi(self.app, BASE + 'approve', method='POST',
                headers=self.headers(), body=payload) for _ in range(2)])
        responses = asyncio.run(simultaneous())
        self.assertEqual(sorted(response[0] for response in responses), [200, 409])
        self.assertEqual(self.snapshot(cid).memory.confirmation.state, 'approved')

    def test_four_routes_are_authenticated_in_openapi(self):
        schema = self.app.openapi()
        for command in ('preview', 'approve', 'status', 'revoke'):
            self.assertIn({'ApiKeyBearer': []}, schema['paths'][BASE + command]['post']['security'])

if __name__ == '__main__':
    unittest.main()
