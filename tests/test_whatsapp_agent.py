"""Turnos reais com stores/serviços e modelo/gateway fictícios; sem provedores."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from app.agent_creation_store import AgentCreationStore
from app.agent_update_store import AgentUpdateStore
from app.agent_deletion_store import AgentDeletionStore
from app.agent_query_contract import QueryPlan
from app.conversation_store import ConversationStore,ConversationError
from app.dialogue_contract import DIALOGUE_TOOLS,DialoguePlan
from app.llm import ModelError
from app.services.agent_creation import AgentCreationService
from app.services.agent_updates import AgentUpdateService
from app.services.agent_deletion import AgentDeletionService
from app.services.agent_dialogue import AgentDialogueService
from app.services.agent_queries import AgentQueriesService
from app.services.agenda_tools import AgendaReadTools
from app.services.agenda import AgendaEvent,CalendarListing,CalendarPeriod
from app.services.confirmations import ConfirmationService
from app.services.confirmation_snapshot import ConfirmationSnapshot
from app.services.event_deletion import DeletionResult,deletion_confirmation,DeletionVersionError,DeletionBindingError
from app.services.event_details import EventDetails
from app.services.event_selection import EventLookup,normalized_title
from app.services.interviews import CreatedInterview,ReconciledCreation
from app.services.conflicts import CalendarConflictError,build_conflict_report
from app.services.whatsapp_agent import WhatsAppAgentService,AgentTurn,validate_agent_turn,explicit_action,MAX_REPLY_CHARS
from test_agent_updates import Gateway as UpdateGateway,agenda,ORIGINAL,ZONE,NOW,OWNER,EID

START='2030-06-17T15:00:00-03:00'
END='2030-06-17T15:30:00-03:00'
TITLE='Entrevista fictícia'

def plan(name='prepare_request',**values):
    if name=='prepare_request':
        args=dict.fromkeys(DIALOGUE_TOOLS[0]['parameters']['required'])
        args.update(mode='start',use_selected_event=False)
        args.update(values)
    else: args=values
    return DialoguePlan(name,args,'fixture-whatsapp-model')

class Model:
    def __init__(self):
        self.plan=plan('ask_dialogue',reason='unsupported_request',field='request')
        self.query_plan=QueryPlan('list_events',{'date':'2030-06-17','period':'day','time_zone':ZONE},'fixture-query-model')
        self.calls=[]
        self.error=None
    def plan_dialogue(self,**request):
        self.calls.append(('dialogue',request))
        if self.error: raise self.error
        return self.plan
    def plan_query(self,**request):
        self.calls.append(('query',request))
        if self.error: raise self.error
        return self.query_plan

class Gateway(UpdateGateway):
    def __init__(self):
        super().__init__()
        self.pool=[]
        self.created=None
        self.create_error=None
        self.conflict=False
        self.delete_error=None
        self.delete_applied=False
    def get_event(self,event_id,*,time_zone):
        self.calls.append(('lookup',event_id))
        events=([self.current] if self.current else [])+self.pool
        selected=next((event for event in events if event.event_id==event_id),None)
        return EventLookup('Genius Testes',agenda(selected) if selected else None)
    def list_events(self,period):
        self.calls.append(('list',period))
        return CalendarListing('Genius Testes',tuple(agenda(event) for event in ([self.current] if self.current else [])+self.pool))
    def search_events_by_title(self,title,*,time_zone):
        self.calls.append(('search',title,time_zone))
        needle=normalized_title(title)
        events=([self.current] if self.current else [])+self.pool
        return CalendarListing('Genius Testes',tuple(agenda(event) for event in events if needle in normalized_title(event.title)))
    def get_confirmation_snapshot(self,event_id,*,time_zone):
        self.calls.append(('confirmation',event_id))
        events=([self.current] if self.current else [])+self.pool
        current=next((event for event in events if event.event_id==event_id),None)
        return ConfirmationSnapshot(self.calendar,'Genius Testes',agenda(current) if current else None,self.version if current else None)
    def create_event(self,draft,*,operation_id,expected_calendar_id):
        assert expected_calendar_id==self.calendar
        if self.conflict:
            busy=AgendaEvent('BusyFixture','Ocupado',draft.start.isoformat(),draft.end.isoformat(),False,draft.time_zone,'confirmed')
            raise CalendarConflictError(build_conflict_report(CalendarPeriod(draft.start,draft.end,draft.time_zone),'Genius Testes',(busy,)))
        self.calls.append(('create',operation_id))
        if self.create_error: raise self.create_error
        self.created=CreatedInterview('FixtureCreated','https://example.invalid/created','Genius Testes')
        self.current=EventDetails('FixtureCreated',draft.title,draft.start.isoformat(),draft.end.isoformat(),False,draft.time_zone,'confirmed','https://example.invalid/created',draft.description,'')
        return self.created
    def reconcile_creation(self,draft,*,operation_id,expected_calendar_id):
        self.calls.append(('reconcile_create',operation_id))
        return ReconciledCreation('found',replace(self.created,reused=True,recovered=True)) if self.created and self.current else ReconciledCreation('absent')
    def delete_event(self,event_id,*,time_zone,execute,confirmation_token,expected_calendar_id,expected_version):
        assert execute and expected_calendar_id==self.calendar
        if self.current is None: return DeletionResult('already_absent','Genius Testes',None)
        if expected_version!=self.version: raise DeletionVersionError('PRIVATE_PROVIDER_DIAGNOSTIC')
        assert confirmation_token==deletion_confirmation(self.calendar,event_id,self.version)
        self.calls.append(('delete',event_id))
        before=agenda(self.current)
        if self.delete_applied: self.current=None
        if self.delete_error: raise self.delete_error
        self.current=None
        return DeletionResult('deleted','Genius Testes',before)

class WhatsAppAgentTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.now=NOW
        self.context=ConversationStore(self.root/'context.sqlite3',clock=lambda:self.now)
        self.creation=AgentCreationStore(self.root/'creation.sqlite3',clock=lambda:self.now)
        self.updates=AgentUpdateStore(self.root/'updates.sqlite3',clock=lambda:self.now)
        self.deletions=AgentDeletionStore(self.root/'delete.sqlite3',clock=lambda:self.now)
        self.model=Model()
        self.gateway=Gateway()
        self.service=self.make_service()
    @contextmanager
    def factory(self): yield self.gateway
    def make_service(self):
        return WhatsAppAgentService(self.context,OWNER,
            dialogue_factory=lambda:AgentDialogueService(self.model,self.factory,clock=lambda:self.now),
            confirmation_factory=lambda:ConfirmationService(self.factory,clock=lambda:self.now),
            creation_factory=lambda:AgentCreationService(self.context,self.creation,self.factory),
            update_factory=lambda:AgentUpdateService(self.context,self.updates,self.factory),
            deletion_factory=lambda:AgentDeletionService(self.context,self.deletions,self.factory),
            query_factory=lambda:AgentQueriesService(self.model,AgendaReadTools(self.factory),clock=lambda:self.now),
            gateway_factory=self.factory)
    def turn(self,message,previous=None,**values):
        result=self.service.process(message,conversation_id=previous.conversation_id if previous else None,
            authorization_action=previous.authorization_action if previous else None,**values)
        self.assertIs(validate_agent_turn(result),result)
        self.assertLessEqual(len(result.reply),MAX_REPLY_CHARS)
        self.assertNotIn('PRIVATE_PROVIDER_DIAGNOSTIC',result.reply)
        self.assertNotIn(self.gateway.calendar,result.reply)
        self.assertNotIn(self.gateway.version,result.reply)
        return result
    def writes(self): return [call for call in self.gateway.calls if call[0] in {'create','patch','delete'}]
    def create_plan(self,mode='start',**values):
        return plan(mode=mode,action='create',title=TITLE,start=START,end=END,time_zone=ZONE,**values)
    def test_nontext_never_calls_model_or_calendar(self):
        result=self.turn(None,event_type='message_image')
        self.assertEqual(result.status,'needs_clarification')
        self.assertIn('texto',result.reply)
        self.assertFalse(self.model.calls)
        self.assertFalse(self.gateway.calls)
    def test_query_is_actual_read_and_keeps_results_out_of_model(self):
        result=self.turn('O que tenho amanhã?')
        self.assertEqual(result.action,'list')
        self.assertIn('Entrevista original',result.reply)
        self.assertEqual([call[0] for call in self.model.calls],['query'])
        self.assertNotIn('Entrevista original',self.model.calls[0][1]['instructions'])
        self.assertFalse(self.writes())
    def test_empty_query_is_confirmed_as_empty_not_provider_error(self):
        self.gateway.current=None
        result=self.turn('Consulte minha agenda')
        self.assertIn('Não encontrei compromissos',result.reply)
        self.assertFalse(self.writes())
    def test_clear_create_uses_existing_executor_without_generic_confirmation(self):
        self.model.plan=self.create_plan()
        result=self.turn('Agende Entrevista fictícia em 17/06/2030 das 15h às 15h30')
        self.assertEqual(result.mutation_state,'confirmed')
        self.assertEqual(len(self.writes()),1)
        self.assertIn('15h',result.reply)
        self.assertIsNone(self.context.load(OWNER,result.conversation_id).memory.pending_request)
    def test_create_missing_fields_preserves_trusted_authorization_across_restart(self):
        self.model.plan=plan(action='create',title=TITLE)
        first=self.turn('Agende Entrevista fictícia')
        self.assertEqual(first.authorization_action,'create')
        self.assertFalse(self.writes())
        self.service=self.make_service()
        self.model.plan=plan(mode='continue',action='create',start=START)
        second=self.turn('17/06/2030 às 15h',first)
        self.assertEqual(second.authorization_action,'create')
        self.model.plan=plan(mode='continue',action='create',end=END)
        final=self.turn('Termina às 15h30',second)
        self.assertEqual(final.mutation_state,'confirmed')
        self.assertEqual(len(self.writes()),1)
        self.assertEqual(len(self.model.calls),3)
    def test_question_ready_create_shows_preview_without_writing(self):
        self.model.plan=self.create_plan()
        result=self.turn('Seria possível criar Entrevista fictícia em 17/06/2030 das 15h às 15h30?')
        self.assertEqual(result.status,'needs_confirmation')
        self.assertIn('sim',result.reply)
        self.assertFalse(self.writes())
    def test_question_can_be_explicitly_confirmed_without_new_llm(self):
        self.model.plan=self.create_plan()
        preview=self.turn('Como ficaria Entrevista fictícia em 17/06/2030 das 15h às 15h30?')
        calls=len(self.model.calls)
        result=self.turn('sim',preview)
        self.assertEqual(result.mutation_state,'confirmed')
        self.assertEqual(len(self.model.calls),calls)
        self.assertEqual(len(self.writes()),1)
    def test_clear_edit_changes_only_requested_information(self):
        self.model.plan=plan(action='edit',event_id=EID,title='Título atualizado')
        before=self.gateway.current
        result=self.turn('Edite '+EID+' para Título atualizado')
        self.assertEqual(result.mutation_state,'confirmed')
        self.assertEqual(self.gateway.current.title,'Título atualizado')
        self.assertEqual(self.gateway.current.description,before.description)
        self.assertEqual(self.gateway.current.start,before.start)
    def test_clear_reschedule_preserves_event_information(self):
        self.model.plan=plan(action='reschedule',event_id=EID,start=START,end=END)
        before=self.gateway.current
        result=self.turn('Reagende '+EID+' em 17/06/2030 das 15h às 15h30')
        self.assertEqual(result.status,'needs_confirmation')
        self.assertEqual(self.gateway.current,before)
        self.assertFalse(self.writes())
        result=self.turn('sim',result)
        self.assertEqual(result.mutation_state,'confirmed')
        self.assertEqual(self.gateway.current.start,START)
        self.assertEqual(self.gateway.current.title,before.title)
        self.assertEqual(self.gateway.current.description,before.description)
    def test_delete_always_previews_and_approval_consumes_once(self):
        self.model.plan=plan(action='delete',event_id=EID)
        first=self.turn('Exclua '+EID)
        self.assertEqual(first.status,'needs_confirmation')
        self.assertIn('Entrevista original',first.reply)
        self.assertFalse(self.writes())
        second=self.turn('confirmo',first)
        self.assertEqual(second.mutation_state,'confirmed')
        self.assertIsNone(self.gateway.current)
        self.assertEqual(len(self.writes()),1)
        third=self.turn('sim',second)
        self.assertEqual(third.status,'consumed')
        self.assertEqual(len(self.writes()),1)
    def test_delete_refusal_never_calls_executor(self):
        self.model.plan=plan(action='delete',event_id=EID)
        first=self.turn('Exclua '+EID)
        result=self.turn('não',first)
        self.assertEqual(result.status,'revoked')
        self.assertFalse(self.writes())
    def test_ambiguous_ok_does_not_authorize_delete(self):
        self.model.plan=plan(action='delete',event_id=EID)
        first=self.turn('Exclua '+EID)
        self.model.plan=plan('ask_dialogue',reason='unclear_answer',field='request')
        result=self.turn('ok',first)
        self.assertNotEqual(result.mutation_state,'confirmed')
        self.assertFalse(self.writes())
    def test_expired_delete_approval_is_not_executed(self):
        self.model.plan=plan(action='delete',event_id=EID)
        first=self.turn('Exclua '+EID)
        self.now+=timedelta(seconds=301)
        result=self.turn('sim',first)
        self.assertEqual(result.status,'expired')
        self.assertFalse(self.writes())
    def test_changed_event_after_preview_invalidates_approval(self):
        self.model.plan=plan(action='delete',event_id=EID)
        first=self.turn('Exclua '+EID)
        self.gateway.version='fixture-version-changed'
        self.gateway.current=replace(self.gateway.current,title='Mudou depois da prévia')
        result=self.turn('sim',first)
        self.assertEqual(result.status,'invalidated')
        self.assertFalse(self.writes())
    def test_new_question_cannot_inherit_earlier_create_authorization(self):
        self.model.plan=plan(action='create',title=TITLE,start=START)
        first=self.turn('Agende Entrevista fictícia em 17/06/2030 às 15h')
        self.model.plan=plan(mode='continue',action='create',end=END)
        result=self.turn('E se terminasse às 15h30?',first)
        self.assertEqual(result.status,'needs_confirmation')
        self.assertFalse(self.writes())
    def test_new_intent_cannot_use_old_continue_draft_or_authorization(self):
        self.model.plan=plan(action='create',title=TITLE,start=START)
        first=self.turn('Agende Entrevista fictícia em 17/06/2030 às 15h')
        self.model.plan=plan(mode='continue',action='create',end=END)
        result=self.turn('Exclua a entrevista às 15h30',first)
        self.assertNotEqual(result.mutation_state,'confirmed')
        self.assertIsNone(result.authorization_action)
        self.assertFalse(self.writes())
    def test_model_error_is_safe_and_never_writes(self):
        self.model.error=ModelError('llm_rate_limited')
        result=self.turn('Agende uma entrevista')
        self.assertEqual(result.status,'failed')
        self.assertIn('IA está indisponível',result.reply)
        self.assertFalse(self.writes())
    def test_conflict_uses_safe_structured_data_and_does_not_claim_success(self):
        self.gateway.conflict=True
        self.model.plan=self.create_plan()
        result=self.turn('Agende Entrevista fictícia em 17/06/2030 das 15h às 15h30')
        self.assertEqual(result.status,'conflict')
        self.assertIn('conflita',result.reply)
        self.assertIn('Ocupado',result.reply)
        self.assertFalse(self.writes())
    def test_unknown_result_is_reported_without_retry(self):
        self.gateway.create_error=OSError('PRIVATE_PROVIDER_DIAGNOSTIC')
        self.model.plan=self.create_plan()
        result=self.turn('Agende Entrevista fictícia em 17/06/2030 das 15h às 15h30')
        self.assertEqual(result.mutation_state,'unknown')
        self.assertIn('não vou repetir',result.reply)
        self.assertEqual(len(self.writes()),1)
    def test_context_expiration_creates_fresh_session_without_old_authorization(self):
        self.model.plan=plan(action='create',title=TITLE)
        first=self.turn('Agende Entrevista fictícia')
        self.now+=timedelta(seconds=1801)
        self.model.plan=plan(mode='continue',action='create',start=START,end=END)
        result=self.turn('17/06/2030 das 15h às 15h30',first)
        self.assertNotEqual(result.conversation_id,first.conversation_id)
        self.assertIsNone(result.authorization_action)
        self.assertFalse(self.writes())
    def test_corrupt_or_unavailable_context_never_silently_creates_another(self):
        context=Mock()
        context.load.side_effect=ConversationError('conversation_unavailable')
        service=self.make_service()
        service._context=context
        from uuid import uuid4
        with self.assertRaises(ConversationError):
            service.process('oi',conversation_id=str(uuid4()))
        context.create.assert_not_called()
    def test_cancel_resets_pending_authorization(self):
        self.model.plan=plan(action='create',title=TITLE)
        first=self.turn('Agende Entrevista fictícia')
        self.model.plan=plan('cancel_request')
        result=self.turn('cancelar pedido',first)
        self.assertEqual(result.status,'cancelled')
        self.assertIsNone(result.authorization_action)
        self.assertFalse(self.writes())
    def test_direct_list_preparation_uses_read_tools_without_second_model_call(self):
        self.model.plan=plan(action='list',date='2030-06-17',period='day')
        result=self.turn('Agenda de 17/06/2030')
        self.assertEqual(result.action,'list')
        self.assertIn('Entrevista original',result.reply)
        self.assertEqual(len(self.model.calls),1)
        self.assertFalse(self.writes())
    def test_preview_too_long_is_revoked_before_any_possible_approval(self):
        title='A'*3400
        self.gateway.current=replace(self.gateway.current,title=title)
        self.model.plan=plan(action='delete',event_id=EID)
        first=self.turn('Exclua '+EID)
        self.assertEqual(first.status,'needs_correction')
        self.assertEqual(self.context.load(OWNER,first.conversation_id).memory.confirmation.state,'revoked')
        result=self.turn('sim',first)
        self.assertNotEqual(result.mutation_state,'confirmed')
        self.assertFalse(self.writes())
    def test_result_validator_rejects_oversize_reply_and_private_repr(self):
        from uuid import uuid4
        valid=AgentTurn('Tudo certo',str(uuid4()),None,'succeeded',None,'not_applied')
        self.assertIs(validate_agent_turn(valid),valid)
        self.assertNotIn(valid.conversation_id,repr(valid))
        for bad in (replace(valid,reply='x'*(MAX_REPLY_CHARS+1)),replace(valid,authorization_action='delete'),replace(valid,mutation_state='invented')):
            with self.assertRaises(ValueError): validate_agent_turn(bad)
    def test_server_gate_rejects_quoted_negative_and_hypothetical_commands(self):
        for text in ('"Agende uma entrevista"','Não agende uma entrevista','Como criar uma entrevista?','E se criar uma entrevista','Seria bom criar uma entrevista'):
            self.assertFalse(explicit_action(text,'create'))
        self.assertTrue(explicit_action('Quero agendar uma entrevista','create'))
        self.assertTrue(explicit_action('Por favor, reagende essa entrevista','reschedule'))

    def test_restrictions_anywhere_override_a_complete_imperative(self):
        tails=('mas não execute nada', 'só prepare os dados', 'apenas uma prévia',
            'somente simulação', 'como exemplo', 'se eu confirmar depois',
            'sem gravar na agenda', 'para demonstração', 'hipoteticamente')
        for tail in tails:
            with self.subTest(tail=tail):
                self.model.plan=self.create_plan()
                message='Agende Entrevista fictícia em 17/06/2030 das 15h às 15h30, '+tail
                self.assertFalse(explicit_action(message,'create'))
                result=self.turn(message)
                self.assertEqual(result.status,'needs_confirmation')
                self.assertIsNone(result.authorization_action)
                self.assertFalse(self.writes())

    def test_restricted_continuation_discards_earlier_write_authorization(self):
        self.model.plan=plan(action='create',title=TITLE,start=START)
        first=self.turn('Agende Entrevista fictícia em 17/06/2030 às 15h')
        self.assertEqual(first.authorization_action,'create')
        self.model.plan=plan(mode='continue',action='create',end=END)
        result=self.turn('Termina às 15h30, mas só prepare uma simulação',first)
        self.assertEqual(result.status,'needs_confirmation')
        self.assertIsNone(result.authorization_action)
        self.assertFalse(self.writes())

    def test_negation_in_title_conservatively_requires_preview(self):
        title='Não desista'
        self.model.plan=plan(action='create',title=title,start=START,end=END,time_zone=ZONE)
        result=self.turn('Agende Não desista em 17/06/2030 das 15h às 15h30')
        self.assertEqual(result.status,'needs_confirmation')
        self.assertFalse(self.writes())

    def test_model_ready_cannot_invent_fields_to_authorize_an_incomplete_request(self):
        self.model.plan=self.create_plan()
        result=self.turn('Agende uma entrevista')
        self.assertEqual(result.status,'needs_confirmation')
        self.assertIsNone(result.authorization_action)
        self.assertFalse(self.writes())

    def test_ungrounded_partial_field_does_not_become_trusted_on_continuation(self):
        self.model.plan=plan(action='create',title=TITLE,start=START)
        first=self.turn('Agende Entrevista fictícia')
        self.assertIsNone(first.authorization_action)
        self.model.plan=plan(mode='continue',action='create',end=END)
        result=self.turn('Termina às 15h30',first)
        self.assertEqual(result.status,'needs_confirmation')
        self.assertFalse(self.writes())

    def test_relative_date_without_local_anchor_requires_preview(self):
        self.model.plan=self.create_plan()
        result=self.turn('Agende Entrevista fictícia amanhã das 15h às 15h30')
        self.assertEqual(result.status,'needs_confirmation')
        self.assertFalse(self.writes())

    def test_explicit_duration_anchors_end_without_guessing(self):
        self.model.plan=self.create_plan()
        result=self.turn('Agende Entrevista fictícia em 17/06/2030 às 15h com duração de 30 minutos')
        self.assertEqual(result.mutation_state,'confirmed')
        self.assertEqual(len(self.writes()),1)

    def test_isolated_number_does_not_authorize_an_invented_finish_time(self):
        self.model.plan=plan(action='create',title=TITLE,start=START)
        first=self.turn('Agende Entrevista fictícia em 17/06/2030 às 15h')
        self.model.plan=plan(mode='continue',action='create',end=END)
        result=self.turn('15',first)
        self.assertEqual(result.status,'needs_clarification')
        pending=self.context.load(OWNER,result.conversation_id).memory.pending_request
        self.assertNotIn('end',pending.fields)
        self.assertIn('end',pending.awaiting)
        self.assertFalse(self.writes())

    def test_invalid_datetime_keeps_correction_instead_of_crashing_grounding(self):
        self.model.plan=plan(action='create',title=TITLE,start='invalid-date')
        result=self.turn('Agende Entrevista fictícia em data a definir')
        self.assertEqual(result.status,'needs_correction')
        self.assertIsNone(result.authorization_action)
        self.assertFalse(self.writes())

    def test_ungrounded_time_zone_requires_preview_even_with_valid_offsets(self):
        self.model.plan=plan(action='create',title=TITLE,start=START,end=END,time_zone='America/Fortaleza')
        result=self.turn('Agende Entrevista fictícia em 17/06/2030 das 15h às 15h30')
        self.assertEqual(result.status,'needs_confirmation')
        self.assertFalse(self.writes())

    def test_unique_read_selection_can_be_used_by_explicit_referential_edit(self):
        queried=self.turn('Consulte minha agenda')
        self.model.plan=plan(action='edit',use_selected_event=True,title='Título atualizado')
        result=self.turn('Edite esse evento para Título atualizado',queried)
        self.assertEqual(result.mutation_state,'confirmed')
        self.assertEqual(self.gateway.current.event_id,EID)
        self.assertEqual(self.gateway.current.title,'Título atualizado')

    def test_title_cannot_be_grounded_by_a_subword_inside_another_title(self):
        self.model.plan=plan(action='create',title='Ana',start=START,end=END,time_zone=ZONE)
        result=self.turn('Agende Banana em 17/06/2030 das 15h às 15h30')
        self.assertEqual(result.status,'needs_confirmation')
        self.assertFalse(self.writes())

    def test_continuation_cannot_ignore_a_literal_time_correction(self):
        self.model.plan=plan(action='create',start=START,end=END)
        first=self.turn('Agende uma entrevista em 17/06/2030 das 15h às 15h30')
        self.assertEqual(first.authorization_action,'create')
        self.model.plan=plan(mode='continue',action='create',title=TITLE)
        result=self.turn('Entrevista fictícia às 16h',first)
        self.assertEqual(result.status,'needs_confirmation')
        self.assertFalse(self.writes())

    def test_continuation_cannot_ignore_a_literal_date_correction(self):
        self.model.plan=plan(action='create',start=START,end=END)
        first=self.turn('Agende uma entrevista em 17/06/2030 das 15h às 15h30')
        self.model.plan=plan(mode='continue',action='create',title=TITLE)
        result=self.turn('Entrevista fictícia em 18/06/2030',first)
        self.assertEqual(result.status,'needs_confirmation')
        self.assertFalse(self.writes())

    def test_model_cannot_ignore_an_explicit_foreign_time_zone(self):
        self.model.plan=self.create_plan()
        result=self.turn('Agende Entrevista fictícia em 17/06/2030 das 15h às 15h30 Europe/Lisbon')
        self.assertEqual(result.status,'needs_confirmation')
        self.assertFalse(self.writes())

    def test_complete_iso_timestamps_are_anchored_without_treating_offsets_as_hours(self):
        self.model.plan=self.create_plan()
        result=self.turn('Agende Entrevista fictícia de '+START+' até '+END)
        self.assertEqual(result.mutation_state,'confirmed')
        self.assertEqual(len(self.writes()),1)

    def test_explicit_hour_duration_is_not_an_invented_clock_time(self):
        self.model.plan=plan(action='create',title=TITLE,start=START,end='2030-06-17T16:00:00-03:00',time_zone=ZONE)
        result=self.turn('Agende Entrevista fictícia em 17/06/2030 às 15h por 1 hora')
        self.assertEqual(result.mutation_state,'confirmed')
        self.assertEqual(len(self.writes()),1)

    def test_qual_query_can_read_the_unique_selected_event(self):
        first=self.turn('Consulte minha agenda')
        self.model.query_plan=QueryPlan('get_context_event',{'time_zone':ZONE},'fixture-query-model')
        result=self.turn('Qual é esse compromisso?',first)
        self.assertEqual(result.action,'get')
        self.assertIn('Entrevista original',result.reply)
        self.assertEqual([call[0] for call in self.model.calls],['query','query'])
        self.assertFalse(self.writes())

    def test_candidate_selection_presents_exact_preview_without_automatic_edit(self):
        second=replace(self.gateway.current,event_id='SecondFixture',start='2030-06-17T11:00:00-03:00',end='2030-06-17T11:30:00-03:00')
        self.gateway.pool=[second]
        self.model.plan=plan(action='edit',search_title='Entrevista original',date='2030-06-17',period='day',title='Título atualizado')
        first=self.turn('Edite Entrevista original em 17/06/2030 para Título atualizado')
        self.assertEqual(first.status,'needs_clarification')
        self.assertIn('1.',first.reply)
        self.assertIn('2.',first.reply)
        self.assertIsNone(first.authorization_action)
        self.model.plan=plan('select_candidate',choice=2)
        selected=self.turn('2',first)
        self.assertEqual(selected.status,'needs_confirmation')
        self.assertIn('11h',selected.reply)
        self.assertIn('Título atualizado',selected.reply)
        pending=self.context.load(OWNER,selected.conversation_id).memory.pending_request
        self.assertEqual(pending.fields['event_id'],'SecondFixture')
        self.assertFalse(self.writes())

    def test_failed_query_is_not_reported_as_empty_agenda(self):
        self.gateway.list_events=Mock(side_effect=OSError('PRIVATE_PROVIDER_DIAGNOSTIC'))
        result=self.turn('Consulte minha agenda')
        self.assertEqual(result.status,'failed')
        self.assertIn('falha de leitura',result.reply)
        self.assertNotIn('Não encontrei compromissos',result.reply)

    def test_large_query_has_bounded_reply_and_reports_omitted_events(self):
        self.gateway.pool=[replace(ORIGINAL,event_id='Fixture'+str(i),title='Entrevista '+str(i)) for i in range(12)]
        result=self.turn('Consulte minha agenda')
        self.assertEqual(result.status,'succeeded')
        self.assertIn('mais 3 compromissos',result.reply)
        self.assertLessEqual(len(result.reply),MAX_REPLY_CHARS)

if __name__=='__main__': unittest.main()
