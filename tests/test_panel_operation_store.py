"""Persistent panel proposal ownership, idempotence and competing user actions."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from uuid import uuid4

from app.panel_operation_store import PanelOperationError, PanelOperationStore


class PanelOperationStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'panel-operations.sqlite3'
        self.now = 1000.0
        self.session = 'a' * 64
        self.other = 'b' * 64
        self.digest = 'c' * 64
        self.store = PanelOperationStore(self.path, clock=lambda: self.now)

    def reserve(self, **changes):
        fields = {'session_hash': self.session, 'request_id': str(uuid4()),
                  'action': 'edit', 'payload_digest': self.digest}
        fields.update(changes)
        return self.store.reserve(**fields)

    def pending(self):
        record = self.reserve().record
        cid = str(uuid4())
        record = self.store.attach_context(self.session, record.operation_id, cid)
        return self.store.attach_preview(self.session, record.operation_id, str(uuid4()),
                                         self.now + 300, {'before': {'title': 'Original'}, 'after': {'title': 'Novo'}})

    def test_duplicate_request_returns_original_operation_after_reopening_database(self):
        request = str(uuid4())
        first = self.reserve(request_id=request)
        second = PanelOperationStore(self.path, clock=lambda: self.now).reserve(self.session, request, 'edit', self.digest)
        self.assertTrue(first.claimed)
        self.assertFalse(second.claimed)
        self.assertEqual(first.record, second.record)

    def test_same_request_id_cannot_change_action_or_payload(self):
        request = str(uuid4())
        self.reserve(request_id=request)
        for action, digest in [('delete', self.digest), ('edit', 'd' * 64)]:
            with self.subTest(action=action, digest=digest), self.assertRaises(PanelOperationError) as caught:
                self.store.reserve(self.session, request, action, digest)
            self.assertEqual(caught.exception.code, 'panel_operation_request_changed')

    def test_session_boundary_is_enforced_for_every_operation(self):
        record = self.pending()
        self.assertIsNone(self.store.get(self.other, record.operation_id))
        actions = [lambda: self.store.claim_confirm(self.other, record.operation_id),
                   lambda: self.store.cancel(self.other, record.operation_id),
                   lambda: self.store.finish(self.other, record.operation_id, 'failed', {}),
                   lambda: self.store.attach_context(self.other, record.operation_id, str(uuid4())),
                   lambda: self.store.attach_preview(self.other, record.operation_id, str(uuid4()), self.now + 200, {})]
        for command in actions:
            with self.assertRaises(PanelOperationError) as caught:
                command()
            self.assertEqual(caught.exception.http_status, 404)
        self.assertEqual(self.store.get(self.session, record.operation_id).state, 'pending')

    def test_request_identifiers_are_scoped_to_session(self):
        request = str(uuid4())
        a = self.reserve(request_id=request).record
        b = self.reserve(session_hash=self.other, request_id=request).record
        self.assertNotEqual(a.operation_id, b.operation_id)

    def test_context_binding_is_immutable_and_preview_requires_it(self):
        record = self.reserve().record
        with self.assertRaises(PanelOperationError):
            self.store.attach_preview(self.session, record.operation_id, str(uuid4()), 1100, {})
        cid = str(uuid4())
        self.store.attach_context(self.session, record.operation_id, cid)
        self.store.attach_context(self.session, record.operation_id, cid)
        with self.assertRaises(PanelOperationError):
            self.store.attach_context(self.session, record.operation_id, str(uuid4()))
        bound = self.store.get(self.session, record.operation_id)
        self.assertEqual(bound.conversation_id, cid)

    def test_preview_binds_exact_data_and_cannot_be_replaced(self):
        record = self.pending()
        with self.assertRaises(PanelOperationError):
            self.store.attach_preview(self.session, record.operation_id, str(uuid4()), 1100, {'after': {'title': 'Another'}})
        stored = self.store.get(self.session, record.operation_id)
        self.assertEqual(stored.dto, record.dto)
        self.assertEqual(stored.proposal_id, record.proposal_id)

    def test_dto_is_a_persisted_snapshot_instead_of_shared_mutable_reference(self):
        record = self.pending()
        record.dto['after']['title'] = 'In-memory change'
        self.assertEqual(self.store.get(self.session, record.operation_id).dto['after']['title'], 'Novo')

    def test_preview_expiry_is_limited_by_both_store_and_confirmation(self):
        record = self.reserve().record
        self.store.attach_context(self.session, record.operation_id, str(uuid4()))
        expiry = datetime.fromtimestamp(self.now + 2000, tz=timezone.utc).isoformat()
        record = self.store.attach_preview(self.session, record.operation_id, str(uuid4()), expiry, {})
        self.assertEqual(record.expires_at, self.now + 600)
        self.now += 600
        self.assertEqual(self.store.get(self.session, record.operation_id).state, 'expired')
        claim = self.store.claim_confirm(self.session, record.operation_id)
        self.assertFalse(claim.claimed)
        self.assertEqual(claim.record.state, 'expired')

    def test_preparing_operations_expire_without_reuse_of_request(self):
        request = str(uuid4())
        record = self.reserve(request_id=request).record
        self.now += 601
        repeated = self.reserve(request_id=request)
        self.assertFalse(repeated.claimed)
        self.assertEqual(repeated.record.operation_id, record.operation_id)
        self.assertEqual(repeated.record.state, 'expired')
        with self.assertRaises(PanelOperationError):
            self.store.attach_context(self.session, record.operation_id, str(uuid4()))

    def test_only_one_parallel_preview_claim_is_created(self):
        request = str(uuid4())
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(lambda _: self.reserve(request_id=request), range(16)))
        self.assertEqual(sum(item.claimed for item in outcomes), 1)
        self.assertEqual(len({item.record.operation_id for item in outcomes}), 1)

    def test_only_one_parallel_confirmation_is_claimed(self):
        record = self.pending()
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(lambda _: self.store.claim_confirm(self.session, record.operation_id), range(16)))
        self.assertEqual(sum(item.claimed for item in outcomes), 1)
        self.assertTrue(all(item.record.state == 'confirming' for item in outcomes))

    def test_cancel_and_confirm_are_mutually_exclusive_under_race(self):
        record = self.pending()
        gate = Barrier(2)
        def confirm():
            gate.wait()
            return self.store.claim_confirm(self.session, record.operation_id).claimed
        def cancel():
            gate.wait()
            try:
                return self.store.cancel(self.session, record.operation_id).state == 'cancelled'
            except PanelOperationError as error:
                self.assertEqual(error.http_status, 409)
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(confirm), pool.submit(cancel)
            self.assertEqual(int(a.result()) + int(b.result()), 1)

    def test_cancel_is_idempotent_and_permanently_blocks_confirmation(self):
        record = self.pending()
        self.assertEqual(self.store.cancel(self.session, record.operation_id).state, 'cancelled')
        self.assertEqual(self.store.cancel(self.session, record.operation_id).state, 'cancelled')
        self.assertFalse(self.store.claim_confirm(self.session, record.operation_id).claimed)
        with self.assertRaises(PanelOperationError):
            self.store.finish(self.session, record.operation_id, 'succeeded', {})

    def test_confirming_is_never_reclaimed_after_process_loss_or_expiry(self):
        record = self.pending()
        self.assertTrue(self.store.claim_confirm(self.session, record.operation_id).claimed)
        self.now += 900
        reopened = PanelOperationStore(self.path, clock=lambda: self.now)
        retry = reopened.claim_confirm(self.session, record.operation_id)
        self.assertFalse(retry.claimed)
        self.assertEqual(retry.record.state, 'confirming')
        with self.assertRaises(PanelOperationError):
            reopened.cancel(self.session, record.operation_id)

    def test_result_is_stable_after_confirmation_and_replays_cannot_modify_it(self):
        record = self.pending()
        self.store.claim_confirm(self.session, record.operation_id)
        result = {'mutation_state': 'applied', 'event': {'title': 'Novo'}}
        done = self.store.finish(self.session, record.operation_id, 'succeeded', result)
        self.assertEqual(self.store.finish(self.session, record.operation_id, 'succeeded', result), done)
        self.assertFalse(self.store.claim_confirm(self.session, record.operation_id).claimed)
        for state, changed in [('failed', result), ('succeeded', {'mutation_state': 'unknown'})]:
            with self.assertRaises(PanelOperationError):
                self.store.finish(self.session, record.operation_id, state, changed)

    def test_unknown_is_terminal_and_not_a_reason_to_retry_execution(self):
        record = self.pending()
        self.store.claim_confirm(self.session, record.operation_id)
        self.store.finish(self.session, record.operation_id, 'unknown', {'mutation_state': 'unknown'})
        self.assertFalse(self.store.claim_confirm(self.session, record.operation_id).claimed)
        with self.assertRaises(PanelOperationError):
            self.store.finish(self.session, record.operation_id, 'succeeded', {})

    def test_preparation_failure_can_be_recorded_without_enabling_execution(self):
        record = self.reserve().record
        self.assertEqual(self.store.finish(self.session, record.operation_id, 'failed', {'error': 'invalid_request'}).state, 'failed')
        self.assertFalse(self.store.claim_confirm(self.session, record.operation_id).claimed)

    def test_pending_operation_cannot_be_marked_successful_without_claim(self):
        record = self.pending()
        with self.assertRaises(PanelOperationError):
            self.store.finish(self.session, record.operation_id, 'succeeded', {})

    def test_open_limit_allows_new_request_after_cancel(self):
        store = PanelOperationStore(self.path, clock=lambda: self.now, max_pending=1)
        record = self.pending()
        with self.assertRaises(PanelOperationError) as caught:
            store.reserve(self.session, str(uuid4()), 'delete', self.digest)
        self.assertEqual(caught.exception.http_status, 429)
        store.cancel(self.session, record.operation_id)
        self.assertTrue(store.reserve(self.session, str(uuid4()), 'delete', self.digest).claimed)

    def test_capacity_limit_does_not_evict_replay_records(self):
        store = PanelOperationStore(self.path, clock=lambda: self.now, max_entries=1)
        record = self.reserve().record
        self.now += 700
        self.assertEqual(store.get(self.session, record.operation_id).state, 'expired')
        with self.assertRaises(PanelOperationError) as caught:
            store.reserve(self.session, str(uuid4()), 'create', self.digest)
        self.assertEqual(caught.exception.http_status, 503)

    def test_invalid_identity_and_timestamp_fail_closed(self):
        for identity in ['', None, 'A' * 64, 'a' * 63]:
            with self.subTest(identity=identity), self.assertRaises(PanelOperationError):
                self.store.get(identity, str(uuid4()))
        for stamp in [float('nan'), float('inf'), -1, True, datetime.now()]:
            store = PanelOperationStore(self.path, clock=lambda: stamp)
            with self.subTest(stamp=stamp), self.assertRaises(PanelOperationError):
                store.reserve(self.session, str(uuid4()), 'create', self.digest)

    def test_invalid_parameters_are_rejected_before_storage(self):
        for change in [{'action': 'send'}, {'action': []}, {'request_id': 'not-uuid'}, {'payload_digest': 'x'}]:
            with self.subTest(change=change), self.assertRaises(PanelOperationError):
                self.reserve(**change)
        record = self.reserve().record
        self.store.attach_context(self.session, record.operation_id, str(uuid4()))
        for dto in [[], {'number': float('nan')}, {'text': 'x' * 140000}]:
            with self.subTest(dto_type=type(dto)), self.assertRaises(PanelOperationError):
                self.store.attach_preview(self.session, record.operation_id, str(uuid4()), 1100, dto)

    def test_repr_does_not_reveal_private_context_or_snapshot(self):
        record = self.pending()
        for secret in [record.operation_id, record.request_id, record.conversation_id, record.proposal_id,
                       record.payload_digest, 'Original', 'Novo']:
            self.assertNotIn(secret, repr(record))

    def test_malformed_existing_schema_and_corrupt_record_are_rejected(self):
        self.store.initialize()
        record = self.pending()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE panel_operations SET state='succeeded', proposal_id=NULL WHERE operation_id=?", (record.operation_id,))
        with self.assertRaises(PanelOperationError):
            self.store.get(self.session, record.operation_id)
        other_path = self.path.with_name('wrong.sqlite3')
        with closing(sqlite3.connect(other_path)) as connection, connection:
            connection.execute('CREATE TABLE panel_operations (id TEXT)')
        with self.assertRaises(PanelOperationError):
            PanelOperationStore(other_path).initialize()

    def test_clock_rollback_does_not_reopen_or_confirm_a_record(self):
        record = self.pending()
        self.now -= 1
        with self.assertRaises(PanelOperationError):
            self.store.claim_confirm(self.session, record.operation_id)


if __name__ == '__main__':
    unittest.main()
