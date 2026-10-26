"""Persistência de identidades e recuperação de operações, sem rede ou dados reais."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

from app.database import CreationIdentityError, CreationRecord, Database, SCHEMA_VERSION


CALENDAR = "calendar@example.invalid"
OPERATION = "a" * 64
REQUEST = "b" * 64
FINGERPRINT = "c" * 64
EVENT = "genius" + "d" * 64


class CreationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / "agenda.db")
        self.db.initialize()

    def reserve(self, **changes):
        arguments = dict(
            calendar_id=CALENDAR, operation_hash=OPERATION, request_hash=REQUEST,
            event_id=EVENT, fingerprint=FINGERPRINT,
        )
        arguments.update(changes)
        return self.db.reserve_creation(**arguments)

    def rows(self, table):
        with self.db.connection() as connection:
            return [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]

    def test_schema_is_created_and_reinitialization_preserves_pending_record(self):
        expected = self.reserve()
        self.db.set_metadata("calendar_binding", "keep")
        self.db.initialize()
        self.assertEqual(SCHEMA_VERSION, "2")
        self.assertEqual(self.db.get_metadata("schema_version"), "2")
        self.assertEqual(self.db.get_metadata("calendar_binding"), "keep")
        self.assertEqual(self.db.get_creation(CALENDAR, REQUEST), expected)

    def test_legacy_schema_migrates_atomically_and_preserves_metadata(self):
        legacy = Database(Path(self.temp.name) / "legacy.db")
        with legacy.connection() as connection:
            connection.execute(
                "CREATE TABLE app_metadata "
                "(key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            connection.executemany(
                "INSERT INTO app_metadata VALUES (?, ?, ?)",
                [("schema_version", "1", "before"), ("calendar_binding", "keep", "original")],
            )
        legacy.initialize()
        self.assertEqual(legacy.get_metadata("schema_version"), "2")
        with legacy.connection() as connection:
            metadata = connection.execute(
                "SELECT value, updated_at FROM app_metadata WHERE key = 'calendar_binding'"
            ).fetchone()
            self.assertEqual(tuple(metadata), ("keep", "original"))
            tables = {row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )}
        self.assertTrue({"creation_requests", "creation_operations"}.issubset(tables))

    def test_failed_migration_rolls_back_new_tables_and_schema_version(self):
        legacy = Database(Path(self.temp.name) / "failed-migration.db")
        with legacy.connection() as connection:
            connection.execute(
                "CREATE TABLE app_metadata "
                "(key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            connection.execute("INSERT INTO app_metadata VALUES ('schema_version', '1', 'before')")
            connection.execute("CREATE TABLE unrelated (value TEXT)")
            # Um índice com este nome força erro após criar a primeira tabela nova.
            connection.execute("CREATE INDEX creation_operations ON unrelated (value)")
        with self.assertRaises(sqlite3.OperationalError):
            legacy.initialize()
        self.assertEqual(legacy.get_metadata("schema_version"), "1")
        with legacy.connection() as connection:
            self.assertIsNone(connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'creation_requests'"
            ).fetchone())

    def test_unknown_version_is_rejected_without_adding_tables_or_changing_metadata(self):
        unknown = Database(Path(self.temp.name) / "future.db")
        with unknown.connection() as connection:
            connection.execute(
                "CREATE TABLE app_metadata "
                "(key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            connection.execute("INSERT INTO app_metadata VALUES ('schema_version', '99', 'before')")
        with self.assertRaisesRegex(RuntimeError, "incompatível"):
            unknown.initialize()
        self.assertEqual(unknown.get_metadata("schema_version"), "99")
        with unknown.connection() as connection:
            names = [row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )]
        self.assertEqual(names, ["app_metadata"])

    def test_reservation_is_pending_durable_and_immutable(self):
        record = self.reserve()
        self.assertIsInstance(record, CreationRecord)
        self.assertEqual(record.state, "pending")
        self.assertEqual(Database(self.db.path).get_creation(CALENDAR, REQUEST), record)
        with self.assertRaises(FrozenInstanceError):
            record.state = "confirmed"
        self.assertEqual(self.reserve(), record)
        self.assertEqual(len(self.rows("creation_requests")), 1)
        self.assertEqual(len(self.rows("creation_operations")), 1)

    def test_same_request_with_new_operation_keeps_original_id_and_fingerprint(self):
        original = self.reserve()
        repeated = self.reserve(
            operation_hash="e" * 64, event_id="genius" + "f" * 64,
            fingerprint="1" * 64,
        )
        self.assertEqual(repeated, original)
        self.assertEqual(len(self.rows("creation_requests")), 1)
        self.assertEqual(len(self.rows("creation_operations")), 2)

    def test_confirmed_request_is_also_reused_by_new_operation(self):
        record = self.reserve()
        self.db.confirm_creation(record)
        repeated = self.reserve(operation_hash="e" * 64, event_id="other", fingerprint="1" * 64)
        self.assertEqual(repeated, replace(record, state="confirmed"))
        self.assertEqual(len(self.rows("creation_requests")), 1)

    def test_same_operation_cannot_change_request_and_leaves_no_partial_alias(self):
        original = self.reserve()
        with self.assertRaisesRegex(CreationIdentityError, "outra solicitação"):
            self.reserve(request_hash="1" * 64, event_id="other", fingerprint="2" * 64)
        self.assertEqual(self.db.get_creation(CALENDAR, REQUEST), original)
        self.assertIsNone(self.db.get_creation(CALENDAR, "1" * 64))
        self.assertEqual(len(self.rows("creation_operations")), 1)

    def test_alias_operation_cannot_later_change_request(self):
        self.reserve()
        self.reserve(operation_hash="e" * 64)
        with self.assertRaises(CreationIdentityError):
            self.reserve(operation_hash="e" * 64, request_hash="1" * 64, event_id="other")
        self.assertEqual(len(self.rows("creation_requests")), 1)
        self.assertEqual(len(self.rows("creation_operations")), 2)

    def test_calendars_have_independent_request_and_operation_namespaces(self):
        first = self.reserve()
        second = self.reserve(calendar_id="second@example.invalid")
        self.assertNotEqual(first.calendar_id, second.calendar_id)
        self.assertEqual(len(self.rows("creation_requests")), 2)
        self.assertEqual(len(self.rows("creation_operations")), 2)

    def test_event_id_collision_is_safe_and_rolls_back_operation(self):
        original = self.reserve()
        with self.assertRaises(CreationIdentityError) as raised:
            self.reserve(operation_hash="1" * 64, request_hash="2" * 64)
        self.assertNotIn(CALENDAR, str(raised.exception))
        self.assertNotIn(EVENT, str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)
        self.assertEqual(self.db.get_creation(CALENDAR, REQUEST), original)
        self.assertEqual(len(self.rows("creation_requests")), 1)
        self.assertEqual(len(self.rows("creation_operations")), 1)

    def test_get_missing_record_returns_none_and_does_not_write(self):
        self.assertIsNone(self.db.get_creation(CALENDAR, REQUEST))
        self.assertEqual(self.rows("creation_requests"), [])
        self.assertEqual(self.rows("creation_operations"), [])

    def test_confirmation_with_own_connection_is_durable_and_repeatable(self):
        original = self.reserve()
        self.assertIsNone(self.db.confirm_creation(original))
        self.db.confirm_creation(original)
        reopened = Database(self.db.path)
        self.assertEqual(reopened.get_creation(CALENDAR, REQUEST), replace(original, state="confirmed"))

    def test_confirmation_uses_supplied_lock_and_commits_only_when_caller_finishes(self):
        original = self.reserve()
        with self.db.calendar_write_lock() as connection:
            self.assertIs(connection.row_factory, sqlite3.Row)
            self.assertTrue(connection.in_transaction)
            self.db.confirm_creation(original, connection=connection)
            self.assertTrue(connection.in_transaction)
            self.assertEqual(
                self.db.get_creation(CALENDAR, REQUEST, connection=connection).state,
                "confirmed",
            )
            self.assertEqual(Database(self.db.path).get_creation(CALENDAR, REQUEST).state, "pending")
        self.assertEqual(self.db.get_creation(CALENDAR, REQUEST).state, "confirmed")

    def test_rollback_of_confirmation_preserves_durable_pending_identity(self):
        original = self.reserve()
        with self.assertRaisesRegex(RuntimeError, "external result uncertain"):
            with self.db.calendar_write_lock() as connection:
                self.db.confirm_creation(original, connection=connection)
                raise RuntimeError("external result uncertain")
        reopened = Database(self.db.path)
        self.assertEqual(reopened.get_creation(CALENDAR, REQUEST), original)
        self.assertEqual(len(self.rows("creation_operations")), 1)

    def test_confirmation_rejects_missing_or_changed_identity(self):
        original = self.reserve()
        variants = [
            replace(original, calendar_id="other@example.invalid"),
            replace(original, request_hash="1" * 64),
            replace(original, event_id="other"),
            replace(original, fingerprint="2" * 64),
        ]
        for invalid in variants:
            with self.subTest(invalid=invalid):
                with self.assertRaises(CreationIdentityError):
                    self.db.confirm_creation(invalid)
                self.assertEqual(self.db.get_creation(CALENDAR, REQUEST), original)

    def test_invalid_arguments_are_rejected_without_storing_any_rows(self):
        for field, invalid in [
            ("calendar_id", ""), ("calendar_id", " private "), ("calendar_id", None),
            ("event_id", ""), ("event_id", 123),
            ("operation_hash", "bad"), ("request_hash", "z" * 64),
            ("fingerprint", "c" * 63), ("fingerprint", None),
        ]:
            with self.subTest(field=field, invalid=invalid):
                with self.assertRaises(CreationIdentityError):
                    self.reserve(**{field: invalid})
        self.assertEqual(self.rows("creation_requests"), [])
        self.assertEqual(self.rows("creation_operations"), [])

    def test_invalid_confirmation_record_and_state_are_rejected(self):
        original = self.reserve()
        for invalid in (None, {}, replace(original, state="deleted"), replace(original, state=[])):
            with self.subTest(invalid=invalid):
                with self.assertRaises(CreationIdentityError):
                    self.db.confirm_creation(invalid)
        self.assertEqual(self.db.get_creation(CALENDAR, REQUEST), original)

    def test_hash_case_is_canonicalized_to_avoid_aliases_for_same_digest(self):
        original = self.reserve()
        repeated = self.reserve(
            operation_hash=OPERATION.upper(), request_hash=REQUEST.upper(),
            fingerprint=FINGERPRINT.upper(),
        )
        self.assertEqual(original, repeated)
        self.assertEqual(self.db.get_creation(CALENDAR, REQUEST.upper()), original)
        self.assertEqual(len(self.rows("creation_operations")), 1)

    def test_foreign_key_prevents_an_alias_without_request(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.calendar_write_lock() as connection:
                connection.execute(
                    "INSERT INTO creation_operations VALUES (?, ?, ?)",
                    (CALENDAR, OPERATION, REQUEST),
                )
        self.assertEqual(self.rows("creation_operations"), [])

    def test_regular_connection_also_enforces_creation_foreign_key(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.connection() as connection:
                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                connection.execute(
                    "INSERT INTO creation_operations VALUES (?, ?, ?)",
                    (CALENDAR, OPERATION, REQUEST),
                )
        self.assertEqual(self.rows("creation_operations"), [])

    def test_orphaned_operation_from_legacy_writer_cannot_allocate_new_identity(self):
        # Representa um arquivo alterado por um escritor externo sem foreign_keys.
        with sqlite3.connect(self.db.path, autocommit=True) as external:
            external.execute(
                "INSERT INTO creation_operations VALUES (?, ?, ?)",
                (CALENDAR, OPERATION, REQUEST),
            )
        external.close()
        with self.assertRaisesRegex(CreationIdentityError, "sem a identidade"):
            self.reserve()
        self.assertIsNone(self.db.get_creation(CALENDAR, REQUEST))
        self.assertEqual(len(self.rows("creation_operations")), 1)

    def test_invalid_database_state_is_rejected_by_check_constraint(self):
        original = self.reserve()
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.calendar_write_lock() as connection:
                connection.execute(
                    "UPDATE creation_requests SET state = 'deleted' WHERE request_hash = ?",
                    (REQUEST,),
                )
        self.assertEqual(self.db.get_creation(CALENDAR, REQUEST), original)

    def test_concurrent_reservations_share_one_identity_and_persist_both_aliases(self):
        barrier = Barrier(2)

        def reserve_contender(suffix):
            barrier.wait(timeout=5)
            return Database(self.db.path).reserve_creation(
                CALENDAR, suffix * 64, REQUEST, "genius" + suffix * 64, suffix * 64,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(reserve_contender, suffix) for suffix in ("e", "f")]
            records = [future.result(timeout=10) for future in futures]
        self.assertEqual(records[0], records[1])
        self.assertEqual(len(self.rows("creation_requests")), 1)
        self.assertEqual(len(self.rows("creation_operations")), 2)

    def test_concurrent_changed_requests_cannot_share_same_operation(self):
        barrier = Barrier(2)

        def reserve_contender(suffix):
            barrier.wait(timeout=5)
            try:
                return Database(self.db.path).reserve_creation(
                    CALENDAR, OPERATION, suffix * 64, "genius" + suffix * 64,
                    suffix * 64,
                )
            except CreationIdentityError as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(reserve_contender, suffix) for suffix in ("e", "f")]
            results = [future.result(timeout=10) for future in futures]
        self.assertEqual(sum(isinstance(value, CreationRecord) for value in results), 1)
        self.assertEqual(sum(isinstance(value, CreationIdentityError) for value in results), 1)
        self.assertEqual(len(self.rows("creation_requests")), 1)
        self.assertEqual(len(self.rows("creation_operations")), 1)

    def test_storage_contains_only_hashes_ids_states_and_timestamps(self):
        self.reserve()
        with self.db.connection() as connection:
            columns = [row["name"] for row in connection.execute("PRAGMA table_info(creation_requests)")]
            self.assertEqual(columns, [
                "calendar_id", "request_hash", "event_id", "fingerprint", "state",
                "created_at", "updated_at",
            ])
            operation_columns = [row["name"] for row in connection.execute(
                "PRAGMA table_info(creation_operations)"
            )]
            self.assertEqual(operation_columns, ["calendar_id", "operation_hash", "request_hash"])
        self.assertEqual(set(CreationRecord.__dataclass_fields__), {
            "calendar_id", "request_hash", "event_id", "fingerprint", "state",
        })


if __name__ == "__main__":
    unittest.main()
