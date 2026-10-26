"""Planejamento sem provedor/transporte, com SQLite real e relógios controlados."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from app.reminder_store import ReminderPlan, ReminderStore, ReminderStoreError
from app.services.reminders import (
    MAX_EVENTS, PLANNING_WINDOW, ReminderCalendarEvent, ReminderCalendarSnapshot,
    ReminderGenerationError, ReminderPlanningService,
)


NOW = datetime(2030, 6, 17, 12, tzinfo=timezone.utc)
CALENDAR = "fixture-calendar@example.invalid"
RECIPIENT = "a" * 64
ZONE = "America/Recife"
TITLE = "Conteúdo privado fictício da entrevista"
OFFSETS = {"day_before": None, "preparation": 60, "imminent": 15}


class ReminderGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "ledger" / "reminders.sqlite3"
        self.now = NOW
        self.store = ReminderStore(self.path, clock=lambda: self.now)
        self.gateway = Mock()
        self.gateway.snapshot.return_value = self.snapshot(self.event())
        self.service = ReminderPlanningService(self.store, self.gateway,
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
    def snapshot(*events, **changes):
        values = dict(calendar_id=CALENDAR, calendar_timezone=ZONE, events=tuple(events))
        values.update(changes)
        return ReminderCalendarSnapshot(**values)

    def rows(self):
        with closing(sqlite3.connect(self.path)) as connection:
            return {table: connection.execute("SELECT * FROM " + table).fetchall()
                    for table in ("reminders", "reminder_history", "channel_observations")}

    def fail_safely(self, callback):
        with self.assertRaises(ReminderGenerationError) as raised:
            callback()
        for secret in (CALENDAR, RECIPIENT, TITLE, str(self.path), "private provider failure",
                       "SELECT ", "INSERT "):
            self.assertNotIn(secret, str(raised.exception))

    def test_no_configuration_does_not_query_google(self):
        summary = self.service.generate()
        self.assertEqual(summary.status, "not_configured")
        self.assertEqual(summary.events_seen, 0)
        self.gateway.snapshot.assert_not_called()

    def test_disabled_or_permission_missing_does_not_query_google(self):
        for changes, expected in (({"enabled": False}, "disabled"),
                                  ({"permission_granted": False}, "permission_required")):
            with self.subTest(changes=changes):
                self.configure(**changes)
                self.assertEqual(self.service.generate().status, expected)
        self.gateway.snapshot.assert_not_called()

    def test_expected_recipient_is_validated_without_consulting_google(self):
        for value in (None, True, "private destination", "A" * 64, "a" * 63):
            with self.subTest(value=value):
                self.fail_safely(lambda: ReminderPlanningService(self.store, self.gateway,
                    expected_recipient_key=value))
        self.configure(recipient_key="b" * 64)
        before = self.rows()
        self.fail_safely(self.service.generate)
        self.assertEqual(self.rows(), before)
        self.gateway.snapshot.assert_not_called()

    def test_two_default_slots_and_exact_eight_day_query(self):
        self.configure()
        summary = self.service.generate()
        self.gateway.snapshot.assert_called_once_with(NOW, NOW + PLANNING_WINDOW,
                                                      expected_calendar_id=CALENDAR)
        self.assertEqual((summary.status, summary.events_seen, summary.eligible_events,
            summary.skipped_count, summary.reminders_considered, summary.state_counts),
            ("planned", 1, 1, 0, 2, (("pending", 2),)))
        records = self.store.list_reminders()
        self.assertEqual({record.slot for record in records}, {"preparation", "imminent"})
        for record in records:
            self.assertEqual(record.due_at, record.start_at - timedelta(minutes=record.offset_minutes))
            self.assertEqual(record.deadline_at, record.due_at + timedelta(seconds=120))
            self.assertEqual(record.calendar_timezone, ZONE)
            self.assertEqual(record.state, "pending")
        with self.assertRaises(FrozenInstanceError):
            summary.status = "other"

    def test_optional_third_slot_and_seven_day_maximum(self):
        self.configure(offsets={"day_before": 10080, "preparation": 30, "imminent": 5})
        self.gateway.snapshot.return_value = self.snapshot(self.event(
            start_utc=NOW + timedelta(days=7, hours=1), end_utc=NOW + timedelta(days=7, hours=2)))
        summary = self.service.generate()
        self.assertEqual(summary.reminders_considered, 3)
        self.assertEqual({record.offset_minutes for record in self.store.list_reminders()}, {10080, 30, 5})

    def test_all_disabled_slots_produce_no_reminders(self):
        self.configure(offsets={slot: None for slot in OFFSETS})
        summary = self.service.generate()
        self.assertEqual(summary.eligible_events, 1)
        self.assertEqual(summary.reminders_considered, 0)
        self.assertEqual(summary.state_counts, ())

    def test_eligibility_and_expanded_occurrence_identity(self):
        self.configure()
        events = [
            self.event("all-day", all_day=True),
            self.event("tentative", status="tentative"),
            self.event("cancelled", status="cancelled"),
            self.event("started", start_utc=NOW),
            self.event("past", start_utc=NOW - timedelta(minutes=1)),
            self.event("outside", start_utc=NOW + timedelta(days=8), end_utc=NOW + timedelta(days=8, hours=1)),
            self.event("series", expanded=False),
            self.event("series-self", recurring_event_id="series-self", expanded=False),
            self.event("occurrence_20300617", recurring_event_id="series"),
        ]
        self.gateway.snapshot.return_value = self.snapshot(*events)
        summary = self.service.generate()
        self.assertEqual((summary.events_seen, summary.eligible_events, summary.skipped_count), (9, 1, 8))
        self.assertEqual({record.event_id for record in self.store.list_reminders()}, {"occurrence_20300617"})

    def test_same_id_identical_event_is_deduplicated(self):
        self.configure()
        event = self.event()
        self.gateway.snapshot.return_value = self.snapshot(event, replace(event))
        summary = self.service.generate()
        self.assertEqual((summary.events_seen, summary.eligible_events, summary.skipped_count,
                          summary.reminders_considered), (2, 1, 1, 2))

    def test_duplicate_id_conflict_rejects_entire_snapshot(self):
        self.configure()
        before = self.rows()
        for change in ({"title": "changed"}, {"start_utc": NOW + timedelta(hours=1)},
                       {"status": "tentative"}, {"expanded": False}, {"all_day": True}):
            with self.subTest(change=change):
                self.gateway.snapshot.return_value = self.snapshot(self.event(), replace(self.event(), **change))
                self.fail_safely(self.service.generate)
                self.assertEqual(self.rows(), before)

    def test_equivalent_aware_instants_are_normalized_to_utc(self):
        self.configure()
        event = self.event()
        local = replace(event, start_utc=event.start_utc.astimezone(timezone(timedelta(hours=-3))),
                        end_utc=event.end_utc.astimezone(timezone(timedelta(hours=-3))))
        self.gateway.snapshot.return_value = self.snapshot(event, local)
        self.assertEqual(self.service.generate().reminders_considered, 2)
        for record in self.store.list_reminders():
            self.assertEqual(record.start_at.tzinfo, timezone.utc)

    def test_invalid_late_event_never_leaves_partial_plans(self):
        self.configure()
        before = self.rows()
        for change in ({"status": "unknown"}, {"start_utc": NOW.replace(tzinfo=None)},
                       {"end_utc": NOW + timedelta(hours=1)}, {"all_day": 1},
                       {"expanded": "yes"}, {"event_id": ""}, {"title": None},
                       {"recurring_event_id": "bad", "event_id": "bad"}):
            with self.subTest(change=change):
                self.gateway.snapshot.return_value = self.snapshot(self.event(), replace(self.event("bad"), **change))
                self.fail_safely(self.service.generate)
                self.assertEqual(self.rows(), before)

    def test_invalid_snapshot_or_calendar_timezone_fails_before_writes(self):
        self.configure()
        before = self.rows()
        snapshots = [object(), self.snapshot(self.event(), calendar_id="other-calendar"),
            self.snapshot(self.event(), calendar_timezone=""),
            self.snapshot(self.event(), calendar_timezone="Windows Standard Time"),
            self.snapshot(self.event(), events=[self.event()]),
            self.snapshot(events=(self.event(),) * (MAX_EVENTS + 1))]
        for snapshot in snapshots:
            with self.subTest(snapshot=repr(snapshot)):
                self.gateway.snapshot.return_value = snapshot
                self.fail_safely(self.service.generate)
                self.assertEqual(self.rows(), before)

    def test_google_late_failure_leaves_ledger_untouched(self):
        self.configure()
        self.service.generate()
        before = self.rows()
        self.gateway.snapshot.side_effect = RuntimeError("private provider failure " + TITLE)
        self.fail_safely(self.service.generate)
        self.assertEqual(self.rows(), before)

    def test_refreshes_clock_after_query_and_skips_started_event(self):
        self.configure()
        def delayed(*args, **kwargs):
            self.now += timedelta(hours=2)
            return self.snapshot(self.event())
        self.gateway.snapshot.side_effect = delayed
        summary = self.service.generate()
        self.assertEqual((summary.eligible_events, summary.skipped_count), (0, 1))
        self.assertEqual(self.store.list_reminders(), ())

    def test_clock_naive_or_moving_backwards_fails_closed(self):
        self.configure()
        self.now = NOW.replace(tzinfo=None)
        self.fail_safely(self.service.generate)
        self.gateway.snapshot.assert_not_called()
        self.now = NOW
        def backward(*args, **kwargs):
            self.now -= timedelta(seconds=1)
            return self.snapshot(self.event())
        self.gateway.snapshot.side_effect = backward
        self.fail_safely(self.service.generate)
        self.assertEqual(self.store.list_reminders(), ())

    def test_policy_changed_during_query_is_not_used(self):
        self.configure()
        def changed(*args, **kwargs):
            self.configure(offsets={**OFFSETS, "imminent": 10})
            return self.snapshot(self.event())
        self.gateway.snapshot.side_effect = changed
        self.fail_safely(self.service.generate)
        self.assertEqual(self.store.list_reminders(), ())

    def test_policy_cas_catches_race_after_service_reread(self):
        self.configure()
        original = self.store.plan_batch
        def changed(plans, *, config_revision):
            self.configure(recipient_key="b" * 64)
            return original(plans, config_revision=config_revision)
        with patch.object(self.store, "plan_batch", side_effect=changed):
            self.fail_safely(self.service.generate)
        self.assertEqual(self.store.list_reminders(), ())

    def test_repeat_restart_and_update_title_or_timezone_reuse_identity(self):
        self.configure()
        self.service.generate()
        before = self.rows()
        self.service.generate()
        self.assertEqual(self.rows(), before)
        initial_keys = {record.key for record in self.store.list_reminders()}
        self.gateway.snapshot.return_value = self.snapshot(self.event(title="Novo título"),
                                                           calendar_timezone="Europe/London")
        restarted = ReminderPlanningService(ReminderStore(self.path, clock=lambda: self.now),
            self.gateway, expected_recipient_key=RECIPIENT, clock=lambda: self.now)
        restarted.generate()
        records = self.store.list_reminders()
        self.assertEqual({record.key for record in records}, initial_keys)
        self.assertTrue(all(record.title == "Novo título" and record.calendar_timezone == "Europe/London"
                            for record in records))

    def test_moved_or_absent_event_does_not_cancel_old_start_or_expire_globally(self):
        self.configure()
        self.service.generate()
        old = self.store.list_reminders()
        self.gateway.snapshot.return_value = self.snapshot(self.event(
            start_utc=NOW + timedelta(hours=4), end_utc=NOW + timedelta(hours=5)))
        self.service.generate()
        self.assertEqual(len(self.store.list_reminders()), 4)
        self.now += timedelta(days=1)
        self.gateway.snapshot.return_value = self.snapshot()
        before = self.rows()
        self.service.generate()
        self.assertEqual(self.rows(), before)
        self.assertTrue(all(self.store.get(record.key).state == "pending" for record in old))

    def test_created_late_event_has_expired_preparation_and_pending_imminent(self):
        self.configure()
        self.gateway.snapshot.return_value = self.snapshot(self.event(
            start_utc=NOW + timedelta(minutes=40), end_utc=NOW + timedelta(minutes=70)))
        summary = self.service.generate()
        self.assertEqual(summary.state_counts, (("expired", 1), ("pending", 1)))
        records = {record.slot: record for record in self.store.list_reminders()}
        self.assertEqual(records["preparation"].state, "expired")
        self.assertEqual(records["imminent"].state, "pending")

    def test_grace_deadline_is_exclusive(self):
        self.configure()
        self.gateway.snapshot.return_value = self.snapshot(
            self.event("at-limit", start_utc=NOW + timedelta(minutes=58)),
            self.event("within", start_utc=NOW + timedelta(minutes=58, seconds=1)))
        self.service.generate()
        records = {(record.event_id, record.slot): record for record in self.store.list_reminders()}
        self.assertEqual(records["at-limit", "preparation"].state, "expired")
        self.assertEqual(records["within", "preparation"].state, "pending")

    def test_reserved_sending_accepted_and_uncertain_are_preserved(self):
        self.configure()
        events = tuple(self.event(state, start_utc=NOW + timedelta(hours=1))
                       for state in ("reserved", "sending", "accepted", "uncertain"))
        self.gateway.snapshot.return_value = self.snapshot(*events)
        self.service.generate()
        initial = {record.event_id: record for record in self.store.list_reminders()
                   if record.slot == "preparation"}
        for state, record in initial.items():
            claim = self.store.reserve(record.key, config_revision=1)
            if state != "reserved":
                self.store.mark_sending(claim)
            if state == "accepted":
                self.store.mark_accepted(claim, "f" * 64)
            elif state == "uncertain":
                self.store.mark_uncertain(claim)
        before = self.rows()
        self.gateway.snapshot.return_value = self.snapshot(*(replace(event, title="Updated") for event in events))
        self.service.generate()
        for state, record in initial.items():
            current = self.store.get(record.key)
            self.assertEqual(current.state, state)
            self.assertEqual(current.title, TITLE)
            self.assertEqual(len(self.store.history(record.key)), len([row for row in before["reminder_history"]
                                                                      if row[1] == record.key]))

    def test_database_error_after_first_plan_rolls_back_entire_batch(self):
        self.configure()
        self.service.generate()
        before = self.rows()
        self.gateway.snapshot.return_value = self.snapshot(self.event(title="Changed"), self.event("other"))
        original = self.store._plan
        calls = 0
        def fail_late(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise sqlite3.OperationalError("private provider failure")
            return original(*args, **kwargs)
        with patch.object(self.store, "_plan", side_effect=fail_late):
            self.fail_safely(self.service.generate)
        self.assertEqual(self.rows(), before)

    def test_concurrent_generations_keep_one_identity_per_slot(self):
        self.configure()
        def generate(_):
            service = ReminderPlanningService(ReminderStore(self.path, clock=lambda: NOW),
                self.gateway, expected_recipient_key=RECIPIENT, clock=lambda: NOW)
            return service.generate()
        with ThreadPoolExecutor(max_workers=2) as pool:
            summaries = tuple(pool.map(generate, range(2)))
        self.assertEqual([summary.reminders_considered for summary in summaries], [2, 2])
        self.assertEqual(len(self.store.list_reminders()), 2)
        self.assertEqual(len(self.rows()["reminder_history"]), 2)

    def test_event_snapshot_and_plan_are_immutable_and_hide_private_data(self):
        objects = (self.event(), self.snapshot(self.event()),
                   ReminderPlan("abc123", NOW, "preparation", TITLE, ZONE))
        for obj in objects:
            self.assertNotIn(TITLE, repr(obj))
            self.assertNotIn(CALENDAR, repr(obj))
            with self.assertRaises(FrozenInstanceError):
                obj.title = "changed"

    def test_batch_rejects_invalid_late_input_before_creating_database(self):
        valid = ReminderPlan("abc123", NOW, "preparation", TITLE, ZONE)
        for plans in ([valid], (valid, object()), (valid, replace(valid, event_id="")),
                      (valid,) * 6001, (valid, replace(valid, title="Changed"))):
            with self.subTest(plans_type=type(plans)):
                with self.assertRaises(ValueError):
                    self.store.plan_batch(plans, config_revision=1)
                self.assertFalse(self.path.parent.exists())

    def test_batch_disabled_slot_or_stale_revision_leaves_no_mutation(self):
        self.configure()
        before = self.rows()
        plans = (ReminderPlan("abc123", NOW, "preparation", TITLE, ZONE),
                 ReminderPlan("abc123", NOW, "day_before", TITLE, ZONE))
        with self.assertRaises(ReminderStoreError):
            self.store.plan_batch(plans, config_revision=1)
        with self.assertRaises(ReminderStoreError):
            self.store.plan_batch((), config_revision=2)
        self.assertEqual(self.rows(), before)

    def test_batch_calculation_overflow_is_checked_before_any_write(self):
        self.configure()
        before = self.rows()
        plans = (ReminderPlan("abc123", NOW, "preparation", TITLE, ZONE),
                 ReminderPlan("old", datetime.min.replace(tzinfo=timezone.utc), "imminent", TITLE, ZONE))
        with self.assertRaises(ValueError):
            self.store.plan_batch(plans, config_revision=1)
        self.assertEqual(self.rows(), before)


if __name__ == "__main__":
    unittest.main()
