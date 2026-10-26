"""Fila ativada por metadados e worker real isolados de provedores."""
import asyncio
from contextlib import closing
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock
from uuid import uuid4
from app.main import create_app
from app.settings import Settings
from app.services.whatsapp_agent import AgentTurn
from app.whatsapp_agent_store import AgentStore,AgentInput
from app.whatsapp_agent_worker import AgentWorker
from app.whatsapp_inbox import InboxStore,InboxClaim,InboxEvent
from app.whatsapp_outbox import OutboxStore
from app.whatsapp_sandbox_sender import SandboxSendError
from test_api_security import InjectedDatabase,TOKEN,WRONG_TOKEN,invoke_asgi

SENDER_KEY=hashlib.sha256(b'private fixture binding').hexdigest()
RECIPIENT='19175550123'
URL='/api/v1/whatsapp/sandbox/agent'

class FakeService:
    def __init__(self): self.calls=[];self.cid=str(uuid4());self.error=None
    def discard_unseen_confirmation(self,conversation_id):
        if conversation_id!=self.cid: raise ValueError()
    def process(self,message,**kwargs):
        self.calls.append((message,kwargs))
        if self.error: raise self.error
        return AgentTurn('Resposta dinâmica: '+str(message),self.cid,None,'succeeded','list','not_applicable')
class FakeSender:
    def __init__(self): self.calls=[];self.error=None
    def send(self,recipient,text):
        self.calls.append((recipient,text))
        if self.error: raise self.error
        return 'e'*64

class WhatsappAgentBoundaryTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.queue=AgentStore(self.root/'turns.sqlite3')
        self.inbox=InboxStore(self.root/'inbox.sqlite3')
        self.outbox=OutboxStore(self.root/'outbox.sqlite3')
        self.service=FakeService();self.sender=FakeSender()
        self.factory=Mock(return_value=self.queue)
        self.config=Settings(_env_file=None,api_auth_token=TOKEN,whatsapp_agent_enabled=True,openai_api_key=None)
        self.app=self.application()
    def application(self,**kwargs):
        app=create_app(self.config,InjectedDatabase(),whatsapp_agent_store_factory=self.factory,**kwargs)
        app.state.whatsapp_agent_inbox_factory=lambda:self.inbox
        return app
    def worker(self):
        return AgentWorker(store=self.queue,outbox=self.outbox,service_factory=lambda:self.service,
                           sender=self.sender,recipient=RECIPIENT,expected_sender_key=SENDER_KEY,poll_seconds=.01)
    def stage(self,text='PRIVATE_INPUT_FIXTURE',event_type='message_text'):
        key=hashlib.sha256(str(uuid4()).encode()).hexdigest()
        claim=self.inbox.claim((InboxEvent(key,event_type),))
        self.queue.stage(claim,(AgentInput(key,event_type,text if event_type=='message_text' else None),),SENDER_KEY)
        return claim
    def job(self,request_id):
        with closing(sqlite3.connect(self.queue.path)) as connection:
            job_id=connection.execute('SELECT job_id FROM agent_jobs WHERE request_id=?',(request_id,)).fetchone()[0]
        return self.queue.get_job(job_id)
    def request(self,body=None,*,raw=None,request_id=None,token=TOKEN,query=b'',app=None):
        if request_id is None:request_id=(body or {}).get('request_id',str(uuid4()))
        headers=[(b'content-type',b'application/json'),(b'x-request-id',request_id.encode())]
        if token is not None:headers.append((b'authorization',f'Bearer {token}'.encode()))
        response=asyncio.run(invoke_asgi(app or self.app,URL,method='POST',headers=headers,
            body=json.dumps(body).encode() if raw is None else raw,query=query))
        value=json.dumps(response)
        for secret in (TOKEN,WRONG_TOKEN,RECIPIENT,'PRIVATE_INPUT_FIXTURE','Resposta dinâmica'):
            self.assertNotIn(secret,value)
        self.assertEqual(response[1]['cache-control'],'no-store')
        return response
    def test_unauthorized_requests_precede_body_and_store(self):
        for token in (None,WRONG_TOKEN):self.assertEqual(self.request(raw=b'broken',token=token)[0],401)
        self.factory.assert_not_called()
    def test_disabled_mode_never_activates_jobs(self):
        claim=self.stage()
        self.config.whatsapp_agent_enabled=False
        self.assertEqual(self.request({'request_id':claim.request_id})[0],503)
        self.factory.assert_not_called()
        self.assertEqual(self.job(claim.request_id).state,'staged')
    def test_strict_body_rejects_text_recipient_and_repeated_fields(self):
        claim=self.stage();rid=claim.request_id
        for body in ({},{'request_id':rid,'text':'hello'},{'request_id':rid,'recipient':RECIPIENT},
            {'request_id':'invalid'},[],{'request_id':rid,'conversation_id':str(uuid4())}):
            self.assertEqual(self.request(body if isinstance(body,dict) else None,raw=json.dumps(body).encode(),request_id=rid)[0],422)
        for raw in (b'{',b' '*1025,('{"request_id":"'+rid+'","request_id":"'+rid+'"}').encode()):
            self.assertEqual(self.request(raw=raw,request_id=rid)[0],422)
        self.assertEqual(self.request({'request_id':rid},request_id=str(uuid4()))[0],422)
        self.assertEqual(self.request({'request_id':rid},query=b'anything=1')[0],422)
        self.factory.assert_not_called()
    def test_unknown_or_unstaged_message_claim_cannot_create_payload(self):
        self.assertEqual(self.request({'request_id':str(uuid4())})[0],409)
        key=hashlib.sha256(b'unstaged').hexdigest();claim=self.inbox.claim((InboxEvent(key,'message_text'),))
        self.assertEqual(self.request({'request_id':claim.request_id})[0],409)
        self.assertFalse(self.queue.get_claim(claim.request_id).known)
    def test_status_only_claim_is_skipped_without_model_or_send(self):
        key=hashlib.sha256(b'status').hexdigest();claim=self.inbox.claim((InboxEvent(key,'status_read'),))
        response=self.request({'request_id':claim.request_id})
        self.assertEqual(response[0],200);self.assertEqual(response[2]['data']['queue_state'],'skipped')
        self.assertEqual(self.worker().run_once(),'idle')
        self.assertFalse(self.sender.calls);self.assertFalse(self.service.calls)
    def test_activation_is_quick_durable_only_and_repeat_is_reused(self):
        claim=self.stage();response=self.request({'request_id':claim.request_id})
        self.assertEqual(response[0],200);self.assertEqual(response[2]['action'],'whatsapp_agent_turn')
        self.assertEqual(response[2]['mutation_state'],'not_applicable')
        self.assertEqual(response[2]['data'],{'authenticated':True,'queue_state':'queued','reused':False})
        self.assertFalse(self.sender.calls);self.assertFalse(self.service.calls)
        self.assertTrue(self.request({'request_id':claim.request_id})[2]['data']['reused'])
        self.assertEqual(self.job(claim.request_id).state,'queued')
    def test_worker_prepares_then_sends_dynamic_reply_once_and_redacts(self):
        claim=self.stage();self.request({'request_id':claim.request_id});worker=self.worker()
        self.assertEqual(worker.run_once(),'prepared')
        job=self.job(claim.request_id);self.assertIsNone(job.text);self.assertIsNotNone(job.reply)
        self.assertEqual(worker.run_once(),'sent')
        self.assertEqual(self.sender.calls,[(RECIPIENT,'Resposta dinâmica: PRIVATE_INPUT_FIXTURE')])
        self.assertEqual(len(self.service.calls),1)
        self.assertEqual(worker.run_once(),'idle')
        job=self.job(claim.request_id);self.assertEqual(job.state,'sent');self.assertIsNone(job.reply)
    def test_prepared_reply_survives_restart_without_another_agent_call(self):
        claim=self.stage();self.queue.activate(claim.request_id)
        self.assertEqual(self.worker().run_once(),'prepared')
        restarted=self.worker();self.assertEqual(restarted.run_once(),'sent')
        self.assertEqual(len(self.service.calls),1);self.assertEqual(len(self.sender.calls),1)
    def test_session_is_bound_to_sender_without_recipient_from_payload(self):
        first=self.stage('primeiro');self.queue.activate(first.request_id)
        worker=self.worker();worker.run_once();worker.run_once()
        second=self.stage('segundo');self.queue.activate(second.request_id);worker.run_once()
        self.assertEqual(self.service.calls[1][1]['conversation_id'],self.service.cid)
        self.assertEqual(self.queue.get_session(SENDER_KEY).conversation_id,self.service.cid)
    def test_uncertain_send_is_never_repeated_and_blocks_followup(self):
        claim=self.stage();self.queue.activate(claim.request_id)
        worker=self.worker();worker.run_once();self.sender.error=SandboxSendError(uncertain=True)
        self.assertEqual(worker.run_once(),'uncertain');self.sender.error=None
        second=self.stage('novo pedido');self.queue.activate(second.request_id)
        self.assertEqual(self.worker().run_once(),'idle');self.assertEqual(len(self.sender.calls),1)
        self.assertEqual(self.request({'request_id':claim.request_id})[0],409)
    def test_definite_rejection_does_not_retry_old_job_but_allows_new_turn(self):
        first=self.stage();self.queue.activate(first.request_id);worker=self.worker();worker.run_once()
        self.sender.error=SandboxSendError(uncertain=False,http_status=401)
        self.assertEqual(worker.run_once(),'rejected');self.sender.error=None
        second=self.stage('novo pedido');self.queue.activate(second.request_id)
        self.assertEqual(worker.run_once(),'prepared');self.assertEqual(worker.run_once(),'sent')
        self.assertEqual(len(self.sender.calls),2);self.assertEqual(self.job(first.request_id).state,'rejected')
    def test_unexpected_agent_failure_is_not_reprocessed(self):
        claim=self.stage();self.queue.activate(claim.request_id)
        self.service.error=RuntimeError('PRIVATE_PROVIDER_FAILURE')
        self.assertEqual(self.worker().run_once(),'uncertain');self.service.error=None
        self.assertEqual(self.worker().run_once(),'idle');self.assertFalse(self.sender.calls)
        self.assertEqual(len(self.service.calls),1)
    def test_policy_rotation_cannot_retarget_old_payload_or_reply(self):
        for prepared in (False,True):
            claim=self.stage();self.queue.activate(claim.request_id)
            if prepared:self.worker().run_once()
            changed=AgentWorker(store=self.queue,outbox=self.outbox,service_factory=lambda:self.service,
                sender=self.sender,recipient='19175550999',expected_sender_key=hashlib.sha256(b'new sender').hexdigest())
            calls=len(self.service.calls)
            self.assertEqual(changed.run_once(),'uncertain')
            self.assertEqual(len(self.service.calls),calls)
            self.assertFalse(self.sender.calls)
            self.assertEqual(self.job(claim.request_id).state,'uncertain')
            # A separate binding for the next case avoids an already-uncertain FIFO barrier.
            self.queue=AgentStore(self.root/('prepared.sqlite3' if not prepared else 'done.sqlite3'))
    def test_already_accepted_outbox_never_sends_again(self):
        claim=self.stage();self.queue.activate(claim.request_id);worker=self.worker();worker.run_once()
        job=self.job(claim.request_id)
        reservation=self.outbox.reserve(job.job_id);self.outbox.mark_accepted(reservation,'e'*64)
        self.assertEqual(worker.run_once(),'sent');self.assertFalse(self.sender.calls)
    def test_delivery_crash_does_not_reclaim_after_restart(self):
        claim=self.stage();self.queue.activate(claim.request_id);worker=self.worker();worker.run_once()
        self.assertEqual(self.queue.claim_next().state,'delivering')
        self.assertEqual(self.worker().run_once(),'idle');self.assertFalse(self.sender.calls)
    def test_processing_crash_does_not_reclaim_after_restart(self):
        claim=self.stage();self.queue.activate(claim.request_id)
        self.assertEqual(self.queue.claim_next().state,'processing')
        self.assertEqual(self.worker().run_once(),'idle');self.assertFalse(self.service.calls)
    def test_worker_background_lifecycle_processes_without_callback_wait(self):
        claim=self.stage();self.queue.activate(claim.request_id);worker=self.worker();worker.start()
        self.addCleanup(worker.stop)
        deadline=time.monotonic()+3
        while time.monotonic()<deadline and self.job(claim.request_id).state!='sent':time.sleep(.01)
        self.assertEqual(self.job(claim.request_id).state,'sent')
        worker.stop();self.assertFalse(worker._thread.is_alive())
    def test_main_lifespan_starts_and_stops_only_in_enabled_mode(self):
        worker=Mock();app=self.application(whatsapp_agent_worker_factory=lambda:worker)
        async def enabled():
            async with app.router.lifespan_context(app):worker.start.assert_called_once()
        asyncio.run(enabled());worker.stop.assert_called_once()
        self.config.whatsapp_agent_enabled=False;worker.reset_mock()
        async def disabled():
            async with app.router.lifespan_context(app):pass
        asyncio.run(disabled());worker.start.assert_not_called()
    def test_route_keeps_bearer_security(self):
        self.assertIn({'ApiKeyBearer':[]},self.app.openapi()['paths'][URL]['post']['security'])
    def test_setup_preserves_strict_optional_agent_mode(self):
        script=Path(__file__).resolve().parents[1]/'scripts/setup_whatsapp_sandbox.py'
        spec=importlib.util.spec_from_file_location('agent_setup_fixture',script);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        cfg={'workflow_id':'1111111111111111','webhook_credential_id':'2222222222222222','webhook_token':'a'*64,'callback_token':'b'*64}
        for mode in (True,False):self.assertEqual(module.validate_config({**cfg,'agent_enabled':mode},set())['agent_enabled'],mode)
        with self.assertRaises(RuntimeError):module.validate_config({**cfg,'agent_enabled':'true'},set())
        with self.assertRaises(RuntimeError):module.validate_config({**cfg,'unrelated':True},set())

if __name__=='__main__':unittest.main()
