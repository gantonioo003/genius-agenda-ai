"""CRUD do painel com stores reais temporários e calendário controlado, sem rede."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from uuid import uuid4

from app.agent_creation_store import AgentCreationStore
from app.agent_deletion_store import AgentDeletionStore
from app.agent_update_store import AgentUpdateStore
from app.conversation_store import ConversationStore
from app.panel_operation_store import PanelOperationStore
from app.services.agenda import AgendaEvent, CalendarListing
from app.services.agenda_tools import minimal_event
from app.services.confirmation_snapshot import ConfirmationSnapshot
from app.services.conflicts import build_conflict_report, require_available
from app.services.event_deletion import DeletionResult, DeletionBindingError, DeletionVersionError, deletion_confirmation
from app.services.event_details import EventDetails, DetailsResult
from app.services.event_selection import EventLookup
from app.services.interviews import CreatedInterview, ReconciledCreation, CalendarBindingError
from app.services.panel_agenda import PanelAgendaError, PanelAgendaService, _civil
from app.services.rescheduling import RescheduleResult
from app.services.update_snapshot import UpdateSnapshot, UpdateBindingError, UpdateVersionError

ZONE = 'America/Recife'
OWNER = sha256(b'panel-fixture-owner').hexdigest()
OTHER = sha256(b'panel-other-owner').hexdigest()
NOW = datetime(2030, 6, 17, 12, tzinfo=timezone.utc)


def agenda(event):
    return AgendaEvent(**{key: getattr(event, key) for key in ('event_id', 'title', 'start', 'end', 'all_day', 'time_zone', 'status', 'html_link')})


class FakeCalendar:
    def __init__(self):
        self.events = {}
        self.versions = {}
        self.created = {}
        self.calendar_id = 'fixture-calendar'
        self.calendar_name = 'Agenda de demonstração'
        self.time_zone = ZONE
        self.writes = []
        self.lost_response = False
        self.entered = self.release = None

    def add(self, event_id='fixture-event', title='Entrevista painel', start='2030-06-18T10:00:00-03:00', end='2030-06-18T10:30:00-03:00', **values):
        item = EventDetails(event_id, title, start, end, values.pop('all_day', False), self.time_zone, 'confirmed',
                            'https://example.invalid/event', values.pop('description', 'Descrição original'), values.pop('location', 'Sala A'))
        self.events[event_id] = item
        self.versions[event_id] = 'version-1'
        return item

    def binding(self):
        from app.api_panel import PanelCalendarBinding
        return PanelCalendarBinding(self.calendar_id, self.calendar_name, self.time_zone)

    @contextmanager
    def factory(self):
        yield self

    def get_event(self, event_id, *, time_zone):
        item = self.events.get(event_id)
        return EventLookup(self.calendar_name, agenda(item) if item else None)

    def get_confirmation_snapshot(self, event_id, *, time_zone):
        item = self.events.get(event_id)
        return ConfirmationSnapshot(self.calendar_id, self.calendar_name, agenda(item) if item else None,
                                    self.versions[event_id] if item else None)

    def get_update_snapshot(self, event_id, *, time_zone):
        return UpdateSnapshot(self.calendar_id, self.calendar_name, self.events[event_id], self.versions[event_id], 'opaque')

    def list_events(self, period, **kwargs):
        return CalendarListing(self.calendar_name, tuple(agenda(item) for item in self.events.values()))

    def check_conflicts(self, period, *, exclude_event_id=None):
        return build_conflict_report(period, self.calendar_name, tuple(agenda(item) for item in self.events.values()), exclude_event_id=exclude_event_id)

    def _pause(self):
        if self.entered:
            self.entered.set()
            if not self.release.wait(5):
                raise RuntimeError('fixture timeout')

    def create_event(self, draft, *, operation_id, expected_calendar_id):
        if expected_calendar_id != self.calendar_id:
            raise CalendarBindingError()
        self._pause()
        from app.services.agenda import CalendarPeriod
        require_available(self.check_conflicts(CalendarPeriod(draft.start, draft.end, draft.time_zone)))
        if operation_id not in self.created:
            event_id = 'fixture-' + sha256(operation_id.encode()).hexdigest()[:20]
            self.add(event_id, draft.title, draft.start.isoformat(), draft.end.isoformat(), description=draft.description, location='')
            self.created[operation_id] = CreatedInterview(event_id, 'https://example.invalid/event', self.calendar_name)
            self.writes.append(('create', event_id))
        if self.lost_response:
            raise OSError('PRIVATE_PROVIDER_DATA')
        return self.created[operation_id]

    def reconcile_creation(self, draft, *, operation_id, expected_calendar_id):
        return ReconciledCreation('found', self.created[operation_id]) if operation_id in self.created else ReconciledCreation('unregistered')

    def _guard(self, event_id, expected_calendar_id, expected_version):
        if self.calendar_id != expected_calendar_id:
            raise UpdateBindingError()
        if self.versions.get(event_id) != expected_version:
            raise UpdateVersionError()

    def update_event_details(self, event_id, change, *, time_zone, execute, expected_calendar_id, expected_version):
        self._guard(event_id, expected_calendar_id, expected_version)
        self._pause()
        before = self.events[event_id]
        requested = {key: getattr(change, key) for key in ('title', 'description', 'location') if getattr(change, key) is not None}
        after = replace(before, **requested)
        self.events[event_id] = after
        self.versions[event_id] += 'x'
        self.writes.append(('edit', event_id))
        if self.lost_response:
            raise OSError('PRIVATE_PROVIDER_DATA')
        return DetailsResult('updated', self.calendar_name, before, after, requested)

    def reschedule_event(self, event_id, change, *, execute, expected_calendar_id, expected_version):
        self._guard(event_id, expected_calendar_id, expected_version)
        self._pause()
        from app.services.agenda import CalendarPeriod
        require_available(self.check_conflicts(CalendarPeriod(change.start, change.end, change.time_zone), exclude_event_id=event_id))
        before = self.events[event_id]
        after = replace(before, start=change.start.isoformat(), end=change.end.isoformat())
        self.events[event_id] = after
        self.versions[event_id] += 'x'
        self.writes.append(('reschedule', event_id))
        if self.lost_response:
            raise OSError('PRIVATE_PROVIDER_DATA')
        return RescheduleResult('rescheduled', self.calendar_name, agenda(before), agenda(after), change.start.isoformat(), change.end.isoformat(), change.time_zone)

    def delete_event(self, event_id, *, time_zone, execute, confirmation_token, expected_calendar_id, expected_version):
        if self.calendar_id != expected_calendar_id:
            raise DeletionBindingError()
        if self.versions.get(event_id) != expected_version:
            raise DeletionVersionError()
        if confirmation_token != deletion_confirmation(self.calendar_id, event_id, expected_version):
            raise ValueError()
        self._pause()
        before = self.events.pop(event_id)
        self.writes.append(('delete', event_id))
        if self.lost_response:
            raise OSError('PRIVATE_PROVIDER_DATA')
        return DeletionResult('deleted', self.calendar_name, agenda(before))


def make_service(root, calendar, clock=None):
    root = Path(root)
    clock = clock or (lambda: datetime.now(timezone.utc))
    return PanelAgendaService(PanelOperationStore(root / 'operations.sqlite3', clock=clock),
        ConversationStore(root / 'context.sqlite3', clock=clock), calendar.factory,
        zone_loader=lambda: calendar.time_zone, clock=clock,
        creation_store=AgentCreationStore(root / 'create.sqlite3', clock=clock),
        update_store=AgentUpdateStore(root / 'update.sqlite3', clock=clock),
        deletion_store=AgentDeletionStore(root / 'delete.sqlite3', clock=clock))


class PanelAgendaTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.calendar = FakeCalendar()
        self.now = NOW
        self.service = make_service(self.temp.name, self.calendar, lambda: self.now)

    def preview(self, action, event=None, event_id=None, request_id=None):
        return self.service.preview(OWNER, request_id or str(uuid4()), action, event_id, event)

    def create(self):
        return self.preview('create', {'title': 'Nova entrevista', 'start': '2030-06-18T14:00', 'end': '2030-06-18T14:30', 'description': 'Preparo'})

    def test_create_requires_preview_and_confirms_once(self):
        preview = self.create()
        self.assertEqual(preview.data['state'], 'pending')
        self.assertEqual(self.calendar.writes, [])
        result = self.service.confirm(OWNER, preview.data['operation_id'])
        self.assertEqual((result.data['state'], result.data['mutation_state']), ('succeeded', 'confirmed'))
        self.assertIsNotNone(result.data['after']['event_id'])
        self.assertEqual(self.service.confirm(OWNER, preview.data['operation_id']).data, result.data)
        self.assertEqual(len(self.calendar.writes), 1)

    def test_edit_has_full_before_and_preserves_interval(self):
        item = self.calendar.add()
        preview = self.preview('edit', {'description': '', 'location': 'Sala B'}, item.event_id)
        self.assertEqual(preview.data['before']['description'], 'Descrição original')
        result = self.service.confirm(OWNER, preview.data['operation_id'])
        self.assertEqual(result.data['state'], 'succeeded')
        self.assertEqual((result.data['after']['description'], result.data['after']['location']), ('', 'Sala B'))
        self.assertEqual(result.data['after']['start'], item.start)

    def test_two_reschedules_and_delete_keep_exact_identity(self):
        item = self.calendar.add()
        for hour in (13, 15):
            preview = self.preview('reschedule', {'start': f'2030-06-18T{hour}:00', 'end': f'2030-06-18T{hour}:30'}, item.event_id)
            result = self.service.confirm(OWNER, preview.data['operation_id'])
            self.assertEqual(result.data['state'], 'succeeded')
            self.assertEqual(result.data['after']['start'], f'2030-06-18T{hour}:00:00-03:00')
        preview = self.preview('delete', event_id=item.event_id)
        self.assertEqual(preview.data['before']['start'], '2030-06-18T15:00:00-03:00')
        self.assertEqual(self.service.confirm(OWNER, preview.data['operation_id']).data['state'], 'succeeded')
        self.assertNotIn(item.event_id, self.calendar.events)

    def test_other_session_cannot_see_confirm_or_cancel(self):
        op = self.create().data['operation_id']
        for command in (self.service.status, self.service.confirm, self.service.cancel):
            with self.assertRaises(PanelAgendaError) as raised:
                command(OTHER, op)
            self.assertEqual(raised.exception.http_status, 404)
        self.assertEqual(self.calendar.writes, [])

    def test_repeated_preview_same_request_is_cached_and_changed_request_rejected(self):
        rid = str(uuid4())
        event = {'title': 'Nova entrevista', 'start': '2030-06-18T14:00', 'end': '2030-06-18T14:30'}
        first = self.preview('create', event, request_id=rid)
        self.assertEqual(self.preview('create', event, request_id=rid).data, first.data)
        with self.assertRaises(PanelAgendaError) as raised:
            self.preview('create', {**event, 'title': 'Outra'}, request_id=rid)
        self.assertEqual(raised.exception.http_status, 409)

    def test_cancel_blocks_confirmation(self):
        op = self.create().data['operation_id']
        self.assertEqual(self.service.cancel(OWNER, op).data['state'], 'cancelled')
        self.assertEqual(self.service.confirm(OWNER, op).data['state'], 'cancelled')
        self.assertEqual(self.calendar.writes, [])

    def test_expired_preview_blocks_confirmation(self):
        op = self.create().data['operation_id']
        self.now += timedelta(minutes=6)
        self.assertEqual(self.service.confirm(OWNER, op).data['state'], 'expired')
        self.assertEqual(self.calendar.writes, [])

    def test_external_version_change_invalidates_before_write(self):
        item = self.calendar.add()
        op = self.preview('edit', {'title': 'Novo título'}, item.event_id).data['operation_id']
        self.calendar.versions[item.event_id] += 'external'
        self.assertEqual(self.service.confirm(OWNER, op).data['state'], 'invalidated')
        self.assertEqual(self.calendar.writes, [])

    def test_calendar_change_invalidates_before_write(self):
        op = self.create().data['operation_id']
        self.calendar.calendar_id = 'different-calendar'
        result = self.service.confirm(OWNER, op)
        self.assertEqual(result.data['state'], 'invalidated')
        self.assertEqual(self.calendar.writes, [])

    def test_conflict_is_visible_before_confirm_and_never_writes(self):
        self.calendar.add(start='2030-06-18T14:00:00-03:00', end='2030-06-18T14:45:00-03:00')
        result = self.create()
        self.assertEqual(result.data['state'], 'conflict')
        self.assertEqual(len(result.data['conflicts']), 1)
        self.assertEqual(self.service.confirm(OWNER, result.data['operation_id']).data['state'], 'conflict')
        self.assertEqual(self.calendar.writes, [])

    def test_conflict_that_appears_after_preview_blocks_execution(self):
        op = self.create().data['operation_id']
        self.calendar.add(start='2030-06-18T14:00:00-03:00', end='2030-06-18T14:45:00-03:00')
        result = self.service.confirm(OWNER, op)
        self.assertEqual(result.data['state'], 'conflict')
        self.assertEqual(self.calendar.writes, [])

    def test_lost_provider_response_is_unknown_and_cannot_repeat_write(self):
        op = self.create().data['operation_id']
        self.calendar.lost_response = True
        self.assertEqual(self.service.confirm(OWNER, op).data['state'], 'unknown')
        self.assertEqual(self.service.confirm(OWNER, op).data['state'], 'unknown')
        self.assertEqual(self.service.status(OWNER, op).data['state'], 'unknown')
        self.assertEqual(len(self.calendar.writes), 1)

    def test_concurrent_confirmations_have_one_executor(self):
        op = self.create().data['operation_id']
        self.calendar.entered, self.calendar.release = Event(), Event()
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(self.service.confirm, OWNER, op)
            self.assertTrue(self.calendar.entered.wait(5))
            second = pool.submit(self.service.confirm, OWNER, op).result()
            self.assertEqual(second.data['state'], 'confirming')
            self.calendar.release.set()
            self.assertEqual(first.result().data['state'], 'succeeded')
        self.assertEqual(len(self.calendar.writes), 1)

    def test_invalid_duration_and_create_location_are_rejected(self):
        result = self.preview('create', {'title': 'Nova', 'start': '2030-06-18T14:30', 'end': '2030-06-18T14:00'})
        self.assertEqual(result.http_status, 422)
        with self.assertRaises(PanelAgendaError):
            self.preview('create', {'title': 'Nova', 'start': '2030-06-18T14:00', 'end': '2030-06-18T14:30', 'location': 'Sala'})
        self.assertEqual(self.calendar.writes, [])

    def test_unchanged_edit_does_not_require_confirmation(self):
        item = self.calendar.add()
        result = self.preview('edit', {'title': item.title}, item.event_id)
        self.assertEqual((result.data['state'], result.data['error']['code']), ('failed', 'no_changes'))

    def test_all_day_reschedule_is_rejected(self):
        item = self.calendar.add(start='2030-06-18', end='2030-06-19', all_day=True)
        result = self.preview('reschedule', {'start': '2030-06-18T14:00', 'end': '2030-06-18T14:30'}, item.event_id)
        self.assertNotEqual(result.data['state'], 'pending')
        self.assertEqual(self.calendar.writes, [])

    def test_all_day_details_and_deletion_keep_dates(self):
        item = self.calendar.add(start='2030-06-18', end='2030-06-19', all_day=True)
        edit = self.preview('edit', {'description': 'Dia de preparo'}, item.event_id)
        edited = self.service.confirm(OWNER, edit.data['operation_id'])
        self.assertEqual(edited.data['state'], 'succeeded')
        self.assertEqual((edited.data['after']['start'], edited.data['after']['end']), (item.start, item.end))
        deletion = self.preview('delete', event_id=item.event_id)
        self.assertEqual(self.service.confirm(OWNER, deletion.data['operation_id']).data['state'], 'succeeded')
        self.assertNotIn(item.event_id, self.calendar.events)

    def test_provider_exception_attributes_are_not_public_error_codes(self):
        class ProviderError(RuntimeError):
            code = 'PRIVATE_PROVIDER_DIAGNOSTIC'
            http_status = 499
        def unavailable():
            raise ProviderError('PRIVATE_DATA')
        self.service.zone_loader = unavailable
        result = self.create()
        self.assertEqual(result.http_status, 503)
        self.assertEqual(result.data['error']['code'], 'panel_agenda_unavailable')
        self.assertNotIn('PRIVATE', str(result.data))

    def test_local_time_rejects_dst_gap_and_fold_but_accepts_explicit_offset(self):
        for value in ('2030-03-10T02:30', '2030-11-03T01:30'):
            with self.assertRaises(ValueError):
                _civil(value, 'America/New_York')
        self.assertEqual(_civil('2030-11-03T01:30:00-04:00', 'America/New_York'), '2030-11-03T01:30:00-04:00')

    def test_public_payload_has_no_internal_conversation_or_proposal_ids(self):
        result = self.create()
        self.assertEqual(set(result.data), {'operation_id', 'action', 'state', 'before', 'after', 'expires_at', 'message', 'mutation_state', 'error', 'conflicts'})
        self.assertNotIn('calendar_id', result.data)
        self.assertNotIn('PRIVATE', repr(result))


if __name__ == '__main__':
    unittest.main()
