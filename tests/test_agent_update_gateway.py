"""SDK Google com transporte e dados fictícios; versões vinculadas antes de PATCH."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime
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
from app.services.event_details import DetailsChange, EventDetails
from app.services.interviews import CalendarOperationError
from app.services.rescheduling import ScheduleChange
from app.services.update_snapshot import (
    UpdateSnapshot, UpdateBindingError, UpdateVersionError, validate_update_snapshot,
)
from app.services.conflicts import CalendarConflictError
from calendar_http_fake import CalendarHttpFake

ZONE = 'America/Recife'
EVENT_ID = 'OpaqueOccurrence_20300617T130000Z'
CALENDAR = 'fixture-update-calendar@example.invalid'


def event(**changes):
    return replace(EventDetails(EVENT_ID, 'Título original', '2030-06-17T10:00:00-03:00',
        '2030-06-17T10:30:00-03:00', False, ZONE, 'confirmed', 'https://example.invalid/event',
        'Descrição privada\nLinha dois', 'Local privado'), **changes)


def snapshot(**changes):
    return replace(UpdateSnapshot(CALENDAR, 'Genius Testes', event(), 'W/"fixture-v1"', 'opaque'), **changes)


class UpdateSnapshotTests(unittest.TestCase):
    def test_snapshot_copies_full_known_event_fields_and_exact_etag(self):
        value = snapshot()
        copied = validate_update_snapshot(value, EVENT_ID, ZONE)
        self.assertEqual(copied, value)
        self.assertIsNot(copied, value)
        self.assertIsNot(copied.event, value.event)
        self.assertEqual(type(copied.event), EventDetails)
        self.assertEqual(copied.event.description, 'Descrição privada\nLinha dois')
        self.assertEqual(copied.event.location, 'Local privado')
        self.assertEqual(copied.version, 'W/"fixture-v1"')

    def test_frozen_private_repr_does_not_expose_event_or_version(self):
        value = snapshot()
        with self.assertRaises(FrozenInstanceError):
            value.version = 'other'
        for private in (EVENT_ID, CALENDAR, 'fixture-v1', 'Título', 'Descrição', 'Local'):
            self.assertNotIn(private, repr(value))

    def test_invalid_calendar_and_version_are_rejected(self):
        for field, values in (
            ('calendar_id', (None, '', ' ', 'two words', True, 'x' * 1025, 'private\n', '\ud800')),
            ('version', (None, '', ' ', True, 'x' * 2049, 'private\r\n', '\ud800')),
            ('calendar_name', (None, '', ' ', 'x' * 4001, '\ud800')),
        ):
            for value in values:
                with self.subTest(field=field, kind=type(value).__name__), self.assertRaises(ValueError):
                    validate_update_snapshot(snapshot(**{field: value}), EVENT_ID, ZONE)

    def test_invalid_identity_interval_status_transparency_and_shapes_rejected(self):
        values = [snapshot(event=event(event_id='DifferentEvent')),
            snapshot(event=event(end=event().start)), snapshot(event=event(time_zone='UTC')),
            snapshot(event=event(status='cancelled')), snapshot(transparency='unknown'),
            snapshot(event=None), None, {}, snapshot(event=event(description=None)),
            snapshot(event=event(location='\ud800'))]
        for value in values:
            with self.subTest(kind=type(value).__name__), self.assertRaises(ValueError):
                validate_update_snapshot(value, EVENT_ID, ZONE)

    def test_empty_original_title_and_all_day_keep_real_values(self):
        value = snapshot(event=event(title='', all_day=True, start='2030-06-17',
            end='2030-06-18', time_zone='America/Sao_Paulo'))
        self.assertEqual(validate_update_snapshot(value, EVENT_ID, ZONE), value)

    def test_oversized_full_snapshot_and_unknown_expected_inputs_rejected(self):
        with self.assertRaises(ValueError):
            validate_update_snapshot(snapshot(event=event(description='x' * (256 * 1024))), EVENT_ID, ZONE)
        for event_id, zone in (('', ZONE), (True, ZONE), ('bad id', ZONE), (EVENT_ID, 'bad/zone')):
            with self.assertRaises(ValueError):
                validate_update_snapshot(snapshot(), event_id, zone)


class AgentUpdateGatewayTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        database = Database(Path(temporary.name) / 'fixture.sqlite3')
        database.initialize()
        self.backend = CalendarHttpFake()
        self.backend.calendar['id'] = CALENDAR
        self.backend.saved[EVENT_ID] = {
            'id': EVENT_ID, 'summary': 'Título original', 'status': 'confirmed', 'etag': '"v1"',
            'description': 'Descrição privada\nLinha dois', 'location': 'Local privado',
            'start': {'dateTime': '2030-06-17T10:00:00-03:00', 'timeZone': ZONE},
            'end': {'dateTime': '2030-06-17T10:30:00-03:00', 'timeZone': ZONE},
            'htmlLink': 'https://example.invalid/event', 'transparency': 'opaque',
        }
        self.original = deepcopy(self.backend.saved[EVENT_ID])
        self.sessions = []
        def session():
            value = self.backend.session()
            self.sessions.append(value)
            return value
        self.addCleanup(patch.stopall)
        patch('app.google_transport.requests.Session', side_effect=session).start()
        patch('app.google_calendar.database', database).start()
        self.resolver = patch('app.google_calendar.resolve_calendar', return_value=self.backend.calendar).start()
        patch('app.google_calendar.get_credentials', side_effect=AssertionError('No OAuth allowed')).start()
        patch('googleapiclient.http.time.sleep').start()
        self.sdk = build('calendar', 'v3', http=AuthorizedHttp(AnonymousCredentials(),
            http=SingleAttemptHttp(), max_refresh_attempts=0), cache_discovery=False, static_discovery=True)
        self.gateway = google_calendar.GoogleCalendarGateway(self.sdk)
        self.addCleanup(self.assert_closed)
        self.addCleanup(self.sdk.close)

    def assert_closed(self):
        self.assertTrue(all(session.closed for session in self.sessions))

    def change(self):
        return ScheduleChange(datetime.fromisoformat('2030-06-17T11:00:00-03:00'),
                              datetime.fromisoformat('2030-06-17T11:30:00-03:00'), ZONE)

    def edit(self, **guards):
        return self.gateway.update_event_details(EVENT_ID, DetailsChange(title='Título novo'),
            time_zone=ZONE, execute=True, expected_calendar_id=guards.get('calendar', CALENDAR),
            expected_version=guards.get('version', '"v1"'))

    def reschedule(self, **guards):
        return self.gateway.reschedule_event(EVENT_ID, self.change(), execute=True,
            expected_calendar_id=guards.get('calendar', CALENDAR), expected_version=guards.get('version', '"v1"'))

    def writes(self):
        return [call for call in self.backend.calls if call['method'] != 'GET']

    def test_update_snapshot_one_get_preserves_full_event_and_transparency(self):
        previous = deepcopy(self.backend.saved)
        result = self.gateway.get_update_snapshot(EVENT_ID, time_zone=ZONE)
        self.assertEqual(result.calendar_id, CALENDAR)
        self.assertEqual(result.event, event())
        self.assertEqual(result.version, '"v1"')
        self.assertEqual(result.transparency, 'opaque')
        self.assertEqual(self.backend.saved, previous)
        self.assertEqual(len(self.backend.calls), 1)
        call = self.backend.calls[0]
        self.assertEqual(call['method'], 'GET')
        self.assertEqual(call['event_id'], EVENT_ID)
        for field in ('description', 'location', 'etag', 'transparency'):
            self.assertIn(field, call['query']['fields'])

    def test_snapshot_missing_cancelled_and_invalid_provider_data_never_write(self):
        for changes in ({'status': 'cancelled'}, {'id': 'Other'}, {'etag': None}, {'etag': 'private\n'},
                        {'recurrence': ['RRULE:FREQ=DAILY']}, {'eventType': 'birthday'},
                        {'transparency': 'unknown'}, {'description': None}):
            self.backend.saved[EVENT_ID] = {**self.original, **changes}
            with self.assertRaises(CalendarOperationError):
                self.gateway.get_update_snapshot(EVENT_ID, time_zone=ZONE)
        self.backend.saved.clear()
        with self.assertRaises(CalendarOperationError):
            self.gateway.get_update_snapshot(EVENT_ID, time_zone=ZONE)
        self.assertEqual(self.writes(), [])

    def test_snapshot_input_validation_precedes_provider(self):
        self.resolver.reset_mock()
        for eid, zone in (('', ZONE), (True, ZONE), ('bad id', ZONE), (EVENT_ID, 'bad/zone')):
            with self.assertRaises(ValueError):
                self.gateway.get_update_snapshot(eid, time_zone=zone)
        self.resolver.assert_not_called()
        self.assertEqual(self.backend.calls, [])

    def test_snapshot_transport_failure_has_no_private_diagnostic(self):
        with patch.object(self.sdk, 'events', side_effect=OSError('PRIVATE_DIAGNOSTIC')):
            with self.assertRaises(CalendarOperationError) as error:
                self.gateway.get_update_snapshot(EVENT_ID, time_zone=ZONE)
        self.assertNotIn('PRIVATE_DIAGNOSTIC', str(error.exception))
        self.assertIsNone(error.exception.__cause__)

    def test_bound_edit_uses_etag_and_preserves_unrequested_details_and_schedule(self):
        result = self.edit()
        self.assertEqual(result.status, 'updated')
        self.assertEqual(result.after.title, 'Título novo')
        for field in ('description', 'location', 'start', 'end', 'all_day', 'time_zone'):
            self.assertEqual(getattr(result.before, field), getattr(result.after, field))
        self.assertEqual(len(self.writes()), 1)
        write = self.writes()[0]
        self.assertEqual(write['method'], 'PATCH')
        self.assertEqual(write['body'], {'summary': 'Título novo'})
        self.assertEqual(write['headers']['If-Match'], '"v1"')
        self.assertEqual(write['query']['sendUpdates'], 'none')

    def test_bound_reschedule_uses_etag_preserves_metadata_and_excludes_self(self):
        # O intervalo novo se sobrepõe ao horário anterior do próprio evento.
        self.change = lambda: ScheduleChange(datetime.fromisoformat('2030-06-17T10:15:00-03:00'),
            datetime.fromisoformat('2030-06-17T10:45:00-03:00'), ZONE)
        result = self.reschedule()
        self.assertEqual(result.status, 'rescheduled')
        self.assertEqual(result.after.start, self.change().start.isoformat())
        for field in ('summary', 'description', 'location', 'transparency'):
            self.assertEqual(self.backend.saved[EVENT_ID][field], self.original[field])
        self.assertEqual(len(self.writes()), 1)
        write = self.writes()[0]
        self.assertEqual(write['headers']['If-Match'], '"v1"')
        self.assertEqual(set(write['body']), {'start', 'end'})

    def test_changed_calendar_rejects_both_commands_before_event_read_or_patch(self):
        for method in (self.edit, self.reschedule):
            with self.assertRaises(UpdateBindingError):
                method(calendar='another-calendar@example.invalid')
        self.assertEqual(self.backend.calls, [])

    def test_untitled_schedule_projects_label_and_preserves_raw_title(self):
        for title in ('', '   '):
            self.backend.saved[EVENT_ID] = {**deepcopy(self.original), 'summary': title}
            self.backend.calls.clear()
            bound = self.gateway.get_update_snapshot(EVENT_ID, time_zone=ZONE)
            self.assertEqual(bound.event.title, title)
            result = self.reschedule()
            self.assertEqual(result.before.title, '(sem título)')
            self.assertEqual(result.after.title, '(sem título)')
            current = self.gateway.get_update_snapshot(EVENT_ID, time_zone=ZONE)
            self.assertEqual(current.event.title, title)
            self.assertEqual(self.backend.saved[EVENT_ID]['summary'], title)
            self.assertEqual(len(self.writes()), 1)

    def test_changed_version_rejects_both_commands_before_patch(self):
        for method in (self.edit, self.reschedule):
            with self.assertRaises(UpdateVersionError):
                method(version='"old-version"')
        self.assertTrue(self.backend.calls)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.backend.saved[EVENT_ID], self.original)

    def test_invalid_guards_precede_calendar_resolution_for_both_commands(self):
        self.resolver.reset_mock()
        for method in (self.edit, self.reschedule):
            for field, values in (('calendar', ('', 'two words', 'private\n', True, '\ud800')),
                                  ('version', ('', 'private\r\n', True, '\ud800'))):
                for value in values:
                    with self.subTest(field=field), self.assertRaises(ValueError):
                        method(**{field: value})
        self.resolver.assert_not_called()
        self.assertEqual(self.backend.calls, [])

    def test_external_race_fails_if_match_without_overwriting_either_action(self):
        for method in (self.edit, self.reschedule):
            self.backend.saved[EVENT_ID] = deepcopy(self.original)
            self.backend.calls.clear()
            def outside_change(_method, eid, body):
                self.backend.saved[eid]['etag'] = '"v99"'
                self.backend.saved[eid]['summary'] = 'Mudança externa'
            self.backend.before_write = outside_change
            with self.assertRaises(CalendarOperationError):
                method()
            self.assertEqual(len(self.writes()), 1)
            self.assertEqual(self.writes()[0]['status'], 412)
            self.assertEqual(self.backend.saved[EVENT_ID]['summary'], 'Mudança externa')
            self.assertEqual(self.backend.saved[EVENT_ID]['start'], self.original['start'])

    def test_lost_patch_response_is_confirmed_by_read_without_second_patch(self):
        for method in (self.edit, self.reschedule):
            self.backend.saved[EVENT_ID] = deepcopy(self.original)
            self.backend.calls.clear()
            self.backend.lose_response_once.add('PATCH')
            result = method()
            self.assertTrue(result.recovered)
            self.assertEqual(len(self.writes()), 1)
            self.assertEqual(self.writes()[0]['method'], 'PATCH')

    def test_schedule_conflict_is_rejected_before_patch(self):
        self.backend.saved['BlockingEvent'] = {**deepcopy(self.original), 'id': 'BlockingEvent',
            'start': {'dateTime': '2030-06-17T11:00:00-03:00'},
            'end': {'dateTime': '2030-06-17T11:30:00-03:00'}}
        with self.assertRaises(CalendarConflictError):
            self.reschedule()
        self.assertEqual(self.writes(), [])

    def test_old_unbound_calls_remain_compatible_and_repetition_is_unchanged(self):
        result = self.gateway.update_event_details(EVENT_ID, DetailsChange(title='Novo'), time_zone=ZONE, execute=True)
        self.assertEqual(result.status, 'updated')
        result = self.gateway.update_event_details(EVENT_ID, DetailsChange(title='Novo'), time_zone=ZONE, execute=True)
        self.assertEqual(result.status, 'unchanged')
        result = self.gateway.reschedule_event(EVENT_ID, self.change(), execute=True)
        self.assertEqual(result.status, 'rescheduled')
        result = self.gateway.reschedule_event(EVENT_ID, self.change(), execute=True)
        self.assertEqual(result.status, 'unchanged')
        self.assertEqual(len(self.writes()), 2)


if __name__ == '__main__':
    unittest.main()
