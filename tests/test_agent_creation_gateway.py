"""Criação vinculada e reconciliação com SDK real e HTTP/SQLite locais."""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from google.auth.credentials import AnonymousCredentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build

from app import google_calendar
from app.database import Database
from app.google_transport import SingleAttemptHttp
from app.services.creation_identity import creation_content_hash
from app.services.interviews import (
    CalendarBindingError, CalendarOperationError, InterviewDraft, ReconciledCreation,
)
from calendar_http_fake import CalendarHttpFake


OPERATION = 'fixture-agent-create-operation-one'
SECOND_OPERATION = 'fixture-agent-create-operation-two'


class AgentCreationGatewayTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = Database(Path(temporary.name) / 'creation.sqlite3')
        self.db.initialize()
        self.backend = CalendarHttpFake()
        self.sessions = []
        self.draft = InterviewDraft('Entrevista fictícia — teste',
            datetime.fromisoformat('2030-06-17T10:00:00-03:00'),
            datetime.fromisoformat('2030-06-17T10:30:00-03:00'),
            'America/Recife', 'Descrição fictícia\nLinha complementar.')
        for target, value in (
            ('database', self.db),
            ('settings', SimpleNamespace(google_calendar_name='Genius Testes',
                                       google_calendar_id=self.backend.calendar['id'])),
        ):
            patcher = patch.object(google_calendar, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(google_calendar, 'get_credentials', side_effect=AssertionError('No OAuth allowed'))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch('app.google_transport.requests.Session', side_effect=self.session)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch('googleapiclient.http.time.sleep')
        patcher.start()
        self.addCleanup(patcher.stop)
        transport = SingleAttemptHttp(timeout=20)
        self.sdk = build('calendar', 'v3', http=AuthorizedHttp(AnonymousCredentials(), http=transport,
                         max_refresh_attempts=0), cache_discovery=False, static_discovery=True)
        self.gateway = google_calendar.GoogleCalendarGateway(self.sdk)
        self.addCleanup(self.assert_sessions_closed)
        self.addCleanup(self.sdk.close)

    def session(self):
        session = self.backend.session()
        self.sessions.append(session)
        return session

    def assert_sessions_closed(self):
        self.assertTrue(all(session.closed for session in self.sessions))

    def create(self, operation=OPERATION, **changes):
        return self.gateway.create_event(self.draft, operation_id=operation,
             expected_calendar_id=changes.get('calendar', self.backend.calendar['id']))

    def reconcile(self, operation=OPERATION, **changes):
        return self.gateway.reconcile_creation(changes.get('draft', self.draft), operation_id=operation,
             expected_calendar_id=changes.get('calendar', self.backend.calendar['id']))

    def rows(self):
        with self.db.connection() as connection:
            return {table: [tuple(row) for row in connection.execute('SELECT * FROM ' + table + ' ORDER BY 1,2')]
                    for table in ('creation_requests', 'creation_operations')}

    def reset_calls(self):
        self.backend.calls.clear()

    def assert_read_only(self):
        self.assertTrue(all(call['method'] == 'GET' for call in self.backend.calls))

    def test_bound_creation_uses_existing_validation_and_single_post(self):
        result = self.create()
        self.assertFalse(result.reused)
        writes = [call for call in self.backend.calls if call['method'] != 'GET']
        self.assertEqual([call['method'] for call in writes], ['POST'])
        self.assertEqual(writes[0]['query']['sendUpdates'], 'none')
        self.assertEqual(len(self.backend.saved), 1)

    def test_existing_call_without_calendar_binding_remains_compatible(self):
        result = self.gateway.create_event(self.draft, operation_id=OPERATION)
        self.assertEqual(result.calendar_name, 'Genius Testes')
        self.assertEqual(result.event_id, 'genius' + sha256(OPERATION.encode()).hexdigest())

    def test_invalid_expected_calendar_fails_before_any_provider_or_reservation(self):
        for value in ('', ' ', 'two words', 'bad\n', 'x' * 1025, '\ud800', True, 12):
            with self.subTest(kind=type(value).__name__), self.assertRaises(ValueError):
                self.create(calendar=value)
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.rows(), {'creation_requests': [], 'creation_operations': []})

    def test_mismatched_calendar_prevents_creation_registration_or_post(self):
        with self.assertRaises(CalendarBindingError):
            self.create(calendar='other-calendar@example.invalid')
        self.assert_read_only()
        self.assertEqual(self.rows(), {'creation_requests': [], 'creation_operations': []})
        self.assertEqual(self.backend.saved, {})

    def test_same_content_under_another_operation_still_deduplicates(self):
        first = self.create()
        repeated = self.create(SECOND_OPERATION)
        self.assertTrue(repeated.reused)
        self.assertEqual(repeated.event_id, first.event_id)
        self.assertEqual(sum(call['method'] == 'POST' for call in self.backend.calls), 1)

    def test_reconciliation_of_found_event_uses_get_only_and_keeps_creation_rows(self):
        created = self.create()
        before = self.rows()
        self.reset_calls()
        result = self.reconcile()
        self.assertEqual(result.status, 'found')
        self.assertEqual(result.created.event_id, created.event_id)
        self.assertTrue(result.created.reused)
        self.assertTrue(result.created.recovered)
        self.assertEqual(self.rows(), before)
        self.assert_read_only()
        event_gets = [call for call in self.backend.calls if call.get('event_id')]
        self.assertEqual(len(event_gets), 1)
        self.assertEqual(event_gets[0]['event_id'], created.event_id)
        self.assertEqual(event_gets[0]['query']['fields'], self.gateway.EVENT_FIELDS)

    def test_reconcile_another_operation_uses_registered_content_identity(self):
        first = self.create()
        before = self.rows()
        self.reset_calls()
        result = self.reconcile(SECOND_OPERATION)
        self.assertEqual(result.created.event_id, first.event_id)
        self.assertNotEqual(first.event_id, 'genius' + sha256(SECOND_OPERATION.encode()).hexdigest())
        self.assertEqual(self.rows(), before)
        self.assert_read_only()

    def test_unregistered_identity_is_not_reported_as_absent_and_never_posts(self):
        result = self.reconcile()
        self.assertEqual(result, ReconciledCreation('unregistered'))
        self.assertIsNone(result.created)
        self.assertEqual(self.rows(), {'creation_requests': [], 'creation_operations': []})
        self.assert_read_only()
        self.assertFalse(any(call.get('event_id') for call in self.backend.calls))

    def test_registered_absence_does_not_recreate_or_change_ledger(self):
        self.create()
        before = self.rows()
        self.backend.saved.clear()
        self.reset_calls()
        result = self.reconcile()
        self.assertEqual(result, ReconciledCreation('absent'))
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.backend.saved, {})
        self.assert_read_only()

    def test_wrong_content_or_fingerprint_is_not_confirmed(self):
        for field, value in (('summary', 'Evento alterado'), ('description', 'Descrição alterada'),
                             ('start', {'dateTime': '2030-06-17T10:01:00-03:00', 'timeZone': 'America/Recife'}),
                             ('end', {'dateTime': '2030-06-17T10:31:00-03:00', 'timeZone': 'America/Recife'}),
                             ('extendedProperties', {'private': {'geniusRequest': '0' * 64}}),
                             ('status', 'cancelled'), ('htmlLink', '')):
            with self.subTest(field=field):
                self.backend.saved.clear()
                # Use a fresh ledger per subcase to avoid a confirmed-absence policy.
                self.db = Database(self.db.path.with_name(field + '.sqlite3'))
                self.db.initialize()
                google_calendar.database = self.db
                created = self.create()
                self.backend.saved[created.event_id][field] = value
                before = self.rows()
                self.reset_calls()
                with self.assertRaises(CalendarOperationError):
                    self.reconcile()
                self.assertEqual(self.rows(), before)
                self.assert_read_only()

    def test_provider_failure_in_reconciliation_stays_read_only_and_safe(self):
        self.create()
        before = self.rows()
        self.backend.fail_reads_after_write = 3
        self.reset_calls()
        with self.assertRaises(CalendarOperationError) as raised:
            self.reconcile()
        self.assertEqual(self.rows(), before)
        self.assert_read_only()
        self.assertNotIn(self.backend.calendar['id'], str(raised.exception))

    def test_reconciliation_mismatched_calendar_does_not_read_event(self):
        self.create()
        before = self.rows()
        self.reset_calls()
        with self.assertRaises(CalendarBindingError):
            self.reconcile(calendar='other-calendar@example.invalid')
        self.assertEqual(self.rows(), before)
        self.assertFalse(any(call.get('event_id') for call in self.backend.calls))
        self.assert_read_only()

    def test_reconciliation_requires_valid_binding_and_draft_before_io(self):
        for value in (None, '', 'bad\r\n', True):
            with self.assertRaises(ValueError):
                self.reconcile(calendar=value)
        with self.assertRaises(ValueError):
            self.reconcile(draft=replace(self.draft, end=self.draft.start))
        self.assertEqual(self.backend.calls, [])

    def test_corrupt_or_mismatched_creation_record_cannot_confirm(self):
        created = self.create()
        good = self.db.get_creation(self.backend.calendar['id'], creation_content_hash(
            self.draft.title, self.draft.description, self.draft.start, self.draft.end))
        for record in (object(), replace(good, calendar_id='other'), replace(good, request_hash='0' * 64),
                       replace(good, state='unknown'), replace(good, fingerprint='bad'),
                       replace(good, event_id='bad id')):
            with self.subTest(kind=type(record).__name__):
                self.reset_calls()
                with patch.object(self.db, 'get_creation', return_value=record), self.assertRaises(CalendarOperationError):
                    self.reconcile()
                self.assertFalse(any(call.get('event_id') for call in self.backend.calls))
                self.assert_read_only()
        self.assertIn(created.event_id, self.backend.saved)

    def test_unknown_write_can_later_be_confirmed_without_second_post(self):
        self.backend.lose_response_once.add('POST')
        self.backend.fail_reads_after_write = 3
        with self.assertRaises(CalendarOperationError):
            self.create()
        self.assertEqual(len(self.backend.saved), 1)
        self.assertEqual(sum(call['method'] == 'POST' for call in self.backend.calls), 1)
        before = self.rows()
        self.backend.fail_reads_after_write = 0
        self.reset_calls()
        result = self.reconcile()
        self.assertEqual(result.status, 'found')
        self.assertTrue(result.created.recovered)
        self.assertEqual(self.rows(), before)
        self.assert_read_only()

    def test_frozen_private_reconciliation_repr_does_not_expose_result(self):
        result = self.reconcile()
        with self.assertRaises(FrozenInstanceError):
            result.status = 'found'
        self.assertEqual(repr(result), 'ReconciledCreation(<private>)')

if __name__ == '__main__':
    unittest.main()
