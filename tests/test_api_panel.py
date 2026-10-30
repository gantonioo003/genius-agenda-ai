"""Leituras do painel com calendário/ledger fictícios e nenhuma rede."""

import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from urllib.parse import urlencode
import unittest
from unittest.mock import patch
import sqlite3

from fastapi import APIRouter, FastAPI, HTTPException

from app.api_panel import PanelCalendarBinding, _panel_session, register_panel_read_routes
from app.reminder_store import ReminderHistory, ReminderRecord, ReminderStore
from app.services.agenda import AgendaEvent
from app.services.event_details import EventDetails
from app.services.event_selection import EventLookup
from app.services.summary_context import SummaryCalendarBinding, SummaryCalendarSnapshot
from app.services.update_snapshot import UpdateSnapshot
from test_api_security import invoke_asgi


ZONE = 'America/Recife'
PRIVATE = 'PRIVATE_DO_NOT_ECHO_PANEL'
NOW = datetime(2030, 6, 18, 1, 0, tzinfo=timezone.utc)


def event():
    return AgendaEvent('OpaquePanelID', 'Entrevista fictícia', '2030-06-17T10:00:00-03:00',
        '2030-06-17T10:30:00-03:00', False, ZONE, 'confirmed', 'https://calendar.google.com/calendar/event?eid=public')


class Gateway:
    def __init__(self):
        self.current = event()
        self.events = (self.current,)
        self.bound = PanelCalendarBinding('private-calendar', 'Agenda de teste', ZONE)
        self.calls = []
        self.failure = None
        self.complete = True
        self.details = EventDetails(**self.current.__dict__, description='<p>Descrição completa</p><script>secret()</script>', location='Sala virtual')

    def binding(self):
        self.calls.append('binding')
        if self.failure:
            raise RuntimeError(PRIVATE)
        return self.bound

    def list_events(self, period, *, expected_binding):
        self.calls.append(('list', period, expected_binding))
        return SummaryCalendarSnapshot(SummaryCalendarBinding(expected_binding.calendar_id, expected_binding.time_zone),
            period, self.events, self.complete, 1, len(self.events))

    def get_event(self, event_id, *, time_zone):
        self.calls.append(('get', event_id))
        return EventLookup(self.bound.calendar_name, self.current)

    def get_update_snapshot(self, event_id, *, time_zone):
        self.calls.append(('details', event_id))
        return UpdateSnapshot(self.bound.calendar_id, self.bound.calendar_name, self.details, 'private-etag', 'opaque')


def reminder(number, *, state='pending'):
    return ReminderRecord(key=f'{number:064x}', calendar_id='private-calendar', event_id='OpaquePanelID',
        recipient_key='a' * 64, start_at=NOW, due_at=NOW, deadline_at=NOW, slot='preparation', offset_minutes=60,
        title='Entrevista fictícia', calendar_timezone=ZONE, config_revision=1, state=state,
        reason='accepted' if state == 'accepted' else 'planned', attempt_id=None,
        message_id_fingerprint='private-fingerprint', created_at=NOW, updated_at=NOW)


class Store:
    def __init__(self):
        self.records = (reminder(1), reminder(2, state='accepted'), reminder(3, state='accepted'))
        self.calls = []
        self.failure = False

    def list_reminders(self, *, states, limit):
        self.calls.append(('list', states, limit))
        if self.failure:
            raise RuntimeError(PRIVATE)
        return tuple(value for value in self.records if states is None or value.state in states)[:limit]

    def get(self, key):
        self.calls.append(('get', key))
        return next((value for value in self.records if value.key == key), None)

    def history(self, key):
        self.calls.append(('history', key))
        return (ReminderHistory(None, 'pending', 'planned', 'private-attempt', NOW),)


class PanelReadTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.gateway = Gateway()
        self.store = Store()
        self.opened = 0

        @contextmanager
        def factory():
            self.opened += 1
            yield self.gateway

        async def authorized():
            return SimpleNamespace(authenticated=True)

        self.app.dependency_overrides[_panel_session] = authorized
        self.app.state.panel_calendar_gateway_factory = factory
        self.app.state.reminder_store_factory = lambda: self.store
        self.app.state.panel_clock = lambda: NOW
        self.app.state.settings = SimpleNamespace(reminder_cards_enabled=True)
        router = APIRouter(prefix='/painel/api')
        register_panel_read_routes(router)
        self.app.include_router(router)

    def request(self, path='/events', query=()):
        result = asyncio.run(invoke_asgi(self.app, '/painel/api' + path,
            query=urlencode(query).encode(), headers=[(b'host', b'127.0.0.1:8000')]))
        self.assertNotIn(PRIVATE, str(result))
        self.assertNotIn('private-etag', str(result))
        self.assertNotIn('private-fingerprint', str(result))
        self.assertNotIn('private-attempt', str(result))
        self.assertEqual(result[1]['cache-control'], 'no-store')
        return result

    def test_default_date_uses_calendar_zone_not_utc_host(self):
        status, _, payload = self.request()
        self.assertEqual(status, 200)
        data = payload['data']
        self.assertEqual(data['reference_date'], '2030-06-17')
        self.assertEqual(data['today'], '2030-06-17')
        self.assertTrue(data['complete'])
        self.assertEqual(data['events'][0]['start'], '10:00')
        self.assertEqual(data['events'][0]['date'], '2030-06-17')
        self.assertIsNone(data['events'][0]['description'])

    def test_week_and_month_use_existing_builders(self):
        for kind, start, end in [('week', '2030-06-17', '2030-06-24'), ('month', '2030-06-01', '2030-07-01')]:
            with self.subTest(kind=kind):
                status, _, payload = self.request(query=[('period', kind), ('date', '2030-06-17')])
                self.assertEqual(status, 200)
                self.assertTrue(payload['data']['period']['start'].startswith(start))
                self.assertTrue(payload['data']['period']['end'].startswith(end))

    def test_invalid_input_never_opens_calendar(self):
        cases = [[('period', 'year')], [('date', '2030-02-30')], [('date', 'bad')],
                 [('period', 'day'), ('period', 'month')], [('unknown', PRIVATE)], [('time_zone', 'Wrong/Zone')]]
        for query in cases:
            with self.subTest(query=query):
                self.assertEqual(self.request(query=query)[0], 422)
        self.assertEqual(self.opened, 0)

    def test_client_cannot_choose_another_calendar_zone(self):
        status, _, _ = self.request(query=[('time_zone', 'UTC')])
        self.assertEqual(status, 422)
        self.assertFalse(any(isinstance(value, tuple) and value[0] == 'list' for value in self.gateway.calls))

    def test_provider_failure_never_becomes_empty_agenda(self):
        self.gateway.failure = True
        status, _, payload = self.request()
        self.assertEqual(status, 502)
        self.assertIsNone(payload['data'])

    def test_incomplete_collection_is_rejected(self):
        self.gateway.complete = False
        self.assertEqual(self.request()[0], 502)

    def test_duplicate_id_collection_is_rejected(self):
        self.gateway.events = (event(), event())
        self.assertEqual(self.request()[0], 502)

    def test_out_of_period_collection_is_rejected(self):
        self.gateway.events = (replace(event(), start='2030-07-17T10:00:00-03:00', end='2030-07-17T10:30:00-03:00'),)
        self.assertEqual(self.request()[0], 502)

    def test_all_day_event_does_not_invent_clock_times(self):
        self.gateway.events = (replace(event(), all_day=True, start='2030-06-17', end='2030-06-18'),)
        status, _, payload = self.request()
        self.assertEqual(status, 200)
        self.assertIsNone(payload['data']['events'][0]['start'])
        self.assertIsNone(payload['data']['events'][0]['end'])
        self.assertEqual(payload['data']['events'][0]['date'], '2030-06-17')

    def test_legitimate_untitled_event_has_clear_display_label(self):
        self.gateway.events = (replace(event(), title=''),)
        status, _, payload = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(payload['data']['events'][0]['title'], '(sem título)')

    def test_full_details_are_plain_text_without_private_version(self):
        status, _, payload = self.request('/events/OpaquePanelID')
        self.assertEqual(status, 200)
        data = payload['data']['event']
        self.assertEqual(data['description'], 'Descrição completa')
        self.assertEqual(data['location'], 'Sala virtual')
        self.assertTrue(data['details_loaded'])
        self.assertNotIn('secret()', str(payload))
        self.assertNotIn('calendar_id', str(payload))

    def test_changed_event_during_details_requires_refresh(self):
        self.gateway.details = replace(self.gateway.details, title='Outro título')
        status, _, payload = self.request('/events/OpaquePanelID')
        self.assertEqual(status, 409)
        self.assertEqual(payload['error']['code'], 'event_changed')

    def test_absent_event_is_404_without_snapshot(self):
        self.gateway.current = None
        self.assertEqual(self.request('/events/OpaquePanelID')[0], 404)
        self.assertFalse(any(isinstance(value, tuple) and value[0] == 'details' for value in self.gateway.calls))

    def test_session_dependency_runs_before_readers(self):
        async def denied():
            raise HTTPException(401, 'Autenticação necessária.')
        self.app.dependency_overrides[_panel_session] = denied
        # Use raw helper because framework HTTPException doesn't use our response headers.
        status, _, _ = asyncio.run(invoke_asgi(self.app, '/painel/api/events'))
        self.assertEqual(status, 401)
        self.assertEqual(self.opened, 0)
        self.assertEqual(self.store.calls, [])

    def test_reminders_are_filtered_paginated_and_private_fields_omitted(self):
        status, _, payload = self.request('/reminders', [('states', 'accepted'), ('limit', '1')])
        self.assertEqual(status, 200)
        data = payload['data']
        self.assertEqual(data['total'], 2)
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['counts_scope'], 'filtered')
        self.assertTrue(data['items'][0]['format_is_planned'])
        self.assertEqual(data['items'][0]['format'], 'card')
        self.assertNotIn('recipient_key', str(payload))
        self.assertNotIn('calendar_id', str(payload))
        self.assertNotIn('message_id_fingerprint', str(payload))
        self.assertIn('entrega e leitura não foram confirmadas', data['items'][0]['reason'])
        second = self.request('/reminders', [('states', 'accepted'), ('limit', '1'), ('cursor', data['next_cursor'])])
        self.assertEqual(second[0], 200)
        self.assertNotEqual(data['items'][0]['id'], second[2]['data']['items'][0]['id'])
        self.assertIsNone(second[2]['data']['next_cursor'])

    def test_reminder_cursor_cannot_cross_changed_collection(self):
        first = self.request('/reminders', [('limit', '1')])[2]['data']
        self.store.records = self.store.records[:2]
        status, _, payload = self.request('/reminders', [('limit', '1'), ('cursor', first['next_cursor'])])
        self.assertEqual(status, 409)
        self.assertEqual(payload['error']['code'], 'collection_changed')

    def test_reminder_cursor_cannot_cross_filters(self):
        first = self.request('/reminders', [('limit', '1')])[2]['data']
        status, _, _ = self.request('/reminders', [('states', 'accepted'), ('limit', '1'), ('cursor', first['next_cursor'])])
        self.assertEqual(status, 409)

    def test_invalid_reminder_queries_do_not_open_ledger(self):
        for query in [[('limit', '0')], [('limit', '51')], [('states', 'delivered')], [('states', 'pending,pending')], [('cursor', '!bad!')], [('limit', '1'), ('limit', '2')]]:
            with self.subTest(query=query):
                self.assertEqual(self.request('/reminders', query)[0], 422)
        self.assertEqual(self.store.calls, [])

    def test_reminder_history_only_returns_public_transitions(self):
        status, _, payload = self.request('/reminders/' + '0' * 63 + '1')
        self.assertEqual(status, 200)
        history = payload['data']['history']
        self.assertEqual(set(history[0]), {'from_state', 'to_state', 'reason', 'at'})
        self.assertEqual(history[0]['to_state'], 'pending')

    def test_reminder_absence_is_404(self):
        self.assertEqual(self.request('/reminders/' + 'f' * 64)[0], 404)

    def test_reminder_failure_is_not_empty_success(self):
        self.store.failure = True
        status, _, payload = self.request('/reminders')
        self.assertEqual(status, 503)
        self.assertIsNone(payload['data'])

    def test_missing_operational_ledger_is_not_created_by_panel_read(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'missing.sqlite3'
            self.app.state.reminder_store_factory = None
            self.app.state.settings.reminder_database_path = path
            self.assertEqual(self.request('/reminders')[0], 503)
            self.assertFalse(path.exists())

    def test_empty_operational_file_is_not_initialized_by_panel_read(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'empty.sqlite3'
            path.touch()
            self.app.state.reminder_store_factory = None
            self.app.state.settings.reminder_database_path = path
            self.assertEqual(self.request('/reminders')[0], 503)
            self.assertEqual(path.read_bytes(), b'')

    def test_real_sqlite_reads_use_readonly_mode_without_ddl_or_write_locks(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'existing.sqlite3'
            operational = ReminderStore(path, clock=lambda: NOW)
            config = operational.configure('private-calendar', 'a' * 64,
                offsets={'day_before': None, 'preparation': 60, 'imminent': 15},
                enabled=True, permission_granted=True, expected_revision=None)
            record = operational.plan('OpaquePanelID', NOW + timedelta(hours=2), 'preparation',
                title='Entrevista fictícia', calendar_timezone=ZONE, config_revision=config.revision)
            before = sha256(path.read_bytes()).hexdigest()
            sql, connects = [], []
            real_connect = sqlite3.connect

            def traced(*args, **kwargs):
                connects.append((args, kwargs))
                connection = real_connect(*args, **kwargs)
                connection.set_trace_callback(sql.append)
                return connection

            # A factory de ReminderStore real também deve ser convertida ao leitor.
            self.app.state.reminder_store_factory = lambda: operational
            with patch('app.api_panel.sqlite3.connect', side_effect=traced):
                listing = self.request('/reminders')
                details = self.request('/reminders/' + record.key)
            self.assertEqual(listing[0], 200)
            self.assertEqual(listing[2]['data']['total'], 1)
            self.assertEqual(details[0], 200)
            self.assertEqual(details[2]['data']['history'][0]['to_state'], 'pending')
            self.assertTrue(connects)
            self.assertTrue(all('mode=ro' in value[0][0] and value[1]['uri'] is True for value in connects))
            self.assertTrue(any(statement == 'BEGIN' for statement in sql))
            self.assertFalse(any(statement.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER', 'DROP', 'BEGIN IMMEDIATE')) for statement in sql))
            self.assertEqual(sha256(path.read_bytes()).hexdigest(), before)
            self.assertEqual(set(Path(directory).iterdir()), {path})

    def test_format_is_planned_and_uses_current_card_setting(self):
        self.app.state.settings.reminder_cards_enabled = False
        item = self.request('/reminders')[2]['data']['items'][0]
        self.assertEqual(item['format'], 'text')
        self.assertTrue(item['format_is_planned'])

    def test_reminder_cap_never_claims_complete_prefix(self):
        self.store.records = tuple(reminder(number) for number in range(10000))
        status, _, payload = self.request('/reminders')
        self.assertEqual(status, 503)
        self.assertEqual(payload['error']['code'], 'reminder_limit_exceeded')

    def test_connection_status_only_claims_api_and_session(self):
        status, _, payload = self.request('/connection/status')
        self.assertEqual(status, 200)
        self.assertEqual(payload['data']['api'], 'responding')
        self.assertTrue(all(value == 'not_checked' for value in payload['data']['integrations'].values()))
        self.assertEqual(self.opened, 0)
        self.assertEqual(self.store.calls, [])


if __name__ == '__main__':
    unittest.main()
