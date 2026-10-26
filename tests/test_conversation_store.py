"""Contexto de consultas: isolamento, validade, CAS e minimização sem rede."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from app.conversation_store import (
    ConversationError, ConversationSnapshot, ConversationStore, QueryMemory, validate_memory,
)


OWNER = sha256(b"fictional-api-key-owner-one").hexdigest()
OTHER_OWNER = sha256(b"fictional-api-key-owner-two").hexdigest()
MEMORY = QueryMemory(
    last_list={"date": "2026-10-24", "period": "week", "time_zone": "America/Recife"},
    selected_event_id="fixture-event_20261024T120000Z", selected_time_zone="America/Recife",
)


class ConversationMemoryTests(unittest.TestCase):
    def assert_invalid(self, memory):
        with self.assertRaises(ConversationError) as raised:
            validate_memory(memory)
        self.assertEqual(raised.exception.code, "invalid_conversation")
        self.assertEqual(raised.exception.status_code, 422)

    def test_empty_memory_and_defaults(self):
        self.assertEqual(validate_memory(QueryMemory()), QueryMemory(None, None, None))

    def test_validation_copies_list_and_preserves_opaque_event_id(self):
        original = QueryMemory(dict(MEMORY.last_list), MEMORY.selected_event_id, MEMORY.selected_time_zone)
        result = validate_memory(original)
        self.assertEqual(result, original)
        self.assertIsNot(result, original)
        self.assertIsNot(result.last_list, original.last_list)
        original.last_list["date"] = "2027-01-01"
        self.assertEqual(result.last_list["date"], "2026-10-24")
        self.assertEqual(result.selected_event_id, MEMORY.selected_event_id)

    def test_date_period_zone_and_shape_are_strict(self):
        invalid = [None, {}, {"last_list": None}, QueryMemory(last_list=[]),
                   QueryMemory(last_list={}), QueryMemory(last_list={**MEMORY.last_list, "title": "private"})]
        for key, value in (
            ("date", "2026-02-30"), ("date", "20261024"), ("date", "2026-W43-6"),
            ("date", "2026-1-01"), ("date", 20261024), ("period", "year"),
            ("period", []), ("time_zone", "private/not-a-zone"), ("time_zone", "../UTC"),
            ("time_zone", 1), ("time_zone", ""),
        ):
            invalid.append(QueryMemory(last_list={**MEMORY.last_list, key: value}))
        for memory in invalid:
            with self.subTest(memory=repr(memory)):
                self.assert_invalid(memory)

    def test_selection_is_pair_and_never_accepts_whitespace_or_controls(self):
        for event_id, zone in (
            ("fixture", None), (None, "UTC"), ("", "UTC"), ("private event", "UTC"),
            ("fixture\n", "UTC"), ("fixture\0", "UTC"), ("fixture\u200b", "UTC"),
            ("x" * 1025, "UTC"), ("fixture", "not-a-zone"), (123, "UTC"),
        ):
            self.assert_invalid(QueryMemory(selected_event_id=event_id, selected_time_zone=zone))
        self.assertEqual(validate_memory(QueryMemory(selected_event_id="MixedCase_Instance", selected_time_zone="UTC")),
                         QueryMemory(selected_event_id="MixedCase_Instance", selected_time_zone="UTC"))

    def test_frozen_objects_and_private_repr(self):
        snapshot = ConversationSnapshot(0, MEMORY)
        with self.assertRaises(FrozenInstanceError):
            snapshot.revision = 1
        with self.assertRaises(FrozenInstanceError):
            MEMORY.selected_event_id = "changed"
        for value in (repr(MEMORY), repr(snapshot)):
            self.assertNotIn(MEMORY.selected_event_id, value)
            self.assertNotIn("2026-10-24", value)
            self.assertNotIn("America/Recife", value)

    def test_errors_are_safe_and_cannot_inject_messages(self):
        statuses = {"conversation_not_found": 404, "conversation_conflict": 409,
                    "conversation_unavailable": 503, "conversation_capacity": 503,
                    "invalid_conversation": 422}
        for code, status in statuses.items():
            error = ConversationError(code)
            self.assertEqual((error.code, error.status_code, error.http_status), (code, status, status))
        self.assertEqual(ConversationError("secret SQL fixture").code, "conversation_unavailable")
        self.assertNotIn("secret", str(ConversationError("secret SQL fixture")))


class ConversationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "data" / "conversations.sqlite3"
        self.now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
        self.store = ConversationStore(self.path, clock=lambda: self.now)

    def execute(self, sql, params=()):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as connection:
            return connection.execute(sql, params).fetchall()

    def rows(self):
        return self.execute("SELECT binding_hash,revision,memory_json,created_at,updated_at,expires_at "
                            "FROM conversation_contexts ORDER BY binding_hash")

    def error(self, code, callback):
        with self.assertRaises(ConversationError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(str(raised.exception), str(ConversationError(code)))

    def test_constructor_and_invalid_binding_do_not_touch_disk(self):
        with patch("app.conversation_store.sqlite3.connect") as connect:
            store = ConversationStore(self.path)
            for owner in (None, "", "a" * 63, "a" * 65, "A" * 64, "g" * 64, OWNER + "\n"):
                self.error("invalid_conversation", lambda: store.create(owner))
                self.error("invalid_conversation", lambda: store.load(owner, str(uuid4())))
                self.error("invalid_conversation", lambda: store.save(owner, str(uuid4()), 0, MEMORY))
                self.error("invalid_conversation", lambda: store.delete(owner, str(uuid4())))
            identifier = str(uuid4())
            for value in (None, "", UUID(identifier), identifier.upper(), identifier.replace("-", ""),
                          "{" + identifier + "}", "4cce9147-9d61-1f1a-b4b7-295cf3f32ade"):
                self.error("invalid_conversation", lambda: store.load(OWNER, value))
                self.error("invalid_conversation", lambda: store.save(OWNER, value, 0, MEMORY))
                self.error("invalid_conversation", lambda: store.delete(OWNER, value))
            connect.assert_not_called()
        self.assertFalse(self.path.parent.exists())

    def test_invalid_memory_or_revision_is_rejected_before_io(self):
        identifier = str(uuid4())
        with patch("app.conversation_store.sqlite3.connect") as connect:
            for memory in (None, {}, QueryMemory(last_list={"private": "raw text"})):
                self.error("invalid_conversation", lambda: self.store.save(OWNER, identifier, 0, memory))
            for revision in (True, None, "0", -1, 9223372036854775806):
                self.error("invalid_conversation", lambda: self.store.save(OWNER, identifier, revision, MEMORY))
            connect.assert_not_called()
        self.assertFalse(self.path.parent.exists())

    def test_invalid_options_and_clock_fail_safely(self):
        for options in ({"ttl_seconds": 0}, {"ttl_seconds": True}, {"ttl_seconds": -1},
                        {"ttl_seconds": float("inf")}, {"max_entries": 0}, {"max_entries": True},
                        {"clock": "not callable"}):
            self.error("invalid_conversation", lambda: ConversationStore(self.path, **options))
        for now in (None, datetime(2026, 1, 1), datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1)))):
            store = ConversationStore(self.path, clock=lambda: now)
            self.error("conversation_unavailable", lambda: store.create(OWNER))
        self.assertFalse(self.path.parent.exists())

    def test_created_uuid_is_canonical_v4_and_empty_revision_zero_is_durable(self):
        identifier = self.store.create(OWNER)
        self.assertEqual(str(UUID(identifier)), identifier)
        self.assertEqual(UUID(identifier).version, 4)
        restarted = ConversationStore(self.path, clock=lambda: self.now)
        self.assertEqual(restarted.load(OWNER, identifier), ConversationSnapshot(0, QueryMemory()))
        row, = self.rows()
        self.assertEqual(row[1], 0)
        self.assertEqual(row[3], self.now.isoformat())
        self.assertEqual(row[4], row[3])
        self.assertEqual(datetime.fromisoformat(row[5]), self.now + timedelta(minutes=30))

    def test_save_increments_cas_and_survives_restart(self):
        identifier = self.store.create(OWNER)
        self.assertIsNone(self.store.save(OWNER, identifier, 0, MEMORY))
        restarted = ConversationStore(self.path, clock=lambda: self.now)
        self.assertEqual(restarted.load(OWNER, identifier), ConversationSnapshot(1, MEMORY))
        self.store.save(OWNER, identifier, 1, QueryMemory())
        self.assertEqual(restarted.load(OWNER, identifier), ConversationSnapshot(2, QueryMemory()))

    def test_load_returns_independent_memory(self):
        identifier = self.store.create(OWNER)
        self.store.save(OWNER, identifier, 0, MEMORY)
        snapshot = self.store.load(OWNER, identifier)
        snapshot.memory.last_list["date"] = "2027-01-01"
        self.assertEqual(self.store.load(OWNER, identifier).memory, MEMORY)

    def test_wrong_owner_missing_expired_all_share_not_found(self):
        identifier = self.store.create(OWNER)
        before = self.rows()
        for owner, selected in ((OTHER_OWNER, identifier), (OWNER, str(uuid4()))):
            self.error("conversation_not_found", lambda: self.store.load(owner, selected))
            self.error("conversation_not_found", lambda: self.store.save(owner, selected, 0, MEMORY))
            self.error("conversation_not_found", lambda: self.store.delete(owner, selected))
        self.assertEqual(self.rows(), before)
        self.now += timedelta(seconds=1800)
        self.error("conversation_not_found", lambda: self.store.load(OWNER, identifier))
        self.assertEqual(self.rows(), [])

    def test_delete_does_not_affect_another_conversation(self):
        identifier = self.store.create(OWNER)
        other = self.store.create(OTHER_OWNER)
        self.assertIsNone(self.store.delete(OWNER, identifier))
        self.error("conversation_not_found", lambda: self.store.load(OWNER, identifier))
        self.assertEqual(self.store.load(OTHER_OWNER, other).revision, 0)

    def test_load_does_not_extend_ttl_but_save_does(self):
        identifier = self.store.create(OWNER)
        before = self.rows()
        self.now += timedelta(seconds=1700)
        self.store.load(OWNER, identifier)
        self.assertEqual(self.rows(), before)
        self.store.save(OWNER, identifier, 0, MEMORY)
        self.now += timedelta(seconds=1799)
        self.assertEqual(self.store.load(OWNER, identifier).revision, 1)
        self.now += timedelta(seconds=1)
        self.error("conversation_not_found", lambda: self.store.load(OWNER, identifier))

    def test_expiry_uses_clock_after_transaction_acquisition(self):
        identifier = self.store.create(OWNER)
        ticks = iter((self.now + timedelta(seconds=1799), self.now + timedelta(seconds=1800)))
        self.store._clock = lambda: next(ticks)
        self.error("conversation_not_found", lambda: self.store.load(OWNER, identifier))
        self.assertEqual(self.rows(), [])

    def test_deletion_removes_selected_id_from_sqlite_free_space(self):
        identifier = self.store.create(OWNER)
        self.store.save(OWNER, identifier, 0, MEMORY)
        self.assertIn(MEMORY.selected_event_id.encode("ascii"), self.path.read_bytes())
        self.store.delete(OWNER, identifier)
        self.assertNotIn(MEMORY.selected_event_id.encode("ascii"), self.path.read_bytes())

    def test_explicit_purge_and_creation_reclaim_expired_capacity(self):
        store = ConversationStore(self.path, clock=lambda: self.now, max_entries=1)
        identifier = store.create(OWNER)
        self.error("conversation_capacity", lambda: store.create(OTHER_OWNER))
        self.assertEqual(store.load(OWNER, identifier).revision, 0)
        self.now += timedelta(seconds=1800)
        replacement = store.create(OTHER_OWNER)
        self.assertEqual(len(self.rows()), 1)
        self.error("conversation_not_found", lambda: store.load(OWNER, identifier))
        self.assertEqual(store.purge_expired(), 0)
        self.now += timedelta(seconds=1800)
        self.assertEqual(store.purge_expired(), 1)
        self.assertEqual(store.purge_expired(), 0)
        self.error("conversation_not_found", lambda: store.load(OTHER_OWNER, replacement))

    def test_every_operation_purges_expired_rows_even_on_not_found(self):
        for operation in ("load", "save", "delete", "create"):
            with self.subTest(operation=operation):
                self.store.create(OWNER)
                self.now += timedelta(seconds=1800)
                if operation == "create":
                    live = self.store.create(OTHER_OWNER)
                    self.assertEqual(len(self.rows()), 1)
                    self.store.delete(OTHER_OWNER, live)
                elif operation == "save":
                    self.error("conversation_not_found", lambda: self.store.save(OTHER_OWNER, str(uuid4()), 0, MEMORY))
                else:
                    self.error("conversation_not_found", lambda: getattr(self.store, operation)(OTHER_OWNER, str(uuid4())))
                self.assertEqual(self.rows(), [])

    def test_stale_revision_cannot_overwrite_or_renew_ttl(self):
        identifier = self.store.create(OWNER)
        self.store.save(OWNER, identifier, 0, MEMORY)
        before = self.rows()
        self.now += timedelta(seconds=60)
        self.error("conversation_conflict", lambda: self.store.save(OWNER, identifier, 0, QueryMemory()))
        self.assertEqual(self.rows(), before)

    def test_backward_clock_cannot_save_invalid_chronology(self):
        identifier = self.store.create(OWNER)
        before = self.rows()
        self.now -= timedelta(seconds=1)
        self.error("conversation_unavailable", lambda: self.store.save(OWNER, identifier, 0, MEMORY))
        self.assertEqual(self.rows(), before)

    def test_database_contains_only_binding_and_minimum_memory(self):
        identifier = self.store.create(OWNER)
        self.store.save(OWNER, identifier, 0, MEMORY)
        raw = self.path.read_bytes()
        for value in (identifier, OWNER, OTHER_OWNER, "fictional-api-key-owner-one", "+5585999990000",
                      "mensagem privada", "título privado", "descrição privada"):
            self.assertNotIn(value.encode("utf-8"), raw)
        row, = self.rows()
        self.assertEqual(len(row[0]), 64)
        self.assertNotEqual(row[0], OWNER)
        self.assertEqual(json.loads(row[2]), {
            "last_list": MEMORY.last_list, "selected_event_id": MEMORY.selected_event_id,
            "selected_time_zone": MEMORY.selected_time_zone,
            "pending_request": None, "confirmation": None,
        })
        self.assertCountEqual(self.execute("SELECT name FROM sqlite_master WHERE type='table'"),
                              [("conversation_metadata",), ("conversation_contexts",)])

    def test_no_connection_or_lock_is_held_between_calls(self):
        identifier = self.store.create(OWNER)
        for action in (lambda: self.store.load(OWNER, identifier),
                       lambda: self.store.save(OWNER, identifier, 0, MEMORY)):
            action()
            with closing(sqlite3.connect(self.path, timeout=0, isolation_level=None)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.rollback()

    def test_foreign_or_corrupt_database_is_never_overwritten(self):
        self.path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("CREATE TABLE agenda_private(value TEXT)")
            connection.commit()
        for content in (self.path.read_bytes(), b"private fixture not a SQLite database"):
            self.path.write_bytes(content)
            self.error("conversation_unavailable", lambda: self.store.create(OWNER))
            self.assertEqual(self.path.read_bytes(), content)

    def test_version_and_schema_tampering_fail_after_initialization(self):
        identifier = self.store.create(OWNER)
        self.execute("UPDATE conversation_metadata SET value='4'")
        before = self.path.read_bytes()
        self.error("conversation_unavailable", lambda: self.store.load(OWNER, identifier))
        self.assertEqual(self.path.read_bytes(), before)
        self.error("conversation_unavailable", lambda: ConversationStore(self.path).create(OWNER))
        self.execute("UPDATE conversation_metadata SET value='2'")
        self.execute("CREATE VIEW unrelated AS SELECT 1")
        self.error("conversation_unavailable", lambda: self.store.load(OWNER, identifier))

    def test_corrupt_saved_rows_are_rejected_even_by_initialized_instance(self):
        identifier = self.store.create(OWNER)
        saved, = self.rows()
        for column, invalid, original in (
            ("binding_hash", "raw-owner", saved[0]), ("memory_json", '{"raw":"private"}', saved[2]),
            ("memory_json", '{"last_list":null,"selected_event_id":null,"selected_time_zone":null,"extra":null}', saved[2]),
            ("memory_json", "[" * 1100, saved[2]), ("created_at", "2026-10-24T12:00:00", saved[3]),
            ("updated_at", "2026-10-24T13:00:00+00:00", saved[4]),
            ("expires_at", saved[3], saved[5]),
        ):
            with self.subTest(column=column, invalid=invalid[:30]):
                self.execute(f"UPDATE conversation_contexts SET {column}=?", (invalid,))
                self.error("conversation_unavailable", lambda: self.store.load(OWNER, identifier))
                self.execute(f"UPDATE conversation_contexts SET {column}=?", (original,))

    def test_commit_failure_rolls_back_creation_and_save(self):
        self.store.initialize()
        real_connect = sqlite3.connect

        class FailingCommit(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError("private SQL fixture")

        def connect(*args, **kwargs):
            return real_connect(*args, factory=FailingCommit, **kwargs)

        with patch("app.conversation_store.sqlite3.connect", side_effect=connect):
            self.error("conversation_unavailable", lambda: self.store.create(OWNER))
        self.assertEqual(self.rows(), [])
        identifier = self.store.create(OWNER)
        before = self.rows()
        with patch("app.conversation_store.sqlite3.connect", side_effect=connect):
            self.error("conversation_unavailable", lambda: self.store.save(OWNER, identifier, 0, MEMORY))
        self.assertEqual(self.rows(), before)

    def test_database_lock_fails_closed(self):
        self.store.initialize()
        with closing(sqlite3.connect(self.path, isolation_level=None)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            with patch("app.conversation_store.BUSY_TIMEOUT_MS", 1):
                self.error("conversation_unavailable", lambda: self.store.create(OWNER))
            connection.rollback()
        self.assertEqual(self.rows(), [])

    def test_cas_between_threads_has_one_winner_and_preserves_other_memory(self):
        identifier = self.store.create(OWNER)
        barrier = Barrier(2)

        def save(_):
            store = ConversationStore(self.path, clock=lambda: self.now)
            barrier.wait(timeout=5)
            try:
                store.save(OWNER, identifier, 0, MEMORY)
                return "saved"
            except ConversationError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(save, range(2))), ["saved", "conversation_conflict"])
        self.assertEqual(self.store.load(OWNER, identifier), ConversationSnapshot(1, MEMORY))

    def test_initialization_and_capacity_are_atomic_between_instances(self):
        barrier = Barrier(2)

        def create(_):
            store = ConversationStore(self.path, max_entries=1, clock=lambda: self.now)
            barrier.wait(timeout=5)
            try:
                store.create(OWNER)
                return "created"
            except ConversationError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(create, range(2))), ["created", "conversation_capacity"])
        self.assertEqual(len(self.rows()), 1)

    def test_cas_is_atomic_between_processes(self):
        identifier = self.store.create(OWNER)
        script = (
            "import sys;from datetime import datetime;"
            "from app.conversation_store import ConversationStore,QueryMemory,ConversationError;"
            "store=ConversationStore(sys.argv[1],clock=lambda:datetime.fromisoformat(sys.argv[4]));"
            "sys.stdin.readline();\n"
            "try:\n store.save(sys.argv[2],sys.argv[3],0,QueryMemory());print('saved')\n"
            "except ConversationError as error:\n print(error.code)\n"
        )
        processes = []
        try:
            for _ in range(2):
                processes.append(subprocess.Popen(
                    [sys.executable, "-B", "-c", script, str(self.path), OWNER, identifier, self.now.isoformat()],
                    cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                ))
            for process in processes:
                process.stdin.write("start\n")
                process.stdin.flush()
            results = []
            for process in processes:
                stdout, stderr = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0, stderr)
                self.assertEqual(stderr, "")
                results.append(stdout.strip())
            self.assertCountEqual(results, ["saved", "conversation_conflict"])
            self.assertEqual(self.store.load(OWNER, identifier).revision, 1)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
