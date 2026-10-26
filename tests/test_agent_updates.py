"""Comandos reais de atualização com SQLite isolado e gateway controlado."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch
from uuid import uuid4

from app.agent_update_store import AgentUpdateError, AgentUpdateStore
from app.confirmation_state import ConfirmationError, ConfirmationProposal, proposal_request_hash
from app.conversation_store import ConversationError, ConversationStore, QueryMemory
from app.dialogue_state import PendingRequest
from app.google_calendar import CalendarSetupError
from app.services.agent_updates import AgentUpdateService, UpdateResult, validate_update_result
from app.services.agenda import AgendaEvent, CalendarPeriod
from app.services.agenda_tools import minimal_event
from app.services.conflicts import CalendarConflictError, build_conflict_report
from app.services.event_details import DetailsResult, EventDetails
from app.services.rescheduling import RescheduleResult
from app.services.update_snapshot import UpdateBindingError, UpdateVersionError, UpdateSnapshot

NOW = datetime(2030, 6, 17, 12, tzinfo=timezone.utc)
OWNER, OTHER = sha256(b'fixture-owner').hexdigest(), sha256(b'other-owner').hexdigest()
CALENDAR, ZONE, EID = 'fixture-calendar', 'America/Recife', 'FixtureExactEvent'
EDIT = {'event_id': EID, 'time_zone': ZONE, 'title': 'Entrevista atualizada', 'description': ''}
SCHEDULE = {'event_id': EID, 'time_zone': ZONE, 'start': '2030-06-17T15:00:00-03:00', 'end': '2030-06-17T15:30:00-03:00'}
ORIGINAL = EventDetails(EID, 'Entrevista original', '2030-06-17T10:00:00-03:00',
    '2030-06-17T10:30:00-03:00', False, ZONE, 'confirmed', 'https://example.invalid/event', 'Descrição original', 'Sala original')

def agenda(value):
    fields = {name: getattr(value, name) for name in ('event_id','title','start','end','all_day','time_zone','status','html_link')}
    if not fields['title'].strip():
        fields['title'] = '(sem título)'
    return AgendaEvent(**fields)

class Gateway:
    def __init__(self):
        self.current, self.calendar, self.version = ORIGINAL, CALENDAR, 'fixture-version-1'
        self.transparency, self.calls = 'opaque', []
        self.snapshot_error = self.write_error = None
        self.snapshot_hook = self.guard_hook = self.after_hook = None
        self.lost_response = self.malformed = False
        self.entered = self.release = None
    def get_update_snapshot(self, event_id, *, time_zone):
        self.calls.append(('read', event_id, time_zone))
        if self.snapshot_hook:
            self.snapshot_hook()
        if self.snapshot_error:
            raise self.snapshot_error
        return UpdateSnapshot(self.calendar, 'Genius Testes', self.current, self.version, self.transparency)
    def guards(self, event_id, expected_calendar_id, expected_version):
        if self.guard_hook:
            self.guard_hook()
        if self.calendar != expected_calendar_id:
            raise UpdateBindingError('PRIVATE_DIAGNOSTIC')
        if self.version != expected_version:
            raise UpdateVersionError('PRIVATE_DIAGNOSTIC')
        assert event_id == self.current.event_id
        if self.entered:
            self.entered.set()
            assert self.release.wait(timeout=5)
        if self.write_error:
            raise self.write_error
    def update_event_details(self, event_id, change, *, time_zone, execute, expected_calendar_id, expected_version):
        self.calls.append(('edit', event_id, expected_calendar_id, expected_version))
        self.guards(event_id, expected_calendar_id, expected_version)
        assert execute and time_zone == ZONE
        before = self.current
        requested = {name: getattr(change, name) for name in ('title','description','location') if getattr(change,name) is not None}
        changed = any(getattr(before,name) != value for name,value in requested.items())
        if changed:
            self.calls.append(('patch', event_id))
            self.current = replace(before, **requested)
            self.version = 'fixture-version-2'
        if self.after_hook:
            self.after_hook()
        if self.lost_response:
            raise OSError('PRIVATE_DIAGNOSTIC')
        return None if self.malformed else DetailsResult('updated' if changed else 'unchanged', 'Genius Testes', before, self.current, requested)
    def reschedule_event(self, event_id, change, *, execute, expected_calendar_id, expected_version):
        self.calls.append(('reschedule',event_id,expected_calendar_id,expected_version))
        self.guards(event_id,expected_calendar_id,expected_version)
        assert execute
        before = self.current
        changed = datetime.fromisoformat(before.start) != change.start or datetime.fromisoformat(before.end) != change.end
        if changed:
            self.calls.append(('patch',event_id))
            self.current=replace(before,start=change.start.isoformat(),end=change.end.isoformat())
            self.version='fixture-version-2'
        if self.after_hook:
            self.after_hook()
        if self.lost_response:
            raise OSError('PRIVATE_DIAGNOSTIC')
        return None if self.malformed else RescheduleResult('rescheduled' if changed else 'unchanged', 'Genius Testes', agenda(before),agenda(self.current),change.start.isoformat(),change.end.isoformat(),change.time_zone)

class AgentUpdateTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.now=NOW
        self.context=ConversationStore(self.root/'context.sqlite3',clock=lambda:self.now)
        self.ledger=AgentUpdateStore(self.root/'updates.sqlite3',clock=lambda:self.now)
        self.cid=self.context.create(OWNER)
        self.gateway=Gateway()
        self.opened=self.closed=0
        self.service=AgentUpdateService(self.context,self.ledger,self.factory)
        self.addCleanup(lambda:self.assertEqual(self.opened,self.closed))
        self.prepare()
    @contextmanager
    def factory(self):
        self.opened+=1
        try: yield self.gateway
        finally: self.closed+=1
    def prepare(self,action='edit',parameters=None,awaiting=(),candidates=()):
        self.action=action
        self.parameters=dict(parameters if parameters is not None else EDIT if action=='edit' else SCHEDULE)
        old=self.context.load(OWNER,self.cid)
        self.context.save(OWNER,self.cid,old.revision,QueryMemory(pending_request=PendingRequest({'action':action,**self.parameters},awaiting,candidates)))
    def memory(self): return self.context.load(OWNER,self.cid).memory
    def save(self,memory):
        old=self.context.load(OWNER,self.cid)
        self.context.save(OWNER,self.cid,old.revision,memory)
    def execute(self):
        result=self.service.execute(OWNER,self.cid,self.action)
        self.assertIs(validate_update_result(result),result)
        self.assertNotIn('PRIVATE_DIAGNOSTIC',repr(result))
        return result
    def record(self): return self.ledger.get(OWNER,self.cid,self.action,self.parameters)
    def patches(self): return [item for item in self.gateway.calls if item[0]=='patch']
    def proposal(self,*,state='approved',version=None,calendar=None,parameters=None,action=None):
        parameters=self.parameters if parameters is None else parameters
        action=self.action if action is None else action
        calendar=calendar or CALENDAR
        version=version or self.gateway.version
        event=minimal_event(agenda(self.gateway.current))
        return ConfirmationProposal(str(uuid4()),action,dict(parameters),
            proposal_request_hash(action,parameters,calendar,EID,version,event),calendar,EID,version,event,
            NOW.isoformat(),(NOW+timedelta(seconds=300)).isoformat(),state,
            NOW.isoformat() if state=='approved' else None,'a'*64 if state=='approved' else None)
    def attach(self,proposal): self.save(replace(self.memory(),confirmation=proposal))

    def test_edit_uses_exact_id_bound_version_and_preserves_unspecified_fields(self):
        result=self.execute()
        self.assertEqual((result.status,result.mutation_state,result.http_status),('succeeded','confirmed',200))
        self.assertEqual(self.gateway.current,replace(ORIGINAL,title=EDIT['title'],description=''))
        self.assertEqual([c for c in self.gateway.calls if c[0]=='edit'],[('edit',EID,CALENDAR,'fixture-version-1')])
        self.assertEqual(self.record().state,'confirmed')
        self.assertTrue(result.operation_id.startswith('agent-update-'))
    def test_reschedule_uses_existing_service_and_preserves_details(self):
        self.prepare('reschedule')
        result=self.execute()
        self.assertEqual(result.status,'succeeded')
        self.assertEqual(self.gateway.current,replace(ORIGINAL,start=SCHEDULE['start'],end=SCHEDULE['end']))
        self.assertEqual(result.data['result'],'rescheduled')
    def test_reschedule_of_untitled_event_preserves_raw_title_and_public_label(self):
        self.prepare('reschedule')
        self.gateway.current=replace(ORIGINAL,title='')
        result=self.execute()
        self.assertEqual(result.status,'succeeded')
        self.assertEqual(result.data['before']['title'],'(sem título)')
        self.assertEqual(self.gateway.current.title,'')
        self.assertEqual(self.execute().status,'succeeded')
        self.assertEqual(len(self.patches()),1)
    def test_clear_location_is_distinct_from_omitted_description(self):
        self.prepare(parameters={'event_id':EID,'time_zone':ZONE,'location':''})
        self.execute()
        self.assertEqual(self.gateway.current.location,'')
        self.assertEqual(self.gateway.current.description,ORIGINAL.description)
    def test_unchanged_request_confirms_without_patch(self):
        self.prepare(parameters={'event_id':EID,'time_zone':ZONE,'title':ORIGINAL.title})
        result=self.execute()
        self.assertEqual(result.data['result'],'unchanged')
        self.assertEqual(self.patches(),[])
        self.assertEqual(self.execute().data['result'],'unchanged')
    def test_repeat_and_restart_only_read_with_same_operation(self):
        first=self.execute()
        self.service=AgentUpdateService(ConversationStore(self.root/'context.sqlite3',clock=lambda:self.now),AgentUpdateStore(self.root/'updates.sqlite3',clock=lambda:self.now),self.factory)
        second=self.execute()
        self.assertEqual(first.operation_id,second.operation_id)
        self.assertTrue(second.data['recovered'])
        self.assertEqual(len(self.patches()),1)
    def test_lost_response_recovers_observed_state_without_patch(self):
        self.gateway.lost_response=True
        first=self.execute()
        self.assertEqual(first.mutation_state,'unknown')
        self.assertEqual(self.record().state,'unknown')
        self.gateway.lost_response=False
        second=self.execute()
        self.assertEqual((second.status,second.operation_id),('succeeded',first.operation_id))
        self.assertEqual(len(self.patches()),1)
    def test_unknown_absent_or_different_state_never_reapplies(self):
        self.gateway.write_error=OSError('PRIVATE_DIAGNOSTIC')
        first=self.execute()
        self.gateway.write_error=None
        second=self.execute()
        self.assertEqual(second.mutation_state,'unknown')
        self.assertEqual(second.operation_id,first.operation_id)
        self.assertEqual(len([c for c in self.gateway.calls if c[0]=='edit']),1)
    def test_unknown_blocks_other_action_and_parameters_before_provider(self):
        self.gateway.write_error=OSError('PRIVATE_DIAGNOSTIC')
        first=self.execute()
        self.prepare('reschedule')
        before=list(self.gateway.calls)
        blocked=self.execute()
        self.assertEqual(blocked.error_code,'update_unresolved')
        self.assertEqual(blocked.operation_id,first.operation_id)
        self.assertEqual(before,self.gateway.calls)
    def test_confirmed_later_drift_is_history_not_reapplication(self):
        self.execute()
        self.gateway.current=replace(self.gateway.current,title='Mudança externa')
        later=self.execute()
        self.assertEqual((later.error_code,later.mutation_state),('update_previously_confirmed','confirmed'))
        self.assertEqual(len(self.patches()),1)
    def test_confirmed_read_failure_does_not_rewrite_or_demote_ledger(self):
        self.execute()
        self.gateway.snapshot_error=OSError('PRIVATE_DIAGNOSTIC')
        self.assertEqual(self.execute().error_code,'update_previously_confirmed')
        self.assertEqual(self.record().state,'confirmed')
        self.assertEqual(len(self.patches()),1)
    def test_read_failure_is_not_an_event_absence_or_write_attempt(self):
        self.gateway.snapshot_error=OSError('PRIVATE_DIAGNOSTIC')
        result=self.execute()
        self.assertEqual((result.error_code,result.mutation_state),('event_read_failed','not_applied'))
        self.assertIsNone(self.record())
        self.assertEqual(self.patches(),[])
    def test_setup_failure_is_known_before_mutation(self):
        self.gateway.snapshot_error=CalendarSetupError('PRIVATE_DIAGNOSTIC')
        result=self.execute()
        self.assertEqual((result.http_status,result.mutation_state),(503,'not_applied'))
    def test_incomplete_or_wrong_action_never_opens_gateway(self):
        self.prepare(awaiting=('title',))
        with self.assertRaises(ConfirmationError): self.execute()
        self.assertEqual(self.opened,0)
        self.prepare()
        with self.assertRaises(ConfirmationError): self.service.execute(OWNER,self.cid,'reschedule')
        self.assertEqual(self.opened,0)
    def test_foreign_conversation_never_opens_gateway(self):
        with self.assertRaises(ConversationError): self.service.execute(OTHER,self.cid,'edit')
        self.assertEqual(self.opened,0)
    def test_context_change_during_read_prevents_patch(self):
        def change():
            self.gateway.snapshot_hook=None
            self.prepare(parameters={**EDIT,'title':'Mudou no contexto'})
        self.gateway.snapshot_hook=change
        result=self.execute()
        self.assertEqual(result.error_code,'conversation_conflict')
        self.assertEqual(self.patches(),[])
    def test_calendar_change_before_patch_is_known_not_applied(self):
        self.gateway.guard_hook=lambda:setattr(self.gateway,'calendar','other-calendar')
        result=self.execute()
        self.assertEqual((result.error_code,result.mutation_state),('update_calendar_changed','not_applied'))
        self.assertEqual(self.record().state,'not_applied')
        self.assertEqual(self.patches(),[])
    def test_version_change_before_patch_is_known_not_applied(self):
        self.gateway.guard_hook=lambda:setattr(self.gateway,'version','external-version')
        result=self.execute()
        self.assertEqual((result.error_code,result.mutation_state),('update_snapshot_changed','not_applied'))
        self.assertEqual(self.patches(),[])
    def test_retry_of_known_failure_cannot_rebind_changed_snapshot(self):
        self.gateway.guard_hook=lambda:setattr(self.gateway,'version','external-version')
        self.execute()
        self.gateway.guard_hook=None
        with self.assertRaises(AgentUpdateError) as caught: self.execute()
        self.assertEqual(caught.exception.code,'update_snapshot_changed')
        self.assertEqual(self.patches(),[])
    def test_schedule_conflict_is_confirmed_no_write_and_excludes_target(self):
        self.prepare('reschedule')
        change_start=datetime.fromisoformat(SCHEDULE['start'])
        change_end=datetime.fromisoformat(SCHEDULE['end'])
        conflict=replace(agenda(ORIGINAL),event_id='FixtureConflict',start=SCHEDULE['start'],end=SCHEDULE['end'])
        self.gateway.write_error=CalendarConflictError(build_conflict_report(CalendarPeriod(change_start,change_end,ZONE),'Genius Testes',(conflict,)))
        result=self.execute()
        self.assertEqual((result.status,result.error_code),('conflict','schedule_conflict'))
        self.assertEqual(self.record().state,'conflict')
        self.assertEqual(self.patches(),[])
    def test_all_day_reschedule_is_refused_before_reservation(self):
        self.prepare('reschedule')
        self.gateway.current=replace(ORIGINAL,start='2030-06-17',end='2030-06-18',all_day=True)
        self.assertEqual(self.execute().error_code,'update_event_unsupported')
        self.assertIsNone(self.record())
    def test_active_pending_proposal_cannot_be_bypassed(self):
        self.attach(self.proposal(state='pending'))
        with self.assertRaises(ConfirmationError) as caught: self.execute()
        self.assertEqual(caught.exception.code,'confirmation_not_approved')
        self.assertEqual(self.opened,0)
    def test_approved_proposal_is_consumed_once_and_replay_only_reads(self):
        self.attach(self.proposal())
        first=self.execute()
        self.assertEqual(self.memory().confirmation.state,'consumed')
        self.assertEqual(self.execute().operation_id,first.operation_id)
        self.assertEqual(len(self.patches()),1)
    def test_proposal_version_or_calendar_drift_prevents_consumption(self):
        self.attach(self.proposal(version='old-version'))
        with self.assertRaises(ConfirmationError) as caught: self.execute()
        self.assertEqual(caught.exception.code,'confirmation_stale')
        self.assertEqual(self.memory().confirmation.state,'approved')
        self.assertEqual(self.patches(),[])
    def test_approved_proposal_expiry_is_enforced_at_atomic_consume(self):
        self.attach(self.proposal())
        self.now=NOW+timedelta(seconds=300)
        with self.assertRaises(ConfirmationError) as caught:self.execute()
        self.assertEqual(caught.exception.code,'confirmation_expired')
        self.assertEqual(self.patches(),[])
    def test_readback_rejects_unrequested_edit_metadata_change(self):
        self.gateway.after_hook=lambda:setattr(self.gateway,'current',replace(self.gateway.current,html_link='https://example.invalid/changed'))
        result=self.execute()
        self.assertEqual(result.mutation_state,'unknown')
        self.assertEqual(self.record().state,'unknown')
    def test_reschedule_readback_rejects_changed_description(self):
        self.prepare('reschedule')
        self.gateway.after_hook=lambda:setattr(self.gateway,'current',replace(self.gateway.current,description='Mudou sem pedido'))
        result=self.execute()
        self.assertEqual(result.mutation_state,'unknown')
        self.assertEqual(self.record().state,'unknown')
    def test_wrong_event_snapshot_never_reserves_or_writes(self):
        self.gateway.current=replace(self.gateway.current,event_id='AnotherEvent')
        self.assertEqual(self.execute().error_code,'event_read_failed')
        self.assertIsNone(self.record())
    def test_invalid_provider_result_after_patch_stays_unknown(self):
        self.gateway.malformed=True
        self.assertEqual(self.execute().mutation_state,'unknown')
        self.gateway.malformed=False
        self.assertEqual(self.execute().status,'succeeded')
        self.assertEqual(len(self.patches()),1)
    def test_simultaneous_commands_patch_once(self):
        self.gateway.entered,self.gateway.release=Event(),Event()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(self.service.execute,OWNER,self.cid,'edit')
            self.assertTrue(self.gateway.entered.wait(timeout=5))
            second=self.service.execute(OWNER,self.cid,'edit')
            self.assertEqual(second.mutation_state,'unknown')
            self.gateway.release.set()
            self.assertEqual(first.result(timeout=5).status,'succeeded')
        self.assertEqual(len(self.patches()),1)
    def test_concurrent_read_confirmation_can_finish_before_original_receipt(self):
        observed=[]
        def reconcile():
            self.gateway.after_hook=None
            observed.append(self.service.execute(OWNER,self.cid,'edit'))
        self.gateway.after_hook=reconcile
        result=self.execute()
        self.assertEqual(result.status,'succeeded')
        self.assertEqual(observed[0].status,'succeeded')
        self.assertEqual(result.operation_id,observed[0].operation_id)
        self.assertEqual(len(self.patches()),1)
    def test_failed_ledger_commit_after_patch_does_not_authorize_retry(self):
        original=self.ledger.finish
        def fail(owner,cid,opid,attemptid,state,result=None):
            if state=='confirmed': raise OSError('PRIVATE_DIAGNOSTIC')
            return original(owner,cid,opid,attemptid,state,result)
        with patch.object(self.ledger,'finish',side_effect=fail):
            self.assertEqual(self.execute().mutation_state,'unknown')
        self.assertEqual(self.execute().status,'succeeded')
        self.assertEqual(len(self.patches()),1)
    def test_invalid_result_projection_rejects_extra_private_fields(self):
        result=self.execute()
        with self.assertRaises(AgentUpdateError):
            validate_update_result(replace(result,data={**result.data,'private':'PRIVATE_DIAGNOSTIC'}))

if __name__=='__main__': unittest.main()
