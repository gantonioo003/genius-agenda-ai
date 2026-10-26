"""Regressões de conversas completas; stores/serviços reais, modelo/gateway locais.

Os planos são fixtures explícitas por turno, não previsões do provedor. Cada
trajetória verifica tanto a resposta quanto o contexto persistido e as escritas.
"""
from dataclasses import replace
from datetime import timedelta
import unittest

from app.agent_query_contract import QueryPlan
import test_whatsapp_agent as support


class AgentConversationPathsTests(unittest.TestCase):
    # Reutilizar apenas o harness; herdar WhatsAppAgentTests executaria novamente
    # seus testes unitários e inflaria a contagem desta regressão de trajetórias.
    setUp = support.WhatsAppAgentTests.setUp
    factory = support.WhatsAppAgentTests.factory
    make_service = support.WhatsAppAgentTests.make_service
    turn = support.WhatsAppAgentTests.turn
    writes = support.WhatsAppAgentTests.writes
    create_plan = support.WhatsAppAgentTests.create_plan

    def memory(self, turn):
        return self.context.load(support.OWNER, turn.conversation_id).memory

    def restart(self):
        """Reabrir SQLite e compor os serviços como outro processo do worker."""
        self.context = support.ConversationStore(self.root / 'context.sqlite3', clock=lambda: self.now)
        self.creation = support.AgentCreationStore(self.root / 'creation.sqlite3', clock=lambda: self.now)
        self.updates = support.AgentUpdateStore(self.root / 'updates.sqlite3', clock=lambda: self.now)
        self.deletions = support.AgentDeletionStore(self.root / 'delete.sqlite3', clock=lambda: self.now)
        self.service = self.make_service()

    def test_demonstrative_period_followups_use_query_context_instead_of_new_draft(self):
        previous=self.turn('Consulte minha agenda em 17/06/2030')
        for message,period in (('E nesta semana?','week'),('E nessa semana?','week'),
                               ('E neste mês?','month'),('E nesse mês?','month')):
            with self.subTest(message=message):
                self.model.query_plan=QueryPlan('list_events',{'date':'2030-06-17','period':period,'time_zone':support.ZONE},'fixture-query-model')
                current=self.turn(message,previous)
                self.assertEqual(current.status,'succeeded')
                self.assertEqual(current.conversation_id,previous.conversation_id)
                self.assertEqual(self.memory(current).last_list['period'],period)
                self.assertEqual(self.model.calls[-1][0],'query')
                self.assertFalse(self.writes())
                previous=current

    def test_missing_finish_invalid_interval_correction_and_fresh_readback(self):
        self.model.plan = support.plan(action='create', title=support.TITLE, start=support.START)
        first = self.turn('Agende Entrevista fictícia em 17/06/2030 às 15h')
        self.assertEqual((first.status, first.authorization_action), ('needs_clarification', 'create'))
        self.assertIn('end', self.memory(first).pending_request.awaiting)

        self.restart()
        self.model.plan = support.plan(mode='continue', action='create', end='2030-06-17T14:30:00-03:00')
        invalid = self.turn('Termina às 14h30', first)
        self.assertEqual(invalid.status, 'needs_correction')
        self.assertEqual(self.memory(invalid).pending_request.fields['title'], support.TITLE)
        self.assertFalse(self.writes())

        self.model.plan = support.plan(mode='continue', action='create', start='2030-06-17T16:00:00-03:00', end='2030-06-17T16:30:00-03:00')
        corrected = self.turn('Corrigindo: das 16h às 16h30', invalid)
        self.assertEqual((corrected.status, corrected.mutation_state), ('succeeded', 'confirmed'))
        self.assertEqual(self.gateway.current.start, '2030-06-17T16:00:00-03:00')
        self.assertEqual(self.gateway.current.end, '2030-06-17T16:30:00-03:00')
        self.assertIsNone(self.memory(corrected).pending_request)
        self.assertEqual(len(self.writes()), 1)

        self.gateway.current = replace(self.gateway.current, title='Título alterado fora da conversa')
        self.model.query_plan = QueryPlan('get_context_event', {'time_zone': support.ZONE}, 'fixture-query-model')
        readback = self.turn('Qual é esse compromisso?', corrected)
        self.assertEqual(readback.action, 'get')
        self.assertIn('Título alterado fora da conversa', readback.reply)
        self.assertEqual(len(self.writes()), 1)

    def test_corrected_date_discards_old_hours_before_completion(self):
        self.model.plan = support.plan(action='create', title=support.TITLE, start=support.START)
        first = self.turn('Agende Entrevista fictícia em 17/06/2030 às 15h')
        self.model.plan = support.plan(mode='continue', action='create', date='2030-06-18')
        corrected = self.turn('Corrigindo a data: 18/06/2030', first)
        pending = self.memory(corrected).pending_request
        self.assertEqual(corrected.status, 'needs_clarification')
        self.assertEqual(pending.fields['date'], '2030-06-18')
        self.assertNotIn('start', pending.fields)
        self.assertNotIn('end', pending.fields)
        self.assertFalse(self.writes())

        self.restart()
        self.model.plan = support.plan(mode='continue', action='create', start='2030-06-18T16:00:00-03:00', end='2030-06-18T16:30:00-03:00')
        complete = self.turn('Das 16h às 16h30', corrected)
        self.assertEqual(complete.mutation_state, 'confirmed')
        self.assertIn('18/06/2030, 16h', complete.reply)
        self.assertEqual(len(self.writes()), 1)

    def test_cancelled_draft_cannot_be_resurrected_by_late_fragment(self):
        self.model.plan = support.plan(action='create', title=support.TITLE, start=support.START)
        first = self.turn('Agende Entrevista fictícia em 17/06/2030 às 15h')
        self.model.plan = support.plan('cancel_request')
        cancelled = self.turn('Cancelar pedido', first)
        self.assertEqual(cancelled.status, 'cancelled')
        self.assertIsNone(self.memory(cancelled).pending_request)
        self.assertIsNone(cancelled.authorization_action)

        self.restart()
        self.model.plan = support.plan(mode='continue', action='create', end=support.END)
        late = self.turn('Termina às 15h30', cancelled)
        self.assertEqual(late.status, 'needs_clarification')
        self.assertIsNone(late.authorization_action)
        self.assertFalse(self.writes())

        self.model.plan = self.create_plan()
        fresh = self.turn('Agende Entrevista fictícia em 17/06/2030 das 15h às 15h30', late)
        self.assertEqual(fresh.mutation_state, 'confirmed')
        self.assertEqual(len(self.writes()), 1)

    def test_model_failure_preserves_draft_but_recovery_requires_confirmation(self):
        self.model.plan = support.plan(action='create', title=support.TITLE, start=support.START)
        first = self.turn('Agende Entrevista fictícia em 17/06/2030 às 15h')
        before = self.memory(first)
        self.model.error = support.ModelError('llm_rate_limited')
        unavailable = self.turn('Termina às 15h30', first)
        self.assertEqual(unavailable.status, 'failed')
        self.assertIsNone(unavailable.authorization_action)
        self.assertEqual(self.memory(unavailable), before)
        self.assertFalse(self.writes())

        self.restart()
        self.model.error = None
        self.model.plan = support.plan(mode='continue', action='create', end=support.END)
        recovered = self.turn('Termina às 15h30', unavailable)
        self.assertEqual(recovered.status, 'needs_confirmation')
        self.assertIn(support.TITLE, recovered.reply)
        self.assertFalse(self.writes())
        approved = self.turn('sim', recovered)
        self.assertEqual(approved.mutation_state, 'confirmed')
        self.assertEqual(len(self.writes()), 1)

    def test_ambiguous_candidates_reject_inferred_choice_then_preview_and_edit_exact_target(self):
        other = replace(support.ORIGINAL, event_id='SecondFixture', start='2030-06-17T11:00:00-03:00', end='2030-06-17T11:30:00-03:00')
        self.gateway.pool = [other]
        self.model.plan = support.plan(action='edit', search_title='Entrevista original', date='2030-06-17', period='day', title='Título atualizado')
        first = self.turn('Edite Entrevista original em 17/06/2030 para Título atualizado')
        self.assertEqual(first.status, 'needs_clarification')
        self.assertEqual(len(self.memory(first).pending_request.candidates), 2)
        self.assertIsNone(first.authorization_action)

        self.model.plan = support.plan('select_candidate', choice=1)
        ambiguous = self.turn('às 10h', first)
        self.assertEqual(ambiguous.status, 'needs_clarification')
        self.assertEqual(len(self.memory(ambiguous).pending_request.candidates), 2)
        self.assertFalse(self.writes())

        self.restart()
        selected = self.turn('a primeira opção', ambiguous)
        self.assertEqual(selected.status, 'needs_confirmation')
        self.assertIn('10h', selected.reply)
        self.assertEqual(self.memory(selected).confirmation.event_id, support.EID)
        self.assertFalse(self.writes())

        model_calls = len(self.model.calls)
        approved = self.turn('confirmo', selected)
        replay = self.turn('sim', approved)
        self.assertEqual(approved.mutation_state, 'confirmed')
        self.assertEqual(replay.status, 'consumed')
        self.assertEqual(self.gateway.current.title, 'Título atualizado')
        self.assertEqual(self.gateway.pool, [other])
        self.assertEqual(self.writes(), [('patch', support.EID)])
        self.assertEqual(len(self.model.calls), model_calls)

    def test_candidate_changed_after_presentation_requires_selection_again(self):
        self.gateway.pool = [replace(support.ORIGINAL, event_id='SecondFixture')]
        self.model.plan = support.plan(action='delete', search_title='Entrevista original', date='2030-06-17', period='day')
        first = self.turn('Exclua Entrevista original em 17/06/2030')
        self.gateway.current = replace(self.gateway.current, title='Evento alterado após a busca')
        self.gateway.version = 'fixture-version-changed'
        self.model.plan = support.plan('select_candidate', choice=1)
        changed = self.turn('1', first)
        self.assertEqual(changed.status, 'needs_clarification')
        self.assertIn('mudou', changed.reply)
        self.assertIsNone(self.memory(changed).confirmation)
        self.assertEqual(len(self.memory(changed).pending_request.candidates), 1)
        self.assertFalse(self.writes())

        selected = self.turn('1', changed)
        self.assertEqual(selected.status, 'needs_confirmation')
        self.assertIn('Evento alterado após a busca', selected.reply)
        rejected = self.turn('não', selected)
        self.assertEqual(rejected.status, 'revoked')
        self.assertFalse(self.writes())

    def test_conflict_alternative_requires_preview_then_writes_only_new_interval(self):
        original = self.gateway.current
        self.gateway.conflict = True
        self.model.plan = self.create_plan()
        conflict = self.turn('Agende Entrevista fictícia em 17/06/2030 das 15h às 15h30')
        self.assertEqual((conflict.status, conflict.mutation_state), ('conflict', 'not_applied'))
        self.assertIn('Ocupado', conflict.reply)
        self.assertEqual(self.gateway.current, original)
        self.assertFalse(self.writes())

        self.gateway.conflict = False
        self.restart()
        self.model.plan = support.plan(mode='continue', action='create', start='2030-06-17T16:00:00-03:00', end='2030-06-17T16:30:00-03:00')
        suggestion = self.turn('E se fosse das 16h às 16h30?', conflict)
        self.assertEqual(suggestion.status, 'needs_confirmation')
        self.assertIsNone(suggestion.authorization_action)
        self.assertEqual(self.memory(suggestion).confirmation.parameters['start'], '2030-06-17T16:00:00-03:00')
        self.assertFalse(self.writes())

        approved = self.turn('sim', suggestion)
        self.assertEqual(approved.mutation_state, 'confirmed')
        self.assertEqual(self.gateway.current.start, '2030-06-17T16:00:00-03:00')
        self.assertEqual(len(self.writes()), 1)

    def test_revoked_proposal_rejects_late_approval_then_new_request_gets_new_proposal(self):
        self.model.plan = support.plan(action='delete', event_id=support.EID)
        first = self.turn('Exclua ' + support.EID)
        first_id = self.memory(first).confirmation.proposal_id
        rejected = self.turn('não', first)
        self.restart()
        late = self.turn('confirmo', rejected)
        self.assertEqual(rejected.status, 'revoked')
        self.assertEqual(self.memory(late).confirmation.state, 'revoked')
        self.assertNotEqual(late.mutation_state, 'confirmed')
        self.assertFalse(self.writes())

        fresh = self.turn('Exclua ' + support.EID, late)
        self.assertEqual(fresh.status, 'needs_confirmation')
        self.assertNotEqual(self.memory(fresh).confirmation.proposal_id, first_id)
        approved = self.turn('confirmo', fresh)
        replay = self.turn('confirmo', approved)
        self.assertEqual(approved.mutation_state, 'confirmed')
        self.assertEqual(replay.status, 'consumed')
        self.assertEqual(self.writes(), [('delete', support.EID)])

    def test_expired_proposal_stays_expired_until_new_preview(self):
        self.model.plan = support.plan(action='reschedule', event_id=support.EID, start=support.START, end=support.END)
        first = self.turn('Como ficaria reagendar ' + support.EID + ' em 17/06/2030 das 15h às 15h30?')
        first_id = self.memory(first).confirmation.proposal_id
        self.now += timedelta(seconds=300)
        expired = self.turn('sim', first)
        self.restart()
        repeated = self.turn('confirmo', expired)
        self.assertEqual((expired.status, repeated.status), ('expired', 'expired'))
        self.assertFalse(self.writes())

        fresh = self.turn('Como ficaria reagendar ' + support.EID + ' em 17/06/2030 das 15h às 15h30?', repeated)
        self.assertEqual(fresh.status, 'needs_confirmation')
        self.assertNotEqual(self.memory(fresh).confirmation.proposal_id, first_id)
        approved = self.turn('sim', fresh)
        self.assertEqual(approved.mutation_state, 'confirmed')
        self.assertEqual(self.writes(), [('patch', support.EID)])

    def test_query_period_continuity_survives_failed_read_and_clears_ambiguous_selection(self):
        first = self.turn('Consulte minha agenda em 17/06/2030')
        self.assertEqual(self.memory(first).selected_event_id, support.EID)
        self.restart()
        self.model.query_plan = QueryPlan('list_events', {'date': '2030-06-24', 'period': 'week', 'time_zone': support.ZONE}, 'fixture-query-model')
        original_list = self.gateway.list_events

        def unavailable(_period):
            raise OSError('PRIVATE_PROVIDER_DIAGNOSTIC')

        self.gateway.list_events = unavailable
        failed = self.turn('E na semana seguinte?', first)
        self.assertEqual(failed.status, 'failed')
        self.assertNotIn('Não encontrei compromissos', failed.reply)
        self.assertEqual(self.memory(failed).last_list, self.memory(first).last_list)
        self.assertEqual(self.memory(failed).selected_event_id, support.EID)

        self.gateway.list_events = original_list
        self.gateway.current = replace(support.ORIGINAL, start='2030-06-24T10:00:00-03:00', end='2030-06-24T10:30:00-03:00')
        self.gateway.pool = [replace(self.gateway.current, event_id='SecondFixture')]
        queried = self.turn('E na semana seguinte?', failed)
        self.assertEqual(queried.status, 'succeeded')
        self.assertEqual(self.memory(queried).last_list['date'], '2030-06-24')
        self.assertIsNone(self.memory(queried).selected_event_id)
        self.model.query_plan = QueryPlan('get_context_event', {'time_zone': support.ZONE}, 'fixture-query-model')
        ambiguous = self.turn('Qual é esse compromisso?', queried)
        self.assertEqual(ambiguous.status, 'needs_clarification')
        self.assertFalse(self.writes())
        self.assertIn('"date":"2030-06-17"', self.model.calls[1][1]['instructions'])
        self.assertIn('"date":"2030-06-17"', self.model.calls[2][1]['instructions'])
        self.assertIn('"date":"2030-06-24"', self.model.calls[3][1]['instructions'])
        self.assertIn('"has_selected_event":false', self.model.calls[3][1]['instructions'])
        for _kind, request in self.model.calls:
            self.assertNotIn(support.EID, request['instructions'])
            self.assertNotIn('Entrevista original', request['instructions'])
            self.assertNotIn(first.conversation_id, request['instructions'])

    def test_removed_selected_event_drops_reference_before_followup_edit(self):
        first = self.turn('Consulte minha agenda')
        self.gateway.current = None
        self.model.query_plan = QueryPlan('get_context_event', {'time_zone': support.ZONE}, 'fixture-query-model')
        missing = self.turn('Qual é esse compromisso?', first)
        self.assertEqual(missing.status, 'not_found')
        self.assertIsNone(self.memory(missing).selected_event_id)

        self.restart()
        self.model.plan = support.plan(action='edit', use_selected_event=True, title='Título atualizado')
        edit = self.turn('Edite esse evento para Título atualizado', missing)
        self.assertEqual(edit.status, 'needs_clarification')
        self.assertIsNone(self.memory(edit).confirmation)
        self.assertFalse(self.writes())

    def test_lost_create_response_reconciles_on_repeated_full_command_without_second_write(self):
        create = self.gateway.create_event

        def create_then_lose_response(draft, **values):
            create(draft, **values)
            raise OSError('PRIVATE_PROVIDER_DIAGNOSTIC')

        self.gateway.create_event = create_then_lose_response
        self.model.plan = self.create_plan()
        command = 'Agende Entrevista fictícia em 17/06/2030 das 15h às 15h30'
        unknown = self.turn(command)
        self.assertEqual((unknown.status, unknown.mutation_state), ('failed', 'unknown'))
        self.assertIn('não vou repetir', unknown.reply)
        self.assertEqual(len(self.writes()), 1)

        self.restart()
        recovered = self.turn(command, unknown)
        self.assertEqual((recovered.status, recovered.mutation_state), ('succeeded', 'confirmed'))
        self.assertEqual(len(self.writes()), 1)
        self.assertTrue(any(call[0] == 'reconcile_create' for call in self.gateway.calls))
        self.assertIsNone(self.memory(recovered).pending_request)


if __name__ == '__main__':
    unittest.main()
