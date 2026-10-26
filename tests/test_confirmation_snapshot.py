"""Leituras de confirmação com SDK real e transporte fictício, sem conta Google."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from google.auth.credentials import AnonymousCredentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import httplib2

from app import google_calendar
from app.google_transport import SingleAttemptHttp
from app.services.agenda import AgendaEvent
from app.services.confirmation_snapshot import ConfirmationSnapshot, validate_confirmation_snapshot
from app.services.event_details import EventDetails
from app.services.interviews import CalendarOperationError
from calendar_http_fake import CalendarHttpFake


ZONE = 'America/Recife'
CALENDAR = 'snapshot-calendar@example.invalid'
EVENT_ID = 'OpaqueOccurrence_20300617T130000Z'

def event(**changes):
    value = AgendaEvent(EVENT_ID, 'Entrevista fictícia', '2030-06-17T10:00:00-03:00',
                        '2030-06-17T10:30:00-03:00', False, ZONE, 'confirmed', None)
    return replace(value, **changes)

class ConfirmationSnapshotContractTests(unittest.TestCase):
    def test_creation_and_confirmed_absence_preserve_calendar_identity(self):
        snapshot = ConfirmationSnapshot(CALENDAR, 'Genius Testes', None, None)
        self.assertEqual(validate_confirmation_snapshot(snapshot, None, ZONE), snapshot)
        self.assertEqual(validate_confirmation_snapshot(snapshot, EVENT_ID, ZONE), snapshot)

    def test_snapshot_copies_projection_and_preserves_exact_opaque_identity_version(self):
        details = EventDetails(**event().__dict__, description='PRIVATE_DESCRIPTION', location='PRIVATE_LOCATION')
        snapshot = ConfirmationSnapshot(CALENDAR, 'Genius Testes', details, 'W/"version-A"')
        canonical = validate_confirmation_snapshot(snapshot, EVENT_ID, ZONE)
        self.assertEqual(type(canonical.event), AgendaEvent)
        self.assertEqual(canonical.version, 'W/"version-A"')
        self.assertEqual(canonical.event.event_id, EVENT_ID)
        self.assertNotIn('description', canonical.event.__dict__)
        self.assertNotIn('location', canonical.event.__dict__)
        self.assertIsNot(canonical, snapshot)

    def test_frozen_private_representation_does_not_expose_binding_or_event(self):
        snapshot = ConfirmationSnapshot(CALENDAR, 'Genius Testes', event(), '"private-version"')
        with self.assertRaises(FrozenInstanceError):
            snapshot.version = 'changed'
        for private in (CALENDAR, EVENT_ID, 'Genius Testes', 'private-version'):
            self.assertNotIn(private, repr(snapshot))

    def test_invalid_calendar_identity_and_names_are_rejected(self):
        base = ConfirmationSnapshot(CALENDAR, 'Genius Testes', None, None)
        for field, values in (
            ('calendar_id', (None, '', ' ', 'two words', 'x' * 1025, 'private\n', '\ud800')),
            ('calendar_name', (None, '', ' ', 'x' * 4001, 'private\0', '\ud800')),
        ):
            for value in values:
                with self.subTest(field=field, value_type=type(value).__name__), self.assertRaises(ValueError):
                    validate_confirmation_snapshot(replace(base, **{field: value}), None, ZONE)

    def test_creation_cannot_claim_event_and_absence_cannot_claim_version(self):
        for value in (ConfirmationSnapshot(CALENDAR, 'Genius Testes', event(), '"v1"'),
                      ConfirmationSnapshot(CALENDAR, 'Genius Testes', None, '"v1"')):
            with self.assertRaises(ValueError):
                validate_confirmation_snapshot(value, None, ZONE)
        with self.assertRaises(ValueError):
            validate_confirmation_snapshot(ConfirmationSnapshot(CALENDAR, 'Genius Testes', None, '"v1"'), EVENT_ID, ZONE)

    def test_existing_event_requires_valid_exact_id_zone_status_and_version(self):
        base = ConfirmationSnapshot(CALENDAR, 'Genius Testes', event(), '"v1"')
        cases = [replace(base, event=event(event_id='different')),
                 replace(base, event=event(status='cancelled')),
                 replace(base, event=event(status='unconfirmed')),
                 replace(base, event=event(time_zone='UTC')),
                 replace(base, event=event(end='2030-06-17T09:00:00-03:00'))]
        cases.extend(replace(base, version=value) for value in (None, '', ' ', True, 'x' * 2049, 'private\r\n', '\ud800'))
        for value in cases:
            with self.assertRaises(ValueError) as error:
                validate_confirmation_snapshot(value, EVENT_ID, ZONE)
            self.assertNotIn('private', str(error.exception))
            self.assertIsNone(error.exception.__cause__)

    def test_all_day_event_keeps_its_calendar_zone(self):
        base = ConfirmationSnapshot(CALENDAR, 'Genius Testes', event(
            start='2030-06-17', end='2030-06-18', all_day=True, time_zone='America/Sao_Paulo'), '"v1"')
        self.assertEqual(validate_confirmation_snapshot(base, EVENT_ID, 'UTC').event.time_zone, 'America/Sao_Paulo')

    def test_unknown_snapshot_shapes_invalid_expected_ids_and_zones_fail(self):
        for value in (None, {}, [], 'private'):
            with self.assertRaises(ValueError):
                validate_confirmation_snapshot(value, EVENT_ID, ZONE)
        base = ConfirmationSnapshot(CALENDAR, 'Genius Testes', None, None)
        for expected_id, zone in (('', ZONE), (False, ZONE), ('bad id', ZONE), (None, 'bad/zone')):
            with self.assertRaises(ValueError):
                validate_confirmation_snapshot(base, expected_id, zone)


class ConfirmationSnapshotGatewayTests(unittest.TestCase):
    def setUp(self):
        self.backend = CalendarHttpFake()
        self.backend.calendar['id'] = CALENDAR
        self.backend.saved[EVENT_ID] = {
            'id': EVENT_ID, 'summary': 'Entrevista fictícia', 'status': 'confirmed', 'etag': '"v1"',
            'description': 'PRIVATE_DESCRIPTION', 'location': 'PRIVATE_LOCATION',
            'start': {'dateTime': '2030-06-17T10:00:00-03:00', 'timeZone': ZONE},
            'end': {'dateTime': '2030-06-17T10:30:00-03:00', 'timeZone': ZONE},
            'htmlLink': 'https://example.invalid/event',
        }
        self.sessions = []
        def session():
            value = self.backend.session()
            request = value.request
            def only_get(method, url, **options):
                self.assertEqual(method.upper(), 'GET', 'Consent snapshots never write events.')
                return request(method, url, **options)
            value.request = only_get
            self.sessions.append(value)
            return value
        self.addCleanup(patch.stopall)
        patch('app.google_transport.requests.Session', side_effect=session).start()
        patch('app.google_calendar.resolve_calendar', return_value=self.backend.calendar).start()
        transport = SingleAttemptHttp()
        http = AuthorizedHttp(AnonymousCredentials(), http=transport, max_refresh_attempts=0)
        self.sdk = build('calendar', 'v3', http=http, cache_discovery=False, static_discovery=True)
        self.addCleanup(self.sdk.close)
        self.gateway = google_calendar.GoogleCalendarGateway(self.sdk)

    def test_existing_event_traverses_real_sdk_and_returns_exact_etag_without_private_details(self):
        previous = deepcopy(self.backend.saved)
        snapshot = self.gateway.get_confirmation_snapshot(EVENT_ID, time_zone=ZONE)
        self.assertEqual(snapshot.calendar_id, CALENDAR)
        self.assertEqual(snapshot.calendar_name, 'Genius Testes')
        self.assertEqual(snapshot.version, '"v1"')
        self.assertEqual(snapshot.event.event_id, EVENT_ID)
        self.assertEqual(type(snapshot.event), AgendaEvent)
        self.assertEqual(self.backend.saved, previous)
        self.assertEqual(len(self.backend.calls), 1)
        call = self.backend.calls[0]
        self.assertEqual(call['method'], 'GET')
        self.assertEqual(call['event_id'], EVENT_ID)
        self.assertEqual(call['query']['timeZone'], ZONE)
        self.assertEqual(call['query']['fields'], self.gateway.DELETION_FIELDS)
        self.assertIsNone(call['body'])
        self.assertNotIn('description', snapshot.event.__dict__)
        self.assertNotIn('location', snapshot.event.__dict__)

    def test_creation_reads_only_calendar_identity_without_event_request(self):
        snapshot = self.gateway.get_confirmation_snapshot(None, time_zone=ZONE)
        self.assertEqual(snapshot, ConfirmationSnapshot(CALENDAR, 'Genius Testes', None, None))
        self.assertEqual(self.backend.calls, [])

    def test_missing_and_cancelled_events_are_confirmed_absence(self):
        for cancelled in (False, True):
            if cancelled:
                self.backend.saved[EVENT_ID] = {'id': EVENT_ID, 'status': 'cancelled'}
            else:
                self.backend.saved.pop(EVENT_ID)
            snapshot = self.gateway.get_confirmation_snapshot(EVENT_ID, time_zone=ZONE)
            self.assertIsNone(snapshot.event)
            self.assertIsNone(snapshot.version)
        self.assertTrue(all(call['method'] == 'GET' for call in self.backend.calls))

    def test_410_is_confirmed_absence_without_treating_other_http_errors_as_absence(self):
        collection = Mock()
        self.sdk.events = Mock(return_value=collection)
        for status in (410, 403, 500):
            response = httplib2.Response({'status': str(status), 'reason': 'Fixture error'})
            collection.get.return_value.execute.side_effect = HttpError(response, b'{}')
            if status == 410:
                self.assertIsNone(self.gateway.get_confirmation_snapshot(EVENT_ID, time_zone=ZONE).event)
            else:
                with self.assertRaises(CalendarOperationError):
                    self.gateway.get_confirmation_snapshot(EVENT_ID, time_zone=ZONE)

    def test_input_validation_precedes_calendar_resolution_and_sdk(self):
        with patch('app.google_calendar.resolve_calendar') as resolver:
            for event_id, zone in (('', ZONE), (False, ZONE), ('bad id', ZONE), (None, 'bad/zone')):
                with self.assertRaises(ValueError):
                    self.gateway.get_confirmation_snapshot(event_id, time_zone=zone)
            resolver.assert_not_called()
        self.assertEqual(self.backend.calls, [])

    def test_bad_versions_identity_and_series_are_errors_without_any_mutation(self):
        base = deepcopy(self.backend.saved[EVENT_ID])
        for changes in ({'etag': None}, {'etag': ''}, {'etag': 'private\n'},
                        {'etag': 'x' * 2049}, {'id': 'different'}, {'status': 'unknown'},
                        {'recurrence': ['RRULE:FREQ=DAILY']}, {'eventType': 'birthday'}):
            self.backend.saved[EVENT_ID] = {**base, **changes}
            with self.assertRaises(CalendarOperationError):
                self.gateway.get_confirmation_snapshot(EVENT_ID, time_zone=ZONE)
        self.assertTrue(all(call['method'] == 'GET' for call in self.backend.calls))

    def test_transport_failure_has_fixed_error_without_raw_diagnostic(self):
        with patch.object(self.gateway, '_deletion_snapshot', side_effect=OSError('PRIVATE_TRANSPORT_DETAIL')):
            with self.assertRaises(CalendarOperationError) as error:
                self.gateway.get_confirmation_snapshot(EVENT_ID, time_zone=ZONE)
        self.assertNotIn('PRIVATE_TRANSPORT_DETAIL', str(error.exception))
        self.assertIsNone(error.exception.__cause__)

    def test_changed_remote_version_is_observable_in_fresh_read(self):
        first = self.gateway.get_confirmation_snapshot(EVENT_ID, time_zone=ZONE)
        self.backend.saved[EVENT_ID]['etag'] = '"v2"'
        self.backend.saved[EVENT_ID]['summary'] = 'Título atualizado externamente'
        second = self.gateway.get_confirmation_snapshot(EVENT_ID, time_zone=ZONE)
        self.assertNotEqual(first.version, second.version)
        self.assertNotEqual(first.event.title, second.event.title)
        self.assertEqual(len(self.backend.calls), 2)
        self.assertTrue(all(call['method'] == 'GET' for call in self.backend.calls))

if __name__ == '__main__':
    unittest.main()
