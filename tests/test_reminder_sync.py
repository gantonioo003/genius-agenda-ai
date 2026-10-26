"""Reconciliação offline: Google fictício, SQLite real e relógios controlados."""

from contextlib import closing
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from app.reminder_store import (ReminderPlan, ReminderStore, ReminderStoreError,
    ReminderSyncCancellation)
from app.services.reminders import ReminderCalendarEvent
from app.services.reminder_sync import (ReminderEventLookup, ReminderLookupSnapshot,
    ReminderSynchronizationError, ReminderSynchronizationService)


NOW = datetime(2030, 6, 17, 12, tzinfo=timezone.utc)
CALENDAR = "fixture-calendar@example.invalid"
RECIPIENT = "a" * 64
ZONE = "America/Recife"
TITLE = "Conteúdo privado fictício"
OFFSETS = {"day_before": None, "preparation": 60, "imminent": 15}


class ReminderSyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "ledger.sqlite3"
        self.now = NOW
        self.store = ReminderStore(self.path, clock=lambda: self.now)
        self.gateway = Mock()
        self.gateway.lookup_snapshot.return_value = self.remote(self.event())
        self.service = ReminderSynchronizationService(self.store, self.gateway,
            expected_recipient_key=RECIPIENT, clock=lambda: self.now)

    def configure(self, **changes):
        current = self.store.config()
        values = dict(calendar_id=CALENDAR, recipient_key=RECIPIENT,
            enabled=True, permission_granted=True, offsets=OFFSETS,
            expected_revision=None if current is None else current.revision)
        values.update(changes)
        return self.store.configure(**values)

    @staticmethod
    def event(event_id="abc123", **changes):
        values = dict(event_id=event_id, title=TITLE,
            start_utc=NOW + timedelta(hours=2), end_utc=NOW + timedelta(hours=3),
            all_day=False, status="confirmed")
        values.update(changes)
        return ReminderCalendarEvent(**values)

    @staticmethod
    def remote(*events, **changes):
        values = dict(calendar_id=CALENDAR, calendar_timezone=ZONE,
            lookups=tuple(ReminderEventLookup(event.event_id, "found", event) for event in events))
        values.update(changes)
        return ReminderLookupSnapshot(**values)

    def seed(self, event=None):
        if self.store.config() is None:
            self.configure()
        event = event or self.event()
        return self.store.plan_batch(tuple(ReminderPlan(event.event_id, event.start_utc,
            slot, event.title, ZONE) for slot, minutes in OFFSETS.items() if minutes is not None),
            config_revision=self.store.config().revision)

    def rows(self):
        with closing(sqlite3.connect(self.path)) as connection:
            return {table: connection.execute("SELECT * FROM " + table).fetchall()
                    for table in ("reminder_config", "reminders", "reminder_history", "channel_observations")}

    def assert_safe_failure(self, callback=None):
        with self.assertRaises(ReminderSynchronizationError) as raised:
            (callback or self.service.sync)()
        for private in (CALENDAR, RECIPIENT, TITLE, str(self.path), "provider secret", "SELECT "):
            self.assertNotIn(private, str(raised.exception))

    def test_not_configured_skips_google(self):
        self.assertEqual(self.service.sync().status, "not_configured")
        self.gateway.lookup_snapshot.assert_not_called()

    def test_disabled_and_permission_missing_skip_google(self):
        for changes, status in (({"enabled": False}, "disabled"),
                                ({"permission_granted": False}, "permission_required")):
            with self.subTest(changes=changes):
                self.configure(**changes)
                self.assertEqual(self.service.sync().status, status)
        self.gateway.lookup_snapshot.assert_not_called()

    def test_empty_active_scope_skips_google(self):
        self.configure()
        summary = self.service.sync()
        self.assertEqual((summary.status, summary.events_checked, summary.tracked_reminders,
                          summary.reminders_considered, summary.state_counts),
                         ("synchronized", 0, 0, 0, ()))
        self.gateway.lookup_snapshot.assert_not_called()

    def test_invalid_binding_rejected_before_empty_scope(self):
        self.configure(recipient_key="b" * 64)
        self.assert_safe_failure()
        self.gateway.lookup_snapshot.assert_not_called()

    def test_binding_shape_and_clock_are_strict(self):
        for binding in (None, True, "private", "A" * 64, "a" * 63):
            self.assert_safe_failure(lambda: ReminderSynchronizationService(self.store,
                self.gateway, expected_recipient_key=binding))
        self.assert_safe_failure(lambda: ReminderSynchronizationService(self.store,
            self.gateway, expected_recipient_key=RECIPIENT, clock=NOW))

    def test_unchanged_idempotent_and_restart_preserve_database(self):
        records = self.seed()
        before = self.rows()
        for _ in range(2):
            summary = self.service.sync()
            self.assertEqual((summary.events_checked, summary.tracked_reminders,
                summary.reminders_considered, summary.cancelled_count, summary.replanned_count,
                summary.unchanged_count, summary.state_counts),
                (1, 2, 2, 0, 0, 2, (("pending", 2),)))
            self.assertEqual(self.rows(), before)
        self.gateway.lookup_snapshot.assert_called_with(("abc123",), expected_calendar_id=CALENDAR)
        restarted = ReminderStore(self.path, clock=lambda: self.now)
        service = ReminderSynchronizationService(restarted, self.gateway,
            expected_recipient_key=RECIPIENT, clock=lambda: self.now)
        self.assertEqual(service.sync(), summary)
        self.assertEqual(tuple(restarted.get(record.key) for record in records), records)

    def test_reschedule_cancels_old_and_plans_all_slots_atomically(self):
        old = self.seed()
        event = self.event(start_utc=NOW + timedelta(hours=4), end_utc=NOW + timedelta(hours=5))
        self.gateway.lookup_snapshot.return_value = self.remote(event)
        summary = self.service.sync()
        self.assertEqual((summary.cancelled_count, summary.replanned_count, summary.reminders_considered,
                          summary.state_counts), (2, 2, 4, (("cancelled", 2), ("pending", 2))))
        for record in old:
            changed = self.store.get(record.key)
            self.assertEqual((changed.state, changed.reason), ("cancelled", "event_rescheduled"))
        active = self.store.list_reminders(states=("pending",))
        self.assertEqual({record.start_at for record in active}, {event.start_utc})

    def test_known_event_moved_beyond_eight_days_is_still_replanned(self):
        self.seed()
        event = self.event(start_utc=NOW + timedelta(days=80), end_utc=NOW + timedelta(days=80, hours=1))
        self.gateway.lookup_snapshot.return_value = self.remote(event)
        self.assertEqual(self.service.sync().replanned_count, 2)
        self.assertEqual({record.start_at for record in self.store.list_reminders(states=("pending",))},
                         {event.start_utc})
        self.gateway.snapshot.assert_not_called()

    def test_missing_or_minimal_cancelled_cancel_all_active(self):
        for outcome in ("absent", "cancelled"):
            with self.subTest(outcome=outcome):
                self.seed()
                self.gateway.lookup_snapshot.return_value = self.remote(
                    lookups=(ReminderEventLookup("abc123", outcome),))
                summary = self.service.sync()
                self.assertEqual((summary.cancelled_count, summary.replanned_count,
                                  summary.state_counts), (2, 0, (("cancelled", 2),)))
                self.assertEqual({record.reason for record in self.store.list_reminders()}, {"event_cancelled"})

    def test_absent_list_does_not_cancel_exact_found_event(self):
        self.seed()
        self.gateway.snapshot.return_value = self.remote(lookups=())
        self.assertEqual(self.service.sync().cancelled_count, 0)
        self.gateway.snapshot.assert_not_called()

    def test_ineligible_events_invalidate_without_plans(self):
        variants = ({"status": "tentative"}, {"all_day": True}, {"expanded": False},
                    {"start_utc": NOW}, {"start_utc": NOW - timedelta(minutes=1)})
        for changes in variants:
            with self.subTest(changes=changes):
                self.seed()
                self.gateway.lookup_snapshot.return_value = self.remote(self.event(**changes))
                summary = self.service.sync()
                self.assertEqual((summary.cancelled_count, summary.replanned_count), (2, 0))
                self.assertEqual({record.reason for record in self.store.list_reminders()}, {"event_changed"})

    def test_same_start_updates_title_timezone_without_identity_change(self):
        old = self.seed()
        self.gateway.lookup_snapshot.return_value = self.remote(self.event(title="Novo título"),
                                                                calendar_timezone="America/Fortaleza")
        summary = self.service.sync()
        self.assertEqual((summary.cancelled_count, summary.replanned_count), (0, 2))
        for record in old:
            changed = self.store.get(record.key)
            self.assertEqual((changed.title, changed.calendar_timezone), ("Novo título", "America/Fortaleza"))

    def claim_due(self, record):
        self.now = record.due_at
        claim = self.store.reserve(record.key, config_revision=record.config_revision)
        self.assertEqual(claim.status, "new")
        return claim

    def test_unchanged_reserved_preserves_attempt_and_history(self):
        old = self.seed()
        claim = self.claim_due(old[0])
        before = self.rows()
        summary = self.service.sync()
        self.assertEqual(summary.unchanged_count, 2)
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.store.get(claim.reminder.key).attempt_id, claim.attempt_id)

    def test_reserved_changed_title_invalidates_old_claim_before_transport(self):
        old = self.seed()
        claim = self.claim_due(old[0])
        self.gateway.lookup_snapshot.return_value = self.remote(self.event(title="Título atual"))
        summary = self.service.sync()
        self.assertEqual((summary.cancelled_count, summary.replanned_count), (1, 2))
        changed = self.store.get(claim.reminder.key)
        self.assertEqual((changed.state, changed.title, changed.attempt_id), ("pending", "Título atual", None))
        self.assertEqual([(item.to_state, item.reason) for item in self.store.history(changed.key)][-2:],
                         [("cancelled", "event_changed"), ("pending", "replanned")])
        with self.assertRaises(ReminderStoreError):
            self.store.mark_sending(claim)

    def test_reserved_timezone_change_invalidates_claim(self):
        old = self.seed()
        claim = self.claim_due(old[0])
        self.gateway.lookup_snapshot.return_value = self.remote(self.event(), calendar_timezone="UTC")
        self.assertEqual(self.service.sync().cancelled_count, 1)
        with self.assertRaises(ReminderStoreError):
            self.store.mark_sending(claim)

    def test_normalized_equivalent_title_does_not_churn_reserved(self):
        old = self.seed(self.event(title="Café reunião"))
        claim = self.claim_due(old[0])
        self.gateway.lookup_snapshot.return_value = self.remote(self.event(title=" Cafe\u0301\n reunião\t"))
        before = self.rows()
        self.assertEqual(self.service.sync().cancelled_count, 0)
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.store.get(claim.reminder.key).attempt_id, claim.attempt_id)

    def test_sending_accepted_and_uncertain_are_preserved(self):
        for target in ("sending", "accepted", "uncertain"):
            with self.subTest(target=target):
                event = self.event(target)
                records = self.seed(event)
                claim = self.claim_due(records[0])
                self.store.mark_sending(claim)
                if target == "accepted":
                    self.store.mark_accepted(claim, "f" * 64)
                elif target == "uncertain":
                    self.store.mark_uncertain(claim)
                protected, history = self.store.get(records[0].key), self.store.history(records[0].key)
                active_ids = tuple(sorted({r.event_id for r in self.store.sync_snapshot().records}))
                self.gateway.lookup_snapshot.return_value = self.remote(lookups=tuple(
                    ReminderEventLookup(event_id, "cancelled") for event_id in active_ids))
                self.service.sync()
                self.assertEqual(self.store.get(protected.key), protected)
                self.assertEqual(self.store.history(protected.key), history)

    def assert_return_to_protected_identity(self, target):
        old = self.seed()
        claim = self.claim_due(old[0])
        self.store.mark_sending(claim)
        if target == "accepted":
            self.store.mark_accepted(claim, "f" * 64)
        else:
            self.store.mark_uncertain(claim)
        protected = self.store.get(old[0].key)
        moved = self.event(start_utc=NOW + timedelta(hours=4), end_utc=NOW + timedelta(hours=5))
        self.gateway.lookup_snapshot.return_value = self.remote(moved)
        self.service.sync()
        self.gateway.lookup_snapshot.return_value = self.remote(self.event())
        summary = self.service.sync()
        self.assertEqual(self.store.get(protected.key), protected)
        self.assertEqual(dict(summary.state_counts)[target], 1)
        self.assertEqual(summary.unchanged_count, 1)
        self.assertEqual(self.store.reserve(protected.key, config_revision=1).status,
                         "accepted" if target == "accepted" else "blocked")

    def test_return_to_accepted_identity_does_not_reopen_it(self):
        self.assert_return_to_protected_identity("accepted")

    def test_return_to_uncertain_identity_does_not_reopen_it(self):
        self.assert_return_to_protected_identity("uncertain")

    def test_late_remote_failure_never_mutates(self):
        self.seed()
        self.seed(self.event("second"))
        before = self.rows()
        self.gateway.lookup_snapshot.side_effect = RuntimeError("provider secret")
        self.assert_safe_failure()
        self.assertEqual(self.rows(), before)

    def test_invalid_snapshot_or_partial_set_never_mutates(self):
        self.seed()
        before = self.rows()
        event = self.event()
        valid = ReminderEventLookup(event.event_id, "found", event)
        snapshots = [object(), self.remote(event, calendar_id="wrong"),
            self.remote(event, calendar_timezone=""), self.remote(event, calendar_timezone="Windows Standard Time"),
            self.remote(lookups=[]), self.remote(lookups=()), self.remote(lookups=(valid, valid)),
            self.remote(self.event("other")), self.remote(lookups=(object(),)),
            self.remote(lookups=(ReminderEventLookup("abc123", "unknown"),)),
            self.remote(lookups=(ReminderEventLookup("abc123", "found"),)),
            self.remote(lookups=(ReminderEventLookup("abc123", "absent", event),)),
            self.remote(lookups=(ReminderEventLookup("abc123", "cancelled", event),)),
            self.remote(lookups=(ReminderEventLookup("abc123", "found", self.event("other")),)),
            self.remote(self.event(status="cancelled")), self.remote(self.event(status="unknown")),
            self.remote(self.event(start_utc=NOW.replace(tzinfo=None))),
            self.remote(self.event(end_utc=NOW)), self.remote(self.event(title=None)),
            self.remote(self.event(all_day=1)), self.remote(self.event(expanded=1)),
            self.remote(self.event(recurring_event_id="abc123"))]
        for remote in snapshots:
            with self.subTest(remote=repr(remote)):
                self.gateway.lookup_snapshot.return_value = remote
                self.assert_safe_failure()
                self.assertEqual(self.rows(), before)

    def test_equivalent_start_offset_preserves_identity(self):
        old = self.seed()
        event = self.event()
        offset = timezone(timedelta(hours=-3))
        self.gateway.lookup_snapshot.return_value = self.remote(replace(event,
            start_utc=event.start_utc.astimezone(offset), end_utc=event.end_utc.astimezone(offset)))
        self.assertEqual(self.service.sync().unchanged_count, 2)
        self.assertEqual({record.key for record in self.store.list_reminders()}, {record.key for record in old})

    def test_updated_clock_excludes_event_started_during_query(self):
        old = self.seed()
        def lookup(*args, **kwargs):
            self.now = old[0].start_at
            return self.remote(self.event())
        self.gateway.lookup_snapshot.side_effect = lookup
        summary = self.service.sync()
        self.assertEqual((summary.cancelled_count, summary.replanned_count), (2, 0))
        self.assertEqual({r.state for r in self.store.list_reminders()}, {"cancelled"})

    def test_clock_rewind_or_invalid_time_fails_without_mutation(self):
        self.seed()
        before = self.rows()
        for value in (NOW - timedelta(seconds=1), NOW.replace(tzinfo=None), "invalid"):
            with self.subTest(value=value):
                self.gateway.lookup_snapshot.side_effect = lambda *args, **kwargs: self.remote(self.event())
                times = iter((NOW, value))
                service = ReminderSynchronizationService(self.store, self.gateway,
                    expected_recipient_key=RECIPIENT, clock=lambda: next(times))
                self.assert_safe_failure(service.sync)
                self.assertEqual(self.rows(), before)

    def test_initial_clock_older_than_ledger_fails_before_google(self):
        self.seed()
        self.now -= timedelta(seconds=1)
        self.assert_safe_failure()
        self.gateway.lookup_snapshot.assert_not_called()

    def test_store_clock_rewind_aborts_before_writes(self):
        self.seed()
        before = self.rows()
        self.store._clock = lambda: NOW - timedelta(seconds=1)
        self.gateway.lookup_snapshot.return_value = self.remote(lookups=(ReminderEventLookup("abc123", "absent"),))
        self.assert_safe_failure()
        self.assertEqual(self.rows(), before)

    def test_store_clock_prevents_pending_for_event_started_after_remote_read(self):
        self.seed()
        self.store._clock = lambda: NOW + timedelta(hours=3)
        summary = self.service.sync()
        self.assertEqual(summary.state_counts, (("expired", 2),))
        self.assertFalse(self.store.list_reminders(states=("pending",)))

    def mutate_during_lookup(self, callback):
        def lookup(*args, **kwargs):
            callback()
            self.after_concurrent = self.rows()
            return self.remote(self.event(start_utc=NOW + timedelta(hours=4),
                                          end_utc=NOW + timedelta(hours=5)))
        self.gateway.lookup_snapshot.side_effect = lookup
        self.assert_safe_failure()
        self.assertEqual(self.rows(), self.after_concurrent)

    def test_cas_detects_configuration_change(self):
        self.seed()
        self.mutate_during_lookup(lambda: self.configure(enabled=False))

    def test_cas_detects_binding_change(self):
        self.seed()
        self.mutate_during_lookup(lambda: self.configure(recipient_key="b" * 64))

    def test_cas_detects_reserved_to_sending_race(self):
        old = self.seed()
        claim = self.claim_due(old[0])
        self.mutate_during_lookup(lambda: self.store.mark_sending(claim))
        self.assertEqual(self.store.get(old[0].key).state, "sending")

    def test_cas_detects_inserted_phantom(self):
        self.seed()
        self.mutate_during_lookup(lambda: self.store.plan("phantom", NOW + timedelta(hours=5),
            "imminent", title="Other", calendar_timezone=ZONE, config_revision=1))

    def test_cas_watermark_detects_aba_at_identical_instant(self):
        old = self.seed()
        self.now = old[0].due_at
        first = self.store.reserve(old[0].key, config_revision=1)
        self.store.release_reserved(first)
        before = self.store.sync_snapshot()
        def aba():
            claim = self.store.reserve(old[0].key, config_revision=1)
            self.store.release_reserved(claim)
            after = self.store.sync_snapshot()
            self.assertEqual(after.records, before.records)
            self.assertGreater(after.history_sequence, before.history_sequence)
        self.mutate_during_lookup(aba)

    def test_cas_watermark_detects_activity_outside_active_scope(self):
        old = self.seed()
        self.now = old[0].due_at
        claim = self.store.reserve(old[0].key, config_revision=1)
        self.store.mark_sending(claim)
        self.mutate_during_lookup(lambda: self.store.mark_uncertain(claim))

    def test_failure_after_cancellation_rolls_back_whole_batch(self):
        self.seed()
        before = self.rows()
        self.gateway.lookup_snapshot.return_value = self.remote(self.event(
            start_utc=NOW + timedelta(hours=4), end_utc=NOW + timedelta(hours=5)))
        with patch.object(self.store, "_plan", side_effect=RuntimeError("late fixture failure")):
            self.assert_safe_failure()
        self.assertEqual(self.rows(), before)

    def test_failure_after_first_plan_rolls_back_cancellations_and_new_rows(self):
        self.seed()
        before = self.rows()
        self.gateway.lookup_snapshot.return_value = self.remote(self.event(
            start_utc=NOW + timedelta(hours=4), end_utc=NOW + timedelta(hours=5)))
        original = self.store._plan
        calls = []
        def fail_second(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("late fixture failure")
            return original(*args, **kwargs)
        with patch.object(self.store, "_plan", side_effect=fail_second):
            self.assert_safe_failure()
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.rows(), before)

    def test_atomic_store_rejects_foreign_event_or_key(self):
        old = self.seed()
        snapshot = self.store.sync_snapshot()
        before = self.rows()
        foreign = ReminderPlan("other", NOW + timedelta(hours=4), "imminent", "Other", ZONE)
        cases = [((foreign,), ()),
            ((), (ReminderSyncCancellation("f" * 64, "event_cancelled"),)),
            ((), (ReminderSyncCancellation(old[0].key, "configuration_changed"),)),
            ((), (ReminderSyncCancellation(old[0].key, "event_cancelled"),) * 2)]
        for plans, cancellations in cases:
            with self.subTest(plans=repr(plans), cancellations=repr(cancellations)):
                with self.assertRaises(ValueError):
                    self.store.apply_sync(snapshot, plans, cancellations)
                self.assertEqual(self.rows(), before)

    def test_store_validates_entire_plan_batch_before_cancel(self):
        old = self.seed()
        snapshot = self.store.sync_snapshot()
        before = self.rows()
        bad = ReminderPlan("abc123", NOW + timedelta(hours=4), "imminent", "New", "Invalid Zone")
        with self.assertRaises(ValueError):
            self.store.apply_sync(snapshot, (bad,),
                (ReminderSyncCancellation(old[0].key, "event_rescheduled"),))
        self.assertEqual(self.rows(), before)

    def test_snapshot_dtos_are_frozen_and_private(self):
        self.seed()
        values = (self.store.sync_snapshot(), self.remote(self.event()),
            ReminderEventLookup("abc123", "cancelled"), ReminderSyncCancellation("f" * 64, "event_cancelled"))
        for value in values:
            self.assertNotIn("abc123", repr(value))
            self.assertNotIn(CALENDAR, repr(value))
        with self.assertRaises(FrozenInstanceError):
            values[0].records = ()
        summary = self.service.sync()
        with self.assertRaises(FrozenInstanceError):
            summary.status = "changed"

    def test_snapshot_reads_complete_scope_beyond_list_default(self):
        self.configure()
        plans = tuple(ReminderPlan("event" + str(index), NOW + timedelta(hours=2),
            "imminent", TITLE, ZONE) for index in range(150))
        self.store.plan_batch(plans, config_revision=1)
        self.gateway.lookup_snapshot.return_value = self.remote(*(
            self.event("event" + str(index)) for index in range(150)))
        summary = self.service.sync()
        self.assertEqual((summary.events_checked, summary.tracked_reminders), (150, 150))
        self.assertEqual(len(self.gateway.lookup_snapshot.call_args.args[0]), 150)

    def test_snapshot_rejects_over_200_unique_events_without_google_or_mutation(self):
        self.configure()
        plans = tuple(ReminderPlan("event" + str(index), NOW + timedelta(hours=2),
            "imminent", TITLE, ZONE) for index in range(201))
        self.store.plan_batch(plans, config_revision=1)
        before = self.rows()
        self.assert_safe_failure()
        self.assertEqual(self.rows(), before)
        self.gateway.lookup_snapshot.assert_not_called()

    def test_snapshot_row_limit_uses_sentinel_and_rejects_overflow(self):
        # Exercita a seleção real com 6001 linhas, evitando 6001 validações
        # históricas no ensaio do limite (já cobertas nas demais operações).
        self.configure()
        plans = tuple(ReminderPlan("same", NOW + timedelta(hours=2, minutes=index),
            "imminent", TITLE, ZONE) for index in range(6000))
        self.store.plan_batch(plans, config_revision=1)
        self.store.plan("same", NOW + timedelta(days=10), "imminent", title=TITLE,
                        calendar_timezone=ZONE, config_revision=1)
        before = self.rows()
        with patch.object(self.store, "_check"):
            self.assert_safe_failure()
        self.assertEqual(self.rows(), before)
        self.gateway.lookup_snapshot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
