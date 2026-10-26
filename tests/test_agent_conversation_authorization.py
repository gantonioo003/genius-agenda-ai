"""Regressões conversacionais locais; SQLite real e modelo/agenda fictícios."""
from datetime import timedelta
import unittest
from unittest.mock import Mock
from uuid import uuid4

import test_whatsapp_agent as fixtures
from app.conversation_store import ConversationError
from app.agent_query_contract import QueryPlan
from app.dialogue_state import PendingRequest
from app.services.whatsapp_agent import _unstated_new_end
from test_agent_updates import OWNER


class AgentConversationAuthorizationTests(unittest.TestCase):
    def fixture(self):
        case=fixtures.WhatsAppAgentTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def test_wait_for_review_never_writes_before_exact_approval(self):
        for tail in ('aguarde minha confirmação','espere minha aprovação',
                     'antes de confirmar quero conferir','quando eu aprovar',
                     'depois eu vou revisar'):
            with self.subTest(tail=tail):
                case=self.fixture()
                case.model.plan=case.create_plan()
                text='Agende Entrevista fictícia em 17/06/2030 das 15h às 15h30, '+tail
                pending=case.turn(text)
                self.assertEqual(pending.status,'needs_confirmation')
                self.assertIsNone(pending.authorization_action)
                self.assertFalse(case.writes())
                complete=case.turn('sim',pending)
                self.assertEqual(complete.mutation_state,'confirmed')
                self.assertEqual(len(case.writes()),1)

    def test_second_mutation_asks_for_one_request_even_if_model_selects_first(self):
        for tail in ('e apague a outra entrevista','e crie Outro compromisso',
                     '; depois reagende a reunião'):
            with self.subTest(tail=tail):
                case=self.fixture()
                case.model.plan=case.create_plan()
                result=case.turn('Agende Entrevista fictícia em 17/06/2030 das 15h às 15h30 '+tail)
                self.assertEqual(result.status,'needs_clarification')
                self.assertIn('um pedido por vez',result.reply)
                self.assertIsNone(result.authorization_action)
                self.assertFalse(case.model.calls)
                self.assertFalse(case.writes())

    def test_multiple_mutations_discard_a_prior_confirmation(self):
        case=self.fixture()
        case.model.plan=fixtures.plan(action='delete',event_id=fixtures.EID)
        first=case.turn('Exclua '+fixtures.EID)
        result=case.turn('Crie uma entrevista e apague a outra',first)
        memory=case.context.load(OWNER,result.conversation_id).memory
        self.assertEqual(result.status,'needs_clarification')
        self.assertIsNone(memory.confirmation)
        self.assertIsNone(memory.pending_request)
        self.assertFalse(case.writes())

    def test_model_cannot_select_a_smaller_id_inside_an_opaque_token(self):
        for token in ('X'+fixtures.EID+'Y',fixtures.EID+'_suffix',
                      fixtures.EID+'.suffix','prefix/'+fixtures.EID):
            with self.subTest(token=token):
                case=self.fixture()
                case.model.plan=fixtures.plan(action='edit',event_id=fixtures.EID,title='Título atualizado')
                result=case.turn('Edite '+token+' para Título atualizado')
                self.assertEqual(result.status,'needs_clarification')
                self.assertIn('identificador completo e exato',result.reply)
                memory=case.context.load(OWNER,result.conversation_id).memory
                self.assertIsNone(memory.pending_request)
                self.assertIsNone(memory.selected_event_id)
                self.assertIsNone(memory.confirmation)
                self.assertFalse(case.writes())

    def test_composed_text_fields_are_previewed_with_the_actual_proposed_roles(self):
        case=self.fixture()
        case.model.plan=fixtures.plan(action='edit',event_id=fixtures.EID,title='Beta',description='Alfa')
        result=case.turn('Edite '+fixtures.EID+' para título Alfa e descrição Beta')
        self.assertEqual(result.status,'needs_confirmation')
        self.assertIn('Título: Beta',result.reply)
        self.assertIn('Descrição: Alfa',result.reply)
        self.assertIsNone(result.authorization_action)
        self.assertFalse(case.writes())

    def test_query_cannot_turn_a_smaller_id_into_a_trusted_edit_selection(self):
        case=self.fixture()
        case.model.query_plan=QueryPlan('get_event',{'event_id':fixtures.EID,'time_zone':fixtures.ZONE},'fixture-query-model')
        first=case.turn('Qual é o compromisso X'+fixtures.EID+'Y')
        self.assertEqual(first.status,'needs_clarification')
        memory=case.context.load(OWNER,first.conversation_id).memory
        self.assertIsNone(memory.selected_event_id)
        case.model.plan=fixtures.plan(action='edit',use_selected_event=True,title='Título atualizado')
        final=case.turn('Edite esse evento para Título atualizado',first)
        self.assertEqual(final.status,'needs_clarification')
        self.assertFalse(case.writes())

    def test_model_cannot_silently_omit_one_of_two_text_roles(self):
        case=self.fixture()
        case.model.plan=fixtures.plan(action='edit',event_id=fixtures.EID,title='Beta')
        result=case.turn('Edite '+fixtures.EID+' para título Alfa e descrição Beta')
        self.assertEqual(result.status,'needs_confirmation')
        self.assertFalse(case.writes())

    def test_text_for_one_role_cannot_automatically_write_another(self):
        case=self.fixture()
        case.model.plan=fixtures.plan(action='edit',event_id=fixtures.EID,title='Beta')
        result=case.turn('Edite '+fixtures.EID+' descrição Beta')
        self.assertEqual(result.status,'needs_confirmation')
        self.assertFalse(case.writes())

    def test_ignored_title_correction_breaks_inherited_authorization(self):
        case=self.fixture()
        case.model.plan=fixtures.plan(action='create',title=fixtures.TITLE,start=fixtures.START)
        first=case.turn('Agende Entrevista fictícia em 17/06/2030 às 15h')
        self.assertEqual(first.authorization_action,'create')
        case.model.plan=fixtures.plan(mode='continue',action='create',end=fixtures.END)
        result=case.turn('Termina às 15h30 e título Nova entrevista',first)
        self.assertEqual(result.status,'needs_confirmation')
        self.assertIsNone(result.authorization_action)
        self.assertIn(fixtures.TITLE,result.reply)
        self.assertFalse(case.writes())

    def test_ignored_description_or_location_correction_breaks_authorization(self):
        for correction in ('descrição Nova pauta','local Sala B'):
            with self.subTest(correction=correction):
                case=self.fixture()
                case.model.plan=fixtures.plan(action='create',title=fixtures.TITLE,start=fixtures.START)
                first=case.turn('Agende Entrevista fictícia em 17/06/2030 às 15h')
                case.model.plan=fixtures.plan(mode='continue',action='create',end=fixtures.END)
                result=case.turn('Termina às 15h30 e '+correction,first)
                self.assertEqual(result.status,'needs_confirmation')
                self.assertIsNone(result.authorization_action)
                self.assertFalse(case.writes())

    def test_correct_single_labeled_field_still_executes(self):
        case=self.fixture()
        case.model.plan=fixtures.plan(action='edit',event_id=fixtures.EID,location='Sala B')
        result=case.turn('Edite '+fixtures.EID+' local para Sala B')
        self.assertEqual(result.mutation_state,'confirmed')
        self.assertEqual(case.gateway.current.location,'Sala B')
        self.assertEqual(len(case.writes()),1)

    def test_explicit_single_field_clear_still_executes(self):
        case=self.fixture()
        case.model.plan=fixtures.plan(action='edit',event_id=fixtures.EID,description='')
        result=case.turn('Edite '+fixtures.EID+' para limpar descrição')
        self.assertEqual(result.mutation_state,'confirmed')
        self.assertEqual(case.gateway.current.description,'')
        self.assertEqual(len(case.writes()),1)

    def test_rejected_delivery_discards_confirmation_locally_with_revision_guard(self):
        case=self.fixture()
        case.model.plan=fixtures.plan(action='delete',event_id=fixtures.EID)
        first=case.turn('Exclua '+fixtures.EID)
        before=case.context.load(OWNER,first.conversation_id)
        self.assertIsNotNone(before.memory.confirmation)
        model_calls=len(case.model.calls)
        gateway_calls=len(case.gateway.calls)
        self.assertIsNone(case.service.discard_unseen_confirmation(first.conversation_id))
        after=case.context.load(OWNER,first.conversation_id)
        self.assertIsNone(after.memory.confirmation)
        self.assertEqual(after.revision,before.revision+1)
        self.assertEqual(after.memory.pending_request,before.memory.pending_request)
        self.assertEqual(len(case.model.calls),model_calls)
        self.assertEqual(len(case.gateway.calls),gateway_calls)
        case.model.plan=fixtures.plan('ask_dialogue',reason='unclear_answer',field='request')
        result=case.turn('sim',first)
        self.assertNotEqual(result.mutation_state,'confirmed')
        self.assertFalse(case.writes())

    def test_discard_does_not_create_a_missing_or_expired_conversation(self):
        case=self.fixture()
        case.context.create=Mock(wraps=case.context.create)
        self.assertIsNone(case.service.discard_unseen_confirmation(str(uuid4())))
        case.context.create.assert_not_called()
        self.assertFalse(case.model.calls)
        self.assertFalse(case.gateway.calls)
        case.model.plan=fixtures.plan(action='delete',event_id=fixtures.EID)
        first=case.turn('Exclua '+fixtures.EID)
        case.context.create.reset_mock()
        model_calls=len(case.model.calls)
        gateway_calls=len(case.gateway.calls)
        case.now+=timedelta(seconds=1801)
        self.assertIsNone(case.service.discard_unseen_confirmation(first.conversation_id))
        case.context.create.assert_not_called()
        self.assertEqual(len(case.model.calls),model_calls)
        self.assertEqual(len(case.gateway.calls),gateway_calls)

    def test_discard_propagates_unavailable_context_and_failed_cas(self):
        case=self.fixture()
        case.model.plan=fixtures.plan(action='delete',event_id=fixtures.EID)
        first=case.turn('Exclua '+fixtures.EID)
        before=case.context.load(OWNER,first.conversation_id)
        case.context.save=Mock(side_effect=ConversationError('conversation_unavailable'))
        with self.assertRaises(ConversationError):
            case.service.discard_unseen_confirmation(first.conversation_id)
        self.assertIsNotNone(case.context.load(OWNER,first.conversation_id).memory.confirmation)
        self.assertEqual(case.context.save.call_args.args[2],before.revision)
        case.context.load=Mock(side_effect=ConversationError('conversation_unavailable'))
        with self.assertRaises(ConversationError):
            case.service.discard_unseen_confirmation(first.conversation_id)

    def test_invented_finish_in_continuation_is_removed_and_asked_without_another_model_call(self):
        case=self.fixture()
        title='Entrevista fictícia Gemini'
        case.model.plan=fixtures.plan(action='create',title=title)
        first=case.turn('Agende '+title)
        case.model.plan=fixtures.plan(mode='continue',action='create',start=fixtures.START,end=fixtures.END)
        second=case.turn('Em 17/06/2030 às 15h',first)
        memory=case.context.load(OWNER,second.conversation_id).memory
        self.assertEqual(second.status,'needs_clarification')
        self.assertIn('término ou a duração',second.reply)
        self.assertEqual(memory.pending_request.fields['title'],title)
        self.assertEqual(memory.pending_request.fields['start'],fixtures.START)
        self.assertNotIn('end',memory.pending_request.fields)
        self.assertNotIn('date',memory.pending_request.fields)
        self.assertIn('end',memory.pending_request.awaiting)
        self.assertIsNone(memory.confirmation)
        self.assertEqual(len(case.model.calls),2)
        self.assertFalse(case.writes())
        case.model.plan=fixtures.plan(mode='continue',action='create',end=fixtures.END)
        complete=case.turn('Por 30 minutos',second)
        self.assertEqual(complete.mutation_state,'confirmed')
        self.assertEqual(case.gateway.current.title,title)
        self.assertEqual(len(case.writes()),1)

    def test_explicit_start_with_invented_finish_in_first_turn_asks_for_finish(self):
        case=self.fixture()
        case.model.plan=case.create_plan()
        result=case.turn('Agende Entrevista fictícia em 17/06/2030 às 15h')
        memory=case.context.load(OWNER,result.conversation_id).memory
        self.assertEqual(result.status,'needs_clarification')
        self.assertNotIn('end',memory.pending_request.fields)
        self.assertEqual(result.authorization_action,'create')
        self.assertFalse(case.writes())

    def test_reschedule_continuation_cannot_estimate_finish(self):
        case=self.fixture()
        case.model.plan=fixtures.plan(action='reschedule',event_id=fixtures.EID)
        first=case.turn('Reagende '+fixtures.EID)
        case.model.plan=fixtures.plan(mode='continue',action='reschedule',start=fixtures.START,end=fixtures.END)
        second=case.turn('Em 17/06/2030 às 15h',first)
        memory=case.context.load(OWNER,second.conversation_id).memory
        self.assertEqual(second.status,'needs_clarification')
        self.assertEqual(memory.pending_request.fields['event_id'],fixtures.EID)
        self.assertNotIn('end',memory.pending_request.fields)
        self.assertFalse(case.writes())
        case.model.plan=fixtures.plan(mode='continue',action='reschedule',end=fixtures.END)
        complete=case.turn('Termina às 15h30',second)
        self.assertEqual(complete.status,'needs_confirmation')
        self.assertFalse(case.writes())
        complete=case.turn('sim',complete)
        self.assertEqual(complete.mutation_state,'confirmed')
        self.assertEqual(len(case.writes()),1)

    def test_previously_explicit_finish_survives_start_only_continuation(self):
        case=self.fixture()
        case.model.plan=fixtures.plan(action='create',title=fixtures.TITLE,end=fixtures.END)
        first=case.turn('Agende Entrevista fictícia em 17/06/2030, termina às 15h30')
        case.model.plan=fixtures.plan(mode='continue',action='create',start=fixtures.START)
        complete=case.turn('Começa às 15h',first)
        self.assertEqual(complete.mutation_state,'confirmed')
        self.assertEqual(case.gateway.current.end,fixtures.END)
        self.assertEqual(len(case.writes()),1)

    def test_relative_explicit_finish_keeps_conservative_preview(self):
        case=self.fixture()
        case.model.plan=fixtures.plan(action='create',title=fixtures.TITLE)
        first=case.turn('Agende Entrevista fictícia')
        case.model.plan=fixtures.plan(mode='continue',action='create',start=fixtures.START,end=fixtures.END)
        result=case.turn('Amanhã das 15h às 15h30',first)
        memory=case.context.load(OWNER,result.conversation_id).memory
        self.assertEqual(result.status,'needs_confirmation')
        self.assertEqual(memory.pending_request.fields['end'],fixtures.END)
        self.assertFalse(case.writes())

    def test_finish_literal_is_compared_in_the_requested_time_zone(self):
        before=PendingRequest({'action':'create','title':fixtures.TITLE})
        pending=PendingRequest({'action':'create','title':fixtures.TITLE,'start':fixtures.START,
                                'end':'2030-06-17T18:30:00+00:00','time_zone':fixtures.ZONE})
        self.assertFalse(_unstated_new_end('Em 17/06/2030 das 15h às 15h30',before,pending))
        self.assertFalse(_unstated_new_end('Em 17/06/2030 às 15h por 30 minutos',before,pending))

    def test_start_clock_alone_does_not_anchor_finish_on_an_invented_next_day(self):
        before=PendingRequest({'action':'create','title':fixtures.TITLE})
        pending=PendingRequest({'action':'create','title':fixtures.TITLE,'start':fixtures.START,
                                'end':'2030-06-18T15:00:00-03:00','time_zone':fixtures.ZONE})
        self.assertTrue(_unstated_new_end('Em 17/06/2030 às 15h',before,pending))


if __name__=='__main__':
    unittest.main()
