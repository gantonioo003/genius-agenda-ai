from contextlib import closing
"""Entrada do agente: texto fica privado antes de n8n; somente metadados saem."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.sandbox_webhook import create_app, main
from app.whatsapp_agent_store import AgentStore
from test_sandbox_webhook import CONFIG,PHONE,OTHER_PHONE,PRIVATE,CALLBACK,WEBHOOK,payload,status_payload,invoke

class AgentIngressTests(unittest.TestCase):
    def setUp(self):
        temporary=TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.receipts=self.root/'data/receipts.jsonl'
        self.diagnostics=self.root/'data/ingress.jsonl'
        self.path=self.root/'data/whatsapp-agent.sqlite3'
        self.inbox=self.root/'data/whatsapp-inbox.sqlite3'
        self.store=AgentStore(self.path)
        self.forwarded=[]
        def forward(summary):
            self.forwarded.append(deepcopy(summary))
            if summary['message_count']:
                status=self.store.get_claim(summary['request_id'])
                self.assertTrue(status.known)
                self.assertTrue(all(state=='staged' for state in status.states))
                self.store.activate(summary['request_id'])
            return {'status':'received','api_reachable':True,'request_id':summary['request_id'],
                    'reply_state':'queued' if summary['message_count'] else 'skipped'}
        self.forward=forward
        self.app=self.application()
    def application(self,**kw):
        return create_app(CONFIG,allowed_sender=PHONE,forward=kw.pop('forward',self.forward),
            receipts_path=self.receipts,diagnostics_path=self.diagnostics,
            agent_enabled=kw.pop('agent_enabled',True),agent_store=kw.pop('agent_store',self.store),**kw)
    def request(self,value=None,*,app=None,**kw):
        body=json.dumps(payload() if value is None else value).encode()
        return asyncio.run(invoke(app or self.app,body,**kw))
    def test_text_is_durable_before_n8n_and_public_paths_contain_metadata_only(self):
        self.assertEqual(self.request(),(200,{'status':'received'}))
        summary,=self.forwarded
        self.assertEqual(set(summary),{'source','request_id','message_count','status_count','event_types'})
        for private in (PRIVATE,PHONE,CALLBACK,WEBHOOK):
            self.assertNotIn(private,json.dumps(summary))
            self.assertNotIn(private,self.receipts.read_text())
            self.assertNotIn(private,self.diagnostics.read_text())
        job=self.store.claim_next()
        self.assertEqual(job.text,PRIVATE)
        self.assertEqual(job.state,'processing')
        self.assertNotEqual(job.sender_key,PHONE)
        self.assertNotIn(PHONE,job.sender_key)
    def test_unauthorized_or_mixed_sender_batch_never_occupies_inbox_or_queue(self):
        value=payload()
        extra=deepcopy(value['entry'][0]['changes'][0]['value']['messages'][0])
        extra.update(id='different-private-id',**{'from':OTHER_PHONE})
        value['entry'][0]['changes'][0]['value']['messages'].append(extra)
        self.assertEqual(self.request(value),(403,{'status':'rejected'}))
        self.assertFalse(self.path.exists())
        self.assertFalse(self.inbox.exists())
        self.assertEqual(self.forwarded,[])
    def test_bad_callback_capability_does_not_store_text(self):
        self.assertEqual(self.request(path='/webhooks/360dialog/'+'wrong-token')[0],404)
        self.assertFalse(self.path.exists())
        self.assertFalse(self.inbox.exists())
    def test_status_only_callback_does_not_create_job_or_agent_database(self):
        self.assertEqual(self.request(status_payload()),(200,{'status':'received'}))
        self.assertFalse(self.path.exists())
        self.assertEqual(self.forwarded[0]['message_count'],0)
    def test_nontext_message_discards_media_body_before_persistence(self):
        value=payload()
        event=value['entry'][0]['changes'][0]['value']['messages'][0]
        event.update(type='image',image={'url':'PRIVATE_MEDIA_URL','caption':'PRIVATE_CAPTION'})
        del event['text']
        self.assertEqual(self.request(value)[0],200)
        job=self.store.claim_next()
        self.assertEqual(job.event_type,'message_image')
        self.assertIsNone(job.text)
        for private in ('PRIVATE_MEDIA_URL','PRIVATE_CAPTION'):
            self.assertNotIn(private,self.path.read_bytes().decode('latin-1'))
            self.assertNotIn(private,json.dumps(self.forwarded))
    def test_duplicates_do_not_create_more_jobs_or_forward_again(self):
        self.assertEqual(self.request()[0],200)
        self.assertEqual(self.request()[0],200)
        self.assertEqual(len(self.forwarded),1)
        self.assertEqual(self.store.state_counts(),{'queued':1})
    def test_partially_duplicate_batch_queues_only_new_message(self):
        self.request()
        value=payload()
        events=value['entry'][0]['changes'][0]['value']['messages']
        second=deepcopy(events[0])
        second['id']='new-private-message-id'
        second['text']['body']='Segundo texto privado'
        events.append(second)
        self.assertEqual(self.request(value)[0],200)
        self.assertEqual([entry['message_count'] for entry in self.forwarded],[1,1])
        self.assertEqual(self.store.state_counts(),{'queued':2})
        with closing(sqlite3.connect(self.path)) as db, db:
            texts=[row[0] for row in db.execute('SELECT text FROM agent_jobs ORDER BY sequence')]
        self.assertEqual(texts,[PRIVATE,'Segundo texto privado'])
    def test_storage_failure_prevents_forwarding_and_callback_success(self):
        with patch.object(self.store,'stage',side_effect=OSError('PRIVATE_QUEUE_ERROR')):
            status,result=self.request()
        self.assertEqual((status,result),(503,{'status':'unavailable'}))
        self.assertEqual(self.forwarded,[])
        with closing(sqlite3.connect(self.inbox)) as db, db:
            self.assertEqual(db.execute('SELECT state FROM inbox_events').fetchall(),[('uncertain',)])
        self.assertNotIn('PRIVATE_QUEUE_ERROR',self.diagnostics.read_text())
    def test_upstream_loss_retains_staged_input_without_starting_agent(self):
        def failed(_summary):raise OSError('PRIVATE_UPSTREAM_ERROR')
        app=self.application(forward=failed)
        self.assertEqual(self.request(app=app),(503,{'status':'unavailable'}))
        self.assertEqual(self.store.state_counts(),{'staged':1})
        self.assertIsNone(self.store.claim_next())
        self.assertEqual(self.request(app=app),(503,{'status':'unavailable'}))
        self.assertEqual(self.store.state_counts(),{'staged':1})
    def test_old_accepted_reply_is_rejected_in_agent_mode(self):
        def legacy(summary):
            return {'status':'received','api_reachable':True,'request_id':summary['request_id'],'reply_state':'accepted'}
        self.assertEqual(self.request(app=self.application(forward=legacy))[0],503)
        self.assertEqual(self.store.state_counts(),{'staged':1})
    def test_legacy_mode_keeps_fixed_reply_contract_without_agent_queue(self):
        def legacy(summary):
            return {'status':'received','api_reachable':True,'request_id':summary['request_id'],'reply_state':'accepted'}
        self.assertEqual(self.request(app=self.application(forward=legacy,agent_enabled=False))[0],200)
        self.assertFalse(self.path.exists())
    def test_mixed_message_and_status_has_one_job_and_both_metadata_counts(self):
        value=payload()
        value['entry'][0]['changes'][0]['value']['statuses']=status_payload()['entry'][0]['changes'][0]['value']['statuses']
        self.assertEqual(self.request(value)[0],200)
        self.assertEqual(self.store.state_counts(),{'queued':1})
        self.assertEqual((self.forwarded[0]['message_count'],self.forwarded[0]['status_count']),(1,1))
    def test_main_requires_strict_boolean_agent_mode(self):
        config={**CONFIG,'agent_enabled':'true'}
        raw_policy=json.dumps({'allowed_phone':PHONE}).encode()
        with patch.object(Path,'read_bytes',side_effect=[json.dumps(config).encode(),raw_policy]),patch('uvicorn.run') as server:
            with self.assertRaises(SystemExit):main()
            server.assert_not_called()
        with self.assertRaises(ValueError):self.application(agent_enabled='true')
    def test_main_agent_mode_resolves_same_custom_queue_path_as_api_settings(self):
        from types import SimpleNamespace
        from app.sandbox_webhook import PROJECT_ROOT
        raw_policy=json.dumps({'allowed_phone':PHONE}).encode()
        for configured in (self.root/'custom-agent.sqlite3',Path('private/custom-agent.sqlite3')):
            config={**CONFIG,'agent_enabled':True}
            expected=configured if configured.is_absolute() else PROJECT_ROOT/configured
            with patch.object(Path,'read_bytes',side_effect=[json.dumps(config).encode(),raw_policy]), \
                    patch('app.settings.Settings',return_value=SimpleNamespace(whatsapp_agent_database_path=configured, reminder_database_path=Path('data/reminders.sqlite3'))), \
                    patch('app.sandbox_webhook.AgentStore') as store,patch('app.sandbox_webhook.create_app') as creator,patch('uvicorn.run'):
                main()
            store.assert_called_once_with(expected)
            self.assertIs(creator.call_args.kwargs['agent_store'],store.return_value)
            self.assertIs(creator.call_args.kwargs['agent_enabled'],True)
    def test_sender_binding_is_stable_per_capability_and_not_plain_phone_hash(self):
        self.request()
        job=self.store.claim_next()
        import hashlib,hmac
        expected=hmac.new(CALLBACK.encode(),b'genius-whatsapp-agent-sender:v1\0'+PHONE.encode(),'sha256').hexdigest()
        self.assertEqual(job.sender_key,expected)
        self.assertNotEqual(job.sender_key,hashlib.sha256(PHONE.encode()).hexdigest())

if __name__=='__main__':unittest.main()
