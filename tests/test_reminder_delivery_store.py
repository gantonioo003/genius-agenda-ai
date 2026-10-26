"""Guardas finais com SQLite real, relógio controlado e nenhum transporte."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from app.reminder_store import ReminderStore, ReminderStoreError


NOW = datetime(2030, 6, 17, 12, tzinfo=timezone.utc)
CALENDAR = "delivery-fixture@example.invalid"
RECIPIENT = "a" * 64
ZONE = "America/Recife"


class ReminderDeliveryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "private" / "reminders.sqlite3"
        self.now = NOW
        self.store = ReminderStore(self.path, clock=lambda: self.now)
        self.config = self.store.configure(CALENDAR, RECIPIENT, enabled=True, permission_granted=True)

    def plan(self, event_id="event123", **changes):
        values = dict(start_at=NOW + timedelta(hours=1), title="Título privado fictício",
                      calendar_timezone=ZONE, config_revision=self.config.revision)
        values.update(changes)
        start = values.pop("start_at")
        return self.store.plan(event_id, start, "preparation", **values)

    def inbound(self, *, age=timedelta(minutes=5), recipient=RECIPIENT, provenance="sandbox_callback_capacity", number=1):
        return self.store.observe_inbound(sha256(str(number).encode()).hexdigest(), recipient,
            str(int((self.now - age).timestamp())), self.now, provenance=provenance)

    def claim(self, record=None):
        return self.store.reserve((record or self.plan()).key, config_revision=self.config.revision)

    def begin(self, claim, **changes):
        values = dict(config_revision=self.config.revision, title="Último título",
                      calendar_timezone="America/Fortaleza", not_before=self.now)
        values.update(changes)
        return self.store.begin_delivery(claim, **values)

    def test_snapshot_includes_only_due_pending_and_blocked_and_expires_overdue(self):
        due = self.plan("due")
        blocked = self.store.mark_blocked(self.plan("blocked").key, "channel_window_unknown")
        future = self.plan("future", start_at=NOW + timedelta(hours=2))
        snapshot = self.store.delivery_snapshot()
        self.assertEqual({record.key for record in snapshot.records}, {due.key, blocked.key})
        self.assertEqual(snapshot.expired_count, 0)
        self.now += timedelta(seconds=120)
        snapshot = self.store.delivery_snapshot()
        self.assertEqual(snapshot.records, ())
        self.assertEqual(snapshot.expired_count, 2)
        self.assertEqual(self.store.get(future.key), future)

    def test_snapshot_refuses_overflow_without_expiring_a_partial_batch(self):
        records = tuple(self.plan("event" + str(number)) for number in range(3))
        self.now += timedelta(seconds=120)
        with self.assertRaises(ReminderStoreError):
            self.store.delivery_snapshot(limit=2)
        self.assertEqual(tuple(self.store.get(record.key) for record in records), records)
        self.assertEqual(self.store.delivery_snapshot(limit=3).expired_count, 3)

    def test_snapshot_rejects_noninteger_or_unbounded_limit(self):
        for limit in (True, 0, -1, 201, "2", 1.5, None):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.store.delivery_snapshot(limit)

    def test_snapshot_expiration_rolls_back_when_a_later_transition_fails(self):
        records = tuple(self.plan("event" + str(number)) for number in range(2))
        self.now += timedelta(seconds=120)
        move, count = self.store._move, [0]
        def fail_later(*args, **kwargs):
            count[0] += 1
            if count[0] == 2:
                raise OSError("private provider error")
            return move(*args, **kwargs)
        with patch.object(self.store, "_move", side_effect=fail_later), self.assertRaises(ReminderStoreError):
            self.store.delivery_snapshot()
        self.assertEqual(tuple(self.store.get(record.key) for record in records), records)

    def test_final_guard_updates_latest_metadata_and_marks_sending_atomically(self):
        self.inbound()
        claim = self.claim()
        result = self.begin(claim, title="  U\u0301ltimo\n título\u200b  ")
        self.assertEqual((result.state, result.title, result.calendar_timezone),
                         ("sending", "Último título", "America/Fortaleza"))
        self.assertEqual(result.attempt_id, claim.attempt_id)
        self.assertEqual(claim.reminder.title, "Título privado fictício")
        self.assertEqual([item.to_state for item in self.store.history(result.key)], ["pending", "reserved", "sending"])
        with self.assertRaises(ReminderStoreError):
            self.begin(claim)

    def test_channel_absent_wrong_binding_untrusted_and_future_fail_closed(self):
        for mode in ("missing", "other", "untrusted", "future"):
            with self.subTest(mode=mode):
                record = self.plan(mode)
                if mode == "other":
                    self.inbound(recipient="b" * 64)
                elif mode == "untrusted":
                    self.inbound(provenance="synthetic")
                elif mode == "future":
                    self.now += timedelta(seconds=1)
                    self.inbound()
                    self.now = NOW
                claim = self.claim(record)
                result = self.begin(claim)
                self.assertEqual((result.state, result.reason), ("blocked", "channel_window_unknown"))
                self.assertIsNone(result.attempt_id)
                self.assertEqual([item.to_state for item in self.store.history(record.key)][-2:], ["pending", "blocked"])
                self.now = NOW

    def test_channel_exact_margin_and_closed_boundaries_block_but_one_second_before_margin_allows(self):
        for index, (age, expected) in enumerate(((timedelta(hours=23, minutes=59), "channel_window_margin"),
                (timedelta(hours=24), "channel_window_closed"),
                (timedelta(hours=23, minutes=58, seconds=59), None))):
            with self.subTest(age=age):
                recipient = format(index + 1, "064x")
                other = ReminderStore(self.path.parent / (str(index) + ".sqlite3"), clock=lambda: self.now)
                config = other.configure(CALENDAR, recipient, enabled=True, permission_granted=True)
                record = other.plan("event123", NOW + timedelta(hours=1), "preparation", title="Title",
                    calendar_timezone=ZONE, config_revision=config.revision)
                other.observe_inbound("c" * 64, recipient, str(int((NOW - age).timestamp())), NOW)
                claim = other.reserve(record.key, config_revision=config.revision)
                result = other.begin_delivery(claim, config_revision=1, title="Title", calendar_timezone=ZONE)
                self.assertEqual(result.state, "sending" if expected is None else "blocked")
                if expected is not None:
                    self.assertEqual(result.reason, expected)

    def test_deadline_boundary_expires_reserved_claim_without_sending(self):
        self.inbound()
        claim = self.claim()
        self.now = claim.reminder.deadline_at
        result = self.begin(claim)
        self.assertEqual(result.state, "expired")
        self.assertIsNone(result.attempt_id)

    def test_clock_regression_after_google_or_reservation_refuses_transition(self):
        self.inbound()
        claim = self.claim()
        with self.assertRaises(ReminderStoreError):
            self.begin(claim, not_before=NOW + timedelta(microseconds=1))
        self.now -= timedelta(microseconds=1)
        with self.assertRaises(ReminderStoreError):
            self.begin(claim, not_before=None)
        self.assertEqual(self.store.get(claim.reminder.key), claim.reminder)

    def test_profile_change_and_changed_reserved_record_fail_cas(self):
        self.inbound()
        claim = self.claim()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("UPDATE reminders SET title=? WHERE key=?", ("Outro título", claim.reminder.key))
            connection.commit()
        with self.assertRaises(ReminderStoreError):
            self.begin(claim)
        self.store.configure(CALENDAR, RECIPIENT, enabled=False, permission_granted=True, expected_revision=1)
        with self.assertRaises(ReminderStoreError):
            self.begin(claim)
        self.assertEqual(self.store.get(claim.reminder.key).state, "cancelled")

    def test_candidate_cas_does_not_cancel_changed_or_reserved_records(self):
        original = self.plan()
        changed = self.store.mark_blocked(original.key, "channel_window_unknown")
        with self.assertRaises(ReminderStoreError):
            self.store.finish_delivery_candidate(original, "event_changed")
        result = self.store.finish_delivery_candidate(changed, "event_changed")
        self.assertEqual(result.state, "cancelled")
        record = self.plan("reserved")
        claim = self.claim(record)
        with self.assertRaises(ReminderStoreError):
            self.store.finish_delivery_candidate(record, "event_cancelled")
        self.assertEqual(self.store.get(record.key), claim.reminder)

    def test_reserved_cancel_requires_same_claim_and_never_touches_sending(self):
        claim = self.claim()
        wrong_id = str(uuid4())
        wrong = replace(claim, attempt_id=wrong_id, reminder=replace(claim.reminder, attempt_id=wrong_id))
        with self.assertRaises(ReminderStoreError):
            self.store.cancel_reserved(wrong, "event_changed")
        self.assertEqual(self.store.cancel_reserved(claim, "event_rescheduled").state, "cancelled")
        self.inbound()
        sending = self.claim(self.plan("sending"))
        self.begin(sending)
        with self.assertRaises(ReminderStoreError):
            self.store.cancel_reserved(sending, "event_cancelled")
        with self.assertRaises(ReminderStoreError):
            self.store.release_reserved(sending)

    def test_reopen_and_snapshot_preserve_reserved_and_potential_send_states(self):
        self.inbound()
        reserved = self.claim(self.plan("reserved"))
        sending = self.claim(self.plan("sending"))
        self.begin(sending)
        accepted = self.claim(self.plan("accepted"))
        self.begin(accepted)
        self.store.mark_accepted(accepted, "f" * 64)
        uncertain = self.claim(self.plan("uncertain"))
        self.begin(uncertain)
        self.store.mark_uncertain(uncertain)
        before = tuple(self.store.get(claim.reminder.key) for claim in (reserved, sending, accepted, uncertain))
        self.now += timedelta(days=1)
        reopened = ReminderStore(self.path, clock=lambda: self.now)
        self.assertEqual(reopened.delivery_snapshot().records, ())
        self.assertEqual(tuple(reopened.get(record.key) for record in before), before)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_competing_final_guards_can_mark_sending_only_once(self):
        self.inbound()
        claim = self.claim()
        barrier = Barrier(2)
        def compete():
            store = ReminderStore(self.path, clock=lambda: NOW)
            barrier.wait(timeout=5)
            try:
                return store.begin_delivery(claim, config_revision=1, title="Title", calendar_timezone=ZONE).state
            except ReminderStoreError:
                return "refused"
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: compete(), range(2)))
        self.assertEqual(sorted(results), ["refused", "sending"])

    def test_blocking_after_release_is_atomic_if_second_write_fails(self):
        claim = self.claim()
        move = self.store._move
        def fail_block(*args, **kwargs):
            if args[2] == "blocked":
                raise OSError("private provider error")
            return move(*args, **kwargs)
        with patch.object(self.store, "_move", side_effect=fail_block), self.assertRaises(ReminderStoreError):
            self.begin(claim)
        self.assertEqual(self.store.get(claim.reminder.key), claim.reminder)


if __name__ == "__main__":
    unittest.main()
