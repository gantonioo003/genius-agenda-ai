"""Ciclos compostos recover→tick→dispatch com SQLite, gateway e SDK reais.

Somente a sessão HTTP Google e o sender são fixtures em memória. As mudanças
remotas acontecem entre ciclos/GETs; não são DTOs de gateway construídos à mão.
"""
from datetime import datetime, timedelta, timezone
import unittest

from reminder_system_fixture import NOW, UTC, ReminderSystemFixture


class RemindersSystemCalendarTests(unittest.TestCase):
    def fixture(self, **options):
        value = ReminderSystemFixture(self, **options)
        self.addCleanup(value.close)
        return value

    def test_default_sixty_then_fifteen_minutes_send_once_across_restarts(self):
        f = self.fixture(); f.event()
        self.assertEqual(f.cycle()[1].tick_status, "completed")
        self.assertEqual({record.slot for record in f.records()}, {"preparation", "imminent"})
        initial_keys = {record.key for record in f.records()}
        for slot in ("preparation", "imminent"):
            f.clock.advance_to(f.record(slot=slot).due_at)
            f.restart()
            self.assertEqual(f.cycle()[2].accepted_count, 1)
            saved = f.history()
            self.assertEqual(f.cycle()[2].transport_attempts, 0)
            self.assertEqual(f.history(), saved)
        self.assertEqual(len(f.attempts), 2)
        self.assertEqual({record.key for record in f.records()}, initial_keys)
        self.assertTrue(all(record.state == "accepted" for record in f.records()))

    def test_optional_day_before_keeps_three_identities_through_all_due_times(self):
        f = self.fixture(offsets={"day_before": 1440, "preparation": 60, "imminent": 15})
        f.event(start=NOW + timedelta(hours=30)); f.cycle()
        keys = {record.key for record in f.records()}
        for slot in ("day_before", "preparation", "imminent"):
            f.clock.advance_to(f.record(slot=slot).due_at)
            f.window(); f.restart()
            self.assertEqual(f.cycle()[2].accepted_count, 1)
        self.assertEqual(len(f.attempts), 3)
        self.assertEqual({record.key for record in f.records()}, keys)
        self.assertEqual(f.cycle()[2].transport_attempts, 0)

    def test_dst_fold_occurrences_keep_distinct_utc_slots_and_local_presentation(self):
        f = self.fixture(now=datetime(2030, 11, 3, 4, tzinfo=UTC), zone="America/New_York")
        for identity, offset in (("fold-one", "-04:00"), ("fold-two", "-05:00")):
            start = datetime.fromisoformat("2030-11-03T01:30:00" + offset)
            f.event(identity, start=start, recurringEventId="series-one",
                    originalStartTime={"dateTime": start.isoformat()})
        f.cycle()
        self.assertEqual(len(f.records()), 4)
        due_times = sorted({record.due_at for record in f.records()})
        for due in due_times:
            f.clock.advance_to(due)
            self.assertEqual(f.cycle()[2].accepted_count, 1)
        self.assertEqual(len({attempt["key"] for attempt in f.attempts}), 4)
        self.assertEqual(sum("03/11/2030 às 1h30 (UTC-04:00)" in item["text"] for item in f.attempts), 2)
        self.assertEqual(sum("03/11/2030 às 1h30 (UTC-05:00)" in item["text"] for item in f.attempts), 2)
        self.assertTrue(all("Horário de Nova York." in item["text"] for item in f.attempts))

    def test_composed_due_and_deadline_boundaries_are_inclusive_then_exclusive(self):
        f = self.fixture(offsets={"day_before": None, "preparation": None, "imminent": 15})
        for number, minutes in enumerate((20, 25, 30)):
            f.event("boundary-" + str(number), start=NOW + timedelta(minutes=minutes))
        f.cycle()
        first = f.record("boundary-0", slot="imminent")
        f.clock.advance_to(first.due_at - timedelta(microseconds=1))
        self.assertEqual(f.cycle()[2].transport_attempts, 0)
        f.clock.advance_to(first.due_at)
        self.assertEqual(f.cycle()[2].accepted_count, 1)
        second = f.record("boundary-1", slot="imminent")
        f.clock.advance_to(second.deadline_at - timedelta(microseconds=1))
        self.assertEqual(f.cycle()[2].accepted_count, 1)
        third = f.record("boundary-2", slot="imminent")
        f.clock.advance_to(third.deadline_at)
        self.assertEqual(f.cycle()[2].transport_attempts, 0)
        self.assertEqual(f.store.get(third.key).state, "expired")
        self.assertEqual(len(f.attempts), 2)

    def test_reschedule_outside_list_horizon_then_return_never_resends_accepted_start(self):
        f = self.fixture(); f.event(); f.cycle()
        original = f.record().start_at
        old_preparation = f.record()
        f.clock.advance_to(old_preparation.due_at)
        self.assertEqual(f.cycle()[2].accepted_count, 1)
        accepted_history = f.store.history(old_preparation.key)
        far = original + timedelta(days=12)
        f.move_event("system-event", far)
        _, tick, dispatch = f.cycle()
        self.assertEqual(tick.synchronization.events_checked, 1)
        self.assertEqual(tick.generation.events_seen, 0)
        self.assertEqual(dispatch.transport_attempts, 0)
        self.assertEqual(f.record(start=far).state, "pending")
        self.assertEqual(f.record(slot="imminent", start=original).state, "cancelled")
        f.move_event("system-event", original); f.restart(); f.cycle()
        self.assertEqual(f.store.history(old_preparation.key), accepted_history)
        f.clock.advance_to(f.record(slot="imminent", start=original).due_at)
        self.assertEqual(f.cycle()[2].accepted_count, 1)
        self.assertEqual(len(f.attempts), 2)
        self.assertEqual(sum(item["key"] == old_preparation.key for item in f.attempts), 1)
        self.assertTrue(all(record.state == "cancelled" for record in f.records() if record.start_at == far))

    def test_title_and_calendar_zone_change_with_same_utc_does_not_duplicate_identity(self):
        f = self.fixture(); f.event(); f.cycle()
        keys = {record.key for record in f.records()}
        start = f.record().start_at
        f.backend.calendar["timeZone"] = "America/New_York"
        f.backend.saved["system-event"]["summary"] = "Título atualizado"
        f.move_event("system-event", start.astimezone(timezone(timedelta(hours=1))))
        f.cycle()
        self.assertEqual({record.key for record in f.records()}, keys)
        self.assertTrue(all(record.calendar_timezone == "America/New_York" for record in f.records()))
        f.clock.advance_to(f.record().due_at)
        self.assertEqual(f.cycle()[2].accepted_count, 1)
        self.assertIn("Título atualizado\n17/06/2030 às 10h\nHorário de Nova York.", f.attempts[0]["text"])

    def test_tombstone_after_planning_cancels_pending_slots_without_sender(self):
        f = self.fixture(); f.event(); f.cycle()
        keys = {record.key for record in f.records()}
        f.backend.saved["system-event"] = {"id": "system-event", "status": "cancelled"}
        _, tick, dispatch = f.cycle()
        self.assertEqual(tick.synchronization.cancelled_count, 2)
        self.assertEqual({record.key for record in f.records()}, keys)
        self.assertTrue(all(record.state == "cancelled" for record in f.records()))
        self.assertEqual(dispatch.transport_attempts, 0)

    def test_qualified_event_404_requires_calendar_get_before_cancel(self):
        f = self.fixture(); f.event(); f.cycle()
        del f.backend.saved["system-event"]
        _, tick, _ = f.cycle()
        self.assertEqual(tick.synchronization.cancelled_count, 2)
        event_404 = [index for index, call in enumerate(f.backend.calls) if call["event_id"] == "system-event" and call["status"] == 404]
        checks = [index for index, call in enumerate(f.backend.calls) if "/calendarList/" in call["path"] and call["status"] == 200]
        self.assertTrue(event_404 and checks and min(checks) > max(event_404))
        self.assertEqual(f.attempts, [])

    def test_event_404_with_lost_calendar_permission_does_not_cancel_or_send(self):
        f = self.fixture(); f.event(); f.cycle()
        before, history = f.records(), f.history()
        del f.backend.saved["system-event"]
        f.backend.calendar_access_status = 403
        _, tick, dispatch = f.cycle()
        self.assertEqual(tick.tick_status, "failed")
        self.assertIsNone(dispatch)
        self.assertEqual(f.records(), before)
        self.assertEqual(f.history(), history)
        self.assertEqual(f.attempts, [])
        f.backend.calendar_access_status = 200
        self.assertEqual(f.cycle()[1].synchronization.cancelled_count, 2)

    def test_google_fault_then_repaired_cycle_can_send_before_original_deadline(self):
        f = self.fixture(); f.event(start=NOW + timedelta(minutes=75)); f.cycle()
        key, before = f.record().key, f.history()
        f.backend.fault = lambda method, path, query: (503, f.backend._error(503)) if path.endswith("/events/system-event") else None
        _, tick, dispatch = f.cycle()
        self.assertEqual(tick.tick_status, "failed")
        self.assertIsNone(dispatch)
        self.assertEqual(f.history(), before)
        self.assertEqual(f.store.get(key).state, "pending")
        failed_reads = [call for call in f.backend.calls if call["status"] == 503]
        self.assertEqual(len(failed_reads), 3)  # execute(num_retries=2), somente leituras.
        f.backend.fault = None
        f.clock.advance_to(f.store.get(key).due_at + timedelta(seconds=60))
        f.restart()
        self.assertEqual(f.cycle()[2].accepted_count, 1)
        self.assertEqual(f.cycle()[2].transport_attempts, 0)

    def test_profile_revoked_after_second_google_get_prevents_sender(self):
        f = self.fixture(offsets={"day_before": None, "preparation": None, "imminent": 15})
        f.event(start=NOW + timedelta(minutes=20)); f.cycle()
        record = f.record(slot="imminent")
        f.clock.advance_to(record.due_at)
        f.backend.event_get_counts.clear()
        def revoke(event_id, number):
            if number == 2:
                config = f.store.config()
                f.store.configure(config.calendar_id, config.recipient_key, offsets=dict(config.offsets),
                    enabled=True, permission_granted=False, expected_revision=config.revision)
        f.backend.after_event_get = revoke
        result = f.dispatch()
        self.assertEqual(result.transport_attempts, 0)
        self.assertEqual(f.store.get(record.key).state, "cancelled")
        self.assertEqual(f.attempts, [])

    def test_deadline_reached_after_second_get_expires_reserved_without_transport(self):
        for minutes in (15, 1):
            with self.subTest(offset_minutes=minutes):
                f = self.fixture(offsets={"day_before": None, "preparation": None, "imminent": minutes})
                f.event(start=NOW + timedelta(minutes=20)); f.cycle()
                record = f.record(slot="imminent")
                self.assertEqual(record.deadline_at,
                    min(record.due_at + timedelta(seconds=120), record.start_at))
                if minutes == 1:
                    self.assertEqual(record.deadline_at, record.start_at)
                f.clock.advance_to(record.due_at)
                f.backend.event_get_counts.clear()
                f.backend.after_event_get = lambda identity, number: f.clock.advance_to(record.deadline_at) if number == 2 else None
                result = f.dispatch()
                self.assertEqual((result.transport_attempts, result.expired_count), (0, 1))
                self.assertEqual(f.store.get(record.key).state, "expired")
                self.assertEqual(f.attempts, [])

    def test_event_rescheduled_before_second_get_cancels_claim_then_next_cycle_replans(self):
        f = self.fixture(offsets={"day_before": None, "preparation": None, "imminent": 15})
        f.event(start=NOW + timedelta(minutes=20)); f.cycle()
        old = f.record(slot="imminent")
        f.clock.advance_to(old.due_at)
        f.backend.event_get_counts.clear()
        new_start = old.start_at + timedelta(hours=1)
        f.backend.before_event_get = lambda identity, number: f.move_event(identity, new_start) if number == 2 else None
        result = f.dispatch()
        self.assertEqual((result.cancelled_count, result.transport_attempts), (1, 0))
        self.assertEqual(f.store.get(old.key).state, "cancelled")
        f.backend.before_event_get = None
        f.cycle()
        current = f.record(slot="imminent", start=new_start)
        self.assertNotEqual(current.key, old.key)
        self.assertEqual(current.state, "pending")
        self.assertEqual(f.attempts, [])

    def test_late_page_failure_cannot_leave_partial_plans_or_send_then_repair_is_complete(self):
        f = self.fixture(); f.event("page-one"); f.event("page-two", start=NOW + timedelta(hours=3))
        f.backend.page_size = 1
        f.backend.empty_first_page = True
        f.backend.fault = lambda method, path, query: (503, f.backend._error(503)) if query.get("pageToken") == "page-1" else None
        _, tick, dispatch = f.cycle()
        self.assertEqual(tick.tick_status, "partial")
        self.assertEqual(tick.synchronization.status, "synchronized")
        self.assertIsNone(dispatch)
        self.assertEqual(f.records(), ())
        self.assertEqual(f.attempts, [])
        f.backend.fault = None
        f.restart()
        self.assertEqual(f.cycle()[1].generation.events_seen, 2)
        self.assertEqual(len(f.records()), 4)
        f.clock.advance_to(f.record("page-one").due_at)
        self.assertEqual(f.cycle()[2].accepted_count, 1)
        self.assertTrue(all(call["method"] == "GET" for call in f.backend.calls))


if __name__ == "__main__":
    unittest.main()
