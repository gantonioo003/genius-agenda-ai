"""Transporte isolado e reserva de envio; somente fixtures, sem chamadas reais."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid1, uuid4

import requests

from app.whatsapp_outbox import OutboxReservation, OutboxStore, OutboxStoreError
from app.whatsapp_sandbox_sender import (
    MAX_RESPONSE_BYTES, SANDBOX_MESSAGES_URL, SandboxSendError, SandboxSender, fingerprint_message_id,
)


PHONE = "15555550123"
TEXT = "PRIVATE_TEST_TEXT_DO_NOT_LOG"
KEY = "K" * 48
PROVIDER_ID = "wamid.PRIVATE_PROVIDER_ID_DO_NOT_LOG"
FINGERPRINT = fingerprint_message_id(PROVIDER_ID)


class FakeResponse:
    def __init__(self, status=201, body=None, chunks=None, stream_error=None):
        self.status_code = status
        raw = json.dumps({"messages": [{"id": PROVIDER_ID}], "meta": {"api_status": "stable"}}).encode()
        self.chunks = chunks if chunks is not None else [raw if body is None else body]
        self.stream_error = stream_error
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def iter_content(self, *, chunk_size):
        if self.stream_error:
            raise self.stream_error
        yield from self.chunks


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = FakeResponse() if response is None else response
        self.error = error
        self.calls = []
        self.adapters = []
        self.trust_env = True
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def mount(self, prefix, adapter):
        self.adapters.append((prefix, adapter))

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


class SandboxSenderTests(unittest.TestCase):
    def sender(self, response=None, error=None):
        self.session = FakeSession(response, error)
        return SandboxSender(KEY, session_factory=lambda: self.session)

    def assert_safe_error(self, callable_, *, uncertain):
        with self.assertRaises(SandboxSendError) as result:
            callable_()
        self.assertIs(result.exception.uncertain, uncertain)
        for private in (PHONE, TEXT, KEY, PROVIDER_ID):
            self.assertNotIn(private, str(result.exception))
            self.assertNotIn(private, repr(result.exception))

    def test_one_fixed_https_post_with_no_proxy_redirect_or_retry(self):
        result = self.sender().send(PHONE, TEXT)
        self.assertEqual(result, FINGERPRINT)
        self.assertRegex(result, r"^[0-9a-f]{64}$")
        self.assertEqual(len(self.session.calls), 1)
        url, options = self.session.calls[0]
        self.assertEqual(url, SANDBOX_MESSAGES_URL)
        self.assertEqual(options, {
            "headers": {"D360-API-KEY": KEY, "Content-Type": "application/json"},
            "json": {"messaging_product": "whatsapp", "recipient_type": "individual",
                     "to": PHONE, "type": "text", "text": {"body": TEXT}},
            "timeout": (3, 8), "allow_redirects": False, "stream": True, "verify": True,
        })
        self.assertFalse(self.session.trust_env)
        self.assertEqual(self.session.adapters[0][0], "https://")
        self.assertEqual(self.session.adapters[0][1].max_retries.total, 0)
        self.assertTrue(self.session.closed)
        self.assertTrue(self.session.response.closed)

    def test_documented_201_and_compatible_200_accept_single_printable_id(self):
        for status in (200, 201):
            for identifier in (PROVIDER_ID, "message_id"):
                with self.subTest(status=status, generic_id=identifier == "message_id"):
                    response = FakeResponse(status, json.dumps({
                        "messages": [{"id": identifier}], "contacts": [{"wa_id": PHONE}],
                    }).encode())
                    fingerprint = self.sender(response).send(PHONE, TEXT)
                    self.assertEqual(fingerprint, fingerprint_message_id(identifier))
                    self.assertNotIn(PHONE, fingerprint)
                    self.assertNotIn(identifier, fingerprint)

    def test_secretstr_style_key_is_injected_without_reading_environment(self):
        class Secret:
            def get_secret_value(self):
                return KEY
        session = FakeSession()
        sender = SandboxSender(Secret(), session_factory=lambda: session)
        self.assertEqual(sender.send(PHONE, TEXT), FINGERPRINT)

    def test_bad_key_fails_before_network_without_echo(self):
        for value in (None, "", "short", KEY + "\r\nInjected:value", object()):
            with self.subTest(value_type=type(value).__name__):
                self.assert_safe_error(lambda: SandboxSender(value), uncertain=False)

    def test_invalid_recipient_and_text_never_initiate_post(self):
        sender = self.sender()
        for phone in (None, "", "+" + PHONE, "0" + PHONE, "١٢٣٤٥٦٧", "123456", "1" * 16, 123):
            with self.subTest(phone_type=type(phone).__name__):
                self.assert_safe_error(lambda: sender.send(phone, TEXT), uncertain=False)
        for text in (None, "", "a" * 4097, [], True):
            with self.subTest(text_type=type(text).__name__):
                self.assert_safe_error(lambda: sender.send(PHONE, text), uncertain=False)
        self.assertEqual(self.session.calls, [])

    def test_valid_text_boundaries_and_unicode_are_preserved(self):
        for text in ("x", "x" * 4096, "Teste — mensagem recebida 👍"):
            with self.subTest(length=len(text)):
                self.sender().send(PHONE, text)
                self.assertEqual(self.session.calls[0][1]["json"]["text"]["body"], text)

    def test_timeout_after_post_initiation_is_uncertain_and_never_retried(self):
        sender = self.sender(error=requests.Timeout(PROVIDER_ID + KEY + TEXT))
        self.assert_safe_error(lambda: sender.send(PHONE, TEXT), uncertain=True)
        self.assertEqual(len(self.session.calls), 1)
        self.assertTrue(self.session.closed)

    def test_stream_disconnect_is_uncertain_and_response_is_closed(self):
        sender = self.sender(FakeResponse(stream_error=requests.ConnectionError(PROVIDER_ID)))
        self.assert_safe_error(lambda: sender.send(PHONE, TEXT), uncertain=True)
        self.assertTrue(self.session.response.closed)

    def test_session_creation_failure_is_not_marked_as_attempted(self):
        def fail():
            raise RuntimeError(KEY + PHONE)
        sender = SandboxSender(KEY, session_factory=fail)
        self.assert_safe_error(lambda: sender.send(PHONE, TEXT), uncertain=False)

    def test_nonaccepted_statuses_are_safe_uncertain_without_automatic_retry(self):
        for status in (202, 204, 301, 302, 400, 401, 403, 429, 500, 502, 504):
            with self.subTest(status=status):
                sender = self.sender(FakeResponse(status, (KEY + PHONE + TEXT).encode()))
                self.assert_safe_error(lambda: sender.send(PHONE, TEXT), uncertain=True)
                self.assertEqual(len(self.session.calls), 1)
                self.assertTrue(self.session.response.closed)

    def test_success_requires_exactly_one_message_with_valid_id(self):
        invalid = [None, [], {}, {"messages": []}, {"messages": [None]},
                   {"messages": [{"id": PROVIDER_ID}, {"id": "second"}]},
                   {"messages": [{"id": None}]}, {"messages": [{"id": True}]},
                   {"messages": [{"id": ""}]}, {"messages": [{"id": "x" * 1025}]},
                   {"messages": [{"id": "contains space"}]}, {"messages": [{"id": "á"}]},
                   {"messages": [{"id": "id\nprivate"}]}]
        for value in invalid:
            with self.subTest(shape=type(value).__name__):
                sender = self.sender(FakeResponse(body=json.dumps(value).encode()))
                self.assert_safe_error(lambda: sender.send(PHONE, TEXT), uncertain=True)

    def test_strict_json_rejects_duplicates_nan_utf8_and_invalid_text(self):
        invalid = [b'{"messages":[],"messages":[{"id":"ok"}]}',
                   b'{"messages":[{"id":"one","id":"two"}]}',
                   b'{"messages":[{"id":"ok"}],"extra":NaN}',
                   b'{"messages":[{"id":"ok"}],"extra":Infinity}',
                   b"{broken", b"\xff", b"[" * 1200]
        for body in invalid:
            with self.subTest(prefix=body[:16]):
                sender = self.sender(FakeResponse(body=body))
                self.assert_safe_error(lambda: sender.send(PHONE, TEXT), uncertain=True)

    def test_body_limit_is_enforced_on_stream_and_exact_boundary_is_allowed(self):
        raw = json.dumps({"messages": [{"id": PROVIDER_ID}]}).encode()
        padded = raw + b" " * (MAX_RESPONSE_BYTES - len(raw))
        self.assertEqual(self.sender(FakeResponse(chunks=[padded])).send(PHONE, TEXT), FINGERPRINT)
        sender = self.sender(FakeResponse(chunks=[padded, b" "]))
        self.assert_safe_error(lambda: sender.send(PHONE, TEXT), uncertain=True)

    def test_fingerprint_is_namespaced_stable_and_different_ids_do_not_collapse(self):
        self.assertEqual(fingerprint_message_id(PROVIDER_ID), FINGERPRINT)
        self.assertNotEqual(FINGERPRINT, fingerprint_message_id(PROVIDER_ID + "2"))
        self.assertNotIn(PROVIDER_ID, FINGERPRINT)


class OutboxStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "data" / "whatsapp-outbox.sqlite3"
        self.store = OutboxStore(self.path)
        self.request_id = str(uuid4())

    def rows(self):
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute(
                "SELECT request_id,state,message_id_fingerprint,created_at,updated_at FROM outbox_messages"
            ).fetchall()

    def sql(self, query, args=()):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(query, args)

    def test_constructor_has_no_io_and_invalid_request_does_not_create_database(self):
        with patch.object(Path, "mkdir", side_effect=AssertionError("mkdir")), \
             patch("app.whatsapp_outbox.sqlite3.connect", side_effect=AssertionError("connect")):
            OutboxStore(self.path)
        for value in (None, "", "private-invalid", str(uuid1()), "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA", 123):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ValueError):
                    self.store.reserve(value)
        self.assertFalse(self.path.parent.exists())

    def test_reservation_is_frozen_and_sending_is_committed_before_return(self):
        reservation = self.store.reserve(self.request_id)
        self.assertEqual(reservation, OutboxReservation("new", self.request_id))
        with self.assertRaises(FrozenInstanceError):
            reservation.status = "accepted"
        row, = self.rows()
        self.assertEqual(row[:3], (self.request_id, "sending", None))
        self.assertEqual(row[3], row[4])
        self.assertTrue(row[3].endswith("+00:00"))
        # A reserva não segura a transação durante uma futura chamada externa.
        with closing(sqlite3.connect(self.path, timeout=0.1)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()

    def test_accepted_replay_reuses_fingerprint_after_restart(self):
        reservation = self.store.reserve(self.request_id)
        self.store.mark_accepted(reservation, FINGERPRINT)
        replay = OutboxStore(self.path).reserve(self.request_id)
        self.assertEqual(replay, OutboxReservation("accepted", self.request_id, FINGERPRINT))
        self.assertEqual(len(self.rows()), 1)

    def test_sending_and_uncertain_replay_are_blocked_after_restart(self):
        reservation = self.store.reserve(self.request_id)
        self.assertEqual(OutboxStore(self.path).reserve(self.request_id).status, "blocked")
        self.store.mark_uncertain(reservation)
        self.assertEqual(OutboxStore(self.path).reserve(self.request_id).status, "blocked")
        self.assertEqual(self.rows()[0][1], "uncertain")

    def test_uncertainty_has_no_expiry_or_automatic_retry(self):
        reservation = self.store.reserve(self.request_id)
        self.store.mark_uncertain(reservation)
        self.sql("UPDATE outbox_messages SET created_at='2000-01-01T00:00:00+00:00',"
                 "updated_at='2000-01-01T00:00:00+00:00'")
        self.assertEqual(OutboxStore(self.path).reserve(self.request_id).status, "blocked")
        with self.assertRaises(OutboxStoreError):
            self.store.mark_accepted(reservation, FINGERPRINT)
        self.assertEqual(self.rows()[0][1], "uncertain")

    def test_different_claim_uuid_has_separate_reservation(self):
        self.store.reserve(self.request_id)
        self.assertEqual(self.store.reserve(str(uuid4())).status, "new")
        self.assertEqual(len(self.rows()), 2)

    def test_accepted_cannot_be_downgraded_or_accepted_twice(self):
        reservation = self.store.reserve(self.request_id)
        self.store.mark_accepted(reservation, FINGERPRINT)
        with self.assertRaises(OutboxStoreError):
            self.store.mark_uncertain(reservation)
        with self.assertRaises(OutboxStoreError):
            self.store.mark_accepted(reservation, FINGERPRINT)
        self.assertEqual(self.store.reserve(self.request_id).status, "accepted")

    def test_wrong_owner_or_nonnew_reservation_cannot_transition(self):
        reservation = self.store.reserve(self.request_id)
        with self.assertRaises(OutboxStoreError):
            self.store.mark_accepted(replace(reservation, request_id=str(uuid4())), FINGERPRINT)
        with self.assertRaises(ValueError):
            self.store.mark_uncertain(OutboxReservation("blocked", self.request_id))
        with self.assertRaises(ValueError):
            self.store.mark_accepted(reservation, PROVIDER_ID)
        self.assertEqual(self.rows()[0][1], "sending")

    def test_failed_accepted_transition_rolls_back_then_can_mark_uncertain(self):
        reservation = self.store.reserve(self.request_id)
        self.sql("CREATE TRIGGER skip_accepted BEFORE UPDATE OF state ON outbox_messages "
                 "WHEN NEW.state='accepted' BEGIN SELECT RAISE(IGNORE); END")
        with self.assertRaises(OutboxStoreError):
            self.store.mark_accepted(reservation, FINGERPRINT)
        self.assertEqual(self.rows()[0][1:3], ("sending", None))
        self.store.mark_uncertain(reservation)
        self.assertEqual(self.store.reserve(self.request_id).status, "blocked")

    def test_failed_uncertainty_transition_leaves_sending_blocked(self):
        reservation = self.store.reserve(self.request_id)
        self.sql("CREATE TRIGGER fail_uncertain BEFORE UPDATE OF state ON outbox_messages "
                 "WHEN NEW.state='uncertain' BEGIN SELECT RAISE(ABORT,'PRIVATE_SQL_FAILURE'); END")
        with self.assertRaises(OutboxStoreError) as result:
            self.store.mark_uncertain(reservation)
        self.assertNotIn("PRIVATE_SQL_FAILURE", str(result.exception))
        self.assertEqual(self.rows()[0][1], "sending")
        self.assertEqual(OutboxStore(self.path).reserve(self.request_id).status, "blocked")

    def test_failed_reserve_does_not_leave_partial_row(self):
        self.store.initialize()
        self.sql("CREATE TRIGGER fail_reserve BEFORE INSERT ON outbox_messages "
                 "BEGIN SELECT RAISE(ABORT,'PRIVATE_SQL_FAILURE'); END")
        with self.assertRaises(OutboxStoreError):
            self.store.reserve(self.request_id)
        self.assertEqual(self.rows(), [])

    def test_two_store_instances_racing_get_one_new_and_one_blocked(self):
        barrier = Barrier(2)
        def worker():
            store = OutboxStore(self.path)
            barrier.wait(timeout=3)
            return store.reserve(self.request_id).status
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            outcomes = [future.result(timeout=5) for future in futures]
        self.assertCountEqual(outcomes, ["new", "blocked"])
        self.assertEqual(len(self.rows()), 1)

    def test_separate_processes_cannot_both_reserve_same_claim(self):
        code = (
            "import sys; from app.whatsapp_outbox import OutboxStore; "
            "store=OutboxStore(sys.argv[1]); print('ready',flush=True); "
            "sys.stdin.readline(); print(store.reserve(sys.argv[2]).status,flush=True)"
        )
        processes = [subprocess.Popen(
            [sys.executable, "-B", "-c", code, str(self.path), self.request_id],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ) for _ in range(2)]
        try:
            for process in processes:
                self.assertEqual(process.stdout.readline().strip(), "ready")
            for process in processes:
                process.stdin.write("go\n")
                process.stdin.flush()
            outputs = [process.communicate(timeout=8) for process in processes]
            self.assertCountEqual([stdout.strip() for stdout, _stderr in outputs], ["new", "blocked"])
            for process, (_stdout, stderr) in zip(processes, outputs):
                self.assertEqual(process.returncode, 0, stderr)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def test_schema_version_changes_are_rejected_even_after_cached_initialization(self):
        self.store.reserve(self.request_id)
        self.sql("UPDATE outbox_metadata SET value='2'")
        before = self.path.read_bytes()
        with self.assertRaises(OutboxStoreError):
            self.store.reserve(self.request_id)
        self.assertEqual(before, self.path.read_bytes())

    def test_corrupt_and_foreign_database_are_not_replaced_or_migrated(self):
        self.path.parent.mkdir()
        raw = b"PRIVATE_CORRUPT_DATABASE"
        self.path.write_bytes(raw)
        with self.assertRaises(OutboxStoreError):
            self.store.initialize()
        self.assertEqual(self.path.read_bytes(), raw)
        other_path = self.path.with_name("foreign.sqlite3")
        with closing(sqlite3.connect(other_path)) as connection, connection:
            connection.execute("CREATE TABLE calendar_operations(id TEXT)")
        before = other_path.read_bytes()
        with self.assertRaises(OutboxStoreError):
            OutboxStore(other_path).initialize()
        self.assertEqual(other_path.read_bytes(), before)

    def test_store_contains_only_correlation_hash_state_and_technical_timestamps(self):
        reservation = self.store.reserve(self.request_id)
        self.store.mark_accepted(reservation, FINGERPRINT)
        with closing(sqlite3.connect(self.path)) as connection:
            dump = "\n".join(connection.iterdump())
            columns = [row[1] for row in connection.execute("PRAGMA table_info(outbox_messages)")]
        self.assertEqual(columns, ["request_id", "state", "message_id_fingerprint", "created_at", "updated_at"])
        for private in (PHONE, TEXT, KEY, PROVIDER_ID):
            self.assertNotIn(private, dump)
            self.assertNotIn(private.encode(), self.path.read_bytes())

    def test_connections_close_after_transitions(self):
        reservation = self.store.reserve(self.request_id)
        self.store.mark_accepted(reservation, FINGERPRINT)
        renamed = self.path.with_name("closed.sqlite3")
        self.path.rename(renamed)
        self.assertTrue(renamed.exists())


if __name__ == "__main__":
    unittest.main()
