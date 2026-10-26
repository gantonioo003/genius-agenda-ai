"""Identidade e reserva persistente de criação, com SQLite real e sem provedores."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
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
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from app.agent_creation_store import AgentCreationError, AgentCreationStore, creation_operation_id

OWNER = sha256(b'fictional-owner-key').hexdigest()
OTHER = sha256(b'fictional-other-key').hexdigest()
NOW = datetime(2030, 6, 17, tzinfo=timezone.utc)
PARAMETERS = {'title': 'Entrevista fictícia privada', 'description': '',
    'start': '2030-06-17T10:00:00-03:00', 'end': '2030-06-17T10:30:00-03:00', 'time_zone': 'America/Recife'}
RESULT = {'event_id': 'FixtureCreatedEvent', 'html_link': 'https://example.invalid/event',
          'calendar_name': 'Genius Testes', 'reused': False, 'recovered': False}

class AgentCreationStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.path = self.folder / 'private' / 'agent-creations.sqlite3'
        self.now = NOW
        self.store = AgentCreationStore(self.path, clock=lambda: self.now)
        self.cid = str(uuid4())

    def reserve(self, **changes):
        return self.store.reserve(OWNER, self.cid, {**PARAMETERS, **changes}, 'fixture-calendar')

    def finish(self, record, state, result=None, **options):
        values = {'operation_id': record.operation_id, 'attempt_id': record.attempt_id, 'state': state, 'result': result}
        values.update(options)
        return self.store.finish(OWNER, self.cid, **values)

    def assert_error(self, function, code):
        with self.assertRaises(AgentCreationError) as raised:
            function()
        self.assertEqual(raised.exception.code, code)
        for private in (OWNER, OTHER, self.cid, str(self.path), PARAMETERS['title']):
            self.assertNotIn(private, str(raised.exception))
            self.assertNotIn(private, repr(raised.exception))

    def test_constructor_is_lazy_and_invalid_inputs_precede_io(self):
        self.assertFalse(self.path.exists())
        for owner, cid, params, calendar in (
            ('bad', self.cid, PARAMETERS, 'fixture'), (OWNER, 'bad', PARAMETERS, 'fixture'),
            (OWNER, self.cid.upper(), PARAMETERS, 'fixture'), (OWNER, self.cid, {}, 'fixture'),
            (OWNER, self.cid, PARAMETERS, ''), (OWNER, self.cid, PARAMETERS, 'wrong calendar'),
        ):
            self.assert_error(lambda: self.store.reserve(owner, cid, params, calendar), 'creation_invalid')
        self.assertFalse(self.path.exists())

    def test_invalid_clock_precedes_database_creation(self):
        store = AgentCreationStore(self.path, clock=lambda: datetime(2030, 6, 17))
        self.assert_error(lambda: store.reserve(OWNER, self.cid, PARAMETERS, 'fixture'), 'creation_unavailable')
        self.assertFalse(self.path.exists())

    def test_stable_operation_and_single_claim_survive_restart(self):
        first = self.reserve()
        self.assertTrue(first.claimed)
        self.assertEqual(first.record.state, 'running')
        self.assertEqual(first.record.operation_id, creation_operation_id(OWNER, self.cid, PARAMETERS))
        self.assertLessEqual(len(first.record.operation_id), 128)
        restarted = AgentCreationStore(self.path)
        second = restarted.reserve(OWNER, self.cid, PARAMETERS, 'fixture-calendar')
        self.assertFalse(second.claimed)
        self.assertEqual(second.record, first.record)

    def test_parameters_are_canonicalized_before_identity(self):
        first = self.reserve(title='  Entrevista fictícia privada  ')
        second = self.reserve()
        self.assertFalse(second.claimed)
        self.assertEqual(first.record.operation_id, second.record.operation_id)
        self.assertEqual(first.record.parameters, PARAMETERS)

    def test_caller_mutation_does_not_change_persisted_data(self):
        parameters = dict(PARAMETERS)
        reservation = self.store.reserve(OWNER, self.cid, parameters, 'fixture-calendar')
        parameters['title'] = 'changed caller'
        reservation.record.parameters['description'] = 'changed record'
        loaded = self.store.get(OWNER, self.cid, PARAMETERS)
        self.assertEqual(loaded.parameters, PARAMETERS)

    def test_owner_and_conversation_are_isolated_and_private_in_database(self):
        first = self.reserve()
        other = self.store.reserve(OTHER, self.cid, PARAMETERS, 'fixture-calendar')
        second_cid = str(uuid4())
        third = self.store.reserve(OWNER, second_cid, PARAMETERS, 'fixture-calendar')
        self.assertEqual(len({first.record.operation_id, other.record.operation_id, third.record.operation_id}), 3)
        raw = self.path.read_bytes()
        for private in (OWNER, OTHER, self.cid, second_cid):
            self.assertNotIn(private.encode(), raw)

    def test_unknown_outcome_is_never_claimed_again(self):
        first = self.reserve().record
        unknown = self.finish(first, 'unknown')
        self.now += timedelta(days=30)
        second = self.reserve()
        self.assertFalse(second.claimed)
        self.assertEqual(second.record, unknown)
        self.assertEqual(self.store.active(OWNER, self.cid), unknown)

    def test_unresolved_creation_blocks_different_intent(self):
        record = self.reserve().record
        self.assert_error(lambda: self.reserve(title='Outra intenção'), 'creation_unresolved')
        self.finish(record, 'unknown')
        self.assert_error(lambda: self.reserve(description='Novo conteúdo'), 'creation_unresolved')
        self.assertIsNone(self.store.get(OWNER, self.cid, {**PARAMETERS, 'title': 'Outra intenção'}))

    def test_calendar_is_immutable_for_same_request(self):
        self.reserve()
        self.assert_error(lambda: self.store.reserve(OWNER, self.cid, PARAMETERS, 'other-calendar'), 'creation_calendar_changed')

    def test_known_unapplied_and_conflict_can_be_explicitly_claimed_again(self):
        for terminal in ('not_applied', 'conflict'):
            with self.subTest(terminal=terminal):
                first = self.reserve().record
                self.finish(first, terminal)
                self.now += timedelta(seconds=1)
                second = self.reserve()
                self.assertTrue(second.claimed)
                self.assertEqual(first.operation_id, second.record.operation_id)
                self.assertNotEqual(first.attempt_id, second.record.attempt_id)
                self.assertEqual(first.created_at, second.record.created_at)

    def test_nonce_rejects_old_completion_after_known_retry(self):
        first = self.reserve().record
        self.finish(first, 'not_applied')
        second = self.reserve().record
        self.assert_error(lambda: self.finish(first, 'confirmed', RESULT), 'creation_claim_conflict')
        self.assertEqual(self.store.active(OWNER, self.cid), second)

    def test_confirmed_receipt_is_immutable_and_not_claimed_again(self):
        first = self.reserve().record
        confirmed = self.finish(first, 'confirmed', RESULT)
        self.assertEqual(self.finish(first, 'confirmed', RESULT), confirmed)
        self.assert_error(lambda: self.finish(first, 'unknown'), 'creation_claim_conflict')
        self.assert_error(lambda: self.finish(first, 'confirmed', {**RESULT, 'event_id': 'OtherEvent'}), 'creation_claim_conflict')
        self.assertFalse(self.reserve().claimed)
        self.assertEqual(self.store.get(OWNER, self.cid, PARAMETERS), confirmed)
        self.assertIsNone(self.store.active(OWNER, self.cid))

    def test_readonly_reconciliation_can_confirm_unknown(self):
        first = self.reserve().record
        self.finish(first, 'unknown')
        confirmed = self.finish(first, 'confirmed', {**RESULT, 'recovered': True})
        self.assertEqual(confirmed.state, 'confirmed')
        self.assertTrue(confirmed.result['recovered'])

    def test_confirmed_request_allows_a_different_new_intent(self):
        record = self.reserve().record
        self.finish(record, 'confirmed', RESULT)
        next_request = self.reserve(title='Outra entrevista')
        self.assertTrue(next_request.claimed)
        self.assertNotEqual(record.operation_id, next_request.record.operation_id)

    def test_invalid_completion_does_not_touch_claim(self):
        first = self.reserve().record
        for state, result in (('running', None), ('unknown', RESULT), ('confirmed', None),
                              ('confirmed', {**RESULT, 'reused': 1}), ('confirmed', {**RESULT, 'unsafe': 'private'}),
                              ('confirmed', {**RESULT, 'event_id': 'bad id'})):
            self.assert_error(lambda: self.finish(first, state, result), 'creation_invalid')
        self.assertEqual(self.store.active(OWNER, self.cid), first)

    def test_unknown_finish_cannot_be_reclassified_as_safe_to_retry(self):
        first = self.reserve().record
        self.finish(first, 'unknown')
        self.assert_error(lambda: self.finish(first, 'not_applied'), 'creation_claim_conflict')

    def test_capacity_rejects_new_rows_without_evicting_uncertain_record(self):
        store = AgentCreationStore(self.path, max_entries=1)
        existing = store.reserve(OWNER, self.cid, PARAMETERS, 'fixture-calendar')
        self.assert_error(lambda: store.reserve(OTHER, self.cid, PARAMETERS, 'fixture-calendar'), 'creation_unavailable')
        self.assertEqual(store.get(OWNER, self.cid, PARAMETERS), existing.record)

    def test_threads_race_to_one_claim(self):
        barrier = Barrier(6)
        def reserve():
            barrier.wait(timeout=5)
            return AgentCreationStore(self.path).reserve(OWNER, self.cid, PARAMETERS, 'fixture-calendar')
        with ThreadPoolExecutor(max_workers=6) as pool:
            values = list(pool.map(lambda _: reserve(), range(6)))
        self.assertEqual(sum(item.claimed for item in values), 1)
        self.assertEqual(len({item.record.attempt_id for item in values}), 1)

    def test_competing_contents_in_same_conversation_allow_one_active_claim(self):
        barrier = Barrier(2)
        def reserve(index):
            barrier.wait(timeout=5)
            try:
                return AgentCreationStore(self.path).reserve(OWNER, self.cid, {**PARAMETERS, 'title': str(index)}, 'fixture-calendar').claimed
            except AgentCreationError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, (1, 2)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count('creation_unresolved'), 1)

    def test_actual_processes_share_exactly_one_claim(self):
        # Both processes wait at a file barrier before reserving in independent SQLite connections.
        gate = self.folder / 'gate'
        source = '''import json,sys,time
from pathlib import Path
from app.agent_creation_store import AgentCreationStore
ready=Path(sys.argv[5]); ready.write_text('ready')
deadline=time.monotonic()+10
while not Path(sys.argv[4]).exists():
    assert time.monotonic()<deadline
    time.sleep(.005)
value=AgentCreationStore(sys.argv[1]).reserve(sys.argv[2],sys.argv[3],json.loads(sys.argv[6]),'fixture-calendar')
print(json.dumps({'claimed':value.claimed,'operation':value.record.operation_id,'attempt':value.record.attempt_id}))
'''
        children = [subprocess.Popen([sys.executable, '-B', '-c', source, str(self.path), OWNER, self.cid,
                    str(gate), str(self.folder / f'ready-{i}'), json.dumps(PARAMETERS)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) for i in range(2)]
        try:
            deadline = time.monotonic() + 10
            while not all((self.folder / f'ready-{i}').exists() for i in range(2)):
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.005)
            gate.write_text('go')
            outputs = [child.communicate(timeout=10) for child in children]
            self.assertEqual([child.returncode for child in children], [0, 0], outputs)
            values = [json.loads(output[0]) for output in outputs]
            self.assertEqual(sum(item['claimed'] for item in values), 1)
            self.assertEqual(len({item['attempt'] for item in values}), 1)
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=10)

    def test_foreign_future_and_corrupt_databases_fail_without_replacement(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b'not-sqlite-private-fixture')
        before = self.path.read_bytes()
        self.assert_error(lambda: self.reserve(), 'creation_unavailable')
        self.assertEqual(self.path.read_bytes(), before)
        other = self.folder / 'foreign.sqlite3'
        with closing(sqlite3.connect(other)) as connection:
            connection.execute('CREATE TABLE foreign_table(value TEXT)')
            connection.commit()
        before = other.read_bytes()
        self.assert_error(lambda: AgentCreationStore(other).get(OWNER, self.cid, PARAMETERS), 'creation_unavailable')
        self.assertEqual(other.read_bytes(), before)
        fresh = self.folder / 'future.sqlite3'
        AgentCreationStore(fresh).initialize()
        with closing(sqlite3.connect(fresh)) as connection:
            connection.execute("UPDATE agent_creation_metadata SET value='99'")
            connection.commit()
        before = fresh.read_bytes()
        self.assert_error(lambda: AgentCreationStore(fresh).active(OWNER, self.cid), 'creation_unavailable')
        self.assertEqual(fresh.read_bytes(), before)

    def test_corrupt_row_is_not_returned_as_valid_receipt(self):
        self.reserve()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("UPDATE agent_creations SET request_hash=?", ('f' * 64,))
            connection.commit()
        self.assert_error(lambda: AgentCreationStore(self.path).get(OWNER, self.cid, PARAMETERS), 'creation_unavailable')

    def test_finish_commit_failure_rolls_back_to_original_running_claim(self):
        first = self.reserve().record
        original = self.store._connection
        class Proxy:
            def __init__(self, connection):
                self.connection = connection
            def __getattr__(self, name):
                return getattr(self.connection, name)
            def commit(self):
                raise sqlite3.OperationalError('PRIVATE_COMMIT_FAILURE')
        @contextmanager
        def broken():
            with original() as connection:
                yield Proxy(connection)
        with patch.object(self.store, '_connection', broken):
            self.assert_error(lambda: self.finish(first, 'confirmed', RESULT), 'creation_unavailable')
        self.assertEqual(self.store.get(OWNER, self.cid, PARAMETERS), first)

    def test_records_are_frozen_and_private_representations_do_not_leak(self):
        reservation = self.reserve()
        with self.assertRaises(FrozenInstanceError):
            reservation.claimed = False
        for encoded in (repr(reservation), repr(reservation.record)):
            for private in (reservation.record.operation_id, reservation.record.attempt_id, PARAMETERS['title'], 'fixture-calendar'):
                self.assertNotIn(private, encoded)

if __name__ == '__main__':
    unittest.main()
