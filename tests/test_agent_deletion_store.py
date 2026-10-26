"""Ledger de exclusão com SQLite e concorrência reais; sem provedores."""
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

from app.agent_deletion_store import AgentDeletionError, AgentDeletionStore, deletion_operation_id
from app.confirmation_state import ConfirmationProposal, proposal_request_hash

OWNER=sha256(b'fixture-private-deletion-owner').hexdigest()
OTHER=sha256(b'fixture-other-deletion-owner').hexdigest()
NOW=datetime(2030,6,17,12,tzinfo=timezone.utc)
EVENT={'event_id':'FixtureExactEvent','title':'Entrevista privada fictícia',
    'start':'2030-06-17T10:00:00-03:00','end':'2030-06-17T10:30:00-03:00',
    'all_day':False,'time_zone':'America/Recife','status':'confirmed','html_link':'https://example.invalid/event'}
PARAMETERS={'event_id':EVENT['event_id'],'time_zone':'America/Recife'}
RESULT={'result':'deleted','calendar_name':'Genius Testes','event_id':EVENT['event_id'],'recovered':False}

def proposal(**changes):
    values=dict(proposal_id=str(uuid4()),action='delete',parameters=dict(PARAMETERS),request_hash='',
        calendar_id='private-deletion-calendar',event_id=EVENT['event_id'],event_version='private-version-1',event=dict(EVENT),
        created_at=NOW.isoformat(),expires_at=(NOW+timedelta(seconds=300)).isoformat(),state='approved',
        approved_at=NOW.isoformat(),approval_message_hash=sha256(b'fictional-confirmo').hexdigest())
    values.update(changes)
    values['request_hash']=proposal_request_hash(values['action'],values['parameters'],values['calendar_id'],
        values['event_id'],values['event_version'],values['event'])
    return ConfirmationProposal(**values)

class AgentDeletionStoreTests(unittest.TestCase):
    def setUp(self):
        temporary=TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(temporary.cleanup)
        self.folder=Path(temporary.name)
        self.path=self.folder/'private'/'agent-deletions.sqlite3'
        self.now=NOW
        self.store=AgentDeletionStore(self.path,clock=lambda:self.now)
        self.cid=str(uuid4())
        self.proposal=proposal()
    def reserve(self,value=None):
        return self.store.reserve(OWNER,self.cid,self.proposal if value is None else value)
    def finish(self,record,state,result=None):
        return self.store.finish(OWNER,self.cid,record.operation_id,record.attempt_id,state,result)
    def error(self,call,code):
        with self.assertRaises(AgentDeletionError) as raised:
            call()
        self.assertEqual(raised.exception.code,code)
        for private in (OWNER,OTHER,self.cid,str(self.path),EVENT['title'],self.proposal.calendar_id,self.proposal.event_version):
            self.assertNotIn(private,str(raised.exception))
            self.assertNotIn(private,repr(raised.exception))
    def test_invalid_inputs_do_not_create_database(self):
        for owner,cid,value in [('bad',self.cid,self.proposal),(OWNER,'bad',self.proposal),
            (OWNER,self.cid,replace(self.proposal,state='pending',approved_at=None,approval_message_hash=None)),
            (OWNER,self.cid,proposal(action='edit',parameters={**PARAMETERS,'title':'Other'})),
            (OWNER,self.cid,replace(self.proposal,request_hash='f'*64)),
            (OWNER,self.cid,proposal(calendar_id='bad calendar')),
            (OWNER,self.cid,proposal(event_version='bad\nversion'))]:
            self.error(lambda:self.store.reserve(owner,cid,value),'deletion_invalid')
        self.assertFalse(self.path.exists())
    def test_invalid_clock_and_capacity_fail_before_database(self):
        self.error(lambda:AgentDeletionStore(self.path,clock=lambda:datetime(2030,1,1)).reserve(OWNER,self.cid,self.proposal),'deletion_unavailable')
        self.assertFalse(self.path.exists())
        for limit in (0,10001,True,'2'):
            self.error(lambda:AgentDeletionStore(self.path,max_entries=limit),'deletion_invalid')
    def test_stable_identity_and_single_claim_survive_restart(self):
        first=self.reserve()
        self.assertTrue(first.claimed)
        self.assertEqual(first.record.operation_id,deletion_operation_id(OWNER,self.cid,self.proposal))
        second=AgentDeletionStore(self.path).reserve(OWNER,self.cid,self.proposal)
        self.assertFalse(second.claimed)
        self.assertEqual(second.record,first.record)
    def test_fresh_proposal_has_new_identity_for_same_event(self):
        other=proposal()
        self.assertNotEqual(deletion_operation_id(OWNER,self.cid,self.proposal),deletion_operation_id(OWNER,self.cid,other))
    def test_record_copies_parameters_and_approved_event(self):
        value=self.reserve().record
        value.parameters['time_zone']='UTC'
        value.event['title']='Caller changed'
        current=self.store.get(OWNER,self.cid,self.proposal.proposal_id)
        self.assertEqual(current.parameters,PARAMETERS)
        self.assertEqual(current.event,EVENT)
        self.assertEqual(current.request_hash,self.proposal.request_hash)
    def test_owner_and_conversation_ids_are_hashed_in_database(self):
        first=self.reserve().record
        second=self.store.reserve(OTHER,self.cid,self.proposal).record
        cid=str(uuid4())
        third=self.store.reserve(OWNER,cid,self.proposal).record
        self.assertEqual(len({first.operation_id,second.operation_id,third.operation_id}),3)
        raw=self.path.read_bytes()
        for value in (OWNER,OTHER,self.cid,cid):
            self.assertNotIn(value.encode(),raw)
    def test_other_scope_does_not_find_or_finish_record(self):
        record=self.reserve().record
        self.assertIsNone(self.store.get(OTHER,self.cid,self.proposal.proposal_id))
        self.assertIsNone(self.store.active(OTHER,self.cid))
        self.error(lambda:self.store.finish(OTHER,self.cid,record.operation_id,record.attempt_id,'unknown'),'deletion_claim_conflict')
    def test_unknown_never_expires_or_reclaims(self):
        record=self.reserve().record
        unknown=self.finish(record,'unknown')
        self.now+=timedelta(days=365)
        value=self.reserve()
        self.assertFalse(value.claimed)
        self.assertEqual(value.record,unknown)
        self.assertEqual(self.store.active(OWNER,self.cid),unknown)
    def test_running_and_unknown_block_fresh_proposals(self):
        record=self.reserve().record
        self.error(lambda:self.reserve(proposal()),'deletion_unresolved')
        self.finish(record,'unknown')
        changed_event={**EVENT,'event_id':'FixtureOtherEvent'}
        other=proposal(parameters={**PARAMETERS,'event_id':'FixtureOtherEvent'},event_id='FixtureOtherEvent',event=changed_event)
        self.error(lambda:self.reserve(other),'deletion_unresolved')
    def test_same_proposal_id_cannot_change_any_binding(self):
        self.reserve()
        for changes in ({'calendar_id':'other-calendar'}, {'event_version':'version-2'}, {'event':{**EVENT,'title':'Other title'}}):
            self.error(lambda:self.reserve(proposal(proposal_id=self.proposal.proposal_id,**changes)),'deletion_proposal_changed')
    def test_not_applied_proposal_is_never_reclaimed(self):
        record=self.reserve().record
        saved=self.finish(record,'not_applied')
        self.now+=timedelta(seconds=1)
        repeated=self.reserve()
        self.assertFalse(repeated.claimed)
        self.assertEqual(repeated.record,saved)
        self.assertIsNone(self.store.active(OWNER,self.cid))
        fresh=self.reserve(proposal())
        self.assertTrue(fresh.claimed)
        self.assertNotEqual(fresh.record.operation_id,record.operation_id)
    def test_confirmed_receipt_is_immutable(self):
        record=self.reserve().record
        confirmed=self.finish(record,'confirmed',RESULT)
        self.assertEqual(self.finish(record,'confirmed',RESULT),confirmed)
        self.assertFalse(self.reserve().claimed)
        self.error(lambda:self.finish(record,'unknown'),'deletion_claim_conflict')
        self.error(lambda:self.finish(record,'confirmed',{**RESULT,'recovered':True}),'deletion_claim_conflict')
        self.assertIsNone(self.store.active(OWNER,self.cid))
    def test_fresh_approval_can_follow_confirmed_proposal(self):
        record=self.reserve().record
        self.finish(record,'confirmed',RESULT)
        fresh=self.reserve(proposal(event_version='version-2'))
        self.assertTrue(fresh.claimed)
        self.assertNotEqual(fresh.record.operation_id,record.operation_id)
    def test_unknown_can_be_confirmed_by_read_only_recovery(self):
        record=self.reserve().record
        self.finish(record,'unknown')
        found=self.finish(record,'confirmed',{**RESULT,'recovered':True})
        self.assertTrue(found.result['recovered'])
        self.assertEqual(found.event,EVENT)
    def test_already_absent_receipt_is_valid_without_claiming_delete(self):
        record=self.reserve().record
        absent={**RESULT,'result':'already_absent'}
        self.assertEqual(self.finish(record,'confirmed',absent).result,absent)
    def test_invalid_receipts_keep_claim_running(self):
        record=self.reserve().record
        for data in (None,{**RESULT,'unsafe':'private'}, {**RESULT,'event_id':'AnotherEvent'},
            {**RESULT,'recovered':1},{**RESULT,'calendar_name':'bad\nname'},
            {**RESULT,'result':'already_absent','recovered':True},{**RESULT,'result':'needs_confirmation'}):
            self.error(lambda:self.finish(record,'confirmed',data),'deletion_invalid')
        self.assertEqual(self.store.active(OWNER,self.cid),record)
    def test_foreign_nonce_and_unknown_operation_cannot_finish(self):
        record=self.reserve().record
        self.error(lambda:self.store.finish(OWNER,self.cid,record.operation_id,str(uuid4()),'confirmed',RESULT),'deletion_claim_conflict')
        self.error(lambda:self.store.finish(OWNER,self.cid,'agent-delete-'+'f'*64,record.attempt_id,'confirmed',RESULT),'deletion_claim_conflict')
        self.assertEqual(self.store.active(OWNER,self.cid),record)
    def test_unknown_cannot_be_downgraded_to_not_applied(self):
        record=self.reserve().record
        self.finish(record,'unknown')
        self.error(lambda:self.finish(record,'not_applied'),'deletion_claim_conflict')
        self.assertEqual(self.store.active(OWNER,self.cid).state,'unknown')
    def test_all_day_approved_snapshot_can_be_reserved(self):
        event={**EVENT,'all_day':True,'start':'2030-06-17','end':'2030-06-18'}
        value=self.reserve(proposal(event=event)).record
        self.assertTrue(value.event['all_day'])
    def test_capacity_does_not_evict_uncertain_record(self):
        store=AgentDeletionStore(self.path,max_entries=1)
        record=store.reserve(OWNER,self.cid,self.proposal).record
        self.error(lambda:store.reserve(OTHER,self.cid,self.proposal),'deletion_unavailable')
        self.assertEqual(store.get(OWNER,self.cid,self.proposal.proposal_id),record)
    def test_threads_race_to_one_claim(self):
        barrier=Barrier(6)
        def claim(_):
            barrier.wait(timeout=5)
            return AgentDeletionStore(self.path).reserve(OWNER,self.cid,self.proposal)
        with ThreadPoolExecutor(max_workers=6) as pool:
            values=list(pool.map(claim,range(6)))
        self.assertEqual(sum(value.claimed for value in values),1)
        self.assertEqual(len({value.record.attempt_id for value in values}),1)
    def test_distinct_proposals_race_to_one_active_claim(self):
        barrier=Barrier(2)
        def claim(value):
            barrier.wait(timeout=5)
            try:
                return AgentDeletionStore(self.path).reserve(OWNER,self.cid,value).claimed
            except AgentDeletionError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            values=list(pool.map(claim,(self.proposal,proposal())))
        self.assertEqual(values.count(True),1)
        self.assertEqual(values.count('deletion_unresolved'),1)
    def test_processes_share_one_claim(self):
        gate=self.folder/'gate'
        source='''import json,sys,time
from pathlib import Path
from app.agent_deletion_store import AgentDeletionStore
from app.confirmation_state import ConfirmationProposal
Path(sys.argv[5]).write_text('ready')
deadline=time.monotonic()+10
while not Path(sys.argv[4]).exists():
    assert time.monotonic()<deadline
    time.sleep(.005)
value=AgentDeletionStore(sys.argv[1]).reserve(sys.argv[2],sys.argv[3],ConfirmationProposal(**json.loads(sys.argv[6])))
print(json.dumps({'claimed':value.claimed,'attempt':value.record.attempt_id}))
'''
        children=[subprocess.Popen([sys.executable,'-B','-c',source,str(self.path),OWNER,self.cid,str(gate),
            str(self.folder/f'ready-{i}'),json.dumps(asdict(self.proposal))],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) for i in range(2)]
        try:
            deadline=time.monotonic()+10
            while not all((self.folder/f'ready-{i}').exists() for i in range(2)):
                self.assertLess(time.monotonic(),deadline)
                time.sleep(.005)
            gate.write_text('go')
            outputs=[child.communicate(timeout=10) for child in children]
            self.assertEqual([child.returncode for child in children],[0,0],outputs)
            values=[json.loads(output[0]) for output in outputs]
            self.assertEqual(sum(value['claimed'] for value in values),1)
            self.assertEqual(len({value['attempt'] for value in values}),1)
        finally:
            for child in children:
                if child.poll() is None: child.terminate()
                child.wait(timeout=10)
    def test_processes_wait_for_released_lock_longer_than_one_second_and_share_one_claim(self):
        self.store.initialize()
        gate=self.folder/'lock-gate'
        source='''import json,sys,time
from pathlib import Path
from app.agent_deletion_store import AgentDeletionStore
from app.confirmation_state import ConfirmationProposal
Path(sys.argv[5]).write_text('ready')
deadline=time.monotonic()+10
while not Path(sys.argv[4]).exists():
    assert time.monotonic()<deadline
    time.sleep(.005)
started=time.monotonic()
Path(sys.argv[7]).write_text('attempting')
value=AgentDeletionStore(sys.argv[1]).reserve(sys.argv[2],sys.argv[3],ConfirmationProposal(**json.loads(sys.argv[6])))
print(json.dumps({'claimed':value.claimed,'attempt':value.record.attempt_id,'elapsed':time.monotonic()-started}))
'''
        children=[]
        try:
            with closing(sqlite3.connect(self.path,isolation_level=None)) as blocker:
                blocker.execute('BEGIN IMMEDIATE')
                children=[subprocess.Popen([sys.executable,'-B','-c',source,str(self.path),OWNER,self.cid,str(gate),
                    str(self.folder/f'lock-ready-{i}'),json.dumps(asdict(self.proposal)),str(self.folder/f'lock-attempt-{i}')],
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) for i in range(2)]
                deadline=time.monotonic()+10
                while not all((self.folder/f'lock-ready-{i}').exists() for i in range(2)):
                    self.assertLess(time.monotonic(),deadline)
                    time.sleep(.005)
                gate.write_text('go')
                while not all((self.folder/f'lock-attempt-{i}').exists() for i in range(2)):
                    self.assertLess(time.monotonic(),deadline)
                    time.sleep(.005)
                # O timeout antigo de 1 s falha antes desta liberação.
                time.sleep(1.3)
                blocker.rollback()
            outputs=[child.communicate(timeout=10) for child in children]
            self.assertEqual([child.returncode for child in children],[0,0],outputs)
            values=[json.loads(output[0]) for output in outputs]
            self.assertTrue(all(value['elapsed']>=1 for value in values))
            self.assertEqual(sum(value['claimed'] for value in values),1)
            self.assertEqual(len({value['attempt'] for value in values}),1)
            self.assertEqual(self.store.active(OWNER,self.cid).attempt_id,values[0]['attempt'])
        finally:
            for child in children:
                if child.poll() is None:child.terminate()
                child.wait(timeout=10)
    def test_foreign_future_and_corrupt_databases_fail_without_replacement(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b'private-corrupt-fixture')
        before=self.path.read_bytes()
        self.error(lambda:self.reserve(),'deletion_unavailable')
        self.assertEqual(self.path.read_bytes(),before)
        foreign=self.folder/'foreign.sqlite3'
        with closing(sqlite3.connect(foreign)) as connection:
            connection.execute('CREATE TABLE foreign_table(value TEXT)')
            connection.commit()
        before=foreign.read_bytes()
        self.error(lambda:AgentDeletionStore(foreign).active(OWNER,self.cid),'deletion_unavailable')
        self.assertEqual(foreign.read_bytes(),before)
        future=self.folder/'future.sqlite3'
        AgentDeletionStore(future).initialize()
        with closing(sqlite3.connect(future)) as connection:
            connection.execute("UPDATE agent_deletion_metadata SET value='99'")
            connection.commit()
        before=future.read_bytes()
        self.error(lambda:AgentDeletionStore(future).active(OWNER,self.cid),'deletion_unavailable')
        self.assertEqual(future.read_bytes(),before)
    def test_corrupt_hash_or_private_event_cannot_be_loaded(self):
        self.reserve()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute('UPDATE agent_deletions SET request_hash=?',('f'*64,))
            connection.commit()
        self.error(lambda:AgentDeletionStore(self.path).active(OWNER,self.cid),'deletion_unavailable')
    def test_commit_failure_rolls_back_without_losing_claim(self):
        record=self.reserve().record
        original=self.store._connection
        class Proxy:
            def __init__(self,connection): self.connection=connection
            def __getattr__(self,name): return getattr(self.connection,name)
            def commit(self): raise sqlite3.OperationalError('PRIVATE_COMMIT_FAILURE')
        @contextmanager
        def broken():
            with original() as connection: yield Proxy(connection)
        with patch.object(self.store,'_connection',broken):
            self.error(lambda:self.finish(record,'confirmed',RESULT),'deletion_unavailable')
        self.assertEqual(self.store.get(OWNER,self.cid,self.proposal.proposal_id),record)
    def test_frozen_private_representations(self):
        value=self.reserve()
        with self.assertRaises(FrozenInstanceError): value.claimed=False
        for encoded in (repr(value),repr(value.record)):
            for private in (value.record.operation_id,value.record.attempt_id,EVENT['title'],self.proposal.event_version):
                self.assertNotIn(private,encoded)

if __name__=='__main__': unittest.main()
