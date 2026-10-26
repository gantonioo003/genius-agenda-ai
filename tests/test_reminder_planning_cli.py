"""Fronteira operacional sem credenciais/provedores, com SQLite e serviço reais."""

from contextlib import contextmanager, redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.plan_reminders import main, run_planning
from app.reminder_calendar import ReminderCalendarBinding
from app.reminder_store import ReminderStore
from app.services.reminders import ReminderCalendarEvent, ReminderCalendarSnapshot


class Gateway:
    def __init__(self):
        self.binding_calls = self.snapshot_calls = 0
        self.start = datetime.now(timezone.utc) + timedelta(hours=3)

    def binding(self):
        self.binding_calls += 1
        return ReminderCalendarBinding('fixture-calendar', 'America/Recife')

    def snapshot(self, start, end, expected_calendar_id=None):
        self.snapshot_calls += 1
        if expected_calendar_id != 'fixture-calendar':
            raise AssertionError('incorrect binding')
        return ReminderCalendarSnapshot('fixture-calendar', 'America/Recife', (
            ReminderCalendarEvent('fixture-event', 'Entrevista fictícia', self.start,
                self.start + timedelta(minutes=30), False, 'confirmed'),))


class ReminderPlanningCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'private.sqlite3'
        self.store = ReminderStore(self.path)
        self.gateway = Gateway()

    def invoke(self, *flags):
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(['--database', str(self.path), *flags])
        return status, json.loads(output.getvalue())

    def test_unconfigured_does_not_open_google_or_policy(self):
        with patch('app.plan_reminders.load_recipient_binding', side_effect=AssertionError('policy read')), \
             patch('app.google_calendar.calendar_client', side_effect=AssertionError('Google')):
            status, output = self.invoke()
        self.assertEqual(status, 0)
        self.assertEqual(output['status'], 'not_configured')
        self.assertEqual(output['messages_sent'], 0)

    def test_enable_or_permission_without_configure_rejected_before_database(self):
        for flag in ['--enable', '--allow-reminders']:
            with self.subTest(flag=flag), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    main(['--database', str(self.path), flag])
                self.assertEqual(result.exception.code, 2)
                self.assertFalse(self.path.exists())

    def test_configure_defaults_disabled_without_snapshot(self):
        result = run_planning(self.store, self.gateway, 'a' * 64, configure=True)
        self.assertEqual(result.status, 'disabled')
        self.assertFalse(self.store.config().enabled)
        self.assertFalse(self.store.config().permission_granted)
        self.assertEqual((self.gateway.binding_calls, self.gateway.snapshot_calls), (1, 0))

    def test_disabled_profile_does_not_read_private_policy(self):
        self.store.configure('fixture-calendar', 'a' * 64)
        with patch('app.plan_reminders.load_recipient_binding', side_effect=AssertionError('policy')):
            status, output = self.invoke()
        self.assertEqual((status, output['status']), (0, 'disabled'))

    def test_active_cli_plans_and_repeat_keeps_same_two_rows(self):
        @contextmanager
        def client():
            yield object()
        with patch('app.plan_reminders.load_recipient_binding', return_value='a' * 64), \
             patch('app.google_calendar.calendar_client', client), \
             patch('app.reminder_calendar.GoogleReminderCalendarGateway', return_value=self.gateway):
            status, first = self.invoke('--configure', '--enable', '--allow-reminders')
            self.assertEqual((status, first['status'], first['reminders_considered']), (0, 'planned', 2))
            keys = tuple(row.key for row in self.store.list_reminders())
            status, second = self.invoke()
        self.assertEqual((status, second['state_counts'], second['messages_sent']), (0, {'pending': 2}, 0))
        self.assertEqual(tuple(row.key for row in self.store.list_reminders()), keys)
        self.assertTrue(all(len(self.store.history(key)) == 1 for key in keys))
        self.assertNotIn('fixture-event', json.dumps(first))
        self.assertNotIn('fixture-calendar', json.dumps(first))

    def test_wrong_policy_binding_fails_before_google_and_keeps_profile(self):
        config = self.store.configure('fixture-calendar', 'a' * 64, enabled=True, permission_granted=True)
        with patch('app.plan_reminders.load_recipient_binding', return_value='b' * 64), \
             patch('app.google_calendar.calendar_client', side_effect=AssertionError('must not open')):
            status, output = self.invoke()
        self.assertEqual((status, output['status'], output['messages_sent']), (1, 'error', 0))
        self.assertEqual(self.store.config(), config)
        self.assertEqual(self.store.list_reminders(), ())
        self.assertNotIn(str(self.path), json.dumps(output))

    def test_reconfigure_preserves_custom_intervals(self):
        offsets = {'day_before': 1440, 'preparation': 30, 'imminent': 5}
        self.store.configure('fixture-calendar', 'a' * 64, offsets=offsets)
        result = run_planning(self.store, self.gateway, 'a' * 64,
            configure=True, enable=True, allow_reminders=True)
        self.assertEqual(result.reminders_considered, 3)
        self.assertEqual(dict(self.store.config().offsets), offsets)


if __name__ == '__main__':
    unittest.main()
