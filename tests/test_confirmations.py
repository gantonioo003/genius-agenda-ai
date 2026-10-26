"""Consentimento de conteúdo/versão exatos com leitura controlada e nenhum executor."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import unittest
from uuid import uuid4

from app.confirmation_state import ConfirmationError
from app.conversation_store import QueryMemory
from app.dialogue_state import CandidateReference, PendingRequest
from app.google_calendar import CalendarSetupError
from app.services.agenda import AgendaEvent
from app.services.confirmation_snapshot import ConfirmationSnapshot
from app.services.confirmations import (
    ConfirmationResult, ConfirmationService, is_confirmation_message, is_rejection_message,
    validate_confirmation_result,
)

NOW = datetime(2026, 10, 24, 21, tzinfo=timezone.utc)
ZONE = 'America/Recife'
EVENT_ID = 'OpaqueFixtureEvent'
PRIVATE_CALENDAR = 'private-fixture-calendar@example.invalid'
PRIVATE_VERSION = 'private-fixture-etag-v1'
PRIVATE_DIAGNOSTIC = 'private-provider-exception-do-not-echo'
START, END = '2030-06-17T10:00:00-03:00', '2030-06-17T10:30:00-03:00'


def event(**changes):
    value = AgendaEvent(EVENT_ID, 'Entrevista fictícia', START, END, False, ZONE,
                        'confirmed', 'https://example.invalid/event')
    return replace(value, **changes)


def draft(action='create', **changes):
    values = {'action': action, 'time_zone': ZONE}
    if action == 'create':
        values.update(title='Nova entrevista fictícia', start=START, end=END, description='')
    elif action == 'edit':
        values.update(event_id=EVENT_ID, description='Texto novo', location='')
    elif action == 'reschedule':
        values.update(event_id=EVENT_ID, start=START, end=END)
    elif action == 'delete':
        values.update(event_id=EVENT_ID)
    values.update(changes)
    return QueryMemory(pending_request=PendingRequest(values))


class Gateway:
    def __init__(self):
        self.calendar_id = PRIVATE_CALENDAR
        self.current = event()
        self.version = PRIVATE_VERSION
        self.error = None
        self.override = None
        self.after_read = None
        self.calls = []
        self.writes = []

    def get_confirmation_snapshot(self, event_id, *, time_zone):
        self.calls.append((event_id, time_zone))
        if self.error:
            raise self.error
        if self.after_read:
            self.after_read()
        if self.override is not None:
            return self.override
        return ConfirmationSnapshot(self.calendar_id, 'Genius Testes',
            self.current if event_id is not None else None,
            self.version if event_id is not None and self.current is not None else None)

    def _write(self, *args, **kwargs):
        self.writes.append((args, kwargs))
        raise AssertionError('A confirmation cannot execute a mutation')

    create_event = _write
    edit_event = _write
    reschedule_event = _write
    delete_event = _write


class ConfirmationServiceTests(unittest.TestCase):
    def setUp(self):
        self.now = NOW
        self.gateway = Gateway()
        self.opened = self.closed = 0
        self.service = ConfirmationService(self.factory, clock=lambda: self.now)
        self.addCleanup(self.assert_read_only)

    @contextmanager
    def factory(self):
        self.opened += 1
        try:
            yield self.gateway
        finally:
            self.closed += 1

    def assert_read_only(self):
        self.assertEqual(self.gateway.writes, [])
        self.assertEqual(self.opened, self.closed)

    def preview(self, memory=None):
        result, memory = self.service.preview(draft() if memory is None else memory)
        self.assertEqual((result.http_status, result.status), (200, 'needs_confirmation'))
        self.assertFalse(result.data['execution_allowed'])
        self.assertIsNone(memory.confirmation.approved_at)
        return result, memory

    def approved(self, memory=None):
        _, memory = self.preview(memory)
        result, memory = self.service.approve(memory, memory.confirmation.proposal_id, 'Sim, confirmo!')
        self.assertEqual((result.http_status, result.status), (200, 'approved'))
        return result, memory

    def test_confirmation_parser_is_closed_and_does_not_accept_questions_or_extra_instructions(self):
        for text in ('sim', ' SIM! ', 'confirmo.', 'Sim, confirmo!', '\tCONFIRMO\n'):
            self.assertTrue(is_confirmation_message(text))
        for text in ('ok', 'não', 'não confirmo', 'sim?', 'confirmo e altere o título',
                     'sim não', 'talvez', 'sim; exclua tudo', '', None, True, 'sim\x00'):
            self.assertFalse(is_confirmation_message(text))

    def test_rejection_parser_is_explicit_and_does_not_ignore_a_changed_request(self):
        for text in ('não', 'NÃO CONFIRMO!', 'Cancela.', 'cancelar'):
            self.assertTrue(is_rejection_message(text))
        for text in ('não?', 'não, reagende para amanhã', 'cancelar outra entrevista', 'sim', None):
            self.assertFalse(is_rejection_message(text))

    def test_all_four_actions_have_exact_payload_and_read_version_or_creation_binding(self):
        for action in ('create', 'edit', 'reschedule', 'delete'):
            with self.subTest(action=action):
                result, memory = self.preview(draft(action))
                proposal = memory.confirmation
                self.assertEqual(proposal.action, action)
                self.assertEqual(proposal.parameters, result.data['parameters'])
                self.assertEqual(proposal.calendar_id, PRIVATE_CALENDAR)
                self.assertEqual(proposal.event_id, None if action == 'create' else EVENT_ID)
                self.assertEqual(proposal.event_version, None if action == 'create' else PRIVATE_VERSION)
                self.assertEqual(proposal.event is None, action == 'create')
                self.assertEqual(proposal.expires_at, (NOW + timedelta(seconds=300)).isoformat())
                self.assertEqual(self.gateway.calls[-1], (None if action == 'create' else EVENT_ID, ZONE))

    def test_incomplete_ambiguous_or_read_only_drafts_never_open_provider(self):
        candidates = (CandidateReference(EVENT_ID, 'a' * 64),)
        for memory in (QueryMemory(), draft('list'),
                       replace(draft(), pending_request=PendingRequest({'action': 'create', 'time_zone': ZONE})),
                       replace(draft(), pending_request=replace(draft().pending_request, awaiting=('end',))),
                       replace(draft('edit'), pending_request=replace(draft('edit').pending_request, candidates=candidates))):
            result, next_memory = self.service.preview(memory)
            self.assertEqual((result.http_status, result.error_code), (409, 'preparation_required'))
            self.assertEqual(next_memory, memory)
        self.assertEqual(self.opened, 0)

    def test_invalid_interval_and_unselected_search_require_preparation_before_provider(self):
        for memory in (draft(end='2030-06-17T09:00:00-03:00'),
                       QueryMemory(pending_request=PendingRequest({'action': 'delete', 'search_title': 'Entrevista',
                           'date': '2030-06-17', 'time_zone': ZONE}))):
            result, same = self.service.preview(memory)
            self.assertEqual(result.error_code, 'preparation_required')
            self.assertEqual(same, memory)
        self.assertEqual(self.opened, 0)

    def test_same_preview_preserves_id_and_original_expiry(self):
        _, memory = self.preview()
        self.now += timedelta(seconds=50)
        result, same = self.service.preview(memory)
        self.assertEqual(same, memory)
        self.assertEqual(result.data['proposal_id'], memory.confirmation.proposal_id)
        self.assertEqual(result.data['expires_at'], memory.confirmation.expires_at)

    def test_approved_preview_does_not_reissue_id_or_renew_consent(self):
        _, memory = self.approved()
        self.now += timedelta(seconds=20)
        result, same = self.service.preview(memory)
        self.assertEqual((result.status, same), ('approved', memory))

    def test_changed_payload_has_new_proposal_without_old_approval(self):
        _, memory = self.approved(draft('edit'))
        pending = replace(memory.pending_request, fields={**memory.pending_request.fields, 'location': 'Sala 2'})
        result, changed = self.service.preview(replace(memory, pending_request=pending))
        self.assertEqual(result.status, 'needs_confirmation')
        self.assertNotEqual(changed.confirmation.request_hash, memory.confirmation.request_hash)
        self.assertNotEqual(changed.confirmation.proposal_id, memory.confirmation.proposal_id)
        self.assertIsNone(changed.confirmation.approved_at)

    def test_new_preview_after_changed_version_requires_new_approval(self):
        _, memory = self.approved(draft('delete'))
        self.gateway.version = 'private-fixture-etag-v2'
        result, changed = self.service.preview(memory)
        self.assertEqual(result.status, 'needs_confirmation')
        self.assertNotEqual(changed.confirmation.proposal_id, memory.confirmation.proposal_id)
        self.assertIsNone(changed.confirmation.approved_at)

    def test_approval_records_utc_time_and_digest_without_echoing_raw_message_or_private_binding(self):
        _, memory = self.preview(draft('edit'))
        self.now += timedelta(seconds=8)
        result, approved = self.service.approve(memory, memory.confirmation.proposal_id, 'SIM, CONFIRMO!!!')
        self.assertEqual(result.status, 'approved')
        self.assertEqual(approved.confirmation.approved_at, self.now.isoformat())
        self.assertRegex(approved.confirmation.approval_message_hash, '^[0-9a-f]{64}$')
        encoded = json.dumps(result.data, ensure_ascii=False)
        for private in (PRIVATE_CALENDAR, PRIVATE_VERSION, approved.confirmation.request_hash,
                        approved.confirmation.approval_message_hash, 'SIM, CONFIRMO!!!'):
            self.assertNotIn(private, encoded)
        self.assertEqual(memory.confirmation.state, 'pending')

    def test_unknown_proposal_and_malformed_id_do_not_open_provider(self):
        _, memory = self.preview()
        before = self.opened
        for identifier, code in ((str(uuid4()), 'confirmation_not_found'), ('invalid', 'confirmation_invalid')):
            result, same = self.service.approve(memory, identifier, 'sim')
            self.assertEqual(result.error_code, code)
            self.assertEqual(same, memory)
        self.assertEqual(self.opened, before)

    def test_ambiguous_or_negative_message_does_not_approve_or_read_google(self):
        _, memory = self.preview()
        before = self.opened
        for message in ('ok', 'não confirmo', 'sim?', 'sim e altere o horário', 'confirmo outra entrevista'):
            result, same = self.service.approve(memory, memory.confirmation.proposal_id, message)
            self.assertEqual((result.http_status, result.error_code), (422, 'ambiguous_confirmation'))
            self.assertEqual(same, memory)
        self.assertEqual(self.opened, before)

    def test_changed_draft_action_payload_or_target_invalidates_before_google(self):
        _, memory = self.preview(draft('edit'))
        before = self.opened
        for fields in (
            {**memory.pending_request.fields, 'description': 'Texto diferente'},
            {**memory.pending_request.fields, 'event_id': 'AnotherFixtureID'},
            {'action': 'delete', 'event_id': EVENT_ID, 'time_zone': ZONE},
        ):
            changed = replace(memory, pending_request=PendingRequest(fields))
            result, invalid = self.service.approve(changed, memory.confirmation.proposal_id, 'sim')
            self.assertEqual((result.http_status, result.status), (409, 'invalidated'))
            self.assertEqual(invalid.confirmation.state, 'invalidated')
            self.assertIsNone(invalid.confirmation.approved_at)
        self.assertEqual(self.opened, before)

    def test_cleared_or_incomplete_draft_cannot_reuse_a_previous_proposal(self):
        _, memory = self.preview()
        for pending in (None, replace(memory.pending_request, awaiting=('end',))):
            result, invalid = self.service.approve(replace(memory, pending_request=pending), memory.confirmation.proposal_id, 'sim')
            self.assertEqual(result.error_code, 'confirmation_stale')
            self.assertEqual(invalid.confirmation.state, 'invalidated')

    def test_calendar_identity_or_version_change_invalidates_approval(self):
        _, memory = self.preview(draft('delete'))
        for calendar, version in ((PRIVATE_CALENDAR, 'private-fixture-etag-v2'),
                                  ('another-private-calendar@example.invalid', PRIVATE_VERSION)):
            self.gateway.calendar_id, self.gateway.version = calendar, version
            result, invalid = self.service.approve(memory, memory.confirmation.proposal_id, 'sim')
            self.assertEqual((result.status, result.error_code), ('invalidated', 'confirmation_stale'))
            self.assertIsNone(invalid.confirmation.approved_at)

    def test_event_changed_even_without_version_change_fails_closed(self):
        _, memory = self.preview(draft('edit'))
        self.gateway.current = event(title='Título mudou entre leituras')
        result, changed = self.service.approve(memory, memory.confirmation.proposal_id, 'sim')
        self.assertEqual((result.http_status, result.status), (409, 'invalidated'))
        self.assertEqual(changed.confirmation.state, 'invalidated')

    def test_confirmed_absence_invalidates_old_proposal(self):
        _, memory = self.preview(draft('delete'))
        self.gateway.current = None
        result, changed = self.service.approve(memory, memory.confirmation.proposal_id, 'sim')
        self.assertEqual(result.error_code, 'confirmation_stale')
        self.assertEqual(changed.confirmation.state, 'invalidated')

    def test_absent_event_without_old_proposal_does_not_create_a_preview(self):
        memory = draft('delete')
        self.gateway.current = None
        result, same = self.service.preview(memory)
        self.assertEqual((result.http_status, result.error_code), (409, 'confirmation_stale'))
        self.assertEqual(same, memory)

    def test_rescheduling_an_all_day_event_is_not_presented_as_a_supported_proposal(self):
        self.gateway.current = event(all_day=True, start='2030-06-17', end='2030-06-18')
        memory = draft('reschedule')
        result, same = self.service.preview(memory)
        self.assertEqual((result.http_status, result.error_code), (409, 'preparation_required'))
        self.assertEqual(same, memory)

    def test_expired_before_approval_is_rejected_without_provider(self):
        _, memory = self.preview()
        before = self.opened
        self.now += timedelta(seconds=300)
        result, changed = self.service.approve(memory, memory.confirmation.proposal_id, 'sim')
        self.assertEqual((result.http_status, result.status), (409, 'expired'))
        self.assertIsNone(changed.confirmation.approved_at)
        self.assertEqual(self.opened, before)

    def test_expiry_during_fresh_read_does_not_register_consent(self):
        _, memory = self.preview(draft('edit'))
        self.gateway.after_read = lambda: setattr(self, 'now', NOW + timedelta(seconds=300))
        result, changed = self.service.approve(memory, memory.confirmation.proposal_id, 'sim')
        self.assertEqual((result.http_status, result.status), (409, 'expired'))
        self.assertIsNone(changed.confirmation.approved_at)

    def test_repeated_approval_keeps_original_timestamp_digest_and_expiry(self):
        _, memory = self.approved()
        self.now += timedelta(seconds=45)
        result, same = self.service.approve(memory, memory.confirmation.proposal_id, 'confirmo')
        self.assertEqual((result.status, same), ('approved', memory))

    def test_revoke_is_idempotent_and_independent_from_google(self):
        _, memory = self.approved(draft('edit'))
        before = self.opened
        self.gateway.error = RuntimeError(PRIVATE_DIAGNOSTIC)
        result, changed = self.service.revoke(memory, memory.confirmation.proposal_id)
        repeated, same = self.service.revoke(changed, changed.confirmation.proposal_id)
        self.assertEqual((result.status, repeated.status), ('revoked', 'revoked'))
        self.assertEqual(changed, same)
        self.assertEqual(changed.pending_request, memory.pending_request)
        self.assertEqual(self.opened, before)

    def test_revoked_proposal_cannot_be_approved_without_new_preview(self):
        _, memory = self.preview()
        _, revoked = self.service.revoke(memory, memory.confirmation.proposal_id)
        result, same = self.service.approve(revoked, revoked.confirmation.proposal_id, 'sim')
        self.assertEqual(result.error_code, 'confirmation_not_approved')
        self.assertEqual(same, revoked)
        preview, new = self.service.preview(revoked)
        self.assertEqual(preview.status, 'needs_confirmation')
        self.assertNotEqual(new.confirmation.proposal_id, revoked.confirmation.proposal_id)

    def test_explicit_new_preview_after_expiry_creates_a_new_window_without_approval(self):
        _, memory = self.approved()
        self.now += timedelta(seconds=301)
        result, changed = self.service.preview(memory)
        self.assertEqual(result.status, 'needs_confirmation')
        self.assertNotEqual(changed.confirmation.proposal_id, memory.confirmation.proposal_id)
        self.assertIsNone(changed.confirmation.approved_at)
        self.assertEqual(changed.confirmation.expires_at, (self.now + timedelta(seconds=300)).isoformat())

    def test_consumed_approval_cannot_be_reused_or_reissued_automatically(self):
        _, memory = self.approved()
        consumed = replace(memory.confirmation, state='consumed', consumed_at=NOW.isoformat())
        memory = replace(memory, confirmation=consumed)
        before = self.opened
        for method, args in ((self.service.preview, (memory,)),
                             (self.service.approve, (memory, consumed.proposal_id, 'sim')),
                             (self.service.revoke, (memory, consumed.proposal_id))):
            result, same = method(*args)
            self.assertEqual(result.error_code, 'confirmation_consumed')
            self.assertEqual(same, memory)
        self.assertEqual(self.opened, before)

    def test_status_is_local_and_can_report_expiry_without_extending_deadline(self):
        _, memory = self.approved(draft('edit'))
        before = self.opened
        self.gateway.error = RuntimeError(PRIVATE_DIAGNOSTIC)
        result, same = self.service.status(memory, memory.confirmation.proposal_id)
        self.assertEqual((result.http_status, result.status, same), (200, 'approved', memory))
        self.now += timedelta(seconds=300)
        result, expired = self.service.status(memory, memory.confirmation.proposal_id)
        self.assertEqual((result.http_status, result.status), (200, 'expired'))
        self.assertEqual(expired.confirmation.expires_at, memory.confirmation.expires_at)
        self.assertEqual(self.opened, before)

    def test_status_detects_changed_draft_without_using_provider(self):
        _, memory = self.preview()
        changed = replace(memory, pending_request=replace(memory.pending_request,
                          fields={**memory.pending_request.fields, 'title': 'Outro título'}))
        before = self.opened
        result, invalidated = self.service.status(changed, changed.confirmation.proposal_id)
        self.assertEqual((result.http_status, result.status), (200, 'invalidated'))
        self.assertEqual(self.opened, before)
        self.assertEqual(invalidated.confirmation.state, 'invalidated')

    def test_provider_failure_preserves_prior_pending_and_approved_memory(self):
        for state in ('pending', 'approved'):
            _, memory = self.preview(draft('edit')) if state == 'pending' else self.approved(draft('edit'))
            self.gateway.error = RuntimeError(PRIVATE_DIAGNOSTIC)
            for method, args in ((self.service.preview, (memory,)),
                                 (self.service.approve, (memory, memory.confirmation.proposal_id, 'sim'))):
                result, same = method(*args)
                self.assertEqual((result.http_status, result.error_code), (502, 'operation_failed'))
                self.assertEqual(same, memory)
                self.assertNotIn(PRIVATE_DIAGNOSTIC, result.message)
            self.gateway.error = None

    def test_missing_calendar_configuration_is_503_and_preserves_draft(self):
        self.gateway.error = CalendarSetupError(PRIVATE_DIAGNOSTIC)
        memory = draft()
        result, same = self.service.preview(memory)
        self.assertEqual((result.http_status, result.error_code), (503, 'calendar_setup_unavailable'))
        self.assertEqual(same, memory)
        self.assertNotIn(PRIVATE_DIAGNOSTIC, result.message)

    def test_invalid_provider_snapshot_never_approves_or_exposes_diagnostic(self):
        _, memory = self.preview(draft('edit'))
        self.gateway.override = ConfirmationSnapshot(PRIVATE_CALENDAR, 'Genius Testes', event(event_id='WrongID'), PRIVATE_VERSION)
        result, same = self.service.approve(memory, memory.confirmation.proposal_id, 'sim')
        self.assertEqual(result.error_code, 'operation_failed')
        self.assertEqual(same, memory)

    def test_clock_rollback_cannot_approve_a_future_created_proposal(self):
        _, memory = self.preview()
        self.now -= timedelta(seconds=1)
        result, same = self.service.approve(memory, memory.confirmation.proposal_id, 'sim')
        self.assertEqual(result.error_code, 'confirmation_unavailable')
        self.assertEqual(same, memory)

    def test_constructor_rejects_nonfinite_or_unbounded_control_values(self):
        for options in ({'ttl_seconds': True}, {'ttl_seconds': 0}, {'ttl_seconds': 901},
                        {'ttl_seconds': 300.0}, {'clock': 'invalid'}):
            with self.assertRaises(ConfirmationError):
                ConfirmationService(self.factory, **options)

    def test_public_result_validator_rejects_smuggled_hashes_or_execution_permission(self):
        result, _ = self.preview(draft('edit'))
        for data in ({**result.data, 'execution_allowed': True},
                     {**result.data, 'event_version': PRIVATE_VERSION},
                     {**result.data, 'request_hash': 'a' * 64}):
            with self.assertRaises(ConfirmationError):
                validate_confirmation_result(replace(result, data=data))

    def test_public_preview_is_detached_from_private_memory(self):
        result, memory = self.preview(draft('edit'))
        result.data['parameters']['description'] = 'Alteração no cliente'
        result.data['event']['title'] = 'Título adulterado no cliente'
        self.assertEqual(memory.confirmation.parameters['description'], 'Texto novo')
        self.assertEqual(memory.confirmation.event['title'], 'Entrevista fictícia')


if __name__ == '__main__':
    unittest.main()
