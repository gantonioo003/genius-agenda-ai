"""Pulso offline: SQLite real, exclusão do SO e provedores fictícios."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock

from app.reminder_store import ReminderPlan, ReminderStore
from app.services.reminder_sync import ReminderEventLookup, ReminderLookupSnapshot
from app.services.reminder_tick import ReminderTickService, reminder_tick_lock
from app.services.reminders import ReminderCalendarEvent, ReminderCalendarSnapshot


NOW = datetime(2030, 6, 17, 12, tzinfo=timezone.utc)
CALENDAR = "tick-calendar@example.invalid"
RECIPIENT = "a" * 64
TITLE = "Título privado fictício"
ZONE = "America/Recife"


class ReminderTickTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "private" / "reminders.sqlite3"
        self.store = ReminderStore(self.path, clock=lambda: NOW)
        self.gateway = Mock()
        self.gateway.lookup_snapshot.return_value = ReminderLookupSnapshot(CALENDAR, ZONE,
            (ReminderEventLookup("event123", "found", self.event()),))
        self.gateway.snapshot.return_value = ReminderCalendarSnapshot(CALENDAR, ZONE, (self.event(),))
        self.opened = 0
        self.closed = 0
        self.binding = Mock(return_value=RECIPIENT)

        @contextmanager
        def gateway_factory():
            self.opened += 1
            try:
                yield self.gateway
            finally:
                self.closed += 1

        self.factory = gateway_factory

    @staticmethod
    def event():
        return ReminderCalendarEvent("event123", TITLE, NOW + timedelta(hours=2),
            NOW + timedelta(hours=3), False, "confirmed")

    def configure(self, **changes):
        old = self.store.config()
        values = dict(calendar_id=CALENDAR, recipient_key=RECIPIENT,
            enabled=True, permission_granted=True, expected_revision=None if old is None else old.revision)
        values.update(changes)
        self.store.configure(**values)

    def seed(self):
        self.configure()
        event = self.event()
        return self.store.plan_batch(tuple(ReminderPlan(event.event_id, event.start_utc, slot,
            event.title, ZONE) for slot in ("preparation", "imminent")), config_revision=1)

    def service(self, **changes):
        values = dict(store=self.store, gateway_factory=self.factory,
                      recipient_binding_loader=self.binding, clock=lambda: NOW)
        values.update(changes)
        return ReminderTickService(**values)

    def public(self, result):
        data = result.public_data()
        rendered = str(data)
        for private in (CALENDAR, RECIPIENT, TITLE, str(self.path), "private provider error"):
            self.assertNotIn(private, rendered)
        self.assertEqual(data["messages_sent"], 0)
        self.assertEqual(data["external_mutations"], 0)
        return data

    def test_no_profile_disabled_and_permission_missing_skip_private_policy_and_google(self):
        for changes, reason in ((None, "not_configured"), ({"enabled": False}, "disabled"),
                                ({"permission_granted": False}, "permission_required")):
            with self.subTest(reason=reason):
                if changes is not None:
                    self.configure(**changes)
                result = self.service().tick()
                data = self.public(result)
                self.assertEqual(result.tick_status, "skipped")
                self.assertEqual(result.skip_reason, reason)
                self.assertEqual(data["local_mutation_state"], "not_applied")
                self.assertEqual(data["synchronization"]["status"], "not_run")
                self.assertEqual(data["generation"]["status"], "not_run")
        self.binding.assert_not_called()
        self.assertEqual(self.opened, 0)

    def test_recipient_mismatch_or_private_policy_error_fails_before_google(self):
        self.configure()
        for recipient in ("b" * 64, "not a binding", None):
            self.binding.return_value = recipient
            result = self.service().tick()
            self.assertEqual(result.tick_status, "failed")
            self.assertEqual(self.public(result)["synchronization"]["status"], "not_run")
        self.binding.side_effect = OSError("private provider error")
        self.assertEqual(self.service().tick().tick_status, "failed")
        self.assertEqual(self.opened, 0)
        self.assertFalse(self.store.list_reminders())

    def test_tick_syncs_before_generation_and_reuses_one_client(self):
        self.seed()
        order = []
        self.gateway.lookup_snapshot.side_effect = lambda *a, **k: (
            order.append("sync") or ReminderLookupSnapshot(CALENDAR, ZONE,
                (ReminderEventLookup("event123", "found", self.event()),)))
        self.gateway.snapshot.side_effect = lambda *a, **k: (
            order.append("generate") or ReminderCalendarSnapshot(CALENDAR, ZONE, (self.event(),)))
        result = self.service().tick()
        data = self.public(result)
        self.assertEqual(order, ["sync", "generate"])
        self.assertEqual((self.opened, self.closed), (1, 1))
        self.assertEqual(result.tick_status, "completed")
        self.assertEqual(data["synchronization"]["events_checked"], 1)
        self.assertEqual(data["generation"]["reminders_considered"], 2)
        self.assertEqual(data["local_mutation_state"], "applied")

    def test_repeated_ticks_preserve_business_identity_and_history(self):
        self.configure()
        first = self.service().tick()
        keys = tuple(record.key for record in self.store.list_reminders())
        histories = tuple(self.store.history(key) for key in keys)
        second = self.service().tick()
        self.assertEqual((first.tick_status, second.tick_status), ("completed", "completed"))
        self.assertEqual(tuple(record.key for record in self.store.list_reminders()), keys)
        self.assertEqual(tuple(self.store.history(key) for key in keys), histories)

    def test_sync_timeout_fails_without_generation_or_cancelling_on_absence(self):
        records = self.seed()
        self.gateway.lookup_snapshot.side_effect = TimeoutError("private provider error")
        result = self.service().tick()
        data = self.public(result)
        self.assertEqual(result.tick_status, "failed")
        self.assertEqual(data["synchronization"]["status"], "failed")
        self.assertEqual(data["generation"]["status"], "not_run")
        self.gateway.snapshot.assert_not_called()
        self.assertEqual(self.store.list_reminders(), records)

    def test_generation_failure_preserves_completed_sync_and_reports_partial_local_state(self):
        self.seed()
        self.gateway.lookup_snapshot.return_value = ReminderLookupSnapshot(CALENDAR, ZONE,
            (ReminderEventLookup("event123", "cancelled"),))
        self.gateway.snapshot.side_effect = RuntimeError("private provider error")
        data = self.public(self.service().tick())
        self.assertEqual(data["tick_status"], "partial")
        self.assertEqual(data["local_mutation_state"], "partial")
        self.assertEqual(data["synchronization"]["status"], "synchronized")
        self.assertEqual(data["synchronization"]["cancelled_count"], 2)
        self.assertEqual(data["generation"]["status"], "failed")
        self.assertTrue(all(record.state == "cancelled" for record in self.store.list_reminders()))

    def test_gateway_cleanup_failure_preserves_both_completed_phase_counts(self):
        self.configure()
        @contextmanager
        def broken_cleanup():
            yield self.gateway
            raise OSError("private provider error")
        data = self.public(self.service(gateway_factory=broken_cleanup).tick())
        self.assertEqual(data["tick_status"], "partial")
        self.assertEqual(data["synchronization"]["status"], "synchronized")
        self.assertEqual(data["generation"]["status"], "planned")
        self.assertEqual(data["generation"]["reminders_considered"], 2)
        self.assertEqual(len(self.store.list_reminders()), 2)

    def test_busy_lock_skips_even_store_read_and_policy(self):
        store = Mock(path=self.path)
        with reminder_tick_lock(self.path) as acquired:
            self.assertTrue(acquired)
            result = self.service(store=store).tick()
        self.assertEqual(result.tick_status, "skipped")
        self.assertEqual(result.skip_reason, "busy")
        store.config.assert_not_called()
        self.binding.assert_not_called()
        self.assertEqual(self.opened, 0)

    def test_slow_google_holds_lock_until_real_thread_work_finishes(self):
        self.configure()
        entered, release = Event(), Event()
        def snapshot(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("Teste não liberou a consulta fictícia.")
            return ReminderCalendarSnapshot(CALENDAR, ZONE, (self.event(),))
        self.gateway.snapshot.side_effect = snapshot
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(self.service().tick)
            self.assertTrue(entered.wait(5))
            try:
                busy = self.service().tick()
                self.assertEqual((busy.tick_status, busy.skip_reason), ("skipped", "busy"))
                self.assertFalse(self.store.list_reminders())
            finally:
                release.set()
            self.assertEqual(pending.result(timeout=5).tick_status, "completed")
        self.assertEqual(len(self.store.list_reminders()), 2)

    def test_real_process_lock_is_released_after_crash_without_ttl_or_schema_change(self):
        program = ("import sys; from app.services.reminder_tick import reminder_tick_lock; "
                   "lock=reminder_tick_lock(sys.argv[1]); "
                   "assert lock.__enter__(); print('locked', flush=True); sys.stdin.read()")
        process = subprocess.Popen([sys.executable, "-B", "-c", program, str(self.path)],
            cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "locked")
            result = self.service().tick()
            self.assertEqual((result.tick_status, result.skip_reason), ("skipped", "busy"))
            self.assertFalse(self.path.exists())
        finally:
            process.kill()
            process.communicate(timeout=5)
        self.assertTrue(Path(str(self.path.resolve()) + ".tick.lock").is_file())
        self.configure()
        self.assertEqual(self.service().tick().tick_status, "completed")
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertFalse(connection.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE '%tick%'").fetchall())

    def test_invalid_database_path_is_safe_failure_without_google(self):
        store = Mock(path="invalid\0path")
        result = self.service(store=store).tick()
        self.assertEqual(result.tick_status, "failed")
        self.public(result)
        store.config.assert_not_called()
        self.binding.assert_not_called()


if __name__ == "__main__":
    unittest.main()
