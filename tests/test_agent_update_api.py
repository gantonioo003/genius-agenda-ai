"""Comandos explícitos edit/reschedule por ASGI, com bancos reais isolados."""
import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
import unittest
from unittest.mock import Mock, patch

from app.agent_update_store import AgentUpdateStore
from app.api_agent_updates import default_agent_update_store
from app.api_conversations import conversation_owner_key
from app.conversation_store import ConversationStore, QueryMemory
from app.dialogue_state import PendingRequest
from app.main import create_app
from app.services.confirmations import ConfirmationService
from app.services.confirmation_snapshot import ConfirmationSnapshot
from app.services.event_details import EventDetails, DetailsResult
from app.services.rescheduling import RescheduleResult
from app.services.agenda import AgendaEvent
from app.services.update_snapshot import UpdateSnapshot, UpdateBindingError, UpdateVersionError
from app.settings import Settings, PROJECT_ROOT
from test_api_security import InjectedDatabase, TOKEN, WRONG_TOKEN, invoke_asgi

NOW=datetime(2030,6,17,12,tzinfo=timezone.utc)
EDIT={'event_id':'FixtureExactEvent','time_zone':'America/Recife','title':'Título novo','description':''}
SCHEDULE={'event_id':'FixtureExactEvent','time_zone':'America/Recife','start':'2030-06-17T15:00:00-03:00','end':'2030-06-17T15:30:00-03:00'}

def agenda(event):
    return AgendaEvent(**{key:getattr(event,key) for key in AgendaEvent.__dataclass_fields__})

class Gateway:
    def __init__(self):
        self.calendar='PRIVATE_UPDATE_CALENDAR'
        self.version='PRIVATE_EVENT_VERSION_1'
        self.event=EventDetails('FixtureExactEvent','Título anterior','2030-06-17T10:00:00-03:00',
            '2030-06-17T10:30:00-03:00',False,'America/Recife','confirmed',
            'https://example.invalid/event','Descrição anterior','Local preservado')
        self.reads=[]
        self.writes=[]
        self.error=None
        self.apply_before_error=False
        self.change_version=False
        self.readback_tamper=False
        self.snapshot_hook=None
        self.lock=Lock()
    def get_update_snapshot(self,event_id,*,time_zone):
        self.reads.append(event_id)
        if self.snapshot_hook:
            self.snapshot_hook()
        assert event_id=='FixtureExactEvent'
        return UpdateSnapshot(self.calendar,'Genius Testes',replace(self.event),self.version,'opaque')
    def get_confirmation_snapshot(self,event_id,*,time_zone):
        return ConfirmationSnapshot(self.calendar,'Genius Testes',agenda(self.event),self.version)
    def guard(self,expected_calendar_id,expected_version):
        if self.change_version:
            self.version='PRIVATE_CHANGED_VERSION'
        if expected_calendar_id!=self.calendar:
            raise UpdateBindingError('PRIVATE_PROVIDER_ERROR')
        if expected_version!=self.version:
            raise UpdateVersionError('PRIVATE_PROVIDER_ERROR')
    def update_event_details(self,event_id,change,*,time_zone,execute,expected_calendar_id=None,expected_version=None):
        with self.lock:
            self.guard(expected_calendar_id,expected_version)
            assert event_id==self.event.event_id and execute is True
            before=replace(self.event)
            requested={key:getattr(change,key) for key in ('title','description','location') if getattr(change,key) is not None}
            if all(getattr(before,key)==value for key,value in requested.items()):
                return DetailsResult('unchanged','Genius Testes',before,before,requested)
            self.writes.append(('edit',event_id,dict(requested)))
            if self.error and not self.apply_before_error:
                raise self.error
            self.event=replace(self.event,**requested)
            self.version='PRIVATE_EVENT_VERSION_2'
            if self.readback_tamper:
                self.event=replace(self.event,location='Mudança não solicitada')
            if self.error:
                raise self.error
            return DetailsResult('updated','Genius Testes',before,replace(self.event),requested)
    def reschedule_event(self,event_id,change,*,execute,expected_calendar_id=None,expected_version=None):
        with self.lock:
            self.guard(expected_calendar_id,expected_version)
            assert event_id==self.event.event_id and execute is True
            before=agenda(self.event)
            if (datetime.fromisoformat(before.start)==change.start and datetime.fromisoformat(before.end)==change.end):
                return RescheduleResult('unchanged','Genius Testes',before,before,change.start.isoformat(),change.end.isoformat(),change.time_zone)
            self.writes.append(('reschedule',event_id,change))
            if self.error and not self.apply_before_error:
                raise self.error
            self.event=replace(self.event,start=change.start.isoformat(),end=change.end.isoformat())
            self.version='PRIVATE_EVENT_VERSION_2'
            if self.readback_tamper:
                self.event=replace(self.event,description='Mudança não solicitada')
            if self.error:
                raise self.error
            return RescheduleResult('rescheduled','Genius Testes',before,agenda(self.event),change.start.isoformat(),change.end.isoformat(),change.time_zone)

class AgentUpdateApiTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.context_path=Path(temp.name)/'context.sqlite3'
        self.ledger_path=Path(temp.name)/'updates.sqlite3'
        self.context=ConversationStore(self.context_path,clock=lambda:NOW)
        self.ledger=AgentUpdateStore(self.ledger_path)
        self.gateway=Gateway()
        @contextmanager
        def factory(): yield self.gateway
        self.gateway_factory=factory
        self.context_factory=Mock(return_value=self.context)
        self.ledger_factory=Mock(return_value=self.ledger)
        self.app=self.application()
    def application(self,token=TOKEN):
        return create_app(Settings(_env_file=None,api_auth_token=token,openai_api_key=None,
            conversation_database_path=self.context_path,agent_update_database_path=self.ledger_path),
            InjectedDatabase(),calendar_gateway_factory=self.gateway_factory,
            conversation_store_factory=self.context_factory,agent_update_store_factory=self.ledger_factory)
    def headers(self,token=TOKEN):
        return [(b'authorization',f'Bearer {token}'.encode()),(b'content-type',b'application/json')]
    def request(self,action,body=None,*,raw=None,query=b'',headers=None,app=None):
        response=asyncio.run(invoke_asgi(app or self.app,'/api/v1/agent/'+action,method='POST',
            body=json.dumps(body).encode() if raw is None else raw,query=query,
            headers=self.headers() if headers is None else headers))
        text=json.dumps(response)
        for private in (TOKEN,WRONG_TOKEN,self.gateway.calendar,self.gateway.version,str(self.context_path),str(self.ledger_path),'PRIVATE_PROVIDER_ERROR'):
            self.assertNotIn(private,text)
        self.assertEqual(response[1]['cache-control'],'no-store')
        return response
    def draft(self,action='edit',fields=None,awaiting=(),candidates=()):
        owner=conversation_owner_key(self.app.state.settings)
        cid=self.context.create(owner)
        self.context.save(owner,cid,0,QueryMemory(pending_request=PendingRequest(
            {'action':action,**(EDIT if action=='edit' else SCHEDULE)} if fields is None else fields,awaiting,candidates)))
        return cid
    def snapshot(self,cid):
        return self.context.load(conversation_owner_key(self.app.state.settings),cid)
    def test_authentication_precedes_input_and_factories_both_routes(self):
        for action in ('edit','reschedule'):
            for headers in ([],self.headers(WRONG_TOKEN)):
                self.assertEqual(self.request(action,raw=b'invalid',headers=headers)[0],401)
        self.context_factory.assert_not_called()
        self.ledger_factory.assert_not_called()
        self.assertFalse(self.gateway.reads)
    def test_unconfigured_api_does_not_open_store(self):
        for action in ('edit','reschedule'):
            self.assertEqual(self.request(action,raw=b'invalid',app=self.application(None))[0],503)
        self.context_factory.assert_not_called()
    def test_strict_json_contract_does_not_open_execution_store(self):
        for action in ('edit','reschedule'):
            cid=self.draft(action)
            bodies=[{},[],{'conversation_id':'not-a-uuid'},{'conversation_id':cid,'event_id':'Other'},
                {'conversation_id':cid,'operation_id':'caller-choice'},{'conversation_id':cid,'confirmed':True},
                {'conversation_id':cid,'changes':{'title':'extra'}}]
            for body in bodies:
                self.assertEqual(self.request(action,body)[0],422)
            for raw in (b'{',b'[]',b'{"conversation_id":null}',b' '*1025,
                ('{"conversation_id":"'+cid+'","conversation_id":"'+cid+'"}').encode()):
                self.assertEqual(self.request(action,raw=raw)[0],422)
            self.assertEqual(self.request(action,{'conversation_id':cid},query=b'event_id=Other')[0],422)
            self.assertEqual(self.request(action,{'conversation_id':cid},headers=self.headers()[:1])[0],422)
        self.ledger_factory.assert_not_called()
        self.assertFalse(self.gateway.writes)
    def test_unknown_conversation_does_not_construct_executor(self):
        from uuid import uuid4
        for action in ('edit','reschedule'):
            self.assertEqual(self.request(action,{'conversation_id':str(uuid4())})[0],404)
        self.ledger_factory.assert_not_called()
    def test_wrong_owner_cannot_mutate_conversation(self):
        cid=self.draft()
        self.assertEqual(self.request('edit',{'conversation_id':cid},app=self.application(WRONG_TOKEN),headers=self.headers(WRONG_TOKEN))[0],404)
        self.assertFalse(self.gateway.writes)
    def test_edit_exact_event_clears_description_preserves_other_fields(self):
        cid=self.draft()
        before=replace(self.gateway.event)
        response=self.request('edit',{'conversation_id':cid})
        self.assertEqual(response[0],200)
        self.assertEqual(response[2]['action'],'edit')
        self.assertEqual(response[2]['mutation_state'],'confirmed')
        self.assertEqual(self.gateway.event.title,EDIT['title'])
        self.assertEqual(self.gateway.event.description,'')
        self.assertEqual(self.gateway.event.location,before.location)
        self.assertEqual(self.gateway.event.start,before.start)
        self.assertEqual(self.gateway.writes[0][1],EDIT['event_id'])
    def test_reschedule_exact_event_preserves_details(self):
        cid=self.draft('reschedule')
        before=replace(self.gateway.event)
        response=self.request('reschedule',{'conversation_id':cid})
        self.assertEqual(response[0],200)
        self.assertEqual(response[2]['action'],'reschedule')
        self.assertEqual(self.gateway.event.start,SCHEDULE['start'])
        self.assertEqual(self.gateway.event.description,before.description)
        self.assertEqual(self.gateway.event.location,before.location)
    def test_repeat_and_application_restart_only_read_same_operation(self):
        for action in ('edit','reschedule'):
            cid=self.draft(action)
            first=self.request(action,{'conversation_id':cid})
            count=len(self.gateway.writes)
            again=self.request(action,{'conversation_id':cid},app=self.application())
            self.assertEqual(again[0],200)
            self.assertEqual(again[2]['operation_id'],first[2]['operation_id'])
            self.assertEqual(len(self.gateway.writes),count)
    def test_incomplete_pending_or_wrong_action_does_not_mutate(self):
        for action in ('edit','reschedule'):
            cid=self.draft(action,awaiting=('title',))
            self.assertEqual(self.request(action,{'conversation_id':cid})[0],409)
            cid=self.draft('reschedule' if action=='edit' else 'edit')
            self.assertEqual(self.request(action,{'conversation_id':cid})[0],409)
        self.assertFalse(self.gateway.writes)
    def test_active_proposal_requires_exact_approval_then_consumes(self):
        for action in ('edit','reschedule'):
            cid=self.draft(action)
            owner=conversation_owner_key(self.app.state.settings)
            confirmations=ConfirmationService(self.gateway_factory,clock=lambda:NOW)
            snapshot=self.snapshot(cid)
            preview,memory=confirmations.preview(snapshot.memory)
            self.context.save(owner,cid,snapshot.revision,memory)
            pending=self.request(action,{'conversation_id':cid})
            self.assertEqual(pending[0],409)
            self.assertEqual(pending[2]['error']['code'],'confirmation_not_approved')
            snapshot=self.snapshot(cid)
            approved,memory=confirmations.approve(snapshot.memory,preview.data['proposal_id'],'confirmo')
            self.context.save(owner,cid,snapshot.revision,memory)
            created=self.request(action,{'conversation_id':cid})
            self.assertEqual(created[0],200)
            self.assertEqual(self.snapshot(cid).memory.confirmation.state,'consumed')
            writes=len(self.gateway.writes)
            self.assertEqual(self.request(action,{'conversation_id':cid})[0],200)
            self.assertEqual(len(self.gateway.writes),writes)
    def test_provider_version_change_refused_before_patch(self):
        for action in ('edit','reschedule'):
            self.gateway=Gateway()
            cid=self.draft(action)
            self.gateway.change_version=True
            response=self.request(action,{'conversation_id':cid})
            self.assertEqual(response[0],409)
            self.assertEqual(response[2]['mutation_state'],'not_applied')
            self.assertFalse(self.gateway.writes)
    def test_unknown_after_applied_recovers_by_read_without_patch(self):
        for action in ('edit','reschedule'):
            self.gateway=Gateway()
            cid=self.draft(action)
            self.gateway.error=OSError('PRIVATE_PROVIDER_ERROR')
            self.gateway.apply_before_error=True
            first=self.request(action,{'conversation_id':cid})
            self.assertEqual(first[0],502)
            self.assertEqual(first[2]['mutation_state'],'unknown')
            self.gateway.error=None
            again=self.request(action,{'conversation_id':cid})
            self.assertEqual(again[0],200)
            self.assertEqual(again[2]['operation_id'],first[2]['operation_id'])
            self.assertEqual(len(self.gateway.writes),1)
    def test_unknown_without_desired_state_never_repeats_patch(self):
        for action in ('edit','reschedule'):
            self.gateway=Gateway()
            cid=self.draft(action)
            self.gateway.error=OSError('PRIVATE_PROVIDER_ERROR')
            first=self.request(action,{'conversation_id':cid})
            self.gateway.error=None
            again=self.request(action,{'conversation_id':cid})
            self.assertEqual(first[0],502)
            self.assertEqual(again[0],502)
            self.assertEqual(again[2]['mutation_state'],'unknown')
            self.assertEqual(len(self.gateway.writes),1)
    def test_changed_context_during_snapshot_blocks_write(self):
        cid=self.draft()
        def change():
            snapshot=self.snapshot(cid)
            self.context.save(conversation_owner_key(self.app.state.settings),cid,snapshot.revision,
                replace(snapshot.memory,pending_request=PendingRequest({'action':'edit',**EDIT,'title':'Outro pedido'})))
        self.gateway.snapshot_hook=change
        response=self.request('edit',{'conversation_id':cid})
        self.assertEqual(response[0],409)
        self.assertFalse(self.gateway.writes)
    def test_readback_with_unsolicited_changes_remains_unknown(self):
        for action in ('edit','reschedule'):
            self.gateway=Gateway()
            cid=self.draft(action)
            self.gateway.readback_tamper=True
            response=self.request(action,{'conversation_id':cid})
            self.assertEqual(response[0],502)
            self.assertEqual(response[2]['mutation_state'],'unknown')
            self.gateway.readback_tamper=False
            again=self.request(action,{'conversation_id':cid})
            self.assertEqual(again[0],502)
            self.assertEqual(len(self.gateway.writes),1)
    def test_invalid_service_result_is_unknown_without_private_error(self):
        cid=self.draft()
        class InvalidService:
            def execute(self,*args): return {'private':'PRIVATE_PROVIDER_ERROR'}
        self.app.state.agent_update_service_factory=InvalidService
        response=self.request('edit',{'conversation_id':cid})
        self.assertEqual(response[0],502)
        self.assertEqual(response[2]['mutation_state'],'unknown')
        self.assertFalse(response[2]['error']['retryable'])
    def test_relative_store_path_is_based_on_project_root(self):
        config=Settings(_env_file=None,agent_update_database_path=Path('data/private-updates.sqlite3'))
        with patch('app.api_agent_updates.AgentUpdateStore') as constructor:
            default_agent_update_store(config)
        constructor.assert_called_once_with(PROJECT_ROOT/'data/private-updates.sqlite3')
    def test_both_routes_retain_bearer_security(self):
        for action in ('edit','reschedule'):
            self.assertIn({'ApiKeyBearer':[]},self.app.openapi()['paths']['/api/v1/agent/'+action]['post']['security'])

if __name__=='__main__': unittest.main()

