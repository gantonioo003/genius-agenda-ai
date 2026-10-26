"""Exclusão conversacional aprovada por ASGI; nenhum provedor real."""
import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
import unittest
from unittest.mock import Mock, patch

from app.agent_deletion_store import AgentDeletionStore
from app.api_agent_deletion import default_agent_deletion_store
from app.api_conversations import conversation_owner_key
from app.conversation_store import ConversationStore, QueryMemory
from app.dialogue_state import PendingRequest
from app.main import create_app
from app.services.agenda import AgendaEvent
from app.services.confirmations import ConfirmationService
from app.services.confirmation_snapshot import ConfirmationSnapshot
from app.services.event_deletion import DeletionResult, DeletionBindingError, DeletionVersionError, deletion_confirmation
from app.settings import Settings, PROJECT_ROOT
from test_api_security import InjectedDatabase, TOKEN, WRONG_TOKEN, invoke_asgi

URL='/api/v1/agent/delete'
PARAMS={'event_id':'FixtureApprovedEvent','time_zone':'America/Recife'}
NOW=datetime(2030,6,17,12,tzinfo=timezone.utc)

class Gateway:
    def __init__(self):
        self.calendar='PRIVATE_DELETION_CALENDAR'
        self.version='PRIVATE_DELETION_VERSION_1'
        self.event=AgendaEvent('FixtureApprovedEvent','Entrevista aprovada','2030-06-17T10:00:00-03:00',
            '2030-06-17T10:30:00-03:00',False,'America/Recife','confirmed','https://example.invalid/approved')
        self.other=AgendaEvent('FixtureUntouchedEvent','Entrevista preservada','2030-06-17T12:00:00-03:00',
            '2030-06-17T12:30:00-03:00',False,'America/Recife','confirmed','https://example.invalid/untouched')
        self.reads=[]
        self.writes=[]
        self.error=None
        self.read_error=None
        self.apply_before_error=False
        self.change_version=False
        self.change_calendar=False
        self.ignore_delete=False
        self.invalid_result=False
        self.snapshot_hook=None
        self.lock=Lock()
    def get_confirmation_snapshot(self,event_id,*,time_zone):
        self.reads.append(event_id)
        if self.snapshot_hook:
            self.snapshot_hook()
        if self.read_error:
            raise self.read_error
        assert event_id==PARAMS['event_id']
        return ConfirmationSnapshot(self.calendar,'Genius Testes',replace(self.event) if self.event else None,
                                    self.version if self.event else None)
    def delete_event(self,event_id,*,time_zone,execute,confirmation_token,expected_calendar_id=None,expected_version=None):
        with self.lock:
            assert event_id==PARAMS['event_id'] and execute is True
            if self.change_calendar:
                self.calendar='PRIVATE_DIFFERENT_CALENDAR'
            if expected_calendar_id!=self.calendar:
                raise DeletionBindingError('PRIVATE_PROVIDER_ERROR')
            if self.event is None:
                return DeletionResult('already_absent','Genius Testes',None)
            if self.change_version:
                self.version='PRIVATE_DELETION_VERSION_2'
            if expected_version!=self.version:
                raise DeletionVersionError('PRIVATE_PROVIDER_ERROR')
            assert confirmation_token==deletion_confirmation(self.calendar,event_id,self.version)
            self.writes.append((event_id,confirmation_token,expected_calendar_id,expected_version))
            before=replace(self.event)
            if self.error and not self.apply_before_error:
                raise self.error
            if not self.ignore_delete:
                self.event=None
            if self.error:
                raise self.error
            if self.invalid_result:
                return {'private':'PRIVATE_PROVIDER_ERROR'}
            return DeletionResult('deleted','Genius Testes',before)

class AgentDeletionApiTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.context_path=Path(temp.name)/'context.sqlite3'
        self.ledger_path=Path(temp.name)/'deletions.sqlite3'
        self.offset=0
        self.clock=lambda:NOW+timedelta(seconds=self.offset)
        self.context=ConversationStore(self.context_path,clock=self.clock)
        self.ledger=AgentDeletionStore(self.ledger_path)
        self.gateway=Gateway()
        @contextmanager
        def factory(): yield self.gateway
        self.gateway_factory=factory
        self.context_factory=Mock(return_value=self.context)
        self.ledger_factory=Mock(return_value=self.ledger)
        self.app=self.application()
    def application(self,token=TOKEN):
        return create_app(Settings(_env_file=None,api_auth_token=token,openai_api_key=None,
            conversation_database_path=self.context_path,agent_deletion_database_path=self.ledger_path),
            InjectedDatabase(),calendar_gateway_factory=self.gateway_factory,
            conversation_store_factory=self.context_factory,agent_deletion_store_factory=self.ledger_factory)
    def headers(self,token=TOKEN):
        return [(b'authorization',f'Bearer {token}'.encode()),(b'content-type',b'application/json')]
    def request(self,body=None,*,raw=None,query=b'',headers=None,app=None):
        response=asyncio.run(invoke_asgi(app or self.app,URL,method='POST',
            body=json.dumps(body).encode() if raw is None else raw,query=query,
            headers=self.headers() if headers is None else headers))
        value=json.dumps(response)
        for private in (TOKEN,WRONG_TOKEN,self.gateway.calendar,self.gateway.version,str(self.context_path),str(self.ledger_path),'PRIVATE_PROVIDER_ERROR'):
            self.assertNotIn(private,value)
        for _,technical,_,_ in self.gateway.writes:
            self.assertNotIn(technical,value)
        self.assertEqual(response[1]['cache-control'],'no-store')
        return response
    def owner(self): return conversation_owner_key(self.app.state.settings)
    def snapshot(self,cid): return self.context.load(self.owner(),cid)
    def draft(self,fields=None,awaiting=()):
        cid=self.context.create(self.owner())
        self.context.save(self.owner(),cid,0,QueryMemory(pending_request=PendingRequest(
            {'action':'delete',**PARAMS} if fields is None else fields,awaiting)))
        return cid
    def proposal(self,cid,approve=True):
        service=ConfirmationService(self.gateway_factory,clock=self.clock)
        snapshot=self.snapshot(cid)
        preview,memory=service.preview(snapshot.memory)
        self.assertEqual(preview.status,'needs_confirmation')
        self.context.save(self.owner(),cid,snapshot.revision,memory)
        if approve:
            snapshot=self.snapshot(cid)
            approved,memory=service.approve(snapshot.memory,preview.data['proposal_id'],'confirmo')
            self.assertEqual(approved.status,'approved')
            self.context.save(self.owner(),cid,snapshot.revision,memory)
        return preview.data['proposal_id']
    def test_authentication_precedes_parser_and_factories(self):
        for headers in ([],self.headers(WRONG_TOKEN)):
            self.assertEqual(self.request(raw=b'invalid',headers=headers)[0],401)
        self.context_factory.assert_not_called()
        self.ledger_factory.assert_not_called()
        self.assertFalse(self.gateway.reads)
    def test_unconfigured_api_does_not_open_execution_store(self):
        self.assertEqual(self.request(raw=b'invalid',app=self.application(None))[0],503)
        self.context_factory.assert_not_called()
    def test_strict_contract_forbids_caller_selected_target_token_and_approval(self):
        cid=self.draft()
        bodies=[{},[],{'conversation_id':'not-uuid'}]+[{'conversation_id':cid,key:value} for key,value in
            [('event_id','OtherEvent'),('proposal_id','other'),('operation_id','caller'),('confirmed',True),('confirmation_token','0'*64),('execute',True)]]
        for body in bodies:
            self.assertEqual(self.request(body)[0],422)
        for raw in (b'{',b' '*1025,b'null',('{"conversation_id":"'+cid+'","conversation_id":"'+cid+'"}').encode()):
            self.assertEqual(self.request(raw=raw)[0],422)
        self.assertEqual(self.request({'conversation_id':cid},query=b'event_id=Other')[0],422)
        self.assertEqual(self.request({'conversation_id':cid},headers=self.headers()[:1])[0],422)
        self.ledger_factory.assert_not_called()
        self.assertFalse(self.gateway.writes)
    def test_unknown_conversation_precedes_executor(self):
        from uuid import uuid4
        self.assertEqual(self.request({'conversation_id':str(uuid4())})[0],404)
        self.ledger_factory.assert_not_called()
    def test_other_owner_cannot_use_conversation(self):
        cid=self.draft()
        self.proposal(cid)
        self.assertEqual(self.request({'conversation_id':cid},app=self.application(WRONG_TOKEN),headers=self.headers(WRONG_TOKEN))[0],404)
        self.assertFalse(self.gateway.writes)
    def test_complete_delete_request_without_approval_cannot_open_gateway(self):
        cid=self.draft()
        self.assertEqual(self.request({'conversation_id':cid})[0],409)
        self.assertFalse(self.gateway.reads)
        self.assertFalse(self.gateway.writes)
    def test_pending_and_revoked_proposals_cannot_delete(self):
        cid=self.draft()
        pid=self.proposal(cid,approve=False)
        count=len(self.gateway.reads)
        self.assertEqual(self.request({'conversation_id':cid})[0],409)
        self.assertEqual(len(self.gateway.reads),count)
        service=ConfirmationService(self.gateway_factory,clock=self.clock)
        snapshot=self.snapshot(cid)
        _,memory=service.revoke(snapshot.memory,pid)
        self.context.save(self.owner(),cid,snapshot.revision,memory)
        self.assertEqual(self.request({'conversation_id':cid})[0],409)
        self.assertFalse(self.gateway.writes)
    def test_expired_approval_never_deletes(self):
        cid=self.draft()
        self.proposal(cid)
        self.offset=301
        self.assertEqual(self.request({'conversation_id':cid})[0],409)
        self.assertFalse(self.gateway.writes)
    def test_approval_cannot_cross_conversations(self):
        approved=self.draft()
        self.proposal(approved)
        other=self.draft()
        self.assertEqual(self.request({'conversation_id':other})[0],409)
        self.assertFalse(self.gateway.writes)
    def test_approved_exact_target_deletes_and_consumes_once(self):
        cid=self.draft()
        self.proposal(cid)
        other=replace(self.gateway.other)
        response=self.request({'conversation_id':cid})
        self.assertEqual(response[0],200)
        self.assertEqual(response[2]['action'],'delete')
        self.assertEqual(response[2]['mutation_state'],'confirmed')
        self.assertEqual(response[2]['data']['event_id'],PARAMS['event_id'])
        self.assertIsNone(self.gateway.event)
        self.assertEqual(self.gateway.other,other)
        self.assertEqual(self.snapshot(cid).memory.confirmation.state,'consumed')
        self.assertEqual(len(self.gateway.writes),1)
    def test_repeat_and_new_application_only_reconcile_absence(self):
        cid=self.draft()
        self.proposal(cid)
        first=self.request({'conversation_id':cid})
        again=self.request({'conversation_id':cid},app=self.application())
        self.assertEqual(again[0],200)
        self.assertEqual(again[2]['operation_id'],first[2]['operation_id'])
        self.assertEqual(len(self.gateway.writes),1)
    def test_changed_provider_version_before_execution_requires_new_preview(self):
        cid=self.draft()
        self.proposal(cid)
        self.gateway.version='PRIVATE_CHANGED_VERSION'
        response=self.request({'conversation_id':cid})
        self.assertEqual(response[0],409)
        self.assertEqual(response[2]['mutation_state'],'not_applied')
        self.assertFalse(self.gateway.writes)
        self.assertEqual(self.snapshot(cid).memory.confirmation.state,'approved')
    def test_version_race_during_execution_does_not_delete(self):
        cid=self.draft()
        self.proposal(cid)
        self.gateway.change_version=True
        response=self.request({'conversation_id':cid})
        self.assertEqual(response[0],409)
        self.assertEqual(response[2]['mutation_state'],'not_applied')
        self.assertFalse(self.gateway.writes)
        self.assertEqual(self.snapshot(cid).memory.confirmation.state,'consumed')
    def test_calendar_race_during_execution_does_not_delete(self):
        cid=self.draft()
        self.proposal(cid)
        self.gateway.change_calendar=True
        self.assertEqual(self.request({'conversation_id':cid})[0],409)
        self.assertFalse(self.gateway.writes)
    def test_already_absent_after_approval_never_calls_delete(self):
        cid=self.draft()
        self.proposal(cid)
        self.gateway.event=None
        response=self.request({'conversation_id':cid})
        self.assertEqual(response[0],200)
        self.assertEqual(response[2]['data']['result'],'already_absent')
        self.assertFalse(self.gateway.writes)
    def test_lost_response_after_deletion_recovers_without_another_delete(self):
        cid=self.draft()
        self.proposal(cid)
        self.gateway.error=OSError('PRIVATE_PROVIDER_ERROR')
        self.gateway.apply_before_error=True
        first=self.request({'conversation_id':cid})
        self.assertEqual(first[0],502)
        self.assertEqual(first[2]['mutation_state'],'unknown')
        self.gateway.error=None
        again=self.request({'conversation_id':cid},app=self.application())
        self.assertEqual(again[0],200)
        self.assertEqual(first[2]['operation_id'],again[2]['operation_id'])
        self.assertEqual(len(self.gateway.writes),1)
    def test_lost_response_with_event_still_present_never_retries_delete(self):
        cid=self.draft()
        self.proposal(cid)
        self.gateway.error=OSError('PRIVATE_PROVIDER_ERROR')
        self.assertEqual(self.request({'conversation_id':cid})[0],502)
        self.gateway.error=None
        again=self.request({'conversation_id':cid})
        self.assertEqual(again[0],502)
        self.assertEqual(again[2]['mutation_state'],'unknown')
        self.assertEqual(len(self.gateway.writes),1)
    def test_success_receipt_without_absence_stays_unknown(self):
        cid=self.draft()
        self.proposal(cid)
        self.gateway.ignore_delete=True
        response=self.request({'conversation_id':cid})
        self.assertEqual(response[0],502)
        self.assertEqual(response[2]['mutation_state'],'unknown')
        self.assertEqual(self.request({'conversation_id':cid})[0],502)
        self.assertEqual(len(self.gateway.writes),1)
    def test_context_changed_during_provider_read_cannot_delete(self):
        cid=self.draft()
        self.proposal(cid)
        def change():
            snapshot=self.snapshot(cid)
            self.context.save(self.owner(),cid,snapshot.revision,replace(snapshot.memory,
                pending_request=PendingRequest({'action':'delete',**PARAMS,'event_id':'DifferentEvent'})))
        self.gateway.snapshot_hook=change
        self.assertEqual(self.request({'conversation_id':cid})[0],409)
        self.assertFalse(self.gateway.writes)
    def test_invalid_provider_result_after_delete_remains_unknown(self):
        cid=self.draft()
        self.proposal(cid)
        self.gateway.invalid_result=True
        response=self.request({'conversation_id':cid})
        self.assertEqual(response[0],502)
        self.assertEqual(response[2]['mutation_state'],'unknown')
    def test_invalid_service_result_after_possible_write_remains_unknown(self):
        cid=self.draft()
        class InvalidService:
            def execute(self,*args): return {'private':'PRIVATE_PROVIDER_ERROR'}
        self.app.state.agent_deletion_service_factory=InvalidService
        response=self.request({'conversation_id':cid})
        self.assertEqual(response[0],502)
        self.assertEqual(response[2]['mutation_state'],'unknown')
    def test_all_day_approved_event_uses_exact_target(self):
        self.gateway.event=replace(self.gateway.event,start='2030-06-17',end='2030-06-18',all_day=True)
        cid=self.draft()
        self.proposal(cid)
        self.assertEqual(self.request({'conversation_id':cid})[0],200)
        self.assertIsNone(self.gateway.event)
        self.assertIsNotNone(self.gateway.other)
    def test_relative_ledger_path_is_resolved_against_project_root(self):
        config=Settings(_env_file=None,agent_deletion_database_path=Path('data/private-deletions.sqlite3'))
        with patch('app.api_agent_deletion.AgentDeletionStore') as constructor:
            default_agent_deletion_store(config)
        constructor.assert_called_once_with(PROJECT_ROOT/'data/private-deletions.sqlite3')
    def test_route_retains_bearer_security(self):
        self.assertIn({'ApiKeyBearer':[]},self.app.openapi()['paths'][URL]['post']['security'])

if __name__=='__main__': unittest.main()
