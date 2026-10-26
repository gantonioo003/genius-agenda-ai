"""Orquestração real e SQLite temporário; sem LLM, Google ou configuração privada."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

from app.agent_creation_store import AgentCreationError, AgentCreationStore
from app.confirmation_state import ConfirmationError, ConfirmationProposal, proposal_request_hash
from app.conversation_store import ConversationError, ConversationStore, QueryMemory
from app.dialogue_contract import DIALOGUE_TOOLS, DialoguePlan
from app.dialogue_state import PendingRequest
from app.google_calendar import CalendarSetupError
from app.services.agent_creation import AgentCreationService, CreationResult, validate_creation_result
from app.services.agent_dialogue import AgentDialogueService
from app.services.agenda import AgendaEvent, CalendarPeriod
from app.services.conflicts import CalendarConflictError, build_conflict_report
from app.services.confirmation_snapshot import ConfirmationSnapshot
from app.services.interviews import CalendarBindingError, CreatedInterview, ReconciledCreation
from uuid import uuid4

NOW = datetime(2030, 6, 17, 12, tzinfo=timezone.utc)
OWNER = sha256(b'fixture-owner').hexdigest()
OTHER = sha256(b'other-fixture-owner').hexdigest()
PARAMETERS = {'title': 'Entrevista fictícia', 'start': '2030-06-17T15:00:00-03:00',
    'end': '2030-06-17T15:30:00-03:00', 'time_zone': 'America/Recife', 'description': ''}
CALENDAR = 'fixture-calendar'

class Gateway:
    def __init__(self):
        self.calendar = CALENDAR
        self.calls = []
        self.current = None
        self.create_error = None
        self.snapshot_error = None
        self.reconcile_error = None
        self.reconcile_override = None
        self.snapshot_callback = None
        self.created_callback = None
        self.lost_response = False
        self.malformed = False
        self.return_reused = False
        self.entered = None
        self.release = None
    def get_confirmation_snapshot(self, event_id, *, time_zone):
        self.calls.append(('snapshot', event_id, time_zone))
        if self.snapshot_error:
            raise self.snapshot_error
        if self.snapshot_callback:
            self.snapshot_callback()
        return ConfirmationSnapshot(self.calendar, 'Genius Testes', None, None)
    def create_event(self, draft, *, operation_id, expected_calendar_id):
        self.calls.append(('create', draft, operation_id, expected_calendar_id))
        if self.entered:
            self.entered.set()
            assert self.release.wait(timeout=5)
        if self.create_error:
            raise self.create_error
        self.current = CreatedInterview('FixtureCreated', 'https://example.invalid/created', 'Genius Testes', self.return_reused)
        if self.created_callback:
            self.created_callback()
        if self.lost_response:
            raise OSError('PRIVATE_PROVIDER_DIAGNOSTIC')
        return None if self.malformed else self.current
    def reconcile_creation(self, draft, *, operation_id, expected_calendar_id):
        self.calls.append(('reconcile', draft, operation_id, expected_calendar_id))
        if self.reconcile_error:
            raise self.reconcile_error
        if self.reconcile_override is not None:
            return self.reconcile_override
        return ReconciledCreation('found', self.current) if self.current else ReconciledCreation('unregistered')

class AgentCreationTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = NOW
        self.context = ConversationStore(self.root / 'conversation.sqlite3', clock=lambda: self.now)
        self.ledger = AgentCreationStore(self.root / 'execution.sqlite3', clock=lambda: self.now)
        self.cid = self.context.create(OWNER)
        self.context.save(OWNER, self.cid, 0, QueryMemory(pending_request=PendingRequest({'action': 'create', **PARAMETERS})))
        self.gateway = Gateway()
        self.opened = self.closed = 0
        self.service = AgentCreationService(self.context, self.ledger, self.factory)
        self.addCleanup(self.assert_closed)
    @contextmanager
    def factory(self):
        self.opened += 1
        try:
            yield self.gateway
        finally:
            self.closed += 1
    def assert_closed(self):
        self.assertEqual(self.opened, self.closed)
    def execute(self):
        result = self.service.execute(OWNER, self.cid)
        text = repr(result)
        self.assertNotIn('PRIVATE_PROVIDER_DIAGNOSTIC', text)
        self.assertIs(validate_creation_result(result), result)
        return result
    def record(self, parameters=PARAMETERS):
        return self.ledger.get(OWNER, self.cid, parameters)
    def writes(self):
        return [call for call in self.gateway.calls if call[0] == 'create']
    def save_memory(self, memory):
        old = self.context.load(OWNER, self.cid)
        self.context.save(OWNER, self.cid, old.revision, memory)
    def confirmation(self, *, state='approved', parameters=PARAMETERS, calendar=CALENDAR):
        return ConfirmationProposal(str(uuid4()), 'create', dict(parameters),
            proposal_request_hash('create', parameters, calendar, None, None, None), calendar,
            None, None, None, NOW.isoformat(), (NOW + timedelta(seconds=300)).isoformat(), state,
            NOW.isoformat() if state == 'approved' else None,
            'a' * 64 if state == 'approved' else None)

    def test_complete_prepared_request_creates_once_with_server_identity(self):
        result = self.execute()
        self.assertEqual((result.status, result.mutation_state, result.http_status), ('succeeded', 'confirmed', 201))
        self.assertEqual(result.data['event_id'], 'FixtureCreated')
        self.assertTrue(result.operation_id.startswith('agent-create-'))
        self.assertEqual(self.writes()[0][2], result.operation_id)
        self.assertEqual(self.writes()[0][3], CALENDAR)
        self.assertEqual(self.record().state, 'confirmed')
        self.assertEqual(self.writes()[0][1].title, PARAMETERS['title'])

    def test_three_dialogue_turns_prepare_then_explicit_command_creates_without_llm(self):
        self.save_memory(QueryMemory())
        arguments = next(item['parameters']['required'] for item in DIALOGUE_TOOLS if item['name'] == 'prepare_request')
        plans = []
        for patch in ({'action': 'create', 'title': PARAMETERS['title'], 'time_zone': PARAMETERS['time_zone']},
                      {'start': PARAMETERS['start']}, {'end': PARAMETERS['end']}):
            plans.append(DialoguePlan('prepare_request', {**dict.fromkeys(arguments),
                'mode': 'start' if not plans else 'continue', 'use_selected_event': False, **patch},
                'fixture-dialogue-model'))
        calls = []
        class Model:
            def plan_dialogue(self, **request):
                calls.append(request)
                return plans[len(calls) - 1]
        dialogue = AgentDialogueService(Model(), self.factory, clock=lambda: NOW)
        for message, expected in (('Criar entrevista', 'needs_clarification'),
                                  ('Dia 17 às 15h', 'needs_clarification'), ('Termina às 15h30', 'ready')):
            previous = self.context.load(OWNER, self.cid)
            result, memory = dialogue.respond(message, previous.memory)
            self.assertEqual(result.status, expected)
            self.assertFalse(result.data['execution_allowed'])
            self.context.save(OWNER, self.cid, previous.revision, memory)
        self.assertEqual(self.gateway.calls, [])
        created = self.execute()
        self.assertEqual((created.status, created.http_status), ('succeeded', 201))
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(self.writes()), 1)

    def test_context_and_owner_are_checked_before_ledger_or_provider(self):
        with self.assertRaises(ConversationError):
            self.service.execute(OTHER, self.cid)
        self.assertEqual(self.gateway.calls, [])
        self.assertFalse((self.root / 'execution.sqlite3').exists())

    def test_incomplete_ambiguous_or_other_action_never_opens_gateway(self):
        for pending in (None, PendingRequest({'action': 'create', 'title': 'Parcial'}, ('start', 'end')),
                        PendingRequest({'action': 'list', 'date': '2030-06-17', 'period': 'day'})):
            self.save_memory(QueryMemory(pending_request=pending))
            with self.assertRaises(ConfirmationError) as raised:
                self.execute()
            self.assertEqual(raised.exception.code, 'preparation_required')
        self.assertEqual(self.gateway.calls, [])

    def test_repetition_reads_existing_result_without_second_write(self):
        first, second = self.execute(), self.execute()
        self.assertEqual(first.operation_id, second.operation_id)
        self.assertEqual(second.http_status, 200)
        self.assertTrue(second.data['reused'])
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(self.gateway.calls[-1][0], 'reconcile')

    def test_existing_global_duplicate_is_reported_as_reused(self):
        self.gateway.return_reused = True
        result = self.execute()
        self.assertEqual(result.http_status, 200)
        self.assertTrue(result.data['reused'])

    def test_lost_response_is_reconciled_by_read_without_reposting(self):
        self.gateway.lost_response = True
        failed = self.execute()
        self.assertEqual((failed.http_status, failed.mutation_state), (502, 'unknown'))
        self.assertEqual(self.record().state, 'unknown')
        recovered = self.execute()
        self.assertEqual(recovered.http_status, 200)
        self.assertTrue(recovered.data['recovered'])
        self.assertEqual(self.record().state, 'confirmed')
        self.assertEqual(len(self.writes()), 1)

    def test_unknown_absent_and_unregistered_never_authorize_another_post(self):
        self.gateway.create_error = OSError('PRIVATE_PROVIDER_DIAGNOSTIC')
        self.execute()
        for state in ('absent', 'unregistered'):
            self.gateway.reconcile_override = ReconciledCreation(state)
            result = self.execute()
            self.assertEqual(result.mutation_state, 'unknown')
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(self.record().state, 'unknown')

    def test_reconciliation_failure_preserves_unknown_record(self):
        self.gateway.create_error = OSError('PRIVATE_PROVIDER_DIAGNOSTIC')
        self.execute()
        before = self.record()
        self.gateway.reconcile_error = OSError('PRIVATE_PROVIDER_DIAGNOSTIC')
        self.assertEqual(self.execute().http_status, 502)
        self.assertEqual(self.record(), before)
        self.assertEqual(len(self.writes()), 1)

    def test_unknown_attempt_blocks_a_different_draft_before_provider(self):
        self.gateway.create_error = OSError('PRIVATE_PROVIDER_DIAGNOSTIC')
        first = self.execute()
        self.save_memory(QueryMemory(pending_request=PendingRequest({'action': 'create', **PARAMETERS, 'title': 'Outra entrevista'})))
        calls = len(self.gateway.calls)
        blocked = self.execute()
        self.assertEqual((blocked.http_status, blocked.error_code), (409, 'creation_unresolved'))
        self.assertEqual(blocked.operation_id, first.operation_id)
        self.assertEqual(len(self.gateway.calls), calls)

    def test_receipt_is_reloaded_after_new_service_and_ledger_instance(self):
        first = self.execute()
        self.ledger = AgentCreationStore(self.root / 'execution.sqlite3', clock=lambda: self.now)
        self.service = AgentCreationService(self.context, self.ledger, self.factory)
        second = self.execute()
        self.assertEqual(first.operation_id, second.operation_id)
        self.assertEqual(len(self.writes()), 1)

    def test_a_removed_confirmed_event_is_not_recreated(self):
        self.execute()
        self.gateway.current = None
        self.gateway.reconcile_override = ReconciledCreation('absent')
        result = self.execute()
        self.assertEqual((result.http_status, result.mutation_state, result.error_code),
                         (409, 'confirmed', 'creation_previously_confirmed'))
        self.assertEqual(len(self.writes()), 1)

    def test_malformed_created_result_stays_unknown_and_can_be_read_later(self):
        self.gateway.malformed = True
        self.assertEqual(self.execute().mutation_state, 'unknown')
        self.assertEqual(self.record().state, 'unknown')
        self.assertEqual(self.execute().status, 'succeeded')
        self.assertEqual(len(self.writes()), 1)

    def test_local_finish_failure_after_google_is_unknown_then_read_only_recovery(self):
        original = self.ledger.finish
        def fail_once(*args, **kwargs):
            raise AgentCreationError('creation_unavailable')
        with patch.object(self.ledger, 'finish', side_effect=fail_once):
            result = self.execute()
        self.assertEqual((result.http_status, result.mutation_state), (502, 'unknown'))
        self.assertEqual(self.record().state, 'running')
        self.assertEqual(self.execute().status, 'succeeded')
        self.assertEqual(len(self.writes()), 1)

    def test_conflict_uses_existing_report_and_allows_only_explicit_new_attempt(self):
        start, end = datetime.fromisoformat(PARAMETERS['start']), datetime.fromisoformat(PARAMETERS['end'])
        conflicting = AgendaEvent('ConflictFixture', 'Conflito fictício', PARAMETERS['start'], PARAMETERS['end'],
                                 False, PARAMETERS['time_zone'], 'confirmed', None)
        self.gateway.create_error = CalendarConflictError(build_conflict_report(
            CalendarPeriod(start, end, PARAMETERS['time_zone']), 'Genius Testes', (conflicting,)))
        first = self.execute()
        self.assertEqual((first.status, first.http_status, first.mutation_state), ('conflict', 409, 'not_applied'))
        self.assertEqual(first.data['conflicts'][0]['event_id'], 'ConflictFixture')
        self.assertEqual(self.record().state, 'conflict')
        self.gateway.create_error = None
        second = self.execute()
        self.assertEqual(second.operation_id, first.operation_id)
        self.assertEqual(second.status, 'succeeded')

    def test_calendar_binding_failure_is_known_not_applied(self):
        self.gateway.create_error = CalendarBindingError('PRIVATE_PROVIDER_DIAGNOSTIC')
        result = self.execute()
        self.assertEqual((result.http_status, result.mutation_state, result.error_code),
                         (409, 'not_applied', 'creation_calendar_changed'))
        self.assertEqual(self.record().state, 'not_applied')

    def test_calendar_changed_during_reconciliation_does_not_create_elsewhere(self):
        self.execute()
        self.gateway.reconcile_error = CalendarBindingError('PRIVATE_PROVIDER_DIAGNOSTIC')
        result = self.execute()
        self.assertEqual((result.http_status, result.error_code), (409, 'creation_calendar_changed'))
        self.assertEqual(len(self.writes()), 1)

    def test_setup_failure_before_claim_is_known_not_applied(self):
        self.gateway.snapshot_error = CalendarSetupError('PRIVATE_PROVIDER_DIAGNOSTIC')
        result = self.execute()
        self.assertEqual((result.http_status, result.mutation_state), (503, 'not_applied'))
        self.assertIsNone(self.record())
        self.assertEqual(self.writes(), [])

    def test_context_change_before_write_releases_claim_as_not_applied(self):
        def changed():
            self.save_memory(QueryMemory(pending_request=PendingRequest({'action': 'create', **PARAMETERS, 'title': 'Dados corrigidos'})))
        self.gateway.snapshot_callback = changed
        result = self.execute()
        self.assertEqual((result.http_status, result.error_code), (409, 'conversation_conflict'))
        self.assertEqual(self.record().state, 'not_applied')
        self.assertEqual(self.writes(), [])

    def test_pending_revoked_or_consumed_confirmation_cannot_be_bypassed(self):
        for state in ('pending', 'revoked'):
            self.save_memory(QueryMemory(pending_request=PendingRequest({'action': 'create', **PARAMETERS}),
                                        confirmation=self.confirmation(state=state)))
            with self.assertRaises(ConfirmationError):
                self.execute()
        self.assertEqual(self.gateway.calls, [])

    def test_approved_confirmation_is_consumed_once_before_creation(self):
        self.save_memory(QueryMemory(pending_request=PendingRequest({'action': 'create', **PARAMETERS}),
                                    confirmation=self.confirmation()))
        result = self.execute()
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual(self.context.load(OWNER, self.cid).memory.confirmation.state, 'consumed')
        self.assertEqual(self.execute().http_status, 200)
        self.assertEqual(len(self.writes()), 1)

    def test_expired_approval_is_not_sent_to_google(self):
        self.save_memory(QueryMemory(pending_request=PendingRequest({'action': 'create', **PARAMETERS}),
                                    confirmation=self.confirmation()))
        self.now += timedelta(seconds=300)
        with self.assertRaises(ConfirmationError) as raised:
            self.execute()
        self.assertEqual(raised.exception.code, 'confirmation_expired')
        self.assertEqual(self.record().state, 'not_applied')
        self.assertEqual(self.writes(), [])

    def test_approved_wrong_calendar_is_rejected_before_reservation(self):
        self.save_memory(QueryMemory(pending_request=PendingRequest({'action': 'create', **PARAMETERS}),
                                    confirmation=self.confirmation(calendar='other-calendar')))
        with self.assertRaises(ConfirmationError) as raised:
            self.execute()
        self.assertEqual(raised.exception.code, 'confirmation_stale')
        self.assertIsNone(self.record())

    def test_concurrent_replay_does_not_demote_an_inflight_owner_or_write_again(self):
        self.gateway.entered, self.gateway.release = Event(), Event()
        with ThreadPoolExecutor(max_workers=1) as pool:
            original = pool.submit(self.execute)
            self.assertTrue(self.gateway.entered.wait(timeout=5))
            replay = self.execute()
            self.assertEqual(replay.mutation_state, 'unknown')
            self.assertEqual(self.record().state, 'running')
            self.gateway.release.set()
            self.assertEqual(original.result(timeout=5).status, 'succeeded')
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(self.record().state, 'confirmed')

    def test_result_validator_rejects_extra_provider_data_and_false_states(self):
        valid = self.execute()
        for changed in (replace(valid, data={**valid.data, 'description': 'PRIVATE_PROVIDER_DIAGNOSTIC'}),
                        replace(valid, mutation_state='not_applied'), replace(valid, http_status=True),
                        replace(valid, operation_id='caller-supplied-id'), replace(valid, data={**valid.data, 'reused': 1})):
            with self.assertRaises(AgentCreationError):
                validate_creation_result(changed)

    def test_concurrent_reconciliation_can_confirm_before_original_receipt(self):
        concurrent = []
        self.gateway.created_callback = lambda: concurrent.append(self.execute())
        original = self.execute()
        self.assertEqual((original.status, original.mutation_state, original.http_status), ('succeeded', 'confirmed', 200))
        self.assertEqual((concurrent[0].status, concurrent[0].http_status), ('succeeded', 200))
        self.assertEqual(original.operation_id, concurrent[0].operation_id)
        self.assertEqual(original.data, concurrent[0].data)
        self.assertTrue(original.data['reused'])
        self.assertTrue(original.data['recovered'])
        self.assertEqual(self.record().state, 'confirmed')
        self.assertEqual(self.record().result, original.data)
        self.assertEqual(len(self.writes()), 1)

    def test_conflicting_persisted_receipt_never_hides_finish_failure(self):
        finish = self.ledger.finish
        def contradictory(*args, **kwargs):
            if len(args) >= 6 and args[4] == 'confirmed':
                other = {**args[5], 'html_link': 'https://example.invalid/different'}
                finish(*args[:5], other)
                raise OSError('PRIVATE_PROVIDER_DIAGNOSTIC')
            return finish(*args, **kwargs)
        with patch.object(self.ledger, 'finish', side_effect=contradictory):
            result = self.execute()
        self.assertEqual((result.status, result.mutation_state, result.http_status), ('failed', 'unknown', 502))
        self.assertEqual(len(self.writes()), 1)

if __name__ == '__main__':
    unittest.main()
