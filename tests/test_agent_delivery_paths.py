"""Callback, API, worker e serviços reais; somente provedores são fixtures.

As falhas são injetadas em fronteiras de persistência depois das transações
reais anteriores. Nenhum teste lê configuração privada ou abre conexão de rede.
"""

import asyncio
from contextlib import closing, contextmanager
from copy import deepcopy
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.agent_creation_store import AgentCreationStore
from app.agent_deletion_store import AgentDeletionStore
from app.agent_update_store import AgentUpdateStore
from app.api_conversations import conversation_owner_key
from app.conversation_store import ConversationStore
from app.database import Database
from app.llm import ModelError
from app.main import create_app as create_api
from app.sandbox_webhook import create_app as create_ingress
from app.services.agent_dialogue import AgentDialogueService
from app.services.agent_queries import AgentQueriesService
from app.services.agenda_tools import AgendaReadTools
from app.services.confirmations import ConfirmationService
from app.settings import Settings
from app.whatsapp_agent_store import AgentStore
from app.whatsapp_agent_worker import AgentWorker, default_whatsapp_agent_service
from app.whatsapp_inbox import InboxStore
from app.whatsapp_outbox import OutboxStore
from app.whatsapp_sandbox_sender import SandboxSendError
from test_api_security import TOKEN, invoke_asgi
from test_sandbox_webhook import CALLBACK, CONFIG, PHONE, WEBHOOK, invoke
from test_whatsapp_agent import EID, END, NOW, START, TITLE, ZONE, Gateway, Model, plan


AGENT_URL = '/api/v1/whatsapp/sandbox/agent'
CREATE_TEXT = 'Agende Entrevista fictícia em 17/06/2030 das 15h às 15h30'
SENDER_KEY = hmac.new(
    CALLBACK.encode(), b'genius-whatsapp-agent-sender:v1\0' + PHONE.encode(),
    hashlib.sha256,
).hexdigest()


def message(identifier, text='Consulte minha agenda', *, kind='text'):
    result = {'id': identifier, 'from': PHONE, 'timestamp': '1700000000', 'type': kind}
    if kind == 'text':
        result['text'] = {'body': text}
    elif kind == 'image':
        result['image'] = {'url': 'PRIVATE_MEDIA_URL', 'caption': 'PRIVATE_MEDIA_CAPTION'}
    return result


def status(identifier, phase):
    return {'id': identifier, 'recipient_id': PHONE, 'timestamp': '1700000000', 'status': phase}


def batch(*messages, statuses=()):
    value = {}
    if messages:
        value['messages'] = list(messages)
    if statuses:
        value['statuses'] = list(statuses)
    return {'entry': [{'changes': [{'field': 'messages', 'value': value}]}]}


class SenderFixture:
    """Aceitação ou perda da resposta do transporte, sem HTTP."""

    def __init__(self):
        self.calls = []
        self.error = None

    def send(self, recipient, text):
        self.calls.append((recipient, text))
        if self.error is not None:
            raise self.error
        return hashlib.sha256(('fixture-response-' + str(len(self.calls))).encode()).hexdigest()


class AgentDeliveryPathTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix='agent-delivery-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = NOW
        self.model, self.gateway, self.sender = Model(), Gateway(), SenderFixture()
        self.forwarded, self.api_responses = [], []
        self.forward_mode = 'normal'
        self.worker_during_forward = False
        self.config = Settings(
            _env_file=None, api_auth_token=TOKEN, whatsapp_agent_enabled=True,
            openai_api_key=None, gemini_api_key=None,
        )
        self.owner = conversation_owner_key(self.config)
        self.database = Database(self.root / 'application.sqlite3')
        self.database.initialize()
        self.restart()

    @contextmanager
    def gateway_factory(self):
        yield self.gateway

    def restart(self):
        """Reabrir todos os stores e reconstruir aplicações e serviços."""
        self.queue = AgentStore(self.root / 'agent.sqlite3')
        self.inbox = InboxStore(self.root / 'inbox.sqlite3')
        self.outbox = OutboxStore(self.root / 'outbox.sqlite3')
        self.context = ConversationStore(self.root / 'context.sqlite3', clock=lambda: self.now)
        self.creation = AgentCreationStore(self.root / 'creation.sqlite3', clock=lambda: self.now)
        self.updates = AgentUpdateStore(self.root / 'updates.sqlite3', clock=lambda: self.now)
        self.deletions = AgentDeletionStore(self.root / 'deletions.sqlite3', clock=lambda: self.now)
        self.api = create_api(
            self.config, self.database, calendar_gateway_factory=self.gateway_factory,
            conversation_store_factory=lambda: self.context,
            agent_creation_store_factory=lambda: self.creation,
            agent_update_store_factory=lambda: self.updates,
            agent_deletion_store_factory=lambda: self.deletions,
            agent_dialogue_service_factory=lambda: AgentDialogueService(
                self.model, self.gateway_factory, clock=lambda: self.now),
            confirmation_service_factory=lambda: ConfirmationService(
                self.gateway_factory, clock=lambda: self.now),
            agent_query_service_factory=lambda: AgentQueriesService(
                self.model, AgendaReadTools(self.gateway_factory), clock=lambda: self.now),
            whatsapp_agent_store_factory=lambda: self.queue,
        )
        self.api.state.whatsapp_agent_inbox_factory = lambda: self.inbox
        self.worker = AgentWorker(
            store=self.queue, outbox=self.outbox,
            service_factory=lambda: default_whatsapp_agent_service(self.api),
            sender=self.sender, recipient=PHONE, expected_sender_key=SENDER_KEY,
        )
        self.ingress = create_ingress(
            CONFIG, allowed_sender=PHONE, forward=self.forward,
            receipts_path=self.root / 'receipts.jsonl',
            diagnostics_path=self.root / 'diagnostics.jsonl',
            inbox_store=self.inbox, agent_store=self.queue, agent_enabled=True,
        )

    def activate(self, request_id):
        response = asyncio.run(invoke_asgi(
            self.api, AGENT_URL, method='POST',
            headers=[(b'authorization', ('Bearer ' + TOKEN).encode()),
                     (b'content-type', b'application/json'),
                     (b'x-request-id', request_id.encode())],
            body=json.dumps({'request_id': request_id}).encode(),
        ))
        self.api_responses.append(response)
        self.assertEqual(response[1]['cache-control'], 'no-store')
        return response

    def forward(self, summary):
        self.forwarded.append(deepcopy(summary))
        if self.forward_mode == 'before_activation':
            raise OSError('PRIVATE_FORWARD_FAILURE')
        response = self.activate(summary['request_id'])
        self.assertEqual(response[0], 200)
        if self.worker_during_forward and summary['message_count']:
            self.assertEqual(self.worker.run_once(), 'prepared')
            self.assertEqual(self.worker.run_once(), 'sent')
        if self.forward_mode == 'after_activation':
            raise OSError('PRIVATE_FORWARD_FAILURE')
        return {'status': 'received', 'api_reachable': True,
                'request_id': summary['request_id'],
                'reply_state': response[2]['data']['queue_state']}

    def receive(self, value):
        response = asyncio.run(invoke(self.ingress, json.dumps(value).encode()))
        self.assertIn(response, ((200, {'status': 'received'}), (503, {'status': 'unavailable'})))
        public = json.dumps([self.forwarded, self.api_responses, response])
        for path in (self.root / 'receipts.jsonl', self.root / 'diagnostics.jsonl'):
            if path.exists():
                public += path.read_text(encoding='utf-8')
        for private in (TOKEN, CALLBACK, WEBHOOK, PHONE, CREATE_TEXT,
                        'PRIVATE_FORWARD_FAILURE', 'PRIVATE_PROVIDER_DIAGNOSTIC',
                        'PRIVATE_MEDIA_URL', 'PRIVATE_MEDIA_CAPTION'):
            self.assertNotIn(private, public)
        return response[0]

    def rows(self, store, query):
        with closing(sqlite3.connect(store.path)) as connection:
            return connection.execute(query).fetchall()

    def jobs(self):
        identifiers = self.rows(self.queue, 'SELECT job_id FROM agent_jobs ORDER BY sequence')
        return [self.queue.get_job(row[0]) for row in identifiers]

    def writes(self):
        return [call for call in self.gateway.calls if call[0] in {'create', 'patch', 'delete'}]

    def create_plan(self):
        self.model.plan = plan(action='create', title=TITLE, start=START, end=END, time_zone=ZONE)

    def prepare_create(self, identifier='PRIVATE_CREATE_ID'):
        self.create_plan()
        self.assertEqual(self.receive(batch(message(identifier, CREATE_TEXT))), 200)
        self.assertEqual(self.worker.run_once(), 'prepared')
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(self.gateway.current.title, TITLE)
        return self.jobs()[-1]

    def assert_no_more_work(self):
        before = (len(self.model.calls), len(self.gateway.calls), len(self.sender.calls))
        self.assertEqual(self.worker.run_once(), 'idle')
        self.assertEqual(self.worker.run_once(), 'idle')
        self.assertEqual(before, (len(self.model.calls), len(self.gateway.calls), len(self.sender.calls)))

    def test_mixed_replayed_batch_and_out_of_order_statuses_keep_one_reply_per_new_message(self):
        original = message('PRIVATE_FIRST_ID')
        self.assertEqual(self.receive(batch(original)), 200)
        self.assertEqual(self.worker.run_once(), 'prepared')
        self.assertEqual(self.worker.run_once(), 'sent')
        session = self.queue.get_session(SENDER_KEY)
        snapshot = self.context.load(self.owner, session.conversation_id)
        duplicate = deepcopy(original)
        duplicate['text']['body'] = CREATE_TEXT
        media = message('PRIVATE_MEDIA_ID', kind='image')
        mixed = batch(duplicate, original, media, media, statuses=(
            status('PRIVATE_OUTBOUND_ID', 'read'), status('PRIVATE_OUTBOUND_ID', 'sent'),
            status('PRIVATE_OUTBOUND_ID', 'delivered'), status('PRIVATE_OUTBOUND_ID', 'read')))
        self.assertEqual(self.receive(mixed), 200)
        self.assertEqual((self.forwarded[-1]['message_count'], self.forwarded[-1]['status_count']), (1, 3))
        self.restart()
        self.assertEqual(self.worker.run_once(), 'prepared')
        self.assertEqual(self.worker.run_once(), 'sent')
        self.assertIn('texto', self.sender.calls[-1][1])
        self.assertEqual(self.queue.get_session(SENDER_KEY), session)
        self.assertEqual(self.context.load(self.owner, session.conversation_id), snapshot)
        self.assertEqual(len(self.model.calls), 1)
        self.assertEqual(len(self.sender.calls), 2)
        self.assertEqual(self.receive(mixed), 200)
        self.assertEqual(len(self.forwarded), 2)
        self.assertEqual(self.queue.state_counts(), {'sent': 2})
        self.assertFalse(self.writes())
        self.assert_no_more_work()

    def test_loss_before_activation_blocks_mixed_retry_and_fifo_until_explicit_activation(self):
        first = message('PRIVATE_FIRST_ID')
        self.forward_mode = 'before_activation'
        self.assertEqual(self.receive(batch(first)), 503)
        request_id = self.forwarded[0]['request_id']
        self.restart()
        self.forward_mode = 'normal'
        self.assertEqual(self.receive(batch(first, message('PRIVATE_SECOND_ID'))), 503)
        self.assertEqual(self.queue.state_counts(), {'staged': 1})
        self.assertEqual(len(self.forwarded), 1)
        self.assert_no_more_work()
        self.assertFalse(self.context.path.exists())
        self.assertEqual(self.receive(batch(message('PRIVATE_SECOND_ID'))), 200)
        self.assertEqual(self.queue.state_counts(), {'queued': 1, 'staged': 1})
        self.assert_no_more_work()
        self.assertEqual(self.activate(request_id)[0], 200)
        for _ in range(2):
            self.assertEqual(self.worker.run_once(), 'prepared')
            self.assertEqual(self.worker.run_once(), 'sent')
        self.assertEqual(len(self.model.calls), 2)
        self.assertEqual(len(self.sender.calls), 2)
        self.assertEqual(len(self.jobs()), 2)
        self.assertEqual(self.receive(batch(first)), 503)
        self.assertFalse(self.writes())
        self.assert_no_more_work()

    def test_loss_after_activation_retains_executable_job_without_repeating_callback_or_creation(self):
        self.create_plan()
        value = batch(message('PRIVATE_CREATE_ID', CREATE_TEXT))
        self.forward_mode = 'after_activation'
        self.assertEqual(self.receive(value), 503)
        self.assertEqual(self.queue.state_counts(), {'queued': 1})
        self.restart()
        self.forward_mode = 'normal'
        self.assertEqual(self.worker.run_once(), 'prepared')
        self.assertEqual(self.worker.run_once(), 'sent')
        self.assertEqual(self.receive(value), 503)
        self.assertEqual(len(self.forwarded), 1)
        self.assertEqual(len(self.model.calls), 1)
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(len(self.sender.calls), 1)
        self.assertEqual(self.rows(self.inbox, 'SELECT state FROM inbox_events'), [('uncertain',)])
        self.assert_no_more_work()

    def test_inbox_completion_failure_after_worker_finishes_never_repeats_effect_or_reply(self):
        self.create_plan()
        self.worker_during_forward = True
        value = batch(message('PRIVATE_CREATE_ID', CREATE_TEXT))
        with patch.object(self.inbox, 'complete', side_effect=OSError('PRIVATE_COMMIT_FAILURE')):
            self.assertEqual(self.receive(value), 503)
        self.assertEqual(self.queue.state_counts(), {'sent': 1})
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(len(self.sender.calls), 1)
        self.restart()
        self.assertEqual(self.receive(value), 503)
        self.assertEqual(len(self.forwarded), 1)
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(len(self.sender.calls), 1)
        self.assert_no_more_work()

    def test_prepared_real_mutation_reply_and_session_survive_full_restart_without_model(self):
        job = self.prepare_create()
        session = self.queue.get_session(SENDER_KEY)
        memory = self.context.load(self.owner, session.conversation_id)
        self.assertIsNone(memory.memory.pending_request)
        self.assertIsNone(job.text)
        self.assertIn('Compromisso confirmado', job.reply)
        self.restart()
        self.model.error = ModelError('llm_unavailable')
        self.assertEqual(self.worker.run_once(), 'sent')
        self.assertEqual(self.sender.calls, [(PHONE, job.reply)])
        self.assertEqual(self.queue.get_session(SENDER_KEY), session)
        self.assertEqual(self.context.load(self.owner, session.conversation_id), memory)
        self.assertEqual(len(self.model.calls), 1)
        self.assertEqual(len(self.writes()), 1)
        finished = self.jobs()[0]
        self.assertEqual(finished.state, 'sent')
        self.assertIsNone(finished.reply)
        self.assert_no_more_work()

    def test_processing_crash_after_real_ingress_cannot_restart_or_pass_later_sender_job(self):
        self.create_plan()
        value = batch(message('PRIVATE_CREATE_ID', CREATE_TEXT))
        self.assertEqual(self.receive(value), 200)
        self.assertEqual(self.queue.claim_next().state, 'processing')
        self.restart()
        self.assertEqual(self.receive(value), 200)
        self.assertEqual(self.receive(batch(message('PRIVATE_FOLLOWUP_ID'))), 200)
        self.assertEqual(self.queue.state_counts(), {'processing': 1, 'queued': 1})
        self.assert_no_more_work()
        self.assertFalse(self.model.calls)
        self.assertFalse(self.writes())
        self.assertFalse(self.sender.calls)
        self.assertFalse(self.context.path.exists())

    def test_delivery_crash_after_calendar_confirmation_cannot_send_or_recreate_after_restart(self):
        self.prepare_create()
        self.assertEqual(self.queue.claim_next().state, 'delivering')
        self.restart()
        self.assertEqual(self.receive(batch(message('PRIVATE_CREATE_ID', CREATE_TEXT))), 200)
        self.assertEqual(self.receive(batch(message('PRIVATE_FOLLOWUP_ID'))), 200)
        self.assertEqual(self.queue.state_counts(), {'delivering': 1, 'queued': 1})
        self.assert_no_more_work()
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(len(self.model.calls), 1)
        self.assertFalse(self.sender.calls)

    def test_session_commit_failure_after_calendar_write_blocks_new_context_and_blind_recreation(self):
        self.create_plan()
        self.assertEqual(self.receive(batch(message('PRIVATE_CREATE_ID', CREATE_TEXT))), 200)
        with patch.object(self.queue, 'set_session', side_effect=OSError('PRIVATE_COMMIT_FAILURE')):
            self.assertEqual(self.worker.run_once(), 'uncertain')
        self.assertEqual(len(self.writes()), 1)
        self.assertIsNone(self.queue.get_session(SENDER_KEY))
        self.assertTrue(self.context.path.exists())
        self.restart()
        self.assertEqual(self.receive(batch(message('PRIVATE_FOLLOWUP_ID', CREATE_TEXT))), 200)
        self.assertEqual(self.queue.state_counts(), {'queued': 1, 'uncertain': 1})
        self.assert_no_more_work()
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(len(self.model.calls), 1)
        self.assertFalse(self.sender.calls)

    def test_reply_commit_failure_keeps_saved_context_but_never_reexecutes_calendar_write(self):
        self.create_plan()
        self.assertEqual(self.receive(batch(message('PRIVATE_CREATE_ID', CREATE_TEXT))), 200)
        with patch.object(self.queue, 'prepare', side_effect=OSError('PRIVATE_COMMIT_FAILURE')):
            self.assertEqual(self.worker.run_once(), 'uncertain')
        session = self.queue.get_session(SENDER_KEY)
        memory = self.context.load(self.owner, session.conversation_id)
        self.assertIsNone(memory.memory.pending_request)
        self.restart()
        self.assertEqual(self.receive(batch(message('PRIVATE_FOLLOWUP_ID', 'sim'))), 200)
        self.assert_no_more_work()
        self.assertEqual(self.queue.get_session(SENDER_KEY), session)
        self.assertEqual(self.context.load(self.owner, session.conversation_id), memory)
        self.assertEqual(len(self.writes()), 1)
        self.assertFalse(self.sender.calls)

    def test_uncertain_transport_and_late_delivery_status_never_unlock_or_resend_confirmed_mutation(self):
        self.prepare_create()
        self.sender.error = SandboxSendError(uncertain=True)
        self.assertEqual(self.worker.run_once(), 'uncertain')
        request_id = self.forwarded[0]['request_id']
        self.restart()
        self.sender.error = None
        self.assertEqual(self.receive(batch(statuses=(
            status('PRIVATE_OUTBOUND_ID', 'read'), status('PRIVATE_OUTBOUND_ID', 'delivered'),
            status('PRIVATE_OUTBOUND_ID', 'sent')))), 200)
        self.assertEqual(self.receive(batch(message('PRIVATE_FOLLOWUP_ID', CREATE_TEXT))), 200)
        self.assertEqual(self.activate(request_id)[0], 409)
        self.assertEqual(self.queue.state_counts(), {'queued': 1, 'uncertain': 1})
        self.assertEqual(self.rows(self.outbox, 'SELECT state FROM outbox_messages'), [('uncertain',)])
        self.assert_no_more_work()
        self.assertEqual(len(self.sender.calls), 1)
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(len(self.model.calls), 1)

    def test_transport_receipt_commit_failure_after_send_is_never_retried_after_restart(self):
        self.prepare_create()
        with patch.object(self.outbox, 'mark_accepted', side_effect=OSError('PRIVATE_COMMIT_FAILURE')):
            self.assertEqual(self.worker.run_once(), 'uncertain')
        self.assertEqual(self.rows(self.outbox, 'SELECT state FROM outbox_messages'), [('uncertain',)])
        self.restart()
        self.assertEqual(self.receive(batch(message('PRIVATE_CREATE_ID', CREATE_TEXT))), 200)
        self.assert_no_more_work()
        self.assertEqual(len(self.sender.calls), 1)
        self.assertEqual(len(self.writes()), 1)

    def test_queue_completion_failure_preserves_accepted_receipt_without_resending(self):
        self.prepare_create()
        with patch.object(self.queue, 'complete', side_effect=OSError('PRIVATE_COMMIT_FAILURE')):
            self.assertEqual(self.worker.run_once(), 'uncertain')
        self.assertEqual(self.rows(self.outbox, 'SELECT state FROM outbox_messages'), [('accepted',)])
        self.assertEqual(self.queue.state_counts(), {'uncertain': 1})
        self.restart()
        self.assertEqual(self.receive(batch(message('PRIVATE_CREATE_ID', CREATE_TEXT))), 200)
        self.assert_no_more_work()
        self.assertEqual(len(self.sender.calls), 1)
        self.assertEqual(len(self.writes()), 1)

    def test_definite_transport_rejection_does_not_recreate_effect_and_allows_read_followup(self):
        self.prepare_create()
        self.sender.error = SandboxSendError(uncertain=False, http_status=401)
        self.assertEqual(self.worker.run_once(), 'rejected')
        self.restart()
        self.sender.error = None
        self.assertEqual(self.receive(batch(message('PRIVATE_CREATE_ID', CREATE_TEXT))), 200)
        self.assertEqual(self.receive(batch(message('PRIVATE_FOLLOWUP_ID'))), 200)
        self.assertEqual(self.worker.run_once(), 'prepared')
        self.assertEqual(self.worker.run_once(), 'sent')
        self.assertIn(TITLE, self.sender.calls[-1][1])
        self.assertEqual(self.queue.state_counts(), {'rejected': 1, 'sent': 1})
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(len(self.sender.calls), 2)
        self.assert_no_more_work()

    def test_rejected_delete_preview_cannot_be_approved_but_new_delivered_preview_can(self):
        self.model.plan = plan(action='delete', event_id=EID)
        self.assertEqual(self.receive(batch(message('PRIVATE_DELETE_ID', 'Exclua ' + EID))), 200)
        self.assertEqual(self.worker.run_once(), 'prepared')
        preview = self.jobs()[0]
        self.assertIn('sim', preview.reply)
        self.assertFalse(self.writes())
        session = self.queue.get_session(SENDER_KEY)
        proposal_id = self.context.load(self.owner, session.conversation_id).memory.confirmation.proposal_id
        self.sender.error = SandboxSendError(uncertain=False, http_status=401)
        self.assertEqual(self.worker.run_once(), 'rejected')
        self.restart()
        self.sender.error = None
        self.assertEqual(self.receive(batch(message('PRIVATE_UNSEEN_APPROVAL_ID', 'sim'))), 200)
        self.assertEqual(self.worker.run_once(), 'prepared')
        self.assertEqual(self.worker.run_once(), 'sent')
        self.assertFalse(self.writes())
        self.assertIsNotNone(self.gateway.current)
        self.assertEqual(self.receive(batch(message('PRIVATE_NEW_DELETE_ID', 'Exclua ' + EID))), 200)
        self.assertEqual(self.worker.run_once(), 'prepared')
        self.assertEqual(self.worker.run_once(), 'sent')
        next_proposal = self.context.load(self.owner, session.conversation_id).memory.confirmation
        self.assertNotEqual(next_proposal.proposal_id, proposal_id)
        self.assertEqual(self.receive(batch(message('PRIVATE_VISIBLE_APPROVAL_ID', 'sim'))), 200)
        self.assertEqual(self.worker.run_once(), 'prepared')
        self.assertEqual(self.worker.run_once(), 'sent')
        self.assertEqual(len(self.writes()), 1)
        self.assertIsNone(self.gateway.current)
        self.assert_no_more_work()

    def test_rejected_preview_revocation_commit_failure_blocks_followup_after_restart(self):
        self.model.plan = plan(action='delete', event_id=EID)
        self.assertEqual(self.receive(batch(message('PRIVATE_DELETE_ID', 'Exclua ' + EID))), 200)
        self.assertEqual(self.worker.run_once(), 'prepared')
        self.sender.error = SandboxSendError(uncertain=False, http_status=401)
        with patch.object(self.context, 'save', side_effect=OSError('PRIVATE_COMMIT_FAILURE')):
            self.assertEqual(self.worker.run_once(), 'uncertain')
        self.assertEqual(self.queue.state_counts(), {'uncertain': 1})
        self.restart()
        self.sender.error = None
        self.assertEqual(self.receive(batch(message('PRIVATE_APPROVAL_ID', 'sim'))), 200)
        self.assertEqual(self.queue.state_counts(), {'queued': 1, 'uncertain': 1})
        self.assert_no_more_work()
        self.assertFalse(self.writes())
        self.assertIsNotNone(self.gateway.current)
        self.assertEqual(len(self.model.calls), 1)
        self.assertEqual(len(self.sender.calls), 1)

    def test_lost_calendar_response_reconciles_on_new_explicit_turn_without_another_write(self):
        self.create_plan()
        create = self.gateway.create_event

        def applied_then_lost(*args, **kwargs):
            create(*args, **kwargs)
            raise OSError('PRIVATE_PROVIDER_DIAGNOSTIC')

        self.gateway.create_event = applied_then_lost
        first = batch(message('PRIVATE_CREATE_ID', CREATE_TEXT))
        self.assertEqual(self.receive(first), 200)
        self.assertEqual(self.worker.run_once(), 'prepared')
        self.assertIn('não vou repetir', self.jobs()[0].reply)
        self.assertEqual(self.worker.run_once(), 'sent')
        session = self.queue.get_session(SENDER_KEY)
        self.assertIsNotNone(self.context.load(self.owner, session.conversation_id).memory.pending_request)
        self.restart()
        self.assertEqual(self.receive(first), 200)
        self.assert_no_more_work()
        self.assertEqual(self.receive(batch(message('PRIVATE_NEW_EXPLICIT_ID', CREATE_TEXT))), 200)
        self.assertEqual(self.worker.run_once(), 'prepared')
        self.assertIn('Compromisso confirmado', self.jobs()[-1].reply)
        self.assertEqual(self.worker.run_once(), 'sent')
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(len([call for call in self.gateway.calls if call[0] == 'reconcile_create']), 1)
        self.assertIsNone(self.context.load(self.owner, session.conversation_id).memory.pending_request)
        self.assertEqual(self.queue.get_session(SENDER_KEY), session)
        self.assertEqual(len(self.sender.calls), 2)
        self.assert_no_more_work()

    def test_unconfirmed_calendar_response_stays_unknown_without_blind_write_on_new_turn(self):
        self.create_plan()
        self.gateway.create_error = OSError('PRIVATE_PROVIDER_DIAGNOSTIC')
        self.assertEqual(self.receive(batch(message('PRIVATE_CREATE_ID', CREATE_TEXT))), 200)
        self.assertEqual(self.worker.run_once(), 'prepared')
        self.assertIn('não vou repetir', self.jobs()[0].reply)
        self.assertEqual(self.worker.run_once(), 'sent')
        self.restart()
        self.gateway.create_error = None
        self.assertEqual(self.receive(batch(message('PRIVATE_NEW_EXPLICIT_ID', CREATE_TEXT))), 200)
        self.assertEqual(self.worker.run_once(), 'prepared')
        self.assertIn('não vou repetir', self.jobs()[-1].reply)
        self.assertNotIn('Compromisso confirmado', self.jobs()[-1].reply)
        self.assertEqual(self.worker.run_once(), 'sent')
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(len([call for call in self.gateway.calls if call[0] == 'reconcile_create']), 1)
        self.assertEqual(len(self.sender.calls), 2)
        self.assert_no_more_work()


if __name__ == '__main__':
    unittest.main()
