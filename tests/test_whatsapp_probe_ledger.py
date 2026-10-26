"""Ensaios locais do ledger; nenhum provedor, destinatário ou inbox/outbox ativo."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from app.whatsapp_probe_ledger import ProbeLedger, ProbeLedgerError, ProbeReservation


CASE = "image_link_v1"
PAYLOAD = sha256(b"public-fictional-fixture-image-v1").hexdigest()
MESSAGE = sha256(b"360dialog:fixture-provider-id").hexdigest()
SAFE_ERROR = "Estado local do ensaio indisponível ou inconsistente."


class ProbeLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "data" / "probes.sqlite3"
        self.ledger = ProbeLedger(self.path)
        self.operation_id = str(uuid4())

    def reserve(self):
        return self.ledger.reserve(self.operation_id, CASE, PAYLOAD)

    def rows(self):
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute(
                "SELECT operation_id,case_name,payload_fingerprint,state,"
                "message_id_fingerprint,created_at,updated_at FROM probe_operations"
            ).fetchall()

    def execute(self, sql, params=()):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as connection:
            connection.execute(sql, params)

    def assert_safe_error(self, callback):
        with self.assertRaises(ProbeLedgerError) as raised:
            callback()
        self.assertEqual(str(raised.exception), SAFE_ERROR)

    def test_constructor_and_invalid_inputs_do_not_touch_disk(self):
        with patch("app.whatsapp_probe_ledger.sqlite3.connect") as connect:
            ledger = ProbeLedger(self.path)
            invalid_operations = [
                None, 42, UUID(self.operation_id), "", self.operation_id.upper(),
                self.operation_id.replace("-", ""), "{" + self.operation_id + "}",
                "4cce9147-9d61-1f1a-b4b7-295cf3f32ade",
            ]
            for value in invalid_operations:
                self.assert_safe_error(lambda: ledger.reserve(value, CASE, PAYLOAD))
            for value in (None, "", "Image_v1", "ação_v1", "x" * 65, "text with spaces", "../image"):
                self.assert_safe_error(lambda: ledger.reserve(self.operation_id, value, PAYLOAD))
            for value in (None, "", "a" * 63, "a" * 65, "A" * 64, "g" * 64, PAYLOAD + "\n"):
                self.assert_safe_error(lambda: ledger.reserve(self.operation_id, CASE, value))
            connect.assert_not_called()
        self.assertFalse(self.path.parent.exists())

    def test_new_reservation_is_frozen_and_durable_before_return(self):
        reservation = self.reserve()
        self.assertEqual(reservation, ProbeReservation("new", self.operation_id, CASE, PAYLOAD))
        with self.assertRaises(FrozenInstanceError):
            reservation.status = "accepted"
        row, = self.rows()
        self.assertEqual(row[:5], (self.operation_id, CASE, PAYLOAD, "sending", None))
        for timestamp in row[5:]:
            self.assertEqual(datetime.fromisoformat(timestamp).utcoffset(), timedelta(0))
        self.assertEqual(row[5], row[6])

    def test_repeating_sending_is_blocked_without_another_row(self):
        self.reserve()
        before = self.rows()
        self.assertEqual(self.reserve(), ProbeReservation("blocked", self.operation_id, CASE, PAYLOAD))
        self.assertEqual(self.rows(), before)

    def test_accepted_is_persistent_and_returns_only_hashed_message_id(self):
        reservation = self.reserve()
        self.ledger.mark_accepted(reservation, MESSAGE)
        restarted = ProbeLedger(self.path)
        self.assertEqual(
            restarted.reserve(self.operation_id, CASE, PAYLOAD),
            ProbeReservation("accepted", self.operation_id, CASE, PAYLOAD, MESSAGE),
        )
        self.assertEqual(self.rows()[0][3:5], ("accepted", MESSAGE))

    def test_restart_never_reopens_sending_or_uncertain(self):
        reservation = self.reserve()
        self.assertEqual(ProbeLedger(self.path).reserve(self.operation_id, CASE, PAYLOAD).status, "blocked")
        self.ledger.mark_uncertain(reservation)
        before = self.rows()
        self.assertEqual(ProbeLedger(self.path).reserve(self.operation_id, CASE, PAYLOAD).status, "blocked")
        self.assertEqual(self.rows(), before)
        self.assertEqual(before[0][3:5], ("uncertain", None))

    def test_same_uuid_cannot_change_case_or_payload_in_any_state(self):
        reservation = self.reserve()
        for state in ("sending", "accepted"):
            before = self.rows()
            self.assert_safe_error(lambda: self.ledger.reserve(self.operation_id, "template_disclaimer_v1", PAYLOAD))
            self.assert_safe_error(lambda: self.ledger.reserve(self.operation_id, CASE, "f" * 64))
            self.assertEqual(self.rows(), before)
            if state == "sending":
                self.ledger.mark_accepted(reservation, MESSAGE)

    def test_new_uuid_is_an_independent_explicit_operation(self):
        self.reserve()
        second = self.ledger.reserve(str(uuid4()), CASE, PAYLOAD)
        self.assertEqual(second.status, "new")
        self.assertEqual(len(self.rows()), 2)

    def test_transitions_require_original_full_binding(self):
        reservation = self.reserve()
        forged = [
            replace(reservation, operation_id=str(uuid4())),
            replace(reservation, case_name="template_disclaimer_v1"),
            replace(reservation, payload_fingerprint="f" * 64),
            replace(reservation, status="blocked"),
            replace(reservation, message_id_fingerprint=MESSAGE),
            replace(reservation, operation_id="not-a-uuid"), None,
        ]
        before = self.rows()
        for value in forged:
            self.assert_safe_error(lambda: self.ledger.mark_accepted(value, MESSAGE))
            self.assert_safe_error(lambda: self.ledger.mark_uncertain(value))
        for value in (None, "", "A" * 64, "g" * 64):
            self.assert_safe_error(lambda: self.ledger.mark_accepted(reservation, value))
        self.assertEqual(self.rows(), before)

    def test_terminal_states_cannot_be_reclassified_or_reused_for_transition(self):
        accepted = self.reserve()
        uncertain = self.ledger.reserve(str(uuid4()), CASE, PAYLOAD)
        self.ledger.mark_accepted(accepted, MESSAGE)
        self.ledger.mark_uncertain(uncertain)
        before = self.rows()
        for reservation in (accepted, uncertain):
            self.assert_safe_error(lambda: self.ledger.mark_accepted(reservation, MESSAGE))
            self.assert_safe_error(lambda: self.ledger.mark_uncertain(reservation))
        self.assertEqual(self.rows(), before)

    def test_commit_failure_rolls_back_reservation_and_transition(self):
        self.ledger.initialize()
        real_connect = sqlite3.connect

        class FailingCommit(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError("private simulated failure")

        def connect(*args, **kwargs):
            return real_connect(*args, factory=FailingCommit, **kwargs)

        with patch("app.whatsapp_probe_ledger.sqlite3.connect", side_effect=connect):
            self.assert_safe_error(self.reserve)
        self.assertEqual(self.rows(), [])
        reservation = self.reserve()
        before = self.rows()
        with patch("app.whatsapp_probe_ledger.sqlite3.connect", side_effect=connect):
            self.assert_safe_error(lambda: self.ledger.mark_accepted(reservation, MESSAGE))
        self.assertEqual(self.rows(), before)
        self.ledger.mark_uncertain(reservation)

    def test_no_transaction_or_connection_remains_between_calls(self):
        reservation = self.reserve()
        with closing(sqlite3.connect(self.path, timeout=0, isolation_level=None)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        self.ledger.mark_accepted(reservation, MESSAGE)
        with closing(sqlite3.connect(self.path, timeout=0, isolation_level=None)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()

    def test_atomic_reservation_between_two_instances_and_threads(self):
        barrier = Barrier(2)

        def reserve_from_instance(_):
            ledger = ProbeLedger(self.path)
            barrier.wait(timeout=5)
            return ledger.reserve(self.operation_id, CASE, PAYLOAD).status

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(reserve_from_instance, range(2)))
        self.assertCountEqual(statuses, ["new", "blocked"])
        self.assertEqual(len(self.rows()), 1)

    def test_atomic_reservation_between_processes(self):
        self.ledger.initialize()
        script = (
            "import sys;from app.whatsapp_probe_ledger import ProbeLedger;"
            "sys.stdin.readline();"
            "print(ProbeLedger(sys.argv[1]).reserve(*sys.argv[2:]).status)"
        )
        processes = []
        try:
            for _ in range(2):
                processes.append(subprocess.Popen(
                    [sys.executable, "-B", "-c", script, str(self.path), self.operation_id, CASE, PAYLOAD],
                    cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                ))
            for process in processes:
                process.stdin.write("start\n")
                process.stdin.flush()
            statuses = []
            for process in processes:
                stdout, stderr = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0, stderr)
                self.assertEqual(stderr, "")
                statuses.append(stdout.strip())
            self.assertCountEqual(statuses, ["new", "blocked"])
            self.assertEqual(len(self.rows()), 1)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)

    def test_locked_database_fails_closed_without_reserving(self):
        self.ledger.initialize()
        with closing(sqlite3.connect(self.path, isolation_level=None)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            with patch("app.whatsapp_probe_ledger.BUSY_TIMEOUT_MS", 1):
                self.assert_safe_error(self.reserve)
            connection.rollback()
        self.assertEqual(self.rows(), [])

    def test_foreign_or_corrupt_database_is_not_overwritten(self):
        self.path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("CREATE TABLE private_data(value TEXT)")
            connection.commit()
        before = self.path.read_bytes()
        self.assert_safe_error(self.reserve)
        self.assertEqual(self.path.read_bytes(), before)
        self.path.write_bytes(b"not a sqlite database; private fixture")
        before = self.path.read_bytes()
        self.assert_safe_error(self.reserve)
        self.assertEqual(self.path.read_bytes(), before)

    def test_schema_version_and_table_changes_fail_after_initialization_too(self):
        self.reserve()
        self.execute("UPDATE probe_metadata SET value='2'")
        self.assert_safe_error(self.reserve)
        self.assert_safe_error(lambda: ProbeLedger(self.path).reserve(str(uuid4()), CASE, PAYLOAD))
        self.execute("UPDATE probe_metadata SET value='1'")
        self.execute("CREATE TABLE unrelated(value TEXT)")
        self.assert_safe_error(self.reserve)

    def test_invalid_saved_metadata_is_rejected_on_restart(self):
        reservation = self.reserve()
        self.ledger.mark_accepted(reservation, MESSAGE)
        for column, invalid, original in (
            ("payload_fingerprint", "invalid", PAYLOAD),
            ("case_name", "invalid name", CASE),
            ("message_id_fingerprint", "raw-provider-message-id", MESSAGE),
            ("created_at", "2026-10-24T12:00:00", self.rows()[0][5]),
        ):
            self.execute(f"UPDATE probe_operations SET {column}=?", (invalid,))
            self.assert_safe_error(lambda: ProbeLedger(self.path).reserve(str(uuid4()), CASE, PAYLOAD))
            self.execute(f"UPDATE probe_operations SET {column}=?", (original,))

    def test_file_contains_only_technical_binding_and_fingerprints(self):
        reservation = self.reserve()
        self.ledger.mark_accepted(reservation, MESSAGE)
        raw = self.path.read_bytes()
        for allowed in (self.operation_id, CASE, PAYLOAD, MESSAGE, "accepted"):
            self.assertIn(allowed.encode("ascii"), raw)
        for forbidden in (b"+5585999990000", b"360dialog:fixture-provider-id", b"public-fictional-fixture-image-v1"):
            self.assertNotIn(forbidden, raw)
        with closing(sqlite3.connect(self.path)) as connection:
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        self.assertCountEqual(tables, [("probe_metadata",), ("probe_operations",)])


if __name__ == "__main__":
    unittest.main()
