"""Persistência dos lembretes: SQLite temporário, relógio fixo e nenhum provedor."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch

from app.reminder_store import ReminderStore, ReminderStoreError


NOW = datetime(2030, 6, 17, 12, tzinfo=timezone.utc)
CALENDAR = "fixture-calendar@example.invalid"
RECIPIENT = "a" * 64
OTHER_RECIPIENT = "b" * 64
FINGERPRINT = "f" * 64
OFFSETS = {"day_before": None, "preparation": 60, "imminent": 15}
PRIVATE_TITLE = "Entrevista fictícia; conteúdo privado do teste"


def event_key(number):
    return sha256(("fixture-inbound-" + str(number)).encode()).hexdigest()


class ReminderStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "nested" / "reminders.sqlite3"
        self.now = NOW
        self.store = ReminderStore(self.path, clock=lambda: self.now)

    def configure(self, **changes):
        current = self.store.config()
        values = {
            "calendar_id": CALENDAR,
            "recipient_key": RECIPIENT,
            "offsets": dict(OFFSETS),
            "enabled": True,
            "permission_granted": True,
            "expected_revision": None if current is None else current.revision,
        }
        values.update(changes)
        return self.store.configure(**values)

    def plan(self, event_id="abc123", *, start_at=None, slot="preparation", **changes):
        configuration = self.store.config()
        if configuration is None:
            configuration = self.configure()
        values = {
            "title": PRIVATE_TITLE,
            "calendar_timezone": "America/Recife",
            "config_revision": configuration.revision,
        }
        values.update(changes)
        return self.store.plan(
            event_id, start_at or NOW + timedelta(hours=1), slot, **values,
        )

    def reserve(self, record):
        return self.store.reserve(record.key, config_revision=self.store.config().revision)

    def inbound(self, number=1, *, message_at=None, received_at=None, **changes):
        instant = message_at or NOW - timedelta(minutes=5)
        values = {
            "event_key": event_key(number),
            "recipient_key": RECIPIENT,
            "provider_timestamp": str(int(instant.timestamp())),
            "received_at": received_at or NOW,
        }
        values.update(changes)
        return self.store.observe_inbound(**values)

    def execute(self, sql, params=()):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as connection:
            connection.execute(sql, params)

    def database_rows(self):
        with closing(sqlite3.connect(self.path)) as connection:
            names = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )]
            return {name: connection.execute('SELECT * FROM "' + name + '"').fetchall()
                    for name in names}

    def assert_safe_error(self, callback, exception=ReminderStoreError):
        with self.assertRaises(exception) as raised:
            callback()
        text = str(raised.exception)
        self.assertTrue(text)
        for sensitive in (str(self.path), PRIVATE_TITLE, CALENDAR, RECIPIENT,
                          "SELECT ", "INSERT ", "private simulated failure"):
            self.assertNotIn(sensitive, text)
        return raised.exception

    def test_constructor_has_no_database_or_directory_side_effect(self):
        with patch("app.reminder_store.sqlite3.connect") as connect:
            store = ReminderStore(self.path, clock=lambda: NOW)
            self.assertIsInstance(store, ReminderStore)
            connect.assert_not_called()
        self.assertFalse(self.path.parent.exists())

    def test_invalid_configuration_is_rejected_before_database_creation(self):
        with patch("app.reminder_store.sqlite3.connect") as connect:
            self.assert_safe_error(lambda: self.store.configure(
                CALENDAR, RECIPIENT, offsets={**OFFSETS, "preparation": True},
            ), ValueError)
            self.assert_safe_error(lambda: self.store.configure(
                CALENDAR, "private-invalid-recipient",
            ), ValueError)
            connect.assert_not_called()
        self.assertFalse(self.path.parent.exists())

    def test_initial_configuration_is_explicit_disabled_and_durable(self):
        self.assertIsNone(self.store.config())
        configuration = self.store.configure(CALENDAR, RECIPIENT)
        self.assertEqual(configuration.revision, 1)
        self.assertFalse(configuration.enabled)
        self.assertFalse(configuration.permission_granted)
        self.assertEqual(dict(configuration.offsets), OFFSETS)
        restarted = ReminderStore(self.path, clock=lambda: NOW)
        self.assertEqual(restarted.config(), configuration)
        with self.assertRaises(FrozenInstanceError):
            configuration.enabled = True

    def test_configuration_rejects_invalid_intervals_and_boolean_coercion(self):
        configuration = self.configure()
        before = self.database_rows()
        invalid_offsets = [
            {**OFFSETS, "preparation": value}
            for value in (True, False, 1.5, "60", 0, -1, 10081)
        ] + [
            {"preparation": 60, "imminent": 15},
            {**OFFSETS, "unknown": 5},
            {**OFFSETS, "preparation": 15},
            {**OFFSETS, "day_before": 30},
        ]
        for offsets in invalid_offsets:
            with self.subTest(offsets=offsets):
                self.assert_safe_error(lambda: self.configure(offsets=offsets), ValueError)
        for changes in ({"enabled": 1}, {"permission_granted": "true"},
                        {"expected_revision": True}, {"expected_revision": 1.0}):
            with self.subTest(changes=changes):
                self.assert_safe_error(lambda: self.configure(**changes), ValueError)
        self.assertEqual(self.store.config(), configuration)
        self.assertEqual(self.database_rows(), before)

    def test_configuration_accepts_limits_individual_disabled_slots_and_all_disabled(self):
        configuration = self.configure(offsets={
            "day_before": 10080, "preparation": None, "imminent": 1,
        })
        self.assertEqual(dict(configuration.offsets), {
            "day_before": 10080, "preparation": None, "imminent": 1,
        })
        configuration = self.configure(offsets=dict.fromkeys(OFFSETS))
        self.assertEqual(dict(configuration.offsets), dict.fromkeys(OFFSETS))

    def test_configuration_update_requires_current_revision_across_instances(self):
        first = self.configure()
        second_instance = ReminderStore(self.path, clock=lambda: NOW)
        changed = second_instance.configure(
            CALENDAR, RECIPIENT, offsets={**OFFSETS, "imminent": 10},
            enabled=True, permission_granted=True, expected_revision=first.revision,
        )
        self.assertGreater(changed.revision, first.revision)
        self.assert_safe_error(lambda: self.store.configure(
            CALENDAR, RECIPIENT, enabled=False, expected_revision=first.revision,
        ))
        self.assert_safe_error(lambda: self.store.configure(CALENDAR, RECIPIENT))
        self.assertEqual(self.store.config(), changed)

    def test_semantic_identity_uses_utc_instant_and_updates_pending_presentation(self):
        first = self.plan()
        equivalent = (NOW + timedelta(hours=1)).astimezone(timezone(timedelta(hours=-3)))
        second = self.plan(start_at=equivalent, title="Título atual", calendar_timezone="UTC")
        self.assertEqual(second.key, first.key)
        self.assertEqual(second.start_at, NOW + timedelta(hours=1))
        self.assertEqual(second.start_at.utcoffset(), timedelta(0))
        self.assertEqual(second.title, "Título atual")
        self.assertEqual(second.calendar_timezone, "UTC")
        self.assertEqual(len(self.store.list_reminders()), 1)

    def test_planning_requires_aware_time_valid_zone_and_current_enabled_configuration(self):
        configuration = self.configure()
        for changes in ({"start_at": datetime(2030, 6, 17, 13)},
                        {"calendar_timezone": "Missing/Timezone"},
                        {"slot": "unknown"}, {"config_revision": True}):
            with self.subTest(changes=changes):
                self.assert_safe_error(lambda: self.plan(**changes), ValueError)
        disabled = self.configure(enabled=False)
        self.assert_safe_error(lambda: self.plan(config_revision=configuration.revision))
        self.assert_safe_error(lambda: self.plan(config_revision=disabled.revision))
        self.configure(permission_granted=False)
        self.assert_safe_error(self.plan)
        self.assertEqual(self.store.list_reminders(), ())

    def test_offset_change_recalculates_pending_without_new_identity(self):
        first = self.plan()
        changed = self.configure(offsets={**OFFSETS, "preparation": 30})
        updated = self.store.get(first.key)
        self.assertEqual(updated.key, first.key)
        self.assertEqual(updated.due_at, first.start_at - timedelta(minutes=30))
        self.assertEqual(updated.config_revision, changed.revision)
        self.assertEqual(updated.state, "pending")
        self.assertEqual(self.plan().key, first.key)
        self.assertEqual(len(self.store.list_reminders()), 1)
        self.configure(offsets={**OFFSETS, "preparation": 90})
        expired = self.store.get(first.key)
        self.assertEqual(expired.key, first.key)
        self.assertEqual(expired.state, "expired")
        self.assertEqual(expired.due_at, first.start_at - timedelta(minutes=90))

    def test_new_start_has_new_identity_and_return_to_accepted_start_does_not_resend(self):
        first = self.plan()
        claim = self.reserve(first)
        self.store.mark_sending(claim)
        self.store.mark_accepted(claim, FINGERPRINT)
        later = self.plan(start_at=first.start_at + timedelta(hours=1))
        self.assertNotEqual(later.key, first.key)
        restored = self.plan(start_at=first.start_at, title="Nome atualizado")
        self.assertEqual(restored.key, first.key)
        self.assertEqual(restored.state, "accepted")
        self.assertEqual(self.reserve(restored).status, "accepted")
        self.assertEqual(len(self.store.list_reminders()), 2)

    def test_configuration_changes_preserve_accepted_uncertain_and_sending_history(self):
        records = [self.plan("abc12" + str(number)) for number in range(4)]
        for record, target in zip(records, ("accepted", "uncertain", "sending")):
            claim = self.reserve(record)
            self.store.mark_sending(claim)
            if target == "accepted":
                self.store.mark_accepted(claim, FINGERPRINT)
            elif target == "uncertain":
                self.store.mark_uncertain(claim)
        terminal_before = {record.key: self.store.get(record.key) for record in records[:3]}
        histories_before = {record.key: self.store.history(record.key) for record in records[:3]}
        self.configure(enabled=False, permission_granted=False,
                       offsets={**OFFSETS, "preparation": 30})
        for record in records[:3]:
            self.assertEqual(self.store.get(record.key), terminal_before[record.key])
            self.assertEqual(self.store.history(record.key), histories_before[record.key])
        self.assertEqual(self.store.get(records[3].key).state, "cancelled")

    def test_recipient_change_cancels_old_binding_and_does_not_redirect_identity(self):
        old = self.plan()
        self.configure(recipient_key=OTHER_RECIPIENT)
        self.assertEqual(self.store.get(old.key).state, "cancelled")
        self.assertEqual(self.store.get(old.key).recipient_key, RECIPIENT)
        current = self.plan()
        self.assertEqual(current.recipient_key, OTHER_RECIPIENT)
        self.assertNotEqual(current.key, old.key)
        self.assertEqual(self.reserve(old).status, "blocked")

    def test_disabling_slot_cancels_pending_and_replanning_reuses_same_identity(self):
        original = self.plan()
        self.configure(offsets={**OFFSETS, "preparation": None})
        self.assertEqual(self.store.get(original.key).state, "cancelled")
        self.configure()
        self.assertEqual(self.store.get(original.key).state, "cancelled")
        replanned = self.plan()
        self.assertEqual(replanned.key, original.key)
        self.assertEqual(replanned.state, "pending")

    def test_reservation_window_includes_due_and_excludes_deadline(self):
        record = self.plan()
        self.now = record.due_at - timedelta(microseconds=1)
        self.assertEqual(self.reserve(record).status, "pending")
        self.now = record.due_at
        claim = self.reserve(record)
        self.assertEqual(claim.status, "new")
        self.store.release_reserved(claim)
        self.now = record.deadline_at
        self.assertEqual(self.reserve(record).status, "expired")
        self.assertEqual(self.store.get(record.key).state, "expired")

    def test_event_start_caps_window_and_expire_due_preserves_future(self):
        self.configure(offsets={**OFFSETS, "imminent": 1})
        short = self.plan(start_at=NOW + timedelta(minutes=1), slot="imminent")
        future = self.plan("abc124", start_at=NOW + timedelta(hours=2))
        self.assertEqual(short.deadline_at, short.start_at)
        self.now = short.start_at
        self.store.expire_due()
        self.assertEqual(self.store.get(short.key).state, "expired")
        self.assertEqual(self.store.get(future.key).state, "pending")
        self.assertNotEqual(self.reserve(short).status, "new")

    def test_final_sending_transition_rechecks_deadline_after_reservation(self):
        record = self.plan()
        claim = self.reserve(record)
        self.now = record.deadline_at
        result = self.store.mark_sending(claim)
        self.assertEqual(result.state, "expired")
        self.assertEqual(self.reserve(record).status, "expired")
        self.assert_safe_error(lambda: self.store.mark_accepted(claim, FINGERPRINT))
        self.assertNotIn("sending", [item.to_state for item in self.store.history(record.key)])

    def test_atomic_reservation_between_instances_in_two_threads(self):
        record = self.plan()
        revision = self.store.config().revision
        barrier = Barrier(2)

        def reserve_from_instance(_):
            store = ReminderStore(self.path, clock=lambda: NOW)
            barrier.wait(timeout=5)
            return store.reserve(record.key, config_revision=revision)

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(reserve_from_instance, range(2)))
        self.assertCountEqual([claim.status for claim in claims], ["new", "blocked"])
        winner = next(claim for claim in claims if claim.status == "new")
        loser = next(claim for claim in claims if claim.status == "blocked")
        self.assertIsNotNone(winner.attempt_id)
        self.assertIsNone(loser.attempt_id)
        self.assertEqual(self.store.get(record.key).state, "reserved")
        self.assertEqual([item.to_state for item in self.store.history(record.key)].count("reserved"), 1)

    def test_released_reservation_uses_new_token_and_rejects_stale_claim(self):
        record = self.plan()
        first = self.reserve(record)
        self.store.release_reserved(first)
        second = self.reserve(record)
        self.assertEqual(second.status, "new")
        self.assertNotEqual(first.attempt_id, second.attempt_id)
        before = self.store.get(record.key)
        self.assert_safe_error(lambda: self.store.mark_sending(first))
        self.assert_safe_error(lambda: self.store.release_reserved(first))
        self.assertEqual(self.store.get(record.key), before)
        self.store.mark_sending(second)
        self.assertEqual(self.store.get(record.key).state, "sending")

    def test_transition_requires_new_claim_with_matching_attempt_token(self):
        record = self.plan()
        claim = self.reserve(record)
        blocked = self.reserve(record)
        before = self.database_rows()
        for invalid in (None, blocked, replace(claim, status="accepted"),
                        replace(claim, attempt_id="invalid-token")):
            with self.subTest(claim=invalid):
                self.assert_safe_error(lambda: self.store.mark_sending(invalid), ValueError)
        self.assertEqual(self.database_rows(), before)
        self.assert_safe_error(lambda: self.store.mark_accepted(claim, FINGERPRINT))
        self.assertEqual(self.store.get(record.key).state, "reserved")

    def test_configuration_change_invalidates_reserved_token(self):
        record = self.plan()
        claim = self.reserve(record)
        old_revision = self.store.config().revision
        self.configure(offsets={**OFFSETS, "preparation": 30})
        self.assertEqual(self.store.get(record.key).state, "cancelled")
        self.assert_safe_error(lambda: self.store.mark_sending(claim))
        self.assert_safe_error(lambda: self.store.reserve(record.key, config_revision=old_revision))

    def test_restart_never_reopens_reserved_sending_or_uncertain_after_elapsed_time(self):
        records = [self.plan("abc13" + str(number)) for number in range(3)]
        claims = [self.reserve(record) for record in records]
        self.store.mark_sending(claims[1])
        self.store.mark_sending(claims[2])
        self.store.mark_uncertain(claims[2])
        self.now += timedelta(days=30)
        restarted = ReminderStore(self.path, clock=lambda: self.now)
        restarted.expire_due()
        for record, state in zip(records, ("reserved", "sending", "uncertain")):
            self.assertEqual(restarted.get(record.key).state, state)
            self.assertEqual(restarted.reserve(record.key, config_revision=self.store.config().revision).status, "blocked")

    def test_only_reserved_can_be_released_and_sending_cannot_be_reclassified_as_unsent(self):
        record = self.plan()
        claim = self.reserve(record)
        self.store.mark_sending(claim)
        self.assert_safe_error(lambda: self.store.release_reserved(claim))
        self.store.mark_uncertain(claim)
        before = self.store.get(record.key)
        self.assert_safe_error(lambda: self.store.release_reserved(claim))
        self.assert_safe_error(lambda: self.store.mark_accepted(claim, FINGERPRINT))
        self.assertEqual(self.store.get(record.key), before)

    def test_provider_acceptance_is_durable_and_is_not_delivery_or_read_receipt(self):
        record = self.plan()
        claim = self.reserve(record)
        self.store.mark_sending(claim)
        self.store.mark_accepted(claim, FINGERPRINT)
        restarted = ReminderStore(self.path, clock=lambda: self.now)
        accepted = restarted.get(record.key)
        self.assertEqual(accepted.state, "accepted")
        self.assertEqual(accepted.message_id_fingerprint, FINGERPRINT)
        self.assertEqual([item.to_state for item in restarted.history(record.key)],
                         ["pending", "reserved", "sending", "accepted"])
        self.assertNotIn("delivered", repr(accepted))
        self.assertNotIn("read", repr(accepted))
        self.assert_safe_error(lambda: self.store.mark_uncertain(claim))

    def test_blocked_reminder_can_be_reconsidered_within_window_without_an_attempt(self):
        record = self.plan()
        self.store.mark_blocked(record.key, "channel_window_unknown")
        blocked = self.store.get(record.key)
        self.assertEqual(blocked.state, "blocked")
        self.assertIsNone(blocked.attempt_id)
        self.assertIsNone(blocked.message_id_fingerprint)
        self.assertEqual(self.reserve(blocked).status, "new")

    def test_explicit_cancel_preserves_history_and_cannot_reclassify_accepted(self):
        original = self.plan()
        self.store.cancel(original.key, "event_rescheduled")
        self.assertEqual(self.store.get(original.key).state, "cancelled")
        self.assertEqual(self.reserve(original).status, "blocked")
        restored = self.plan()
        self.assertEqual(restored.key, original.key)
        claim = self.reserve(restored)
        self.store.mark_sending(claim)
        self.store.mark_accepted(claim, FINGERPRINT)
        self.assert_safe_error(lambda: self.store.cancel(original.key))
        self.assertEqual([item.to_state for item in self.store.history(original.key)],
                         ["pending", "cancelled", "pending", "reserved", "sending", "accepted"])

    def test_unknown_identity_is_read_only_and_list_filters_do_not_create_rows(self):
        pending = self.plan()
        blocked = self.plan("abc125")
        self.store.mark_blocked(blocked.key, "template_unavailable")
        unknown = "c" * 64
        before = self.database_rows()
        self.assertIsNone(self.store.get(unknown))
        self.assertEqual(self.store.history(unknown), ())
        self.assertEqual(self.store.list_reminders(states=("pending",)), (pending,))
        self.assertEqual(self.store.list_reminders(states=()), ())
        for callback in (lambda: self.store.reserve(unknown, config_revision=self.store.config().revision),
                         lambda: self.store.cancel(unknown),
                         lambda: self.store.mark_blocked(unknown, "template_unavailable")):
            self.assert_safe_error(callback)
        self.assertEqual(self.database_rows(), before)

    def test_no_database_connection_or_lock_remains_between_calls(self):
        record = self.plan()
        claim = self.reserve(record)
        with closing(sqlite3.connect(self.path, timeout=0, isolation_level=None)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        self.store.mark_sending(claim)
        with closing(sqlite3.connect(self.path, timeout=0, isolation_level=None)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()

    def test_commit_failure_rolls_back_reservation_and_history(self):
        record = self.plan()
        revision = self.store.config().revision
        before = self.database_rows()
        real_connect = sqlite3.connect

        class FailingCommit(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError("private simulated failure")

        def connect(*args, **kwargs):
            return real_connect(*args, factory=FailingCommit, **kwargs)

        with patch("app.reminder_store.sqlite3.connect", side_effect=connect):
            self.assert_safe_error(lambda: self.store.reserve(record.key, config_revision=revision))
        self.assertEqual(self.database_rows(), before)
        self.assertEqual(self.reserve(record).status, "new")

    def test_commit_failure_rolls_back_compound_configuration_and_provider_result(self):
        records = [self.plan("abc14" + str(number)) for number in range(2)]
        before_configuration = self.database_rows()
        real_connect = sqlite3.connect

        class FailingCommit(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError("private simulated failure")

        def connect(*args, **kwargs):
            return real_connect(*args, factory=FailingCommit, **kwargs)

        revision = self.store.config().revision
        with patch("app.reminder_store.sqlite3.connect", side_effect=connect):
            self.assert_safe_error(lambda: self.store.configure(
                CALENDAR, RECIPIENT, enabled=False, expected_revision=revision,
            ))
        self.assertEqual(self.database_rows(), before_configuration)
        claim = self.reserve(records[0])
        self.store.mark_sending(claim)
        before_accepted = self.database_rows()
        with patch("app.reminder_store.sqlite3.connect", side_effect=connect):
            self.assert_safe_error(lambda: self.store.mark_accepted(claim, FINGERPRINT))
        self.assertEqual(self.database_rows(), before_accepted)
        self.store.mark_uncertain(claim)
        self.assertEqual(self.store.get(records[0].key).state, "uncertain")

    def test_foreign_database_and_non_sqlite_bytes_are_not_overwritten(self):
        self.path.parent.mkdir(parents=True)
        self.execute("CREATE TABLE private_data(value TEXT)")
        before = self.path.read_bytes()
        self.assert_safe_error(self.store.initialize)
        self.assertEqual(self.path.read_bytes(), before)
        self.path.write_bytes(b"not a database; private fixture bytes")
        before = self.path.read_bytes()
        self.assert_safe_error(self.store.initialize)
        self.assertEqual(self.path.read_bytes(), before)

    def test_unknown_version_and_dropped_index_fail_closed_after_initialization(self):
        record = self.plan()
        self.execute("UPDATE reminder_metadata SET value='99' WHERE name='schema_version'")
        tampered = self.path.read_bytes()
        self.assert_safe_error(lambda: self.store.get(record.key))
        self.assert_safe_error(ReminderStore(self.path, clock=lambda: NOW).initialize)
        self.assertEqual(self.path.read_bytes(), tampered)
        self.execute("UPDATE reminder_metadata SET value='1' WHERE name='schema_version'")
        self.execute("DROP INDEX reminders_due")
        tampered = self.path.read_bytes()
        self.assert_safe_error(self.store.expire_due)
        self.assert_safe_error(ReminderStore(self.path, clock=lambda: NOW).initialize)
        self.assertEqual(self.path.read_bytes(), tampered)

    def test_corrupt_semantic_binding_history_and_inbound_rows_are_not_used(self):
        record = self.plan()
        self.execute("UPDATE reminders SET recipient_key=? WHERE key=?", (OTHER_RECIPIENT, record.key))
        self.assert_safe_error(lambda: self.store.get(record.key))
        self.assert_safe_error(ReminderStore(self.path, clock=lambda: NOW).initialize)
        self.execute("UPDATE reminders SET recipient_key=? WHERE key=?", (RECIPIENT, record.key))
        self.execute("UPDATE reminder_history SET to_state='accepted' WHERE reminder_key=?", (record.key,))
        self.assert_safe_error(lambda: self.store.reserve(record.key, config_revision=1))
        self.execute("UPDATE reminder_history SET to_state='pending' WHERE reminder_key=?", (record.key,))
        self.inbound()
        self.execute("UPDATE channel_observations SET message_at=? WHERE event_key=?",
                     ((NOW + timedelta(hours=1)).isoformat(), event_key(1)))
        self.assert_safe_error(lambda: self.store.last_inbound(RECIPIENT))
        self.assert_safe_error(ReminderStore(self.path, clock=lambda: NOW).initialize)

    def test_private_dataclass_representations_do_not_expose_content_or_binding(self):
        record = self.plan()
        claim = self.reserve(record)
        observation = self.inbound()
        for value in (self.store.config(), record, claim, observation):
            with self.subTest(value_type=type(value).__name__):
                for sensitive in (PRIVATE_TITLE, CALENDAR, RECIPIENT):
                    self.assertNotIn(sensitive, repr(value))

    def test_inbound_preserves_original_seconds_utc_separately_from_received_time(self):
        original = NOW - timedelta(hours=2)
        received = NOW.astimezone(timezone(timedelta(hours=-3)))
        observation = self.inbound(message_at=original, received_at=received)
        self.assertTrue(observation.valid)
        self.assertEqual(observation.message_at, original)
        self.assertEqual(observation.received_at, NOW)
        self.assertEqual(observation.received_at.utcoffset(), timedelta(0))
        self.assertEqual(observation.provenance, "sandbox_callback_capacity")
        self.assertEqual(self.store.last_inbound(RECIPIENT), observation)
        restarted = ReminderStore(self.path, clock=lambda: NOW)
        self.assertEqual(restarted.last_inbound(RECIPIENT), observation)

    def test_inbound_dedup_retains_first_observation_even_when_first_timestamp_invalid(self):
        first = self.inbound(provider_timestamp="not-seconds")
        self.assertFalse(first.valid)
        self.assertIsNone(self.store.last_inbound(RECIPIENT))
        self.now = NOW + timedelta(minutes=1)
        duplicate = self.inbound(message_at=NOW, received_at=NOW + timedelta(minutes=1))
        self.assertEqual(duplicate, first)
        self.assertIsNone(self.store.last_inbound(RECIPIENT))
        cross_recipient_duplicate = self.inbound(recipient_key=OTHER_RECIPIENT, message_at=NOW)
        self.assertEqual(cross_recipient_duplicate, first)
        self.assertIsNone(self.store.last_inbound(OTHER_RECIPIENT))

    def test_inbound_invalid_future_nontext_and_synthetic_observations_never_open_window(self):
        cases = [
            {"provider_timestamp": None}, {"provider_timestamp": ""},
            {"provider_timestamp": "1.5"}, {"provider_timestamp": "+1"},
            {"provider_timestamp": "0"}, {"provider_timestamp": "-1"},
            {"provider_timestamp": True}, {"provider_timestamp": "١٩٠٧٩٢٨٠٠٠"},
            {"provider_timestamp": str(int(NOW.timestamp() * 1000))},
            {"provider_timestamp": str(int(NOW.timestamp()) + 1)},
            {"message_type": "status_delivered"}, {"message_type": "image"},
            {"provenance": "synthetic"},
        ]
        for number, changes in enumerate(cases, 1):
            with self.subTest(changes=changes):
                observation = self.inbound(number, **changes)
                self.assertFalse(observation.valid)
                self.assertIsNone(self.store.last_inbound(RECIPIENT))

    def test_inbound_out_of_order_does_not_reduce_or_artificially_extend_window(self):
        newest = NOW - timedelta(hours=1)
        self.inbound(1, message_at=newest)
        self.now = NOW + timedelta(hours=4)
        self.inbound(2, message_at=NOW - timedelta(hours=3), received_at=NOW + timedelta(hours=4))
        self.assertEqual(self.store.last_inbound(RECIPIENT).message_at, newest)
        self.now = NOW + timedelta(hours=5)
        self.inbound(3, message_at=NOW - timedelta(minutes=30), received_at=NOW + timedelta(hours=5))
        self.assertEqual(self.store.last_inbound(RECIPIENT).message_at, NOW - timedelta(minutes=30))

    def test_inbound_recipient_binding_is_isolated_and_configuration_does_not_backfill(self):
        self.configure()
        self.assertIsNone(self.store.last_inbound(RECIPIENT))
        self.inbound()
        self.configure(recipient_key=OTHER_RECIPIENT)
        self.assertIsNone(self.store.last_inbound(OTHER_RECIPIENT))
        other_instant = NOW - timedelta(minutes=1)
        self.inbound(2, recipient_key=OTHER_RECIPIENT, message_at=other_instant)
        self.assertEqual(self.store.last_inbound(OTHER_RECIPIENT).message_at, other_instant)
        self.assertEqual(self.store.last_inbound(RECIPIENT).message_at, NOW - timedelta(minutes=5))


if __name__ == "__main__":
    unittest.main()
