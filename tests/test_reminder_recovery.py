"""Recuperação local: SQLite, exclusão de SO e crash; nenhum provedor ou rede."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from app.reminder_store import (
    ReminderClaim, ReminderRecoverySnapshot, ReminderStore, ReminderStoreError,
)
from app.services.reminder_recovery import (
    ReminderRecoveryError, ReminderRecoveryService, ReminderRecoverySummary,
)
from app.services.reminder_tick import reminder_tick_lock


NOW = datetime(2030, 6, 17, 12, tzinfo=timezone.utc)
CALENDAR = "recovery-calendar@example.invalid"
RECIPIENT = "a" * 64
TITLE = "Conteúdo privado fictício"
ZONE = "America/Recife"
FINGERPRINT = "b" * 64


class ReminderRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "private" / "reminders.sqlite3"
        self.now = NOW
        self.store = ReminderStore(self.path, clock=lambda: self.now)

    def config(self, **changes):
        old = self.store.config()
        values = dict(enabled=True, permission_granted=True,
                      expected_revision=None if old is None else old.revision)
        values.update(changes)
        return self.store.configure(CALENDAR, RECIPIENT, **values)

    def plan(self, event_id="event123", **changes):
        config = self.store.config() or self.config()
        values = dict(title=TITLE, calendar_timezone=ZONE, config_revision=config.revision)
        values.update(changes)
        return self.store.plan(event_id, NOW + timedelta(hours=1), "preparation", **values)

    def reserved(self, event_id="event123"):
        record = self.plan(event_id)
        return self.store.reserve(record.key, config_revision=self.store.config().revision)

    def sending(self, event_id="event123"):
        claim = self.reserved(event_id)
        self.store.mark_sending(claim)
        return claim

    def accepted(self, event_id="accepted"):
        claim = self.sending(event_id)
        self.store.mark_accepted(claim, FINGERPRINT)
        return claim

    def uncertain(self, event_id="uncertain"):
        claim = self.sending(event_id)
        self.store.mark_uncertain(claim)
        return claim

    def service(self, **changes):
        values = dict(store=self.store, clock=lambda: self.now)
        values.update(changes)
        return ReminderRecoveryService(**values)

    def public(self, result):
        data = result.public_data()
        self.assertEqual(set(data), {"recovery_status", "skip_reason", "reservations_recovered", "expired_count",
            "uncertain_count", "protected_count", "messages_sent", "external_mutations"})
        self.assertEqual((data["messages_sent"], data["external_mutations"]), (0, 0))
        for private in (CALENDAR, RECIPIENT, TITLE, str(self.path), "private failure", "SELECT "):
            self.assertNotIn(private, str(data))
        return data

    def rows(self):
        with closing(sqlite3.connect(self.path)) as connection:
            return {table: connection.execute("SELECT * FROM " + table + " ORDER BY 1").fetchall()
                    for table in ("reminder_config", "reminders", "reminder_history", "channel_observations")}

    def protected_rows(self, keys):
        with closing(sqlite3.connect(self.path)) as connection:
            placeholders = ",".join("?" for _ in keys)
            return (connection.execute("SELECT * FROM reminders WHERE key IN (" + placeholders + ") ORDER BY key", keys).fetchall(),
                    connection.execute("SELECT * FROM reminder_history WHERE reminder_key IN (" + placeholders +
                                       ") ORDER BY sequence", keys).fetchall())

    def assert_safe_error(self, callback):
        with self.assertRaises(ReminderRecoveryError) as caught:
            callback()
        for private in (CALENDAR, RECIPIENT, TITLE, str(self.path), "private failure", "SELECT "):
            self.assertNotIn(private, str(caught.exception))

    def test_absent_disabled_permission_missing_skip_before_snapshot_and_clock(self):
        for changes, reason in ((None, "not_configured"), ({"enabled": False}, "disabled"),
                                ({"permission_granted": False}, "permission_required")):
            if changes is not None:
                self.config(**changes)
            with patch.object(self.store, "recovery_snapshot") as snapshot:
                clock = Mock(side_effect=AssertionError("clock should remain unopened"))
                data = self.public(self.service(clock=clock).recover())
                self.assertEqual((data["recovery_status"], data["skip_reason"]), ("skipped", reason))
                snapshot.assert_not_called()
                clock.assert_not_called()

    def test_busy_requires_actual_exclusive_lock_before_store_access(self):
        store = Mock(path=self.path)
        with reminder_tick_lock(self.path) as acquired:
            self.assertTrue(acquired)
            data = self.public(self.service(store=store).recover())
        self.assertEqual(data["skip_reason"], "busy")
        store.config.assert_not_called()
        store.recovery_snapshot.assert_not_called()

    def test_empty_profile_completes_without_generation_or_external_factories(self):
        self.config()
        data = self.public(self.service().recover())
        self.assertEqual(data, {"recovery_status": "completed", "skip_reason": None,
            "reservations_recovered": 0, "expired_count": 0, "uncertain_count": 0,
            "protected_count": 0, "messages_sent": 0, "external_mutations": 0})

    def test_abandoned_reservation_is_released_with_stale_claim_invalid_and_fresh_attempt(self):
        old = self.reserved()
        data = self.public(self.service().recover())
        self.assertEqual(data["reservations_recovered"], 1)
        record = self.store.get(old.reminder.key)
        self.assertEqual((record.state, record.reason, record.attempt_id), ("pending", "released", None))
        with self.assertRaises(ReminderStoreError):
            self.store.mark_sending(old)
        fresh = self.store.reserve(record.key, config_revision=1)
        self.assertEqual(fresh.status, "new")
        self.assertNotEqual(fresh.attempt_id, old.attempt_id)
        with self.assertRaises(ReminderStoreError):
            self.store.release_reserved(old)
        self.assertEqual(self.store.get(record.key).attempt_id, fresh.attempt_id)

    def test_sending_becomes_uncertain_with_same_attempt_and_never_releases_or_resends(self):
        claim = self.sending()
        data = self.public(self.service().recover())
        self.assertEqual(data["uncertain_count"], 1)
        record = self.store.get(claim.reminder.key)
        self.assertEqual((record.state, record.reason, record.attempt_id), ("uncertain", "uncertain", claim.attempt_id))
        self.assertEqual(self.store.reserve(record.key, config_revision=1).status, "blocked")
        for callback in (lambda: self.store.release_reserved(claim), lambda: self.store.mark_sending(claim),
                         lambda: self.store.mark_accepted(claim, FINGERPRINT)):
            with self.assertRaises(ReminderStoreError):
                callback()
        again = self.public(self.service().recover())
        self.assertEqual((again["uncertain_count"], again["protected_count"]), (0, 1))

    def test_protected_accepted_uncertain_are_byte_identical_when_only_protected_scope_exists(self):
        accepted, uncertain = self.accepted(), self.uncertain()
        before_rows, before_bytes = self.rows(), self.path.read_bytes()
        data = self.public(self.service().recover())
        self.assertEqual((data["protected_count"], data["uncertain_count"], data["reservations_recovered"]), (2, 0, 0))
        self.assertEqual(self.rows(), before_rows)
        self.assertEqual(self.path.read_bytes(), before_bytes)
        self.assertEqual(self.store.get(accepted.reminder.key).message_id_fingerprint, FINGERPRINT)
        self.assertEqual(self.store.get(uncertain.reminder.key).attempt_id, uncertain.attempt_id)

    def test_pending_blocked_cancelled_expired_future_rows_are_not_recovery_candidates(self):
        pending = self.plan("pending")
        blocked = self.store.mark_blocked(self.plan("blocked").key, "channel_window_unknown")
        cancelled = self.store.cancel(self.plan("cancelled").key)
        future = self.store.plan("future", NOW + timedelta(hours=2), "preparation", title=TITLE,
                                 calendar_timezone=ZONE, config_revision=1)
        self.now += timedelta(seconds=120)
        self.store.expire_due()
        before = self.rows()
        result = self.service().recover()
        self.assertEqual((result.reservations_recovered, result.expired_count, result.uncertain_count), (0, 0, 0))
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.store.get(future.key), future)

    def test_deadline_is_exclusive_and_expiration_never_authorizes_transport(self):
        claim = self.reserved()
        self.now += timedelta(seconds=120)
        data = self.public(self.service().recover())
        self.assertEqual((data["reservations_recovered"], data["expired_count"]), (0, 1))
        record = self.store.get(claim.reminder.key)
        self.assertEqual((record.state, record.reason, record.attempt_id), ("expired", "reminder_expired", None))
        self.assertEqual(self.store.reserve(record.key, config_revision=1).status, "expired")

    def test_one_microsecond_before_deadline_can_release_but_still_requires_full_dispatch_gate(self):
        claim = self.reserved()
        self.now += timedelta(seconds=120, microseconds=-1)
        self.assertEqual(self.service().recover().reservations_recovered, 1)
        self.assertEqual(self.store.get(claim.reminder.key).state, "pending")

    def test_sending_past_event_start_is_still_uncertain_not_expired(self):
        claim = self.sending()
        self.now += timedelta(hours=2)
        result = self.service().recover()
        self.assertEqual((result.uncertain_count, result.expired_count), (1, 0))
        self.assertEqual(self.store.get(claim.reminder.key).state, "uncertain")

    def test_profile_change_before_recovery_does_not_revive_old_binding(self):
        claim = self.reserved()
        self.config(enabled=False)
        before = self.rows()
        self.assertEqual(self.service().recover().skip_reason, "disabled")
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.store.get(claim.reminder.key).state, "cancelled")

    def test_sending_from_previous_binding_becomes_uncertain_without_redirecting(self):
        claim = self.sending()
        self.store.configure("other-calendar", "c" * 64, enabled=True, permission_granted=True, expected_revision=1)
        self.assertEqual(self.service().recover().uncertain_count, 1)
        current = self.store.get(claim.reminder.key)
        self.assertEqual((current.recipient_key, current.calendar_id, current.attempt_id),
                         (RECIPIENT, CALENDAR, claim.attempt_id))

    def test_store_clock_before_reservation_refuses_without_changing_any_row(self):
        self.reserved()
        before = self.rows()
        self.now -= timedelta(microseconds=1)
        self.assert_safe_error(self.service().recover)
        self.assertEqual(self.rows(), before)

    def test_service_clock_regression_after_snapshot_refuses_atomic_batch(self):
        self.reserved()
        before = self.rows()
        clock = Mock(side_effect=[NOW, NOW - timedelta(microseconds=1)])
        self.assert_safe_error(self.service(clock=clock).recover)
        self.assertEqual(self.rows(), before)

    def test_clock_regression_between_snapshot_and_apply_refuses_even_empty_scope(self):
        self.config()
        snapshot = self.store.recovery_snapshot()
        self.now -= timedelta(microseconds=1)
        with self.assertRaises(ReminderStoreError):
            self.store.apply_recovery(snapshot)

    def test_final_clock_must_not_precede_service_not_before(self):
        self.reserved()
        snapshot, before = self.store.recovery_snapshot(), self.rows()
        with self.assertRaises(ReminderStoreError):
            self.store.apply_recovery(snapshot, not_before=NOW + timedelta(microseconds=1))
        self.assertEqual(self.rows(), before)

    def test_invalid_naive_or_missing_service_clock_is_sanitized(self):
        self.reserved()
        before = self.rows()
        for clock in (lambda: None, lambda: NOW.replace(tzinfo=None), lambda: "private failure"):
            self.assert_safe_error(self.service(clock=clock).recover)
        self.assertEqual(self.rows(), before)

    def test_snapshot_captures_config_full_scope_and_history_in_one_transaction(self):
        reserved, sending = self.reserved("reserved"), self.sending("sending")
        self.accepted()
        self.uncertain()
        snapshot = self.store.recovery_snapshot()
        self.assertEqual(snapshot.config, self.store.config())
        self.assertEqual({record.key for record in snapshot.records}, {reserved.reminder.key, sending.reminder.key})
        self.assertEqual(snapshot.protected_count, 2)
        self.assertEqual(snapshot.captured_at, self.now)
        self.assertGreater(snapshot.history_sequence, 0)
        for private in (CALENDAR, RECIPIENT, TITLE, reserved.attempt_id):
            self.assertNotIn(private, repr(snapshot))

    def test_snapshot_cap_overflow_refuses_without_recovering_truncated_batch(self):
        self.reserved("one")
        self.sending("two")
        before = self.rows()
        with self.assertRaises(ReminderStoreError):
            self.store.recovery_snapshot(limit=1)
        self.assertEqual(self.rows(), before)
        snapshot = self.store.recovery_snapshot(limit=2)
        result = self.store.apply_recovery(snapshot)
        self.assertEqual((result.reservations_recovered, result.uncertain_count), (1, 1))

    def test_snapshot_limits_are_strict_integers_without_unbounded_bypass(self):
        self.config()
        for limit in (True, False, 0, -1, 201, "2", 1.5, None):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.store.recovery_snapshot(limit)

    def test_config_cas_change_refuses_entire_batch(self):
        claim = self.reserved()
        snapshot = self.store.recovery_snapshot()
        self.config(offsets={"day_before": None, "preparation": 30, "imminent": 15})
        after_configuration = self.rows()
        with self.assertRaises(ReminderStoreError):
            self.store.apply_recovery(snapshot)
        self.assertEqual(self.rows(), after_configuration)
        self.assertEqual(self.store.get(claim.reminder.key).state, "cancelled")

    def test_inserted_phantom_reservation_refuses_old_snapshot(self):
        self.reserved("one")
        snapshot = self.store.recovery_snapshot()
        self.reserved("two")
        before = self.rows()
        with self.assertRaises(ReminderStoreError):
            self.store.apply_recovery(snapshot)
        self.assertEqual(self.rows(), before)

    def test_history_watermark_detects_aba_when_scope_and_config_return_to_same_values(self):
        self.reserved()
        pending = self.plan("outside-scope")
        snapshot = self.store.recovery_snapshot()
        self.store.mark_blocked(pending.key, "channel_window_unknown")
        self.store.plan("outside-scope", pending.start_at, pending.slot, title=pending.title,
                        calendar_timezone=pending.calendar_timezone, config_revision=pending.config_revision)
        current = self.store.recovery_snapshot()
        self.assertEqual(current.records, snapshot.records)
        self.assertEqual(current.config, snapshot.config)
        self.assertNotEqual(current.history_sequence, snapshot.history_sequence)
        before = self.rows()
        with self.assertRaises(ReminderStoreError):
            self.store.apply_recovery(snapshot)
        self.assertEqual(self.rows(), before)

    def test_late_failure_rolls_back_recovery_and_uncertainty_in_same_batch(self):
        self.reserved("one")
        self.sending("two")
        before = self.rows()
        snapshot = self.store.recovery_snapshot()
        move, calls = self.store._move, [0]
        def fail_second(*args, **kwargs):
            calls[0] += 1
            result = move(*args, **kwargs)
            if calls[0] == 2:
                raise sqlite3.OperationalError("private failure")
            return result
        with patch.object(self.store, "_move", side_effect=fail_second):
            with self.assertRaises(ReminderStoreError):
                self.store.apply_recovery(snapshot)
        self.assertEqual(self.rows(), before)

    def test_corrupted_history_with_potential_send_is_never_released_as_safe_reservation(self):
        claim = self.reserved()
        # Mesmo uma adulteração direta que mantém o registro reserved não pode
        # transformar evidência de sending em autorização de novo envio.
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("INSERT INTO reminder_history(reminder_key,from_state,to_state,reason,attempt_id,at)"
                " VALUES(?,?,?,?,?,?)", (claim.reminder.key, "reserved", "sending", "sending",
                                        claim.attempt_id, NOW.isoformat()))
            connection.commit()
        before = self.rows()
        self.assert_safe_error(self.service().recover)
        self.assertEqual(self.rows(), before)

    def test_forged_snapshot_types_and_duplicate_records_are_refused(self):
        self.reserved()
        snapshot = self.store.recovery_snapshot()
        for invalid in (None, {}, replace(snapshot, records=list(snapshot.records)),
                        replace(snapshot, records=snapshot.records * 2), replace(snapshot, history_sequence=True),
                        replace(snapshot, protected_count=True), replace(snapshot, limit=201),
                        replace(snapshot, captured_at=NOW.replace(tzinfo=None))):
            with self.subTest(invalid=repr(invalid)), self.assertRaises((ValueError, ReminderStoreError)):
                self.store.apply_recovery(invalid)

    def test_commit_then_raise_is_unknown_to_service_and_never_fabricates_zero_result(self):
        claim = self.reserved()
        original = self.store.apply_recovery
        def commit_then_raise(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("private failure")
        with patch.object(self.store, "apply_recovery", side_effect=commit_then_raise):
            self.assert_safe_error(self.service().recover)
        self.assertEqual(self.store.get(claim.reminder.key).state, "pending")

    def test_lock_cleanup_failure_preserves_known_committed_counts(self):
        claim = self.reserved()
        @contextmanager
        def cleanup_failure(path):
            with reminder_tick_lock(path) as acquired:
                yield acquired
            raise OSError("private failure")
        with patch("app.services.reminder_recovery.reminder_tick_lock", side_effect=cleanup_failure):
            data = self.public(self.service().recover())
        self.assertEqual((data["recovery_status"], data["reservations_recovered"]), ("failed", 1))
        self.assertEqual(self.store.get(claim.reminder.key).state, "pending")

    def test_service_rejects_config_change_between_gate_and_snapshot(self):
        self.reserved()
        original = self.store.recovery_snapshot
        def changed(*args, **kwargs):
            self.config(enabled=False)
            return original(*args, **kwargs)
        with patch.object(self.store, "recovery_snapshot", side_effect=changed):
            self.assert_safe_error(self.service().recover)
        self.assertFalse(self.store.config().enabled)

    def test_schema_one_tables_and_indexes_are_unchanged_after_recovery(self):
        self.reserved()
        with closing(sqlite3.connect(self.path)) as connection:
            before = connection.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name").fetchall()
        self.service().recover()
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name").fetchall(), before)

    def test_real_process_crash_recovers_only_abandoned_claims_and_preserves_44_old_protected_records(self):
        keys = []
        for number in range(22):
            keys.append(self.accepted("old-accepted-" + str(number)).reminder.key)
            keys.append(self.uncertain("old-uncertain-" + str(number)).reminder.key)
        before = self.protected_rows(keys)
        reserved_record, sending_record = self.plan("abandoned-reserved"), self.plan("abandoned-sending")
        program = ("import sys;from datetime import datetime;from app.reminder_store import ReminderStore;"
            "from app.services.reminder_tick import reminder_tick_lock;"
            "store=ReminderStore(sys.argv[1],clock=lambda:datetime.fromisoformat(sys.argv[4]));"
            "lock=reminder_tick_lock(store.path);assert lock.__enter__();"
            "claim1=store.reserve(sys.argv[2],config_revision=1);"
            "claim2=store.reserve(sys.argv[3],config_revision=1);"
            "assert claim1.status==claim2.status=='new';"
            "assert store.mark_sending(claim2).state=='sending';"
            "print('ready',flush=True);sys.stdin.read()")
        process = subprocess.Popen([sys.executable, "-B", "-c", program, str(self.path), reserved_record.key,
            sending_record.key, NOW.isoformat()], cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        reader = ThreadPoolExecutor(max_workers=1)
        try:
            self.assertEqual(reader.submit(process.stdout.readline).result(timeout=10).strip(), "ready")
            old_reserved = self.store.get(reserved_record.key)
            old_sending = self.store.get(sending_record.key)
            data = self.public(self.service().recover())
            self.assertEqual((data["recovery_status"], data["skip_reason"]), ("skipped", "busy"))
            self.assertEqual(self.store.get(reserved_record.key), old_reserved)
            self.assertEqual(self.store.get(sending_record.key), old_sending)
        finally:
            process.kill()
            process.communicate(timeout=5)
            reader.shutdown(wait=True)
        # Posse do lock após o crash, sem esperar TTL, libera só a reserva que
        # comprovadamente nunca alcançou sending. O outro claim fica incerto.
        result = self.public(self.service().recover())
        self.assertEqual((result["reservations_recovered"], result["uncertain_count"], result["protected_count"]), (1, 1, 44))
        self.assertEqual(self.protected_rows(keys), before)
        old_claim = ReminderClaim("new", old_reserved, old_reserved.attempt_id)
        with self.assertRaises(ReminderStoreError):
            self.store.mark_sending(old_claim)
        fresh = self.store.reserve(reserved_record.key, config_revision=1)
        self.assertNotEqual(fresh.attempt_id, old_reserved.attempt_id)
        with self.assertRaises(ReminderStoreError):
            self.store.release_reserved(old_claim)
        uncertain = self.store.get(sending_record.key)
        self.assertEqual((uncertain.state, uncertain.attempt_id), ("uncertain", old_sending.attempt_id))
        self.assertEqual(self.store.reserve(sending_record.key, config_revision=1).status, "blocked")

    def test_public_summary_rejects_unknown_status_private_reason_booleans_and_external_work(self):
        cases = (ReminderRecoverySummary("private failure"), ReminderRecoverySummary("completed", "private failure"),
                 ReminderRecoverySummary("skipped", "busy", reservations_recovered=1),
                 ReminderRecoverySummary("completed", uncertain_count=True),
                 ReminderRecoverySummary("completed", protected_count=-1),
                 ReminderRecoverySummary("completed", messages_sent=1),
                 ReminderRecoverySummary("completed", external_mutations=True))
        for summary in cases:
            self.assert_safe_error(summary.public_data)
        self.assertEqual(ReminderRecoverySummary("failed", reservations_recovered=1).public_data()["reservations_recovered"], 1)


if __name__ == "__main__":
    unittest.main()
