"""Prévia de card autenticada, somente de leitura, sem provedores reais."""

import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import sqlite3
from zoneinfo import ZoneInfo

from fastapi import APIRouter, FastAPI
from PIL import Image, ImageDraw

from app.api_panel import PanelCalendarBinding
from app.api_panel_cards import MAX_PREVIEW_BYTES, register_panel_card_routes
from app.interview_card import CardRenderError, render_interview_card
from app.panel_access import PanelAccessStore
from app.panel_security import COOKIE_NAME, PanelAccessError, panel_access_error_handler
from app.reminder_store import ReminderRecord, ReminderStore, _title
from app.services.agenda import AgendaEvent
from app.services.event_details import EventDetails
from app.services.event_selection import EventLookup
from app.services.update_snapshot import UpdateSnapshot
from test_panel_web import raw_request


ZONE = 'America/Recife'
NOW = datetime(2030, 6, 19, 12, tzinfo=timezone.utc)
KEY = 'a' * 64
PRIVATE = 'private-calendar-and-secret-error'


class Gateway:
    def __init__(self):
        self.bound = PanelCalendarBinding(PRIVATE, 'Agenda fictícia', ZONE)
        self.event = AgendaEvent('OpaqueEvent', 'Entrevista fictícia', NOW.astimezone(ZoneInfo(ZONE)).isoformat(),
            (NOW + timedelta(minutes=30)).astimezone(ZoneInfo(ZONE)).isoformat(), False, ZONE, 'confirmed')
        self.details = EventDetails(**self.event.__dict__)
        self.calls = []

    def binding(self):
        self.calls.append('binding')
        return self.bound

    def get_event(self, event_id, *, time_zone):
        self.calls.append(('get', event_id, time_zone))
        return EventLookup(self.bound.calendar_name, self.event)

    def get_update_snapshot(self, event_id, *, time_zone):
        self.calls.append(('details', event_id, time_zone))
        return UpdateSnapshot(self.bound.calendar_id, self.bound.calendar_name,
                              self.details, 'private-etag', 'opaque')


def reminder():
    return ReminderRecord(KEY, PRIVATE, 'OpaqueEvent', 'b' * 64, NOW,
        NOW - timedelta(hours=1), NOW - timedelta(minutes=58), 'preparation', 60,
        'Entrevista fictícia', ZONE, 1, 'accepted', 'accepted', None,
        'private-message-fingerprint', NOW, NOW)


class Store:
    def __init__(self):
        self.record = reminder()
        self.calls = []

    def get(self, key):
        self.calls.append(key)
        return self.record if self.record is not None and self.record.key == key else None


class PanelCardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.access = PanelAccessStore(self.directory / 'access.sqlite3', clock=lambda: 1000.)
        self.sid, pending = self.access.begin()
        self.assertTrue(self.access.authorize(pending.pairing_code))
        self.gateway = Gateway()
        self.store = Store()
        self.opened = 0

        @contextmanager
        def factory():
            self.opened += 1
            yield self.gateway

        self.app = FastAPI()
        self.app.state.settings = SimpleNamespace(panel_enabled=True, api_port=8000,
            reminder_cards_enabled=True, api_auth_token='private-service-key')
        self.app.state.panel_access_store_factory = lambda: self.access
        self.app.state.reminder_store_factory = lambda: self.store
        self.app.state.panel_calendar_gateway_factory = factory
        self.app.state.panel_clock = lambda: NOW + timedelta(days=2)
        self.app.add_exception_handler(PanelAccessError, panel_access_error_handler)
        router = APIRouter(prefix='/painel/api')
        register_panel_card_routes(router)
        self.app.include_router(router)

    def request(self, key=KEY, *, authenticated=True, headers=()):
        values = [(b'host', b'127.0.0.1:8000')]
        if authenticated:
            values.append((b'cookie', f'{COOKIE_NAME}={self.sid}'.encode()))
        values.extend(headers)
        result = asyncio.run(raw_request(self.app,
            '/painel/api/reminders/' + key + '/card-preview', headers=values))
        self.assertNotIn(PRIVATE.encode(), result[2])
        self.assertNotIn(b'private-service-key', result[2])
        if result[0] >= 400:
            self.assertIsNone(json.loads(result[2]).get('data'))
        return result

    def test_session_and_same_origin_are_checked_before_any_private_io(self):
        for headers in [(), ((b'authorization', b'Bearer private-service-key'),)]:
            self.assertEqual(self.request(authenticated=False, headers=headers)[0], 401)
        self.assertEqual(self.request(headers=((b'origin', b'https://other.example'),))[0], 403)
        self.assertEqual(self.request(headers=((b'sec-fetch-site', b'cross-site'),))[0], 403)
        self.assertEqual(self.store.calls, [])
        self.assertEqual(self.opened, 0)

    def test_get_needs_no_csrf_and_returns_private_verified_png(self):
        with patch('app.card_media_store.CardMediaPublisher.publish') as publish, \
             patch('app.whatsapp_sandbox_sender.SandboxSender.send_image') as send_image, \
             patch('app.whatsapp_sandbox_sender.SandboxSender.send') as send_text:
            status, headers, data = self.request(headers=((b'origin', b'http://127.0.0.1:8000'),))
        self.assertEqual(status, 200)
        self.assertEqual(headers['content-type'], 'image/png')
        self.assertEqual(headers['cache-control'], 'private, no-store')
        self.assertEqual(headers['vary'], 'Cookie')
        self.assertEqual(headers['x-content-type-options'], 'nosniff')
        self.assertLessEqual(len(data), MAX_PREVIEW_BYTES)
        with Image.open(BytesIO(data)) as image:
            self.assertEqual((image.size, image.mode, image.format), ((1254, 1254), 'RGB', 'PNG'))
        for operation in (publish, send_image, send_text):
            operation.assert_not_called()

    def test_invalid_id_does_not_open_ledger_or_calendar(self):
        for value in ('ABC', 'A' * 64, 'a' * 63):
            self.assertEqual(self.request(value)[0], 422)
        self.assertEqual((self.store.calls, self.opened), ([], 0))

    def test_missing_reminder_does_not_open_calendar(self):
        self.store.record = None
        self.assertEqual(self.request()[0], 404)
        self.assertEqual(self.opened, 0)

    def test_only_currently_planned_card_format_can_render(self):
        self.store.record = replace(reminder(), offset_minutes=15)
        self.assertEqual(self.request()[0], 422)
        self.store.record = reminder()
        self.app.state.settings.reminder_cards_enabled = False
        self.assertEqual(self.request()[0], 422)
        self.assertEqual(self.opened, 0)

    def test_missing_deleted_and_all_day_events_never_render(self):
        for event, status in ((None, 404), (replace(self.gateway.event, status='cancelled'), 404),
                              (replace(self.gateway.event, all_day=True), 409)):
            self.gateway.event = event
            with patch('app.api_panel_cards._render') as render:
                self.assertEqual(self.request()[0], status)
                render.assert_not_called()

    def test_changed_calendar_timezone_title_start_or_identity_blocks_preview(self):
        initial = self.gateway.details
        alternatives = [replace(initial, title='Outro título'), replace(initial, event_id='OtherEvent'),
            replace(initial, start=(NOW + timedelta(minutes=1)).astimezone(ZoneInfo(ZONE)).isoformat())]
        for current in alternatives:
            self.gateway.event = current
            self.gateway.details = current
            with patch('app.api_panel_cards._render') as render:
                self.assertEqual(self.request()[0], 409)
                render.assert_not_called()
        self.gateway.event, self.gateway.details = initial, initial
        for bound in (replace(self.gateway.bound, calendar_id='another-private-calendar'),
                      replace(self.gateway.bound, time_zone='America/Sao_Paulo')):
            self.gateway.bound = bound
            self.assertEqual(self.request()[0], 409)

    def test_event_changed_between_lookup_and_details_is_rejected(self):
        self.gateway.details = replace(self.gateway.details, end=(NOW + timedelta(hours=1)).astimezone(ZoneInfo(ZONE)).isoformat())
        self.assertEqual(self.request()[0], 409)

    def test_ledger_changed_during_calendar_read_is_rejected(self):
        with patch.object(self.store, 'get', side_effect=[reminder(), replace(reminder(), title='Alterado')]), \
             patch('app.api_panel_cards._render') as render:
            self.assertEqual(self.request()[0], 409)
            render.assert_not_called()

    def test_full_current_title_is_sent_to_renderer_not_truncated_projection(self):
        title = 'Entrevista técnica ' + 'informações ' * 24
        self.gateway.event = replace(self.gateway.event, title=title)
        self.gateway.details = replace(self.gateway.details, title=title)
        self.store.record = replace(reminder(), title=_title(title))
        with patch('app.api_panel_cards.render_interview_card', wraps=render_interview_card) as render:
            self.assertEqual(self.request()[0], 200)
        self.assertEqual(render.call_args.args[0], title)
        self.assertIs(render.call_args.kwargs['preview'], True)

    def test_temporary_files_are_removed_on_success_and_partial_render_failure(self):
        captured = []
        original = render_interview_card
        def render(*args, **kwargs):
            captured.append(args[4])
            return original(*args, **kwargs)
        with patch('app.api_panel_cards.render_interview_card', side_effect=render):
            self.assertEqual(self.request()[0], 200)
        def fail(*args, **kwargs):
            captured.append(args[4])
            args[4].write_bytes(b'partial')
            raise RuntimeError(PRIVATE)
        with patch('app.api_panel_cards.render_interview_card', side_effect=fail):
            result = self.request()
        self.assertEqual(result[0], 503)
        self.assertTrue(captured)
        self.assertTrue(all(not path.parent.exists() for path in captured))

    def test_oversized_or_non_png_output_is_sanitized_and_deleted(self):
        for value in (b'not PNG', b'x' * (MAX_PREVIEW_BYTES + 1)):
            captured = []
            def bad_render(*args, **_kwargs):
                captured.append(args[4])
                args[4].write_bytes(value)
            with patch('app.api_panel_cards.render_interview_card', side_effect=bad_render):
                self.assertEqual(self.request()[0], 503)
            self.assertFalse(captured[0].parent.exists())

    def test_google_failure_is_sanitized_without_render(self):
        with patch.object(self.gateway, 'binding', side_effect=RuntimeError(PRIVATE)), \
             patch('app.api_panel_cards._render') as render:
            self.assertEqual(self.request()[0], 502)
            render.assert_not_called()

    def test_actual_sqlite_ledger_is_readonly_and_byte_identical(self):
        path = self.directory / 'reminders.sqlite3'
        operational = ReminderStore(path, clock=lambda: NOW - timedelta(hours=2))
        config = operational.configure(PRIVATE, 'b' * 64, enabled=True,
            permission_granted=True, expected_revision=None)
        record = operational.plan('OpaqueEvent', NOW, 'preparation', title='Entrevista fictícia',
                                 calendar_timezone=ZONE, config_revision=config.revision)
        self.app.state.reminder_store_factory = lambda: operational
        before = sha256(path.read_bytes()).hexdigest()
        connections, statements = [], []
        original = sqlite3.connect
        def connect(*args, **kwargs):
            connection = original(*args, **kwargs)
            if 'mode=ro' in str(args[0]):
                connections.append((args, kwargs))
                connection.set_trace_callback(statements.append)
            return connection
        with patch('app.api_panel.sqlite3.connect', side_effect=connect):
            self.assertEqual(self.request(record.key)[0], 200)
        self.assertTrue(connections)
        self.assertTrue(all(value[1]['uri'] for value in connections))
        self.assertFalse(any(sql.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE',
            'CREATE', 'ALTER', 'DROP', 'BEGIN IMMEDIATE')) for sql in statements))
        self.assertEqual(sha256(path.read_bytes()).hexdigest(), before)
        self.assertFalse(path.with_name(path.name + '-wal').exists())

    def test_missing_ledger_is_not_created(self):
        path = self.directory / 'absent.sqlite3'
        self.app.state.reminder_store_factory = None
        self.app.state.settings.reminder_database_path = path
        self.assertEqual(self.request()[0], 503)
        self.assertFalse(path.exists())


class PreviewLabelsTests(unittest.TestCase):
    def test_preview_labels_are_neutral_for_past_today_and_future(self):
        with TemporaryDirectory() as directory:
            for now in (NOW - timedelta(days=2), NOW, NOW + timedelta(days=2)):
                captured = []
                original = ImageDraw.ImageDraw.text
                def text(draw, position, value, *args, **kwargs):
                    captured.append(value)
                    return original(draw, position, value, *args, **kwargs)
                with patch.object(ImageDraw.ImageDraw, 'text', new=text):
                    render_interview_card('Entrevista fictícia', NOW, NOW + timedelta(minutes=30),
                        ZONE, Path(directory) / 'preview.png', now=now, preview=True)
                self.assertIn('Prévia do compromisso', captured)
                self.assertIn('Prévia • não enviada', captured)
                self.assertNotIn('Chegou o seu dia!', captured)
                self.assertNotIn('Sua entrevista está chegando', captured)

    def test_preview_is_strict_bool_and_cannot_be_demonstration(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / 'preview.png'
            for arguments in ({'preview': 1}, {'preview': 'yes'}, {'preview': True, 'demonstration': True}):
                with self.assertRaises(CardRenderError):
                    render_interview_card('Fictícia', NOW, NOW + timedelta(minutes=30), ZONE,
                                          output, now=NOW, **arguments)
                self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
