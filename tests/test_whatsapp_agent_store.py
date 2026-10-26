from contextlib import closing
"""Fila privada: SQLite real, fases duráveis e barreira de repetição por remetente."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch
from uuid import uuid4

from app.whatsapp_agent_store import AgentInput, AgentJob, AgentSession, AgentStore, AgentStoreError
from app.whatsapp_inbox import InboxClaim, InboxEvent, make_event_key

NOW=datetime(2030,6,17,12,tzinfo=timezone.utc)
SENDER='a'*64
OTHER='b'*64
PRIVATE='Texto privado para o agente, nunca em metadados públicos'
FINGERPRINT='f'*64

def message(number=1,kind='text'):
    event_type='message_'+kind
    return InboxEvent(make_event_key('message','fixture-event-'+str(number),event_type),event_type)

def claim(events): return InboxClaim('new',str(uuid4()),tuple(events),0)

class AgentStoreTests(unittest.TestCase):
    def setUp(self):
        temporary=TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path=Path(temporary.name)/'agent.sqlite3'
        self.store=AgentStore(self.path,clock=lambda:NOW)
    def stage(self,number=1,*,sender=SENDER,kind='text',text=PRIVATE,store=None):
        event=message(number,kind)
        batch=claim((event,))
        (store or self.store).stage(batch,(AgentInput(event.event_key,event.event_type,text if kind=='text' else None),),sender)
        return batch
    def queued(self,number=1,**kw):
        batch=self.stage(number,**kw)
        self.store.activate(batch.request_id)
        return batch
    def sent(self,number=1):
        self.queued(number)
        processing=self.store.claim_next()
        self.store.prepare(processing,'Resposta preparada')
        delivering=self.store.claim_next()
        self.store.complete(delivering,FINGERPRINT)
        return delivering
    def test_constructor_has_no_filesystem_side_effect(self):
        self.assertFalse(self.path.exists())
    def test_stage_persists_private_text_but_does_not_start_job(self):
        batch=self.stage()
        self.assertEqual(self.store.get_claim(batch.request_id).states,('staged',))
        self.assertIsNone(self.store.claim_next())
        with closing(sqlite3.connect(self.path)) as db, db:
            text,sender,provider=db.execute('SELECT text,sender_key,event_key FROM agent_jobs').fetchone()
        self.assertEqual(text,PRIVATE)
        self.assertEqual(sender,SENDER)
        self.assertNotIn('fixture-event',provider)
    def test_status_only_claim_never_creates_agent_database(self):
        event=InboxEvent(make_event_key('status','fixture-status','status_delivered'),'status_delivered')
        self.assertEqual(self.store.stage(claim((event,)),(),SENDER),0)
        self.assertFalse(self.path.exists())
    def test_mixed_claim_stages_only_message_inputs(self):
        event=message()
        status=InboxEvent(make_event_key('status','same','status_sent'),'status_sent')
        batch=claim((event,status))
        self.assertEqual(self.store.stage(batch,(AgentInput(event.event_key,event.event_type,PRIVATE),),SENDER),1)
        self.assertEqual(self.store.get_claim(batch.request_id).count,1)
    def test_activation_is_idempotent_and_unknown_id_does_not_create_work(self):
        batch=self.stage()
        for _ in range(3):
            activated=self.store.activate(batch.request_id)
            self.assertTrue(activated.known)
            self.assertEqual((activated.count,activated.state),(1,'queued'))
        self.assertFalse(self.store.activate(str(uuid4())).known)
        self.assertEqual(self.store.state_counts(),{'queued':1})
    def test_private_representations_never_expose_message_or_reply(self):
        self.queued()
        processing=self.store.claim_next()
        prepared=self.store.prepare(processing,'Resposta privada')
        for value in (AgentInput(processing.event_key,'message_text',PRIVATE),processing,prepared,AgentSession(str(uuid4()),'create')):
            self.assertNotIn(PRIVATE,repr(value))
            self.assertNotIn('Resposta privada',repr(value))
    def test_prepare_and_completion_logically_redact_content(self):
        self.queued()
        processing=self.store.claim_next()
        prepared=self.store.prepare(processing,'Resposta exata')
        self.assertIsNone(prepared.text)
        self.assertEqual(prepared.reply,'Resposta exata')
        delivering=self.store.claim_next()
        self.assertEqual(delivering.state,'delivering')
        sent=self.store.complete(delivering,FINGERPRINT)
        self.assertEqual(sent.state,'sent')
        self.assertIsNone(sent.text)
        self.assertIsNone(sent.reply)
        self.assertEqual(sent.fingerprint,FINGERPRINT)
        self.assertIsNone(self.store.claim_next())
    def test_fifo_staged_first_message_blocks_later_activated_message(self):
        first=self.stage(1)
        self.queued(2)
        self.assertIsNone(self.store.claim_next())
        self.store.activate(first.request_id)
        job=self.store.claim_next()
        self.assertEqual(job.request_id,first.request_id)
    def test_prepared_response_is_delivered_before_next_queued_turn(self):
        self.queued(1)
        self.queued(2)
        first=self.store.claim_next()
        self.store.prepare(first,'Primeira resposta')
        delivering=self.store.claim_next()
        self.assertEqual(delivering.job_id,first.job_id)
        self.assertIsNone(self.store.claim_next())
        self.store.complete(delivering,FINGERPRINT)
        self.assertNotEqual(self.store.claim_next().job_id,first.job_id)
    def test_separate_senders_can_progress_independently(self):
        self.queued(1)
        self.queued(2,sender=OTHER)
        first=self.store.claim_next()
        second=self.store.claim_next()
        self.assertEqual({first.sender_key,second.sender_key},{SENDER,OTHER})
    def test_uncertain_processing_blocks_followup_and_restart(self):
        self.queued(1)
        self.queued(2)
        first=self.store.claim_next()
        self.store.uncertain(first)
        self.assertIsNone(self.store.claim_next())
        restarted=AgentStore(self.path,clock=lambda:NOW)
        self.assertIsNone(restarted.claim_next())
        self.assertEqual(restarted.get_job(first.job_id).state,'uncertain')
    def test_delivery_uncertainty_retains_exact_reply_without_resending(self):
        self.queued()
        job=self.store.claim_next()
        self.store.prepare(job,'Resposta possivelmente enviada')
        delivery=self.store.claim_next()
        uncertain=self.store.uncertain(delivery)
        self.assertEqual(uncertain.reply,'Resposta possivelmente enviada')
        self.assertIsNone(AgentStore(self.path,clock=lambda:NOW).claim_next())
    def test_crash_during_processing_is_not_automatically_reclaimed(self):
        self.queued()
        first=self.store.claim_next()
        restarted=AgentStore(self.path,clock=lambda:NOW)
        self.assertIsNone(restarted.claim_next())
        self.assertEqual(restarted.get_job(first.job_id),first)
    def test_prepared_reply_resumes_after_restart_without_reinterpreting(self):
        self.queued()
        first=self.store.claim_next()
        self.store.prepare(first,'Resposta persistente')
        next_job=AgentStore(self.path,clock=lambda:NOW).claim_next()
        self.assertEqual(next_job.state,'delivering')
        self.assertEqual(next_job.reply,'Resposta persistente')
        self.assertIsNone(next_job.text)
    def test_stale_nonce_cannot_prepare_or_complete_work(self):
        self.queued()
        job=self.store.claim_next()
        with self.assertRaises(AgentStoreError):self.store.prepare(replace(job,attempt_id=str(uuid4())),'Resposta')
        self.store.prepare(job,'Resposta')
        delivery=self.store.claim_next()
        with self.assertRaises(AgentStoreError):self.store.complete(replace(delivery,attempt_id=job.attempt_id),FINGERPRINT)
        self.store.complete(delivery,FINGERPRINT)
        with self.assertRaises(AgentStoreError):self.store.complete(delivery,FINGERPRINT)
    def test_duplicate_stage_never_rebinds_request_or_sender(self):
        batch=self.stage()
        event=batch.events[0]
        inputs=(AgentInput(event.event_key,event.event_type,PRIVATE),)
        self.assertEqual(self.store.stage(batch,inputs,SENDER),1)
        with self.assertRaises(AgentStoreError):self.store.stage(claim((event,)),inputs,SENDER)
        with self.assertRaises(AgentStoreError):self.store.stage(batch,inputs,OTHER)
        self.assertEqual(self.store.state_counts(),{'staged':1})
    def test_input_must_match_exact_fresh_claim_and_contain_no_media_payload(self):
        event=message()
        for inputs in ((),(AgentInput('c'*64,'message_text',PRIVATE),),
            (AgentInput(event.event_key,'message_text',PRIVATE),)*2,
            (AgentInput(event.event_key,'message_image','Media payload forbidden'),)):
            with self.subTest(inputs=repr(inputs)),self.assertRaises(AgentStoreError):
                self.store.stage(claim((event,)),inputs,SENDER)
        self.assertFalse(self.path.exists())
    def test_nontext_message_records_only_type_and_identity(self):
        batch=self.stage(kind='image')
        self.store.activate(batch.request_id)
        job=self.store.claim_next()
        self.assertEqual(job.event_type,'message_image')
        self.assertIsNone(job.text)
    def test_limits_invalid_text_and_corrupt_identifiers_fail_before_database(self):
        event=message()
        for text in ('', 'x'*4097, '\ud800', '\0'):
            with self.subTest(text=repr(text)),self.assertRaises(AgentStoreError):
                self.store.stage(claim((event,)),(AgentInput(event.event_key,event.event_type,text),),SENDER)
        with self.assertRaises(AgentStoreError):self.store.stage(claim((event,)),(AgentInput(event.event_key,event.event_type,PRIVATE),),'phone')
        self.assertFalse(self.path.exists())
    def test_capacity_never_evicts_sent_or_uncertain_history(self):
        limited=AgentStore(self.path,clock=lambda:NOW,max_entries=1)
        self.stage(store=limited)
        with self.assertRaises(AgentStoreError):self.stage(2,store=limited)
        self.assertEqual(limited.state_counts(),{'staged':1})
    def test_known_rejection_redacts_reply_and_allows_only_next_turn(self):
        self.queued(1)
        self.queued(2)
        first=self.store.claim_next()
        with self.assertRaises(AgentStoreError):self.store.reject(first)
        self.store.prepare(first,'Resposta rejeitada')
        delivery=self.store.claim_next()
        rejected=self.store.reject(delivery)
        self.assertEqual(rejected.state,'rejected')
        self.assertIsNone(rejected.reply)
        self.assertIsNone(rejected.fingerprint)
        self.assertNotEqual(self.store.claim_next().job_id,first.job_id)
        with self.assertRaises(AgentStoreError):self.store.reject(delivery)
    def test_sessions_are_private_durable_and_actions_are_allowlisted(self):
        cid=str(uuid4())
        self.assertIsNone(self.store.get_session(SENDER))
        self.store.set_session(SENDER,cid,'create')
        self.assertEqual(AgentStore(self.path,clock=lambda:NOW).get_session(SENDER),AgentSession(cid,'create'))
        self.store.set_session(SENDER,cid,None)
        self.assertIsNone(self.store.get_session(SENDER).authorization_action)
        for action in ('delete','list','unknown'):
            with self.assertRaises(AgentStoreError):self.store.set_session(SENDER,cid,action)
        with self.assertRaises(AgentStoreError):self.store.set_session(SENDER,'wrong-uuid')
    def test_future_schema_and_foreign_database_are_not_overwritten(self):
        self.stage()
        with closing(sqlite3.connect(self.path)) as db, db:db.execute("UPDATE agent_queue_metadata SET value='999'")
        before=self.path.read_bytes()
        with self.assertRaises(AgentStoreError):AgentStore(self.path).initialize()
        self.assertEqual(self.path.read_bytes(),before)
        foreign=self.path.with_name('foreign.sqlite3')
        with closing(sqlite3.connect(foreign)) as db, db:db.execute('CREATE TABLE unrelated(value TEXT)')
        before=foreign.read_bytes()
        with self.assertRaises(AgentStoreError):AgentStore(foreign).initialize()
        self.assertEqual(foreign.read_bytes(),before)
    def test_corrupt_row_or_database_fails_closed(self):
        self.stage()
        with closing(sqlite3.connect(self.path)) as db, db:db.execute("UPDATE agent_jobs SET state='invented'")
        with self.assertRaises(AgentStoreError):self.store.claim_next()
        corrupt=self.path.with_name('corrupt.sqlite3')
        corrupt.write_bytes(b'not sqlite')
        with self.assertRaises(AgentStoreError):AgentStore(corrupt).initialize()
    def test_failed_prepare_commit_rolls_back_without_losing_input(self):
        self.queued()
        job=self.store.claim_next()
        original=sqlite3.connect
        class FailingCommit(sqlite3.Connection):
            def commit(self):raise sqlite3.OperationalError('PRIVATE_SQL_ERROR')
        def connection(*args,**kw):return original(*args,**kw,factory=FailingCommit)
        with patch('app.whatsapp_agent_store.sqlite3.connect',side_effect=connection):
            with self.assertRaises(AgentStoreError):self.store.prepare(job,'Resposta')
        self.assertEqual(self.store.get_job(job.job_id),job)
    def test_threads_claim_one_job_only(self):
        self.queued()
        def run(_):
            value=AgentStore(self.path,clock=lambda:NOW).claim_next()
            return None if value is None else value.job_id
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs=list(pool.map(run,range(4)))
        self.assertEqual(sum(job is not None for job in jobs),1)
    def test_processes_claim_one_job_only_and_keep_it_after_exit(self):
        self.queued()
        root=str(Path(__file__).resolve().parents[1])
        code="import sys;sys.path.insert(0,sys.argv[1]);from datetime import datetime,timezone;from app.whatsapp_agent_store import AgentStore;j=AgentStore(sys.argv[2],clock=lambda:datetime(2030,6,17,12,tzinfo=timezone.utc)).claim_next();print('none' if j is None else 'claimed')"
        children=[subprocess.Popen([sys.executable,'-B','-c',code,root,str(self.path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) for _ in range(2)]
        results=[]
        for child in children:
            out,err=child.communicate(timeout=20)
            self.assertEqual(child.returncode,0,err)
            results.append(out.strip())
        self.assertEqual(sorted(results),['claimed','none'])
        self.assertIsNone(AgentStore(self.path).claim_next())

if __name__=='__main__':unittest.main()
