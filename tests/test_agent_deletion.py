"""Exclusão aprovada com SQLite real e gateway fictício, sem provedores reais."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch
from uuid import uuid4

from app.agent_deletion_store import AgentDeletionError, AgentDeletionStore
from app.confirmation_state import ConfirmationError, ConfirmationProposal, proposal_request_hash
from app.conversation_store import ConversationError, ConversationStore, QueryMemory
from app.dialogue_state import PendingRequest
from app.google_calendar import CalendarSetupError
from app.services.agent_deletion import AgentDeletionService, AgentDeletionResult, validate_deletion_result
from app.services.agenda import AgendaEvent
from app.services.agenda_tools import minimal_event
from app.services.confirmation_snapshot import ConfirmationSnapshot
from app.services.event_deletion import DeletionResult, DeletionBindingError, DeletionVersionError, deletion_confirmation

NOW=datetime(2030,6,17,12,tzinfo=timezone.utc)
OWNER,OTHER=sha256(b'fixture-owner').hexdigest(),sha256(b'other-owner').hexdigest()
CALENDAR,ZONE,EID='fixture-deletion-calendar','America/Recife','FixtureApprovedEvent'
PARAMETERS={'event_id':EID,'time_zone':ZONE}
ORIGINAL=AgendaEvent(EID,'Entrevista aprovada','2030-06-17T10:00:00-03:00',
    '2030-06-17T10:30:00-03:00',False,ZONE,'confirmed','https://example.invalid/event')

class Gateway:
    def __init__(self):
        self.current,self.calendar,self.version=ORIGINAL,CALENDAR,'fixture-version-1'
        self.calls=[]
        self.snapshot_error=self.write_error=None
        self.snapshot_hook=self.guard_hook=self.after_hook=None
        self.lost_response=self.malformed=False
        self.entered=self.release=None
    def get_confirmation_snapshot(self,event_id,*,time_zone):
        self.calls.append(('read',event_id,time_zone))
        if self.snapshot_hook: self.snapshot_hook()
        if self.snapshot_error: raise self.snapshot_error
        return ConfirmationSnapshot(self.calendar,'Genius Testes',self.current,self.version if self.current else None)
    def delete_event(self,event_id,*,time_zone,execute,confirmation_token,expected_calendar_id,expected_version):
        self.calls.append(('execute',event_id,expected_calendar_id,expected_version,confirmation_token))
        if self.guard_hook: self.guard_hook()
        if self.calendar!=expected_calendar_id: raise DeletionBindingError('PRIVATE_DIAGNOSTIC')
        if self.current is None: return DeletionResult('already_absent','Genius Testes',None)
        if self.version!=expected_version: raise DeletionVersionError('PRIVATE_DIAGNOSTIC')
        assert event_id==self.current.event_id and execute and time_zone==ZONE
        assert confirmation_token==deletion_confirmation(self.calendar,event_id,self.version)
        if self.entered:
            self.entered.set()
            assert self.release.wait(timeout=5)
        if self.write_error: raise self.write_error
        before=self.current
        self.calls.append(('delete',event_id))
        self.current=None
        if self.after_hook: self.after_hook()
        if self.lost_response: raise OSError('PRIVATE_DIAGNOSTIC')
        return None if self.malformed else DeletionResult('deleted','Genius Testes',before)

class AgentDeletionTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.now=NOW
        self.context=ConversationStore(self.root/'context.sqlite3',clock=lambda:self.now)
        self.ledger=AgentDeletionStore(self.root/'deletions.sqlite3',clock=lambda:self.now)
        self.cid=self.context.create(OWNER)
        self.gateway=Gateway()
        self.opened=self.closed=0
        self.service=AgentDeletionService(self.context,self.ledger,self.factory)
        self.addCleanup(lambda:self.assertEqual(self.opened,self.closed))
        self.prepare()
    @contextmanager
    def factory(self):
        self.opened+=1
        try: yield self.gateway
        finally: self.closed+=1
    def prepare(self,parameters=None,awaiting=(),action='delete'):
        self.parameters=dict(parameters or PARAMETERS)
        old=self.context.load(OWNER,self.cid)
        self.context.save(OWNER,self.cid,old.revision,QueryMemory(
            pending_request=PendingRequest({'action':action,**self.parameters},awaiting)))
    def memory(self): return self.context.load(OWNER,self.cid).memory
    def save(self,memory):
        old=self.context.load(OWNER,self.cid)
        self.context.save(OWNER,self.cid,old.revision,memory)
    def proposal(self,*,state='approved',version=None,calendar=None,event=None,parameters=None,action='delete'):
        parameters=dict(parameters or self.parameters)
        calendar=calendar or CALENDAR
        version=version or self.gateway.version
        event=minimal_event(event or self.gateway.current or ORIGINAL)
        return ConfirmationProposal(str(uuid4()),action,parameters,
            proposal_request_hash(action,parameters,calendar,parameters['event_id'],version,event),
            calendar,parameters['event_id'],version,event,NOW.isoformat(),
            (NOW+timedelta(seconds=300)).isoformat(),state,
            NOW.isoformat() if state=='approved' else None,'a'*64 if state=='approved' else None)
    def attach(self,proposal=None):
        self.approval=proposal or self.proposal()
        self.save(replace(self.memory(),confirmation=self.approval))
        return self.approval
    def execute(self):
        result=self.service.execute(OWNER,self.cid)
        self.assertIs(validate_deletion_result(result),result)
        self.assertNotIn('PRIVATE_DIAGNOSTIC',repr(result))
        return result
    def deletes(self): return [call for call in self.gateway.calls if call[0]=='delete']
    def record(self,proposal=None): return self.ledger.get(OWNER,self.cid,(proposal or self.approval).proposal_id)

    def test_approved_exact_event_is_deleted_with_server_token_and_version(self):
        self.attach()
        result=self.execute()
        self.assertEqual((result.status,result.mutation_state,result.http_status),('succeeded','confirmed',200))
        self.assertEqual(result.data['result'],'deleted')
        self.assertEqual(self.deletes(),[('delete',EID)])
        call=next(c for c in self.gateway.calls if c[0]=='execute')
        self.assertEqual(call[1:4],(EID,CALENDAR,'fixture-version-1'))
        self.assertEqual(call[4],deletion_confirmation(CALENDAR,EID,'fixture-version-1'))
        self.assertEqual(self.memory().confirmation.state,'consumed')
        self.assertEqual(self.record().state,'confirmed')
        self.assertTrue(result.operation_id.startswith('agent-delete-'))
    def test_no_proposal_never_opens_gateway(self):
        with self.assertRaises(ConfirmationError) as error: self.execute()
        self.assertEqual(error.exception.code,'confirmation_not_approved')
        self.assertEqual(self.opened,0)
        self.assertFalse(self.deletes())
    def test_pending_proposal_cannot_execute(self):
        self.attach(self.proposal(state='pending'))
        with self.assertRaises(ConfirmationError): self.execute()
        self.assertEqual(self.opened,0)
    def test_revoked_proposal_cannot_execute(self):
        self.attach(self.proposal(state='revoked'))
        with self.assertRaises(ConfirmationError): self.execute()
        self.assertEqual(self.opened,0)
    def test_expired_proposal_state_cannot_execute(self):
        self.attach(self.proposal(state='expired'))
        with self.assertRaises(ConfirmationError) as error: self.execute()
        self.assertEqual(error.exception.code,'confirmation_expired')
        self.assertEqual(self.opened,0)
    def test_actual_expiry_is_checked_by_atomic_consume(self):
        self.attach()
        self.now=NOW+timedelta(seconds=300)
        with self.assertRaises(ConfirmationError) as error: self.execute()
        self.assertEqual(error.exception.code,'confirmation_expired')
        self.assertFalse(self.deletes())
        self.assertEqual(self.record().state,'not_applied')
    def test_wrong_action_proposal_cannot_authorize_delete(self):
        self.attach(self.proposal(action='edit',parameters={**PARAMETERS,'title':'Novo título'}))
        with self.assertRaises(ConfirmationError): self.execute()
        self.assertEqual(self.opened,0)
    def test_different_event_approval_cannot_authorize_target(self):
        other=replace(ORIGINAL,event_id='OtherFixtureEvent')
        self.attach(self.proposal(event=other,parameters={**PARAMETERS,'event_id':other.event_id}))
        with self.assertRaises(ConfirmationError): self.execute()
        self.assertEqual(self.opened,0)
    def test_incomplete_pending_request_never_reads_calendar(self):
        self.prepare(awaiting=('event_id',))
        with self.assertRaises(ConfirmationError): self.execute()
        self.assertEqual(self.opened,0)
    def test_wrong_prepared_action_never_reads_calendar(self):
        self.prepare(action='edit',parameters={**PARAMETERS,'title':'Novo título'})
        with self.assertRaises(ConfirmationError): self.execute()
        self.assertEqual(self.opened,0)
    def test_another_owner_cannot_use_conversation(self):
        self.attach()
        with self.assertRaises(ConversationError): self.service.execute(OTHER,self.cid)
        self.assertEqual(self.opened,0)
    def test_changed_provider_version_requires_new_approval(self):
        self.attach()
        self.gateway.version='fixture-version-2'
        with self.assertRaises(ConfirmationError) as error: self.execute()
        self.assertEqual(error.exception.code,'confirmation_stale')
        self.assertFalse(self.deletes())
        self.assertIsNone(self.record())
        self.assertEqual(self.memory().confirmation.state,'approved')
    def test_changed_provider_calendar_requires_new_approval(self):
        self.attach()
        self.gateway.calendar='other-fixture-calendar'
        with self.assertRaises(ConfirmationError): self.execute()
        self.assertFalse(self.deletes())
    def test_changed_event_snapshot_even_same_version_is_refused(self):
        self.attach()
        self.gateway.current=replace(ORIGINAL,title='Mudou sem versão')
        with self.assertRaises(ConfirmationError): self.execute()
        self.assertFalse(self.deletes())
    def test_context_change_during_read_prevents_consume_and_delete(self):
        self.attach()
        self.gateway.snapshot_hook=lambda:self.prepare()
        with self.assertRaises((ConversationError,ConfirmationError)): self.execute()
        self.assertFalse(self.deletes())
        self.assertEqual(self.record().state,'not_applied')
    def test_version_race_after_consume_is_known_not_applied(self):
        self.attach()
        self.gateway.guard_hook=lambda:setattr(self.gateway,'version','fixture-version-2')
        result=self.execute()
        self.assertEqual((result.http_status,result.mutation_state,result.error_code),(409,'not_applied','delete_snapshot_changed'))
        self.assertFalse(self.deletes())
        self.assertEqual(self.record().state,'not_applied')
        self.assertEqual(self.memory().confirmation.state,'consumed')
        count=len(self.gateway.calls)
        self.assertEqual(self.execute().error_code,'delete_claim_conflict')
        self.assertEqual(len(self.gateway.calls),count)
    def test_calendar_race_after_consume_is_known_not_applied(self):
        self.attach()
        self.gateway.guard_hook=lambda:setattr(self.gateway,'calendar','other-fixture-calendar')
        result=self.execute()
        self.assertEqual((result.http_status,result.mutation_state,result.error_code),(409,'not_applied','delete_calendar_changed'))
        self.assertFalse(self.deletes())
    def test_fresh_proposal_after_known_refusal_is_new_operation(self):
        first=self.attach()
        self.gateway.guard_hook=lambda:setattr(self.gateway,'version','fixture-version-2')
        refused=self.execute()
        self.gateway.guard_hook=None
        self.prepare()
        self.attach()
        result=self.execute()
        self.assertEqual(result.status,'succeeded')
        self.assertNotEqual(result.operation_id,refused.operation_id)
        self.assertEqual(self.record(first).state,'not_applied')
        self.assertEqual(len(self.deletes()),1)
    def test_already_absent_consumes_approved_proposal_without_delete(self):
        self.attach()
        self.gateway.current=None
        result=self.execute()
        self.assertEqual(result.data['result'],'already_absent')
        self.assertFalse(result.data['recovered'])
        self.assertFalse(self.deletes())
        self.assertEqual(self.memory().confirmation.state,'consumed')
    def test_initial_absence_reappearing_before_final_read_is_not_success(self):
        self.attach()
        self.gateway.current=None
        def reappear():
            if len([call for call in self.gateway.calls if call[0]=='read'])==2:
                self.gateway.current=ORIGINAL
        self.gateway.snapshot_hook=reappear
        result=self.execute()
        self.assertEqual(result.mutation_state,'unknown')
        self.assertFalse(self.deletes())
        self.assertEqual(self.record().state,'unknown')
    def test_repeat_and_new_process_objects_only_read(self):
        self.attach()
        first=self.execute()
        self.service=AgentDeletionService(ConversationStore(self.root/'context.sqlite3',clock=lambda:self.now),
            AgentDeletionStore(self.root/'deletions.sqlite3',clock=lambda:self.now),self.factory)
        again=self.execute()
        self.assertEqual(again.operation_id,first.operation_id)
        self.assertEqual(again.data['result'],'already_absent')
        self.assertEqual(len(self.deletes()),1)
        self.assertEqual(len([c for c in self.gateway.calls if c[0]=='execute']),1)
    def test_lost_response_absence_recovers_without_second_delete(self):
        self.attach()
        self.gateway.lost_response=True
        unknown=self.execute()
        self.assertEqual((unknown.http_status,unknown.mutation_state),(502,'unknown'))
        self.assertEqual(self.record().state,'unknown')
        self.gateway.lost_response=False
        result=self.execute()
        self.assertEqual(result.operation_id,unknown.operation_id)
        self.assertEqual(result.status,'succeeded')
        self.assertEqual(result.data['result'],'already_absent')
        self.assertEqual(len(self.deletes()),1)
    def test_uncertain_live_state_never_repeats_delete(self):
        self.attach()
        self.gateway.write_error=OSError('PRIVATE_DIAGNOSTIC')
        first=self.execute()
        self.gateway.write_error=None
        again=self.execute()
        self.assertEqual((first.mutation_state,again.mutation_state),('unknown','unknown'))
        self.assertEqual(again.operation_id,first.operation_id)
        self.assertFalse(self.deletes())
        self.assertEqual(len([c for c in self.gateway.calls if c[0]=='execute']),1)
    def test_new_approval_does_not_bypass_unknown_operation(self):
        old=self.attach()
        self.gateway.write_error=OSError('PRIVATE_DIAGNOSTIC')
        first=self.execute()
        self.gateway.write_error=None
        self.prepare()
        fresh=self.attach()
        self.assertNotEqual(fresh.proposal_id,old.proposal_id)
        again=self.execute()
        self.assertEqual(again.operation_id,first.operation_id)
        self.assertEqual(again.mutation_state,'unknown')
        self.assertIsNone(self.record(fresh))
        self.assertFalse(self.deletes())
    def test_other_target_is_blocked_while_original_delete_unknown(self):
        self.attach()
        self.gateway.write_error=OSError('PRIVATE_DIAGNOSTIC')
        first=self.execute()
        self.prepare(parameters={**PARAMETERS,'event_id':'OtherFixtureEvent'})
        count=len(self.gateway.calls)
        again=self.execute()
        self.assertEqual((again.http_status,again.error_code),(409,'delete_unresolved'))
        self.assertEqual(again.operation_id,first.operation_id)
        self.assertEqual(again.data['event_id'],EID)
        self.assertEqual(len(self.gateway.calls),count)
    def test_read_failure_is_not_absence_and_preserves_approval(self):
        self.attach()
        self.gateway.snapshot_error=OSError('PRIVATE_DIAGNOSTIC')
        result=self.execute()
        self.assertEqual((result.http_status,result.mutation_state,result.error_code),(502,'not_applied','event_read_failed'))
        self.assertEqual(self.memory().confirmation.state,'approved')
        self.assertIsNone(self.record())
    def test_snapshot_for_another_id_cannot_authorize_delete(self):
        self.attach()
        self.gateway.current=replace(ORIGINAL,event_id='OtherFixtureEvent')
        result=self.execute()
        self.assertEqual(result.error_code,'event_read_failed')
        self.assertEqual(self.memory().confirmation.state,'approved')
        self.assertFalse(self.deletes())
    def test_gateway_setup_failure_before_read_is_known_not_applied(self):
        self.attach()
        @contextmanager
        def unavailable(): raise CalendarSetupError('PRIVATE_DIAGNOSTIC'); yield
        result=AgentDeletionService(self.context,self.ledger,unavailable).execute(OWNER,self.cid)
        self.assertEqual((result.http_status,result.mutation_state),(503,'not_applied'))
        self.assertFalse(self.deletes())
    def test_bad_provider_receipt_stays_unknown(self):
        self.attach()
        self.gateway.malformed=True
        result=self.execute()
        self.assertEqual(result.mutation_state,'unknown')
        self.assertEqual(self.record().state,'unknown')
    def test_deleted_receipt_must_match_the_approved_snapshot(self):
        self.attach()
        original=self.gateway.delete_event
        def mismatch(*args,**kwargs):
            result=original(*args,**kwargs)
            return replace(result,event=replace(result.event,title='Outro conteúdo no recibo'))
        with patch.object(self.gateway,'delete_event',side_effect=mismatch):
            result=self.execute()
        self.assertEqual(result.mutation_state,'unknown')
        self.assertEqual(len(self.deletes()),1)
    def test_postread_still_live_is_unknown_not_success(self):
        self.attach()
        self.gateway.after_hook=lambda:setattr(self.gateway,'current',ORIGINAL)
        result=self.execute()
        self.assertEqual(result.mutation_state,'unknown')
        self.assertEqual(len(self.deletes()),1)
        self.assertEqual(self.execute().mutation_state,'unknown')
        self.assertEqual(len(self.deletes()),1)
    def test_postread_failure_is_unknown_then_readonly_absence_recovers(self):
        self.attach()
        self.gateway.after_hook=lambda:setattr(self.gateway,'snapshot_error',OSError('PRIVATE_DIAGNOSTIC'))
        result=self.execute()
        self.assertEqual(result.mutation_state,'unknown')
        self.gateway.snapshot_error=None
        self.assertEqual(self.execute().status,'succeeded')
        self.assertEqual(len(self.deletes()),1)
    def test_confirmed_event_reappearing_is_not_deleted_again(self):
        self.attach()
        first=self.execute()
        self.gateway.current=replace(ORIGINAL,title='Evento restaurado')
        self.gateway.version='fixture-version-restored'
        again=self.execute()
        self.assertEqual((again.http_status,again.mutation_state,again.error_code),(409,'confirmed','delete_previously_confirmed'))
        self.assertEqual(again.operation_id,first.operation_id)
        self.assertEqual(len(self.deletes()),1)
    def test_all_day_approved_event_can_be_deleted(self):
        self.gateway.current=replace(ORIGINAL,start='2030-06-17',end='2030-06-18',all_day=True)
        self.attach()
        self.assertEqual(self.execute().status,'succeeded')
        self.assertEqual(len(self.deletes()),1)
    def test_untitled_public_event_can_be_deleted(self):
        self.gateway.current=replace(ORIGINAL,title='(sem título)')
        self.attach()
        self.assertEqual(self.execute().status,'succeeded')
        self.assertEqual(len(self.deletes()),1)
    def test_concurrent_command_while_writer_runs_only_reads(self):
        self.attach()
        self.gateway.entered,self.gateway.release=Event(),Event()
        with ThreadPoolExecutor(max_workers=2) as pool:
            future=pool.submit(self.execute)
            self.assertTrue(self.gateway.entered.wait(timeout=5))
            repeated=self.execute()
            self.assertEqual(repeated.mutation_state,'unknown')
            self.gateway.release.set()
            completed=future.result(timeout=5)
        self.assertEqual(completed.status,'succeeded')
        self.assertEqual(repeated.operation_id,completed.operation_id)
        self.assertEqual(len(self.deletes()),1)
    def test_concurrent_absence_receipt_is_reused_when_writer_finishes(self):
        self.attach()
        original_finish=self.ledger.finish
        def race(owner,cid,opid,attempt,state,result=None):
            if state=='confirmed' and result['result']=='deleted':
                original_finish(owner,cid,opid,attempt,'confirmed',{'result':'already_absent',
                    'calendar_name':result['calendar_name'],'event_id':EID,'recovered':False})
                raise AgentDeletionError('deletion_claim_conflict')
            return original_finish(owner,cid,opid,attempt,state,result)
        with patch.object(self.ledger,'finish',side_effect=race): result=self.execute()
        self.assertEqual(result.status,'succeeded')
        self.assertEqual(result.data['result'],'already_absent')
        self.assertEqual(len(self.deletes()),1)
    def test_failure_saving_receipt_keeps_operation_unknown_without_new_delete(self):
        self.attach()
        original_finish=self.ledger.finish
        def fail(owner,cid,opid,attempt,state,result=None):
            if state=='confirmed': raise AgentDeletionError('deletion_unavailable')
            return original_finish(owner,cid,opid,attempt,state,result)
        with patch.object(self.ledger,'finish',side_effect=fail): result=self.execute()
        self.assertEqual(result.mutation_state,'unknown')
        self.assertEqual(self.record().state,'running')
        self.assertEqual(self.execute().status,'succeeded')
        self.assertEqual(len(self.deletes()),1)
    def test_result_validator_rejects_invalid_envelopes(self):
        valid=AgentDeletionResult('succeeded','confirmed','Ausência confirmada.',
            {'result':'already_absent','calendar_name':'Genius Testes','event_id':EID,'recovered':False},'agent-delete-'+'a'*64)
        self.assertIs(validate_deletion_result(valid),valid)
        bad=[replace(valid,mutation_state='not_applied'),replace(valid,operation_id='caller'),
            replace(valid,data={**valid.data,'recovered':True}),replace(valid,data={**valid.data,'private':'PRIVATE_DIAGNOSTIC'}),
            replace(valid,http_status=201),replace(valid,status='deleted')]
        for result in bad:
            with self.assertRaises(AgentDeletionError) as error: validate_deletion_result(result)
            self.assertNotIn('PRIVATE_DIAGNOSTIC',str(error.exception))

if __name__=='__main__': unittest.main()
