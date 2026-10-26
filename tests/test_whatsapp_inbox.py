"""Persistência/idempotência da entrada com identidades fictícias e sem rede."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier
from time import monotonic
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from app.whatsapp_inbox import (
    BUSY_TIMEOUT_MS, InboxClaim, InboxEvent, InboxStore, InboxStoreError, make_event_key,
)


PRIVATE_ID = "wamid.PRIVATE_15555550123_text_not_for_database"
PRIVATE_PHONE = "15555550123"


def event(provider_id=PRIVATE_ID, event_type="message_text", kind="message"):
    return InboxEvent(make_event_key(kind, provider_id, event_type), event_type)


class InboxKeyTests(unittest.TestCase):
    def test_message_key_ignores_changed_type_and_content_does_not_enter_api(self):
        text = make_event_key("message", PRIVATE_ID, "message_text")
        reaction = make_event_key("message", PRIVATE_ID, "message_reaction")
        self.assertEqual(text, reaction)
        self.assertRegex(text, r"^[0-9a-f]{64}$")
        self.assertNotIn(PRIVATE_ID, text)

    def test_message_and_status_ids_are_independent_namespaces(self):
        self.assertNotEqual(make_event_key("message", PRIVATE_ID, "message_text"),
                            make_event_key("status", PRIVATE_ID, "status_sent"))

    def test_status_phase_changes_key_but_same_phase_remains_same_key(self):
        sent = make_event_key("status", PRIVATE_ID, "status_sent")
        self.assertEqual(sent, make_event_key("status", PRIVATE_ID, "status_sent"))
        self.assertNotEqual(sent, make_event_key("status", PRIVATE_ID, "status_read"))

    def test_id_and_type_limits_reject_safely(self):
        invalid = [("other", PRIVATE_ID, "message_text"), (None, PRIVATE_ID, "message_text"),
                   ("message", "", "message_text"), ("message", "x" * 1025, "message_text"),
                   ("message", 123, "message_text"), ("message", PRIVATE_ID, ""),
                   ("message", PRIVATE_ID, "x" * 41), ("message", PRIVATE_ID, "PRIVATE / value"),
                   ("message", PRIVATE_ID, None)]
        for args in invalid:
            with self.subTest(kind=args[0]):
                with self.assertRaises(ValueError) as result:
                    make_event_key(*args)
                self.assertEqual(str(result.exception), "Evento de recebimento inválido.")
        make_event_key("message", "x" * 1024, "message_" + "m" * 32)

    def test_event_type_must_match_message_or_status_namespace(self):
        for kind, event_type in (("message", "status_read"), ("status", "message_text")):
            with self.assertRaises(ValueError):
                make_event_key(kind, PRIVATE_ID, event_type)

    def test_event_is_frozen_and_rejects_raw_or_noncanonical_keys(self):
        item = event()
        with self.assertRaises(FrozenInstanceError):
            item.event_type = "message_image"
        for key in (PRIVATE_ID, item.event_key.upper(), "a" * 63, "g" * 64):
            with self.subTest(key_length=len(key)):
                with self.assertRaises(ValueError):
                    InboxEvent(key, "message_text")


class InboxStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "data" / "whatsapp-inbox.sqlite3"
        self.store = InboxStore(self.path)

    def rows(self):
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute(
                "SELECT event_key,event_type,request_id,state FROM inbox_events ORDER BY event_key"
            ).fetchall()

    def sql(self, statement, parameters=()):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(statement, parameters)

    def test_constructor_has_no_file_or_directory_io(self):
        with patch.object(Path, "mkdir", side_effect=AssertionError("mkdir")), \
             patch("app.whatsapp_inbox.sqlite3.connect", side_effect=AssertionError("connect")):
            InboxStore(self.path)
        self.assertFalse(self.path.parent.exists())

    def test_claim_lazily_initializes_and_persists_before_returning(self):
        claim = self.store.claim([event()])
        self.assertEqual(claim.status, "new")
        self.assertEqual(claim.events, (event(),))
        self.assertEqual(claim.duplicate_count, 0)
        self.assertEqual(UUID(claim.request_id).version, 4)
        self.assertEqual(self.rows(), [(event().event_key, "message_text", claim.request_id, "processing")])
        with self.assertRaises(FrozenInstanceError):
            claim.status = "duplicate"

    def test_completed_event_is_duplicate_after_new_store_instance(self):
        claim = self.store.claim([event()])
        self.store.complete(claim)
        replay = InboxStore(self.path).claim([event()])
        self.assertEqual(replay, InboxClaim("duplicate", None, (), 1))
        self.assertEqual(self.rows()[0][3], "processed")

    def test_processing_survives_restart_and_blocks_replay(self):
        self.store.claim([event()])
        self.assertEqual(InboxStore(self.path).claim([event()]).status, "blocked")
        self.assertEqual(self.rows()[0][3], "processing")

    def test_uncertain_result_survives_restart_without_automatic_retry(self):
        claim = self.store.claim([event()])
        self.store.mark_uncertain(claim)
        replay = InboxStore(self.path).claim([event()])
        self.assertEqual(replay, InboxClaim("blocked", None, (), 1))
        self.assertEqual(self.rows()[0][3], "uncertain")

    def test_same_id_changed_type_remains_duplicate_without_overwriting_metadata(self):
        claim = self.store.claim([event()])
        self.store.complete(claim)
        changed = event(event_type="message_image")
        self.assertEqual(self.store.claim([changed]).status, "duplicate")
        self.assertEqual(self.rows()[0][1], "message_text")

    def test_fresh_ids_with_same_content_type_are_not_collapsed(self):
        first = self.store.claim([event()])
        second = self.store.claim([event("wamid.other-private")])
        self.assertEqual((first.status, second.status), ("new", "new"))
        self.assertNotEqual(first.request_id, second.request_id)
        self.assertEqual(len(self.rows()), 2)

    def test_statuses_out_of_order_are_independent_and_same_phase_replay_is_duplicate(self):
        for phase in ("status_read", "status_sent", "status_delivered"):
            item = event(event_type=phase, kind="status")
            claim = self.store.claim([item])
            self.assertEqual(claim.status, "new")
            self.store.complete(claim)
            # Timestamp/content are intentionally absent from the storage key API.
            self.assertEqual(self.store.claim([event(event_type=phase, kind="status")]).status, "duplicate")
        self.assertEqual(len(self.rows()), 3)

    def test_equal_provider_id_in_message_and_status_is_not_suppressed(self):
        message = self.store.claim([event()])
        self.store.complete(message)
        status = self.store.claim([event(event_type="status_sent", kind="status")])
        self.assertEqual(status.status, "new")
        self.assertEqual(len(self.rows()), 2)

    def test_duplicates_inside_batch_reserve_only_first_unique_event(self):
        changed = event(event_type="message_image")
        other = event("wamid.other-private")
        claim = self.store.claim([event(), changed, other, other])
        self.assertEqual(claim.status, "new")
        self.assertEqual(claim.events, (event(), other))
        self.assertEqual(claim.duplicate_count, 2)
        self.assertEqual(len(self.rows()), 2)

    def test_processed_and_fresh_batch_reserves_only_fresh(self):
        old = self.store.claim([event()])
        self.store.complete(old)
        fresh = event("wamid.fresh-private")
        claim = self.store.claim([event(), fresh, event()])
        self.assertEqual(claim.events, (fresh,))
        self.assertEqual(claim.duplicate_count, 2)
        self.store.complete(claim)
        self.assertEqual({row[3] for row in self.rows()}, {"processed"})

    def test_processed_fresh_and_pending_batch_inserts_nothing_even_after_uncertainty(self):
        completed = self.store.claim([event("wamid.processed-private")])
        self.store.complete(completed)
        pending_event = event("wamid.pending-private")
        pending = self.store.claim([pending_event])
        fresh = event("wamid.never-insert-private")
        batch = (completed.events[0], fresh, pending_event)
        before = self.rows()
        self.assertEqual(self.store.claim(batch), InboxClaim("blocked", None, (), 2))
        self.assertEqual(self.rows(), before)
        self.store.mark_uncertain(pending)
        self.assertEqual(self.store.claim(batch).status, "blocked")
        self.assertEqual(len(self.rows()), 2)
        self.assertNotIn(fresh.event_key, {row[0] for row in self.rows()})

    def test_complete_rejects_wrong_owner_missing_keys_and_changed_types_atomically(self):
        claim = self.store.claim([event(), event("wamid.second-private")])
        originals = self.rows()
        for forged in (replace(claim, request_id=str(uuid4())),
                       replace(claim, events=claim.events[:1]),
                       replace(claim, events=(InboxEvent(claim.events[0].event_key, "message_image"),
                                              claim.events[1]))):
            with self.subTest(request_id_changed=forged.request_id != claim.request_id):
                with self.assertRaises(InboxStoreError):
                    self.store.complete(forged)
                self.assertEqual(self.rows(), originals)
        self.store.complete(claim)
        with self.assertRaises(InboxStoreError):
            self.store.complete(claim)
        self.assertEqual({row[3] for row in self.rows()}, {"processed"})

    def test_insert_failure_rolls_back_entire_claim(self):
        self.store.initialize()
        second = event("wamid.second-private")
        self.sql(
            "CREATE TRIGGER fail_insert BEFORE INSERT ON inbox_events WHEN NEW.event_key='" +
            second.event_key + "' BEGIN SELECT RAISE(ABORT,'PRIVATE_DATABASE_FAILURE'); END"
        )
        with self.assertRaises(InboxStoreError) as result:
            self.store.claim([event(), second])
        self.assertNotIn("PRIVATE_DATABASE_FAILURE", str(result.exception))
        self.assertEqual(self.rows(), [])

    def test_partial_update_row_count_mismatch_rolls_back_all_keys(self):
        claim = self.store.claim([event(), event("wamid.second-private")])
        self.sql(
            "CREATE TRIGGER skip_update BEFORE UPDATE OF state ON inbox_events WHEN OLD.event_key='" +
            claim.events[1].event_key + "' AND NEW.state='processed' BEGIN SELECT RAISE(IGNORE); END"
        )
        with self.assertRaises(InboxStoreError):
            self.store.complete(claim)
        self.assertEqual({row[3] for row in self.rows()}, {"processing"})
        self.assertEqual(self.store.claim(claim.events).status, "blocked")

    def test_uncertainty_storage_failure_leaves_processing_and_blocks_replay(self):
        claim = self.store.claim([event()])
        self.sql(
            "CREATE TRIGGER fail_uncertain BEFORE UPDATE OF state ON inbox_events "
            "WHEN NEW.state='uncertain' BEGIN SELECT RAISE(ABORT,'PRIVATE_FAILURE'); END"
        )
        with self.assertRaises(InboxStoreError):
            self.store.mark_uncertain(claim)
        self.assertEqual(self.rows()[0][3], "processing")
        self.assertEqual(self.store.claim([event()]).status, "blocked")

    def test_two_instances_racing_threads_have_one_new_claim(self):
        barrier = Barrier(2)
        def worker():
            store = InboxStore(self.path)
            barrier.wait(timeout=3)
            return store.claim([event()])
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            claims = [future.result(timeout=5) for future in futures]
        self.assertCountEqual([claim.status for claim in claims], ["new", "blocked"])
        self.assertEqual(len(self.rows()), 1)

    def test_two_processes_share_the_same_sqlite_claim_boundary(self):
        code = (
            "import sys; from app.whatsapp_inbox import InboxStore,InboxEvent; "
            "store=InboxStore(sys.argv[1]); item=InboxEvent(sys.argv[2],'message_text'); "
            "print('ready',flush=True); sys.stdin.readline(); "
            "print(store.claim([item]).status,flush=True)"
        )
        processes = [subprocess.Popen(
            [sys.executable, "-B", "-c", code, str(self.path), event().event_key],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        ) for _ in range(2)]
        try:
            for process in processes:
                self.assertEqual(process.stdout.readline().strip(), "ready")
            for process in processes:
                process.stdin.write("go\n")
                process.stdin.flush()
            outcomes = [process.communicate(timeout=8) for process in processes]
            self.assertCountEqual([stdout.strip() for stdout, _stderr in outcomes], ["new", "blocked"])
            for process, (_stdout, stderr) in zip(processes, outcomes):
                self.assertEqual(process.returncode, 0, stderr)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def test_busy_database_times_out_without_inserting_or_exposing_sql(self):
        self.store.initialize()
        lock = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(lock.close)
        lock.execute("BEGIN IMMEDIATE")
        started = monotonic()
        with self.assertRaises(InboxStoreError) as result:
            self.store.claim([event()])
        elapsed = monotonic() - started
        lock.rollback()
        self.assertEqual(BUSY_TIMEOUT_MS, 1000)
        self.assertLess(elapsed, 2.5)
        self.assertNotIn(str(self.path), str(result.exception))
        self.assertEqual(self.rows(), [])

    def test_database_contains_hashes_and_safe_metadata_without_raw_identifiers(self):
        claim = self.store.claim([event()])
        self.store.complete(claim)
        with closing(sqlite3.connect(self.path)) as connection:
            dump = "\n".join(connection.iterdump())
            columns = [row[1] for row in connection.execute("PRAGMA table_info(inbox_events)")]
        self.assertEqual(columns, ["event_key", "event_type", "request_id", "state", "created_at", "updated_at"])
        for private in (PRIVATE_ID, PRIVATE_PHONE, "text_not_for_database"):
            self.assertNotIn(private, dump)
            self.assertNotIn(private.encode(), self.path.read_bytes())

    def test_connections_are_closed_after_operations(self):
        claim = self.store.claim([event()])
        self.store.complete(claim)
        moved = self.path.with_name("closed-inbox.sqlite3")
        self.path.rename(moved)
        self.assertTrue(moved.exists())

    def test_unsupported_version_is_rejected_without_overwriting_data(self):
        self.store.initialize()
        self.sql("UPDATE inbox_metadata SET value='2' WHERE name='schema_version'")
        before = self.path.read_bytes()
        with self.assertRaises(InboxStoreError):
            self.store.claim([event()])
        self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_file_is_rejected_without_replacing_it(self):
        self.path.parent.mkdir()
        original = b"not-a-sqlite-file PRIVATE_CORRUPT_PAYLOAD"
        self.path.write_bytes(original)
        with self.assertRaises(InboxStoreError) as result:
            self.store.initialize()
        self.assertNotIn("PRIVATE_CORRUPT_PAYLOAD", str(result.exception))
        self.assertEqual(self.path.read_bytes(), original)

    def test_foreign_database_is_not_adopted_or_migrated(self):
        self.path.parent.mkdir()
        self.sql("CREATE TABLE calendar_operations (title TEXT)")
        self.sql("INSERT INTO calendar_operations(title) VALUES(?)", (PRIVATE_ID,))
        before = self.path.read_bytes()
        with self.assertRaises(InboxStoreError):
            self.store.initialize()
        self.assertEqual(self.path.read_bytes(), before)

    def test_incomplete_schema_and_invalid_persisted_state_fail_closed(self):
        self.store.initialize()
        self.sql("DROP TABLE inbox_events")
        with self.assertRaises(InboxStoreError):
            InboxStore(self.path).initialize()

    def test_initialization_scan_is_cached_but_schema_version_is_checked_per_operation(self):
        with patch.object(self.store, "_initialize_once", wraps=self.store._initialize_once) as initialize:
            claim = self.store.claim([event()])
            self.store.complete(claim)
            self.assertEqual(self.store.claim([event()]).status, "duplicate")
            initialize.assert_called_once()
        self.sql("UPDATE inbox_metadata SET value='2' WHERE name='schema_version'")
        with self.assertRaises(InboxStoreError):
            self.store.claim([event()])

    def test_technical_timestamps_are_persisted_without_provider_timestamp(self):
        claim = self.store.claim([event()])
        with closing(sqlite3.connect(self.path)) as connection:
            created, initial_updated = connection.execute(
                "SELECT created_at,updated_at FROM inbox_events"
            ).fetchone()
        self.assertEqual(created, initial_updated)
        self.assertTrue(created.endswith("+00:00"))
        self.store.complete(claim)
        with closing(sqlite3.connect(self.path)) as connection:
            after_created, updated = connection.execute(
                "SELECT created_at,updated_at FROM inbox_events"
            ).fetchone()
        self.assertEqual(created, after_created)
        self.assertTrue(updated.endswith("+00:00"))

    def test_invalid_batches_and_claims_do_not_write_new_events(self):
        for batch in ([], None, ["raw-id"], [event()] * 201):
            with self.subTest(batch_type=type(batch).__name__):
                with self.assertRaises(ValueError):
                    self.store.claim(batch)
        self.assertFalse(self.path.exists())
        for claim in (None, InboxClaim("duplicate", None, (), 1),
                      InboxClaim("new", "invalid", (event(),), 0),
                      InboxClaim("new", str(uuid4()), (), 0)):
            with self.subTest(claim_type=type(claim).__name__):
                with self.assertRaises(ValueError):
                    self.store.complete(claim)


if __name__ == "__main__":
    unittest.main()
