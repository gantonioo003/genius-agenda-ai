"""Reserva durável de alterações com SQLite real, sem provedores."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from dataclasses import asdict, FrozenInstanceError, replace
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

from app.agent_update_store import AgentUpdateError, AgentUpdateStore, update_operation_id
from app.services.event_details import EventDetails
from app.services.update_snapshot import UpdateSnapshot

OWNER = sha256(b'fictional-update-owner').hexdigest()
OTHER = sha256(b'fictional-other-update-owner').hexdigest()
NOW = datetime(2030, 6, 17, tzinfo=timezone.utc)
PARAMETERS = {'event_id': 'FixtureExactEvent', 'time_zone': 'America/Recife', 'title': 'Novo título privado'}
SCHEDULE = {'event_id': 'FixtureExactEvent', 'time_zone': 'America/Recife',
            'start': '2030-06-17T11:00:00-03:00', 'end': '2030-06-17T11:30:00-03:00'}
BEFORE = EventDetails('FixtureExactEvent', 'Título privado anterior', '2030-06-17T10:00:00-03:00',
    '2030-06-17T10:30:00-03:00', False, 'America/Recife', 'confirmed', 'https://example.invalid/event', 'Descrição privada\nlinha 2', 'Local privado')
SNAPSHOT = UpdateSnapshot('private-calendar-fixture', 'Genius Testes', BEFORE, 'private-version-1', 'opaque')
RESULT = {'result': 'updated', 'calendar_name': 'Genius Testes', 'before': asdict(BEFORE),
          'after': asdict(replace(BEFORE, title=PARAMETERS['title'])), 'requested': {'title': PARAMETERS['title']}, 'recovered': False}

class AgentUpdateStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.path = self.folder / 'private' / 'agent-updates.sqlite3'
        self.now = NOW
        self.store = AgentUpdateStore(self.path, clock=lambda: self.now)
        self.cid = str(uuid4())

    def reserve(self, *, action='edit', parameters=None, snapshot=SNAPSHOT):
        return self.store.reserve(OWNER, self.cid, action, PARAMETERS if parameters is None else parameters, snapshot)

    def finish(self, record, state, result=None):
        return self.store.finish(OWNER, self.cid, record.operation_id, record.attempt_id, state, result)

    def assert_error(self, function, code):
        with self.assertRaises(AgentUpdateError) as raised:
            function()
        self.assertEqual(raised.exception.code, code)
        for private in (OWNER, OTHER, self.cid, str(self.path), PARAMETERS['title'], SNAPSHOT.version, SNAPSHOT.calendar_id):
            self.assertNotIn(private, str(raised.exception))
            self.assertNotIn(private, repr(raised.exception))

    def test_invalid_inputs_and_clock_do_not_create_database(self):
        for owner, cid, action, params, snapshot in (
            ('bad', self.cid, 'edit', PARAMETERS, SNAPSHOT), (OWNER, 'bad', 'edit', PARAMETERS, SNAPSHOT),
            (OWNER, self.cid, 'delete', PARAMETERS, SNAPSHOT), (OWNER, self.cid, 'edit', {}, SNAPSHOT),
            (OWNER, self.cid, 'edit', PARAMETERS, replace(SNAPSHOT, calendar_id='wrong calendar')),
            (OWNER, self.cid, 'edit', PARAMETERS, replace(SNAPSHOT, version='bad\nversion')),
            (OWNER, self.cid, 'edit', PARAMETERS, replace(SNAPSHOT, event=replace(BEFORE, event_id='OtherEvent'))),
        ):
            self.assert_error(lambda: self.store.reserve(owner, cid, action, params, snapshot), 'update_invalid')
        self.assertFalse(self.path.exists())
        self.assert_error(lambda: AgentUpdateStore(self.path, clock=lambda: datetime(2030, 1, 1)).reserve(OWNER, self.cid, 'edit', PARAMETERS, SNAPSHOT), 'update_unavailable')
        self.assertFalse(self.path.exists())

    def test_stable_identity_and_single_claim_survive_restart(self):
        first = self.reserve()
        self.assertTrue(first.claimed)
        self.assertEqual(first.record.operation_id, update_operation_id(OWNER, self.cid, 'edit', PARAMETERS))
        second = AgentUpdateStore(self.path).reserve(OWNER, self.cid, 'edit', PARAMETERS, SNAPSHOT)
        self.assertFalse(second.claimed)
        self.assertEqual(second.record, first.record)

    def test_canonical_parameters_keep_same_identity(self):
        first = self.reserve(parameters={**PARAMETERS, 'title': '  ' + PARAMETERS['title'] + '  '})
        self.assertEqual(first.record.parameters, PARAMETERS)
        self.assertFalse(self.reserve().claimed)

    def test_same_fields_in_distinct_action_do_not_share_identity(self):
        edit_id = update_operation_id(OWNER, self.cid, 'edit', PARAMETERS)
        schedule_id = update_operation_id(OWNER, self.cid, 'reschedule', SCHEDULE)
        self.assertNotEqual(edit_id, schedule_id)

    def test_snapshot_is_copied_and_keeps_unrequested_private_fields(self):
        first = self.reserve().record
        first.before['description'] = 'Caller mutation'
        first.parameters['title'] = 'Caller mutation'
        actual = self.store.get(OWNER, self.cid, 'edit', PARAMETERS)
        self.assertEqual(actual.before, asdict(BEFORE))
        self.assertEqual(actual.parameters, PARAMETERS)
        self.assertEqual(actual.event_version, SNAPSHOT.version)
        self.assertEqual(actual.transparency, SNAPSHOT.transparency)

    def test_owner_and_conversation_scopes_do_not_leak_in_sqlite(self):
        first = self.reserve().record
        second = self.store.reserve(OTHER, self.cid, 'edit', PARAMETERS, SNAPSHOT).record
        other_cid = str(uuid4())
        third = self.store.reserve(OWNER, other_cid, 'edit', PARAMETERS, SNAPSHOT).record
        self.assertEqual(len({first.operation_id, second.operation_id, third.operation_id}), 3)
        raw = self.path.read_bytes()
        for private in (OWNER, OTHER, self.cid, other_cid):
            self.assertNotIn(private.encode(), raw)

    def test_unknown_never_expires_or_reclaims(self):
        first = self.reserve().record
        expected = self.finish(first, 'unknown')
        self.now += timedelta(days=365)
        repeated = self.reserve()
        self.assertFalse(repeated.claimed)
        self.assertEqual(repeated.record, expected)
        self.assertEqual(self.store.active(OWNER, self.cid), expected)

    def test_unresolved_blocks_different_content_and_action(self):
        first = self.reserve().record
        self.assert_error(lambda: self.reserve(parameters={**PARAMETERS, 'location': ''}), 'update_unresolved')
        self.finish(first, 'unknown')
        self.assert_error(lambda: self.reserve(action='reschedule', parameters=SCHEDULE), 'update_unresolved')

    def test_calendar_is_immutable_in_record(self):
        self.reserve()
        self.assert_error(lambda: self.reserve(snapshot=replace(SNAPSHOT, calendar_id='other-fixture-calendar')), 'update_calendar_changed')

    def test_known_not_applied_and_conflict_allow_only_identical_snapshot_retry(self):
        for state in ('not_applied', 'conflict'):
            first = self.reserve().record
            self.finish(first, state)
            self.now += timedelta(seconds=1)
            for changed in (replace(SNAPSHOT, version='version-2'), replace(SNAPSHOT, transparency='transparent'),
                            replace(SNAPSHOT, event=replace(BEFORE, description='Changed externally'))):
                self.assert_error(lambda: self.reserve(snapshot=changed), 'update_snapshot_changed')
            second = self.reserve().record
            self.assertEqual(first.operation_id, second.operation_id)
            self.assertNotEqual(first.attempt_id, second.attempt_id)
            self.assertEqual(first.before, second.before)
            self.finish(second, 'not_applied')

    def test_stale_nonce_cannot_finish_retry(self):
        old = self.reserve().record
        self.finish(old, 'not_applied')
        new = self.reserve().record
        self.assert_error(lambda: self.finish(old, 'confirmed', RESULT), 'update_claim_conflict')
        self.assertEqual(self.store.active(OWNER, self.cid), new)

    def test_confirmed_receipt_is_immutable_and_not_claimed_again(self):
        first = self.reserve().record
        confirmed = self.finish(first, 'confirmed', RESULT)
        self.assertEqual(self.finish(first, 'confirmed', RESULT), confirmed)
        self.assertFalse(self.reserve().claimed)
        self.assert_error(lambda: self.finish(first, 'unknown'), 'update_claim_conflict')
        self.assert_error(lambda: self.finish(first, 'confirmed', {**RESULT, 'recovered': True}), 'update_claim_conflict')
        self.assertIsNone(self.store.active(OWNER, self.cid))
        self.assertTrue(self.reserve(parameters={**PARAMETERS, 'description': ''}).claimed)

    def test_read_only_reconciliation_can_confirm_unknown_once(self):
        first = self.reserve().record
        self.finish(first, 'unknown')
        result = self.finish(first, 'confirmed', {**RESULT, 'recovered': True})
        self.assertTrue(result.result['recovered'])
        self.assertEqual(result.before, asdict(BEFORE))

    def test_invalid_receipts_cannot_finish_running_claim(self):
        first = self.reserve().record
        for data in (None, {**RESULT, 'unsafe': 'private'}, {**RESULT, 'recovered': 1},
            {**RESULT, 'after': asdict(replace(BEFORE, title='Wrong target value'))},
            {**RESULT, 'after': asdict(replace(BEFORE, title=PARAMETERS['title'], location='Unrequested change'))},
            {**RESULT, 'after': asdict(replace(BEFORE, title=PARAMETERS['title'], status='tentative'))},
            {**RESULT, 'after': asdict(replace(BEFORE, title=PARAMETERS['title'], html_link='https://example.invalid/other'))},
            {**RESULT, 'before': asdict(replace(BEFORE, description='Wrong snapshot'))}):
            self.assert_error(lambda: self.finish(first, 'confirmed', data), 'update_invalid')
        self.assertEqual(self.store.active(OWNER, self.cid), first)

    def test_unknown_cannot_be_downgraded_to_retryable(self):
        first = self.reserve().record
        self.finish(first, 'unknown')
        for state in ('not_applied', 'conflict'):
            self.assert_error(lambda: self.finish(first, state), 'update_claim_conflict')

    def test_schedule_receipt_matches_interval_and_preserved_identity(self):
        record = self.reserve(action='reschedule', parameters=SCHEDULE).record
        before = {key: value for key, value in asdict(BEFORE).items() if key not in {'description', 'location'}}
        after = {**before, 'start': SCHEDULE['start'], 'end': SCHEDULE['end']}
        result = {'result': 'rescheduled', 'calendar_name': 'Genius Testes', 'before': before, 'after': after,
            'requested_start': SCHEDULE['start'], 'requested_end': SCHEDULE['end'], 'time_zone': SCHEDULE['time_zone'], 'recovered': False}
        self.assert_error(lambda: self.finish(record, 'confirmed', {**result, 'after': {**after, 'title': 'Unrequested'}}), 'update_invalid')
        self.assertEqual(self.finish(record, 'confirmed', result).result, result)

    def test_all_day_edit_allowed_but_reschedule_rejected_before_io(self):
        snapshot = replace(SNAPSHOT, event=replace(BEFORE, all_day=True, start='2030-06-17', end='2030-06-18'))
        self.assert_error(lambda: self.reserve(action='reschedule', parameters=SCHEDULE, snapshot=snapshot), 'update_invalid')
        self.assertFalse(self.path.exists())
        self.assertTrue(self.reserve(snapshot=snapshot).claimed)

    def test_untitled_schedule_receipt_uses_display_label_without_losing_raw_snapshot(self):
        snapshot = replace(SNAPSHOT, event=replace(BEFORE, title=''))
        record = self.reserve(action='reschedule', parameters=SCHEDULE, snapshot=snapshot).record
        before = {key: value for key, value in asdict(snapshot.event).items() if key not in {'description', 'location'}}
        before['title'] = '(sem título)'
        after = {**before, 'start': SCHEDULE['start'], 'end': SCHEDULE['end']}
        result = {'result': 'rescheduled', 'calendar_name': 'Genius Testes', 'before': before, 'after': after,
            'requested_start': SCHEDULE['start'], 'requested_end': SCHEDULE['end'], 'time_zone': SCHEDULE['time_zone'], 'recovered': False}
        confirmed = self.finish(record, 'confirmed', result)
        self.assertEqual(confirmed.before['title'], '')
        self.assertEqual(confirmed.result['after']['title'], '(sem título)')

    def test_capacity_fails_without_evicting_uncertain_record(self):
        store = AgentUpdateStore(self.path, max_entries=1)
        existing = store.reserve(OWNER, self.cid, 'edit', PARAMETERS, SNAPSHOT)
        self.assert_error(lambda: store.reserve(OTHER, self.cid, 'edit', PARAMETERS, SNAPSHOT), 'update_unavailable')
        self.assertEqual(store.get(OWNER, self.cid, 'edit', PARAMETERS), existing.record)

    def test_threads_race_to_exactly_one_claim(self):
        barrier = Barrier(6)
        def claim():
            barrier.wait(timeout=5)
            return AgentUpdateStore(self.path).reserve(OWNER, self.cid, 'edit', PARAMETERS, SNAPSHOT)
        with ThreadPoolExecutor(max_workers=6) as pool:
            values = list(pool.map(lambda _: claim(), range(6)))
        self.assertEqual(sum(value.claimed for value in values), 1)
        self.assertEqual(len({value.record.attempt_id for value in values}), 1)

    def test_distinct_intents_racing_allow_one_active_claim(self):
        barrier = Barrier(2)
        def claim(index):
            barrier.wait(timeout=5)
            try:
                return AgentUpdateStore(self.path).reserve(OWNER, self.cid, 'edit', {**PARAMETERS, 'title': str(index)}, SNAPSHOT).claimed
            except AgentUpdateError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(claim, (1, 2)))
        self.assertEqual(values.count(True), 1)
        self.assertEqual(values.count('update_unresolved'), 1)

    def test_processes_share_one_claim(self):
        gate = self.folder / 'gate'
        source = '''import json,sys,time
from pathlib import Path
from app.agent_update_store import AgentUpdateStore
from app.services.update_snapshot import UpdateSnapshot
from app.services.event_details import EventDetails
Path(sys.argv[5]).write_text('ready')
deadline=time.monotonic()+10
while not Path(sys.argv[4]).exists():
    assert time.monotonic()<deadline
    time.sleep(.005)
snapshot=json.loads(sys.argv[7]);snapshot['event']=EventDetails(**snapshot['event'])
value=AgentUpdateStore(sys.argv[1]).reserve(sys.argv[2],sys.argv[3],'edit',json.loads(sys.argv[6]),UpdateSnapshot(**snapshot))
print(json.dumps({'claimed':value.claimed,'operation':value.record.operation_id,'attempt':value.record.attempt_id}))
'''
        children = [subprocess.Popen([sys.executable, '-B', '-c', source, str(self.path), OWNER, self.cid,
            str(gate), str(self.folder / f'ready-{i}'), json.dumps(PARAMETERS), json.dumps(asdict(SNAPSHOT))],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) for i in range(2)]
        try:
            deadline = time.monotonic()+10
            while not all((self.folder / f'ready-{i}').exists() for i in range(2)):
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.005)
            gate.write_text('go')
            outputs = [child.communicate(timeout=10) for child in children]
            self.assertEqual([child.returncode for child in children], [0,0], outputs)
            values = [json.loads(output[0]) for output in outputs]
            self.assertEqual(sum(value['claimed'] for value in values), 1)
            self.assertEqual(len({value['attempt'] for value in values}), 1)
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=10)

    def test_foreign_future_and_corrupt_databases_fail_without_replacement(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b'private-corrupt-fixture')
        before = self.path.read_bytes()
        self.assert_error(lambda: self.reserve(), 'update_unavailable')
        self.assertEqual(self.path.read_bytes(), before)
        foreign = self.folder/'foreign.sqlite3'
        with closing(sqlite3.connect(foreign)) as connection:
            connection.execute('CREATE TABLE foreign_table(value TEXT)')
            connection.commit()
        before = foreign.read_bytes()
        self.assert_error(lambda: AgentUpdateStore(foreign).get(OWNER, self.cid, 'edit', PARAMETERS), 'update_unavailable')
        self.assertEqual(foreign.read_bytes(), before)
        future = self.folder/'future.sqlite3'
        AgentUpdateStore(future).initialize()
        with closing(sqlite3.connect(future)) as connection:
            connection.execute("UPDATE agent_update_metadata SET value='99'")
            connection.commit()
        before = future.read_bytes()
        self.assert_error(lambda: AgentUpdateStore(future).active(OWNER,self.cid), 'update_unavailable')
        self.assertEqual(future.read_bytes(), before)

    def test_corrupt_hash_and_snapshot_cannot_be_loaded(self):
        self.reserve()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute('UPDATE agent_updates SET request_hash=?', ('f'*64,))
            connection.commit()
        self.assert_error(lambda: AgentUpdateStore(self.path).active(OWNER,self.cid), 'update_unavailable')

    def test_commit_failure_rolls_back_without_losing_claim(self):
        record = self.reserve().record
        original = self.store._connection
        class Proxy:
            def __init__(self, connection):
                self.connection=connection
            def __getattr__(self,name):
                return getattr(self.connection,name)
            def commit(self):
                raise sqlite3.OperationalError('PRIVATE_COMMIT_FAILURE')
        @contextmanager
        def broken():
            with original() as connection:
                yield Proxy(connection)
        with patch.object(self.store,'_connection',broken):
            self.assert_error(lambda:self.finish(record,'confirmed',RESULT),'update_unavailable')
        self.assertEqual(self.store.get(OWNER,self.cid,'edit',PARAMETERS),record)

    def test_frozen_private_representations(self):
        reservation=self.reserve()
        with self.assertRaises(FrozenInstanceError):
            reservation.claimed=False
        for encoded in (repr(reservation),repr(reservation.record)):
            for private in (reservation.record.operation_id,reservation.record.attempt_id,PARAMETERS['title'],SNAPSHOT.calendar_id,SNAPSHOT.version):
                self.assertNotIn(private,encoded)

if __name__=='__main__':
    unittest.main()
