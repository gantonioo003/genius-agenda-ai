"""Autorização de resposta a partir de claims reais, sem rede ou segredos."""

from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import UUID, uuid1, uuid4

from app.whatsapp_inbox import InboxClaim, InboxEvent, InboxStore, InboxStoreError, make_event_key


def event(identifier, event_type="message_text"):
    kind = "status" if event_type.startswith("status_") else "message"
    return InboxEvent(make_event_key(kind, identifier, event_type), event_type)


class ProcessingReplyClaimTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "inbox.sqlite3"
        self.store = InboxStore(self.path)

    def rows(self):
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute(
                "SELECT event_key,event_type,request_id,state,created_at,updated_at "
                "FROM inbox_events ORDER BY event_key"
            ).fetchall()

    def sql(self, statement, parameters=()):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(statement, parameters)

    def test_invalid_request_ids_are_denied_without_file_or_initialization_io(self):
        canonical = str(uuid4())
        invalid = (None, 1, True, UUID(canonical), "", "not-a-uuid", canonical.upper(),
                   "{" + canonical + "}", canonical.replace("-", ""), str(uuid1()))
        with patch.object(self.store, "initialize", side_effect=AssertionError("No database I/O")):
            for candidate in invalid:
                with self.subTest(candidate_type=type(candidate).__name__):
                    self.assertIsNone(self.store.lookup_processing_claim(candidate))
        self.assertFalse(self.path.exists())

    def test_missing_canonical_uuid_has_no_claim_and_does_not_insert_events(self):
        self.assertIsNone(self.store.lookup_processing_claim(str(uuid4())))
        self.assertEqual(self.rows(), [])

    def test_processing_message_returns_same_owner_and_complete_event_set(self):
        first, second = event("message-one"), event("message-two", "message_image")
        original = self.store.claim([second, first, first])
        result = self.store.lookup_processing_claim(original.request_id)
        self.assertEqual(result, InboxClaim(
            "new", original.request_id,
            tuple(sorted((first, second), key=lambda value: value.event_key)), 0,
        ))
        self.assertEqual({row[3] for row in self.rows()}, {"processing"})

    def test_status_only_is_returned_for_the_service_to_skip_sending(self):
        original = self.store.claim([event("outbound-one", "status_read")])
        result = self.store.lookup_processing_claim(original.request_id)
        self.assertEqual(result.events, original.events)
        self.assertTrue(all(value.event_type.startswith("status_") for value in result.events))

    def test_mixed_claim_keeps_message_and_status_rows_in_one_owner_snapshot(self):
        original = self.store.claim([event("message-one"), event("status-one", "status_delivered")])
        result = self.store.lookup_processing_claim(original.request_id)
        self.assertEqual(set(result.events), set(original.events))
        self.assertEqual(result.request_id, original.request_id)

    def test_completed_or_uncertain_claim_is_not_authorized_after_reopening(self):
        for index, transition in enumerate((self.store.complete, self.store.mark_uncertain)):
            with self.subTest(transition=transition.__name__):
                original = self.store.claim([event(f"message-{index}")])
                transition(original)
                self.assertIsNone(InboxStore(self.path).lookup_processing_claim(original.request_id))

    def test_partial_processing_owner_is_rejected_when_any_owned_row_is_not_processing(self):
        for index, state in enumerate(("processed", "uncertain")):
            with self.subTest(state=state):
                first, second = event(f"first-{index}"), event(f"second-{index}")
                original = self.store.claim([first, second])
                self.sql("UPDATE inbox_events SET state=? WHERE event_key=?", (state, second.event_key))
                self.assertIsNone(self.store.lookup_processing_claim(original.request_id))
                self.assertEqual(len([row for row in self.rows() if row[2] == original.request_id]), 2)

    def test_reopened_store_reads_persisted_claim_without_creating_another_owner(self):
        original = self.store.claim([event("persisted")])
        before = self.rows()
        result = InboxStore(self.path).lookup_processing_claim(original.request_id)
        self.assertEqual(result.events, original.events)
        self.assertEqual(result.request_id, original.request_id)
        self.assertEqual(self.rows(), before)

    def test_read_lookup_never_inserts_updates_or_holds_a_write_transaction(self):
        original = self.store.claim([event("read-only")])
        before_rows, before_bytes = self.rows(), self.path.read_bytes()
        statements = []
        real_connect = sqlite3.connect

        def traced_connect(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection

        with patch("app.whatsapp_inbox.sqlite3.connect", side_effect=traced_connect):
            self.assertIsNotNone(self.store.lookup_processing_claim(original.request_id))
        self.assertTrue(statements)
        self.assertTrue(all(statement.lstrip().upper().startswith(("SELECT", "PRAGMA", "BEGIN", "ROLLBACK"))
                            for statement in statements))
        self.assertNotIn("BEGIN IMMEDIATE", (statement.upper() for statement in statements))
        self.assertEqual(self.rows(), before_rows)
        self.assertEqual(self.path.read_bytes(), before_bytes)
        with closing(sqlite3.connect(self.path, timeout=0, isolation_level=None)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()

    def test_unsupported_version_is_rejected_even_after_initialization_cache(self):
        original = self.store.claim([event("versioned")])
        self.sql("UPDATE inbox_metadata SET value='999' WHERE name='schema_version'")
        before = self.path.read_bytes()
        with self.assertRaises(InboxStoreError):
            self.store.lookup_processing_claim(original.request_id)
        self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_file_and_foreign_schema_fail_without_replacement(self):
        self.path.write_bytes(b"not a SQLite database")
        before = self.path.read_bytes()
        with self.assertRaises(InboxStoreError):
            self.store.lookup_processing_claim(str(uuid4()))
        self.assertEqual(self.path.read_bytes(), before)
        foreign = Path(self.temporary.name) / "foreign.sqlite3"
        with closing(sqlite3.connect(foreign)) as connection, connection:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
        before_foreign = foreign.read_bytes()
        with self.assertRaises(InboxStoreError):
            InboxStore(foreign).lookup_processing_claim(str(uuid4()))
        self.assertEqual(foreign.read_bytes(), before_foreign)

    def test_invalid_claim_metadata_fails_closed_instead_of_authorizing_reply(self):
        for index, (field, value) in enumerate((
            ("event_type", "other_private_type"),
            ("event_type", "message_not_a_supported_type"),
            ("created_at", "PRIVATE_INVALID_TIMESTAMP"),
            ("updated_at", "2030-06-17T10:00:00-03:00"),
        )):
            with self.subTest(field=field, case=index):
                original = self.store.claim([event(f"bad-row-{index}")])
                self.sql(f"UPDATE inbox_events SET {field}=? WHERE request_id=?", (value, original.request_id))
                with self.assertRaises(InboxStoreError) as failure:
                    self.store.lookup_processing_claim(original.request_id)
                self.assertNotIn(value, str(failure.exception))


if __name__ == "__main__":
    unittest.main()
