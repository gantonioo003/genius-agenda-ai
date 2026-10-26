"""Entrega offline: SQLite real e agenda/transporte estritamente fictícios."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock, patch

from app.reminder_store import ChannelObservation, ReminderStore
from app.services.reminder_delivery import (
    ReminderDeliveryService, ReminderDeliverySummary, ReminderRecipient, reminder_text,
)
from app.services.reminder_sync import ReminderEventLookup, ReminderLookupSnapshot
from app.services.reminder_tick import reminder_tick_lock
from app.services.reminders import ReminderCalendarEvent
from app.whatsapp_sandbox_sender import SandboxSendError


NOW = datetime(2030, 6, 17, 12, tzinfo=timezone.utc)
CALENDAR = "delivery-calendar@example.invalid"
BINDING = "a" * 64
PHONE = "5511999999999"
TITLE = "Entrevista privada fictícia"
ZONE = "America/Recife"
FINGERPRINT = "b" * 64


class ReminderDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "private" / "reminders.sqlite3"
        self.now = NOW
        self.store = ReminderStore(self.path, clock=lambda: self.now)
        self.gateway = Mock()
        self.gateway.lookup_snapshot.side_effect = lambda ids, **kwargs: self.remote(ids[0])
        self.gateway_opened = 0
        self.gateway_closed = 0
        @contextmanager
        def gateway_factory():
            self.gateway_opened += 1
            try:
                yield self.gateway
            finally:
                self.gateway_closed += 1
        self.gateway_factory = gateway_factory
        self.recipient_loader = Mock(return_value=ReminderRecipient(PHONE, BINDING))
        self.sender = Mock()
        self.sender.send.return_value = FINGERPRINT
        self.sender_factory = Mock(return_value=self.sender)

    def event(self, event_id="event123", **changes):
        values = dict(event_id=event_id, title=TITLE, start_utc=NOW + timedelta(hours=1),
                      end_utc=NOW + timedelta(hours=2), all_day=False, status="confirmed")
        values.update(changes)
        return ReminderCalendarEvent(**values)

    def remote(self, event_id="event123", *, outcome="found", zone=ZONE, **changes):
        event = self.event(event_id, **changes) if outcome == "found" else None
        return ReminderLookupSnapshot(CALENDAR, zone, (ReminderEventLookup(event_id, outcome, event),))

    def configure(self, **changes):
        old = self.store.config()
        values = dict(enabled=True, permission_granted=True,
                      expected_revision=None if old is None else old.revision)
        values.update(changes)
        return self.store.configure(CALENDAR, BINDING, **values)

    def seed(self, event_id="event123", *, future=False):
        config = self.store.config() or self.configure()
        event = self.event(event_id)
        return self.store.plan(event_id, event.start_utc + (timedelta(minutes=1) if future else timedelta()),
            "preparation", title=TITLE, calendar_timezone=ZONE, config_revision=config.revision)

    def inbound(self, *, age=timedelta(hours=1), binding=BINDING, provenance="sandbox_callback_capacity", timestamp=None):
        message = self.now - age
        return self.store.observe_inbound(sha256((str(age) + binding + provenance).encode()).hexdigest(),
            binding, str(int(message.timestamp())) if timestamp is None else timestamp,
            self.now, provenance=provenance)

    def service(self, **changes):
        values = dict(store=self.store, gateway_factory=self.gateway_factory,
                      recipient_loader=self.recipient_loader, sender_factory=self.sender_factory,
                      clock=lambda: self.now)
        values.update(changes)
        return ReminderDeliveryService(**values)

    def public(self, summary):
        data = summary.public_data()
        self.assertEqual(set(data), {"dispatch_status", "skip_reason", "reminders_considered",
            "expired_count", "cancelled_count", "blocked_count", "accepted_count", "uncertain_count",
            "failed_count", "transport_attempts"})
        self.assertEqual(data["transport_attempts"], data["accepted_count"] + data["uncertain_count"])
        for private in (CALENDAR, PHONE, BINDING, TITLE, str(self.path), "private provider error"):
            self.assertNotIn(private, str(data))
        return data

    def assert_no_transport(self):
        self.sender.send.assert_not_called()

    def test_profile_gates_do_not_open_any_private_factory(self):
        for config, reason in ((None, "not_configured"), ({"enabled": False}, "disabled"),
                               ({"permission_granted": False}, "permission_required")):
            if config is not None:
                self.configure(**config)
            data = self.public(self.service().dispatch())
            self.assertEqual((data["dispatch_status"], data["skip_reason"]), ("skipped", reason))
        self.recipient_loader.assert_not_called()
        self.sender_factory.assert_not_called()
        self.assertEqual(self.gateway_opened, 0)

    def test_busy_skips_before_store_or_private_factories(self):
        with reminder_tick_lock(self.path):
            store = Mock(path=self.path)
            data = self.public(self.service(store=store).dispatch())
        self.assertEqual(data["skip_reason"], "busy")
        store.config.assert_not_called()
        self.recipient_loader.assert_not_called()

    def test_no_due_work_avoids_policy_google_and_sender(self):
        self.configure()
        self.seed(future=True)
        data = self.public(self.service().dispatch())
        self.assertEqual((data["dispatch_status"], data["reminders_considered"]), ("completed", 0))
        self.recipient_loader.assert_not_called()
        self.assertEqual(self.gateway_opened, 0)
        self.sender_factory.assert_not_called()

    def test_delivery_reads_twice_and_sends_latest_title_timezone_then_persists_acceptance(self):
        record = self.seed()
        self.inbound()
        self.gateway.lookup_snapshot.side_effect = [self.remote(), self.remote(title="Nova\nentrevista\u202e", zone="UTC")]
        data = self.public(self.service().dispatch())
        self.assertEqual((data["dispatch_status"], data["accepted_count"], data["transport_attempts"]), ("completed", 1, 1))
        self.assertEqual(self.gateway.lookup_snapshot.call_count, 2)
        self.assertEqual(self.recipient_loader.call_count, 2)
        self.assertEqual((self.gateway_opened, self.gateway_closed), (1, 1))
        self.sender.send.assert_called_once_with(PHONE,
            "Sua entrevista começa em 1 hora.\nNova entrevista\n17/06/2030 às 13h\nHorário de UTC.")
        latest = self.store.get(record.key)
        self.assertEqual((latest.state, latest.message_id_fingerprint, latest.calendar_timezone), ("accepted", FINGERPRINT, "UTC"))

    def test_restart_or_repeated_dispatch_never_repeats_acceptance(self):
        self.seed()
        self.inbound()
        self.assertEqual(self.service().dispatch().accepted_count, 1)
        reopened = ReminderStore(self.path, clock=lambda: self.now)
        self.assertEqual(self.service(store=reopened).dispatch().transport_attempts, 0)
        self.assertEqual(self.service().dispatch().transport_attempts, 0)
        self.sender.send.assert_called_once()

    def test_unknown_closed_margin_invalid_and_synthetic_channel_never_open_google_or_transport(self):
        cases = [(None, "channel_window_unknown"), (timedelta(hours=24), "channel_window_closed"),
                 (timedelta(hours=24, seconds=-60), "channel_window_margin")]
        for index, (age, reason) in enumerate(cases):
            with self.subTest(reason=reason):
                record = self.seed("event" + str(index))
                if age is not None:
                    self.inbound(age=age)
                data = self.public(self.service().dispatch())
                self.assertGreaterEqual(data["blocked_count"], 1)
                self.assertEqual(self.store.get(record.key).reason, reason)
        self.assertEqual(self.gateway_opened, 0)
        self.assert_no_transport()

    def test_other_binding_synthetic_or_future_provider_timestamp_does_not_authorize(self):
        self.seed()
        self.inbound(binding="c" * 64)
        self.inbound(provenance="synthetic")
        self.inbound(timestamp=str(int((self.now + timedelta(seconds=1)).timestamp())))
        data = self.public(self.service().dispatch())
        self.assertEqual(data["blocked_count"], 1)
        self.assertEqual(self.gateway_opened, 0)
        self.assert_no_transport()

    def test_future_observation_is_rejected_even_when_injected_as_valid(self):
        self.seed()
        observation = ChannelObservation("d" * 64, BINDING, self.now + timedelta(seconds=1),
            self.now + timedelta(seconds=1), True, "valid", "sandbox_callback_capacity")
        with patch.object(self.store, "last_inbound", return_value=observation):
            self.assertEqual(self.service().dispatch().blocked_count, 1)
        self.assert_no_transport()

    def test_channel_can_reopen_blocked_identity_before_deadline(self):
        record = self.seed()
        self.assertEqual(self.service().dispatch().blocked_count, 1)
        self.inbound()
        self.assertEqual(self.service().dispatch().accepted_count, 1)
        self.assertEqual(self.store.get(record.key).state, "accepted")

    def test_window_just_before_margin_is_allowed(self):
        self.seed()
        self.inbound(age=timedelta(hours=24, seconds=-61))
        self.assertEqual(self.service().dispatch().accepted_count, 1)

    def test_overdue_is_expired_without_policy_or_google(self):
        record = self.seed()
        self.now += timedelta(seconds=120)
        data = self.public(self.service().dispatch())
        self.assertEqual((data["expired_count"], data["reminders_considered"]), (1, 1))
        self.assertEqual(self.store.get(record.key).state, "expired")
        self.recipient_loader.assert_not_called()
        self.assertEqual(self.gateway_opened, 0)

    def test_exact_absence_and_changed_start_cancel_before_reserve(self):
        record = self.seed()
        self.inbound()
        self.gateway.lookup_snapshot.side_effect = [self.remote(outcome="absent")]
        self.assertEqual(self.service().dispatch().cancelled_count, 1)
        self.assertEqual(self.store.get(record.key).state, "cancelled")
        self.assertEqual(self.gateway.lookup_snapshot.call_count, 1)
        self.assert_no_transport()

    def test_event_change_after_reserve_cancels_only_owned_claim(self):
        record = self.seed()
        self.inbound()
        self.gateway.lookup_snapshot.side_effect = [self.remote(), self.remote(start_utc=NOW + timedelta(hours=2),
                                                                              end_utc=NOW + timedelta(hours=3))]
        self.assertEqual(self.service().dispatch().cancelled_count, 1)
        latest = self.store.get(record.key)
        self.assertEqual((latest.state, latest.reason), ("cancelled", "event_rescheduled"))
        self.assert_no_transport()

    def test_tentative_all_day_series_and_started_are_not_sent(self):
        for changes in ({"status": "tentative"}, {"all_day": True}, {"expanded": False}):
            record = self.seed("event" + str(len(self.store.list_reminders())))
            self.inbound()
            self.gateway.lookup_snapshot.side_effect = lambda ids, **kwargs: self.remote(ids[0], **changes)
            self.assertEqual(self.service().dispatch().cancelled_count, 1)
            self.assertEqual(self.store.get(record.key).reason, "event_changed")
        self.assert_no_transport()

    def test_malformed_or_wrong_calendar_snapshot_is_not_empty_success(self):
        self.seed()
        self.inbound()
        self.gateway.lookup_snapshot.side_effect = None
        self.gateway.lookup_snapshot.return_value = ReminderLookupSnapshot("wrong", ZONE, ())
        result = self.service().dispatch()
        self.assertEqual((result.dispatch_status, result.failed_count, result.transport_attempts), ("failed", 1, 0))
        self.assert_no_transport()

    def test_provider_read_failure_after_reserve_releases_only_own_reservation(self):
        record = self.seed()
        self.inbound()
        self.gateway.lookup_snapshot.side_effect = [self.remote(), OSError("private provider error")]
        self.assertEqual(self.service().dispatch().failed_count, 1)
        self.assertEqual(self.store.get(record.key).state, "pending")
        self.assertEqual([history.to_state for history in self.store.history(record.key)], ["pending", "reserved", "pending"])
        self.assert_no_transport()

    def test_deadline_reached_during_either_google_read_prevents_transport(self):
        record = self.seed()
        self.inbound()
        calls = 0
        def lookup(ids, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.now += timedelta(seconds=120)
            return self.remote(ids[0])
        self.gateway.lookup_snapshot.side_effect = lookup
        self.assertEqual(self.service().dispatch().expired_count, 1)
        self.assertEqual(self.store.get(record.key).state, "expired")
        self.assert_no_transport()

    def test_deadline_reached_during_first_google_read_does_not_reserve_or_send(self):
        record = self.seed()
        self.inbound()
        def lookup(ids, **kwargs):
            self.now += timedelta(seconds=120)
            return self.remote(ids[0])
        self.gateway.lookup_snapshot.side_effect = lookup
        self.assertEqual(self.service().dispatch().expired_count, 1)
        self.assertEqual(self.store.get(record.key).state, "expired")
        self.assertNotIn("reserved", [history.to_state for history in self.store.history(record.key)])
        self.assert_no_transport()

    def test_window_enters_margin_during_second_google_read_is_blocked_atomically(self):
        record = self.seed()
        self.inbound(age=timedelta(hours=24, seconds=-61))
        calls = 0
        def lookup(ids, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.now += timedelta(seconds=1)
            return self.remote(ids[0])
        self.gateway.lookup_snapshot.side_effect = lookup
        self.assertEqual(self.service().dispatch().blocked_count, 1)
        self.assertEqual(self.store.get(record.key).reason, "channel_window_margin")
        self.assert_no_transport()

    def test_config_changed_during_second_read_revokes_reservation_before_sender(self):
        record = self.seed()
        self.inbound()
        calls = 0
        def lookup(ids, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.configure(enabled=False)
            return self.remote(ids[0])
        self.gateway.lookup_snapshot.side_effect = lookup
        self.assertEqual(self.service().dispatch().failed_count, 1)
        self.assertEqual(self.store.get(record.key).state, "cancelled")
        self.assert_no_transport()

    def test_private_policy_change_after_second_read_prevents_sending(self):
        record = self.seed()
        self.inbound()
        self.recipient_loader.side_effect = [ReminderRecipient(PHONE, BINDING), ReminderRecipient("5511888888888", BINDING)]
        self.assertEqual(self.service().dispatch().failed_count, 1)
        self.assertEqual(self.store.get(record.key).state, "pending")
        self.assert_no_transport()

    def test_recipient_binding_mismatch_or_invalid_phone_fails_without_google(self):
        self.seed()
        self.inbound()
        for recipient in (ReminderRecipient(PHONE, "c" * 64), ReminderRecipient("+5511999999999", BINDING),
                          ReminderRecipient(PHONE, "bad"), None):
            self.recipient_loader.return_value = recipient
            self.assertEqual(self.service().dispatch().failed_count, 1)
        self.assertEqual(self.gateway_opened, 0)
        self.assert_no_transport()

    def test_sender_creation_error_does_not_reserve_or_begin_transport(self):
        record = self.seed()
        self.inbound()
        self.sender_factory.side_effect = RuntimeError("private provider error")
        self.assertEqual(self.service().dispatch().failed_count, 1)
        self.assertEqual(self.store.get(record.key).state, "pending")
        self.assertEqual(len(self.store.history(record.key)), 1)

    def test_every_sender_failure_is_uncertain_even_if_sender_claims_no_acceptance(self):
        record = self.seed()
        self.inbound()
        self.sender.send.side_effect = SandboxSendError(uncertain=False)
        data = self.public(self.service().dispatch())
        self.assertEqual((data["dispatch_status"], data["uncertain_count"], data["transport_attempts"]), ("partial", 1, 1))
        self.assertEqual(self.store.get(record.key).state, "uncertain")
        self.assertEqual(self.service().dispatch().transport_attempts, 0)
        self.sender.send.assert_called_once()

    def test_bad_fingerprint_is_uncertain_and_not_accepted(self):
        record = self.seed()
        self.inbound()
        self.sender.send.return_value = "private provider error"
        self.assertEqual(self.service().dispatch().uncertain_count, 1)
        self.assertEqual(self.store.get(record.key).state, "uncertain")

    def test_ledger_failure_after_provider_acceptance_leaves_sending_protected(self):
        record = self.seed()
        self.inbound()
        with patch.object(self.store, "mark_accepted", side_effect=OSError("private provider error")), \
             patch.object(self.store, "mark_uncertain", side_effect=OSError("private provider error")):
            data = self.public(self.service().dispatch())
        self.assertEqual((data["uncertain_count"], data["accepted_count"], data["failed_count"]), (1, 0, 1))
        self.assertEqual(self.store.get(record.key).state, "sending")
        self.assertEqual(self.service().dispatch().transport_attempts, 0)
        self.sender.send.assert_called_once()

    def test_previous_reserved_claim_is_never_recovered_or_released(self):
        record = self.seed()
        claim = self.store.reserve(record.key, config_revision=1)
        self.inbound()
        self.now += timedelta(minutes=1)
        self.assertEqual(self.service().dispatch().reminders_considered, 0)
        latest = self.store.get(record.key)
        self.assertEqual((latest.state, latest.attempt_id), ("reserved", claim.attempt_id))
        self.assertEqual(self.gateway_opened, 0)
        self.assert_no_transport()

    def test_parallel_dispatches_share_os_lock_until_sender_really_finishes(self):
        self.seed()
        self.inbound()
        entered, release = Event(), Event()
        def send(*args):
            entered.set()
            if not release.wait(5):
                raise AssertionError("Test fixture did not release sender")
            return FINGERPRINT
        self.sender.send.side_effect = send
        with ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(self.service().dispatch)
            self.assertTrue(entered.wait(5))
            try:
                second = self.service().dispatch()
                self.assertEqual((second.dispatch_status, second.skip_reason), ("skipped", "busy"))
            finally:
                release.set()
            self.assertEqual(first.result(5).accepted_count, 1)
        self.sender.send.assert_called_once()

    def test_batch_failure_preserves_prior_acceptance_without_retry(self):
        self.seed("event123")
        self.seed("event456")
        self.inbound()
        self.sender.send.side_effect = [FINGERPRINT, OSError("private provider error")]
        data = self.public(self.service().dispatch())
        self.assertEqual((data["dispatch_status"], data["accepted_count"], data["uncertain_count"], data["transport_attempts"]),
                         ("partial", 1, 1, 2))
        self.assertEqual({record.state for record in self.store.list_reminders()}, {"accepted", "uncertain"})
        self.assertEqual(self.service().dispatch().transport_attempts, 0)

    def test_real_process_crash_releases_os_lock_but_never_replays_sending(self):
        record = self.seed()
        self.inbound()
        program = ("import sys; from datetime import datetime; from app.reminder_store import ReminderStore; "
                   "from app.services.reminder_tick import reminder_tick_lock; "
                   "clock=lambda: datetime.fromisoformat(sys.argv[3]); "
                   "store=ReminderStore(sys.argv[1],clock=clock); "
                   "lock=reminder_tick_lock(store.path); assert lock.__enter__(); "
                   "claim=store.reserve(sys.argv[2],config_revision=1); "
                   "assert store.mark_sending(claim).state=='sending'; "
                   "print('ready',flush=True);sys.stdin.read()")
        process = subprocess.Popen([sys.executable, "-B", "-c", program, str(self.path), record.key, NOW.isoformat()],
            cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            self.assertEqual(self.service().dispatch().skip_reason, "busy")
        finally:
            process.kill()
            process.communicate(timeout=5)
        result = self.service().dispatch()
        self.assertEqual((result.dispatch_status, result.reminders_considered, result.transport_attempts), ("completed", 0, 0))
        self.assertEqual(self.store.get(record.key).state, "sending")
        self.assert_no_transport()

    def test_concurrent_busy_call_on_same_instance_does_not_reset_clock_guard(self):
        record = self.seed()
        self.inbound()
        entered, release = Event(), Event()
        def lookup(ids, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("Test fixture did not release lookup")
            self.now -= timedelta(seconds=1)
            return self.remote(ids[0])
        self.gateway.lookup_snapshot.side_effect = lookup
        service = self.service()
        with ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(service.dispatch)
            self.assertTrue(entered.wait(5))
            try:
                self.assertEqual(service.dispatch().skip_reason, "busy")
            finally:
                release.set()
            self.assertEqual(first.result(5).failed_count, 1)
        self.assertEqual(self.store.get(record.key).state, "pending")
        self.assert_no_transport()

    def test_cleanup_failure_preserves_recorded_provider_acceptance(self):
        self.seed()
        self.inbound()
        @contextmanager
        def gateway():
            yield self.gateway
            raise OSError("private provider error")
        data = self.public(self.service(gateway_factory=gateway).dispatch())
        self.assertEqual((data["dispatch_status"], data["accepted_count"], data["transport_attempts"], data["failed_count"]),
                         ("partial", 1, 1, 1))

    def test_slow_sender_factory_does_not_bypass_deadline(self):
        record = self.seed()
        self.inbound()
        def factory():
            self.now += timedelta(seconds=120)
            return self.sender
        self.sender_factory.side_effect = factory
        self.assertEqual(self.service().dispatch().expired_count, 1)
        self.assertEqual(self.store.get(record.key).state, "expired")
        self.assert_no_transport()

    def test_clock_regression_during_remote_read_fails_closed(self):
        record = self.seed()
        self.inbound()
        def lookup(ids, **kwargs):
            self.now -= timedelta(seconds=1)
            return self.remote(ids[0])
        self.gateway.lookup_snapshot.side_effect = lookup
        self.assertEqual(self.service().dispatch().failed_count, 1)
        self.assertEqual(self.store.get(record.key).state, "pending")
        self.assert_no_transport()

    def test_title_normalization_body_budget_and_private_repr(self):
        title = "  Cafe\u0301\n\t\u202e\x00teste "
        text = reminder_text(title, NOW, ZONE)
        self.assertIn("Café teste", text)
        self.assertNotIn("\u202e", text)
        self.assertNotIn("\x00", text)
        long = reminder_text("a" * 161, NOW, ZONE)
        self.assertEqual(long.splitlines()[1], "a" * 159 + "…")
        self.assertIn("\nCompromisso\n", reminder_text("\n\x00\u202e", NOW, ZONE))
        self.assertLessEqual(len(long), 1000)
        self.assertNotIn(PHONE, repr(ReminderRecipient(PHONE, BINDING)))
        self.assertNotIn(BINDING, repr(ReminderRecipient(PHONE, BINDING)))

    def test_invalid_format_is_blocked_before_reservation(self):
        record = self.seed()
        self.inbound()
        self.gateway.lookup_snapshot.side_effect = [self.remote(title="bad\ud800title")]
        # O contrato do evento já recusa Unicode sem codificação válida.
        data = self.public(self.service().dispatch())
        self.assertEqual(data["transport_attempts"], 0)
        self.assertEqual(data["failed_count"], 1)
        self.assertEqual(self.store.get(record.key).state, "pending")
        self.assert_no_transport()

    def test_summary_rejects_private_status_and_inconsistent_transport_counts(self):
        for summary in (ReminderDeliverySummary("private provider error"),
                        ReminderDeliverySummary("completed", accepted_count=1),
                        ReminderDeliverySummary("completed", failed_count=1),
                        ReminderDeliverySummary("failed"),
                        ReminderDeliverySummary("skipped", "busy", reminders_considered=1)):
            with self.assertRaises(Exception) as raised:
                summary.public_data()
            self.assertNotIn("private provider error", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
