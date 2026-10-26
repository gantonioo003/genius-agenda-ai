"""Orquestração de leituras novas com CAS e confirmações existentes reais."""

from dataclasses import replace
from unittest.mock import Mock, patch
import unittest

from app.conversation_store import ConversationError
from app.services.preparation_chat import PreparationChatResult
from app.services.whatsapp_agent import validate_agent_turn
import test_whatsapp_agent as fixtures


class WhatsAppPreparationTests(unittest.TestCase):
    def setUp(self):
        self.preparation = Mock()
        self.preparation.respond.return_value = PreparationChatResult('Resumo factual da semana.', 'succeeded', 'summarize_agenda')
        fixtures.WhatsAppAgentTests.setUp(self)

    def make_service(self):
        service = fixtures.WhatsAppAgentTests.make_service(self)
        service._preparation = lambda: self.preparation
        return service

    turn = fixtures.WhatsAppAgentTests.turn
    factory = fixtures.WhatsAppAgentTests.factory
    writes = fixtures.WhatsAppAgentTests.writes
    create_plan = fixtures.WhatsAppAgentTests.create_plan

    def preview(self):
        self.model.plan = self.create_plan()
        result = self.turn('Pode agendar Entrevista fictícia em 17/06/2030 das 15h às 15h30?')
        self.assertEqual(result.status, 'needs_confirmation')
        return result

    def test_summary_routes_before_general_model_and_never_grants_write_authorization(self):
        result = self.turn('Resume minha semana')
        self.assertEqual(result.action, 'summarize_agenda')
        self.assertEqual(result.reply, 'Resumo factual da semana.')
        self.assertEqual(result.mutation_state, 'not_applicable')
        self.assertIsNone(result.authorization_action)
        self.assertFalse(self.model.calls)
        self.assertFalse(self.writes())
        self.preparation.respond.assert_called_once()

    def test_three_read_actions_use_the_same_turn_validation_and_transport_contract(self):
        for text, action in [('Resumo do mês', 'summarize_agenda'),
                             ('Me prepara para essa entrevista', 'prepare_briefing'),
                             ('Horários livres amanhã das 09:00 às 12:00 por 30 minutos', 'find_free_slots')]:
            with self.subTest(action=action):
                self.preparation.respond.return_value = PreparationChatResult('Leitura conferida.', 'succeeded', action)
                result = self.turn(text)
                self.assertEqual(result.action, action)
                self.assertIs(validate_agent_turn(result), result)
                self.assertIsNone(result.authorization_action)
        self.assertFalse(self.writes())

    def test_pending_confirmation_is_revoked_persistently_before_preparation(self):
        previous = self.preview()
        before = self.context.load(fixtures.OWNER, previous.conversation_id)
        self.assertEqual(before.memory.confirmation.state, 'pending')
        def inspect(_message, *, memory):
            stored = self.context.load(fixtures.OWNER, previous.conversation_id)
            self.assertEqual(stored.memory, memory)
            self.assertEqual(memory.confirmation.state, 'revoked')
            self.assertEqual(memory.pending_request, before.memory.pending_request)
            return PreparationChatResult('Resumo factual.', 'succeeded', 'summarize_agenda')
        self.preparation.respond.side_effect = inspect
        result = self.turn('Resume minha semana', previous)
        self.assertIn('A prévia anterior perdeu validade', result.reply)
        self.assertNotIn('evento foi cancelado', result.reply)
        self.assertIsNone(result.authorization_action)
        self.assertFalse(self.writes())
        self.preparation.respond.side_effect = None
        answer = self.turn('sim', result)
        self.assertNotEqual(answer.mutation_state, 'confirmed')
        self.assertFalse(self.writes())

    def test_approved_but_unconsumed_confirmation_is_also_revoked(self):
        previous = self.preview()
        snapshot = self.context.load(fixtures.OWNER, previous.conversation_id)
        approved, memory = self.service._confirm().approve(snapshot.memory, snapshot.memory.confirmation.proposal_id, 'sim')
        self.assertEqual(approved.status, 'approved')
        self.context.save(fixtures.OWNER, previous.conversation_id, snapshot.revision, memory)
        result = self.turn('Resumo do mês', previous)
        self.assertEqual(self.context.load(fixtures.OWNER, previous.conversation_id).memory.confirmation.state, 'revoked')
        self.assertIn('prévia anterior perdeu validade', result.reply)
        self.assertFalse(self.writes())

    def test_consumed_approval_and_completed_operation_are_preserved(self):
        previous = self.preview()
        confirmed = self.turn('sim', previous)
        self.assertEqual(confirmed.mutation_state, 'confirmed')
        before = self.context.load(fixtures.OWNER, confirmed.conversation_id)
        self.assertEqual(before.memory.confirmation.state, 'consumed')
        writes = list(self.writes())
        result = self.turn('Resume minha semana', confirmed)
        after = self.context.load(fixtures.OWNER, confirmed.conversation_id)
        self.assertEqual(after.memory, before.memory)
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual(self.writes(), writes)

    def test_stale_cas_prevents_even_a_read_service_from_running(self):
        previous = self.preview()
        with patch.object(self.context, 'save', side_effect=ConversationError('conversation_conflict')):
            result = self.turn('Resume minha semana', previous)
        self.assertEqual(result.status, 'failed')
        self.preparation.respond.assert_not_called()
        self.assertFalse(self.writes())

    def test_unconfirmed_revocation_cannot_continue_to_read(self):
        previous = self.preview()
        snapshot = self.context.load(fixtures.OWNER, previous.conversation_id)
        invalid = Mock()
        invalid.revoke.side_effect = RuntimeError('PRIVATE_PROVIDER_DIAGNOSTIC')
        self.service._confirm = lambda: invalid
        result = self.turn('Resumo do mês', previous)
        self.assertEqual(result.status, 'failed')
        self.preparation.respond.assert_not_called()
        self.assertEqual(self.context.load(fixtures.OWNER, previous.conversation_id).memory, snapshot.memory)
        self.assertFalse(self.writes())

    def test_conversation_change_during_read_does_not_publish_stale_reply(self):
        previous = self.turn('Resume minha semana')
        def concurrent(_message, *, memory):
            current = self.context.load(fixtures.OWNER, previous.conversation_id)
            self.context.save(fixtures.OWNER, previous.conversation_id, current.revision, current.memory)
            return PreparationChatResult('PRIVATE_STALE_RESPONSE', 'succeeded', 'summarize_agenda')
        self.preparation.respond.side_effect = concurrent
        result = self.turn('Resumo do mês', previous)
        self.assertEqual(result.status, 'failed')
        self.assertNotIn('PRIVATE_STALE_RESPONSE', result.reply)
        self.assertFalse(self.writes())

    def test_source_failure_cannot_restore_old_confirmation_or_authorization(self):
        previous = self.preview()
        self.preparation.respond.side_effect = RuntimeError('PRIVATE_PROVIDER_DIAGNOSTIC')
        result = self.turn('Resume minha semana', previous)
        self.assertEqual(result.status, 'failed')
        self.assertIsNone(result.authorization_action)
        self.assertEqual(self.context.load(fixtures.OWNER, previous.conversation_id).memory.confirmation.state, 'revoked')
        self.assertFalse(self.writes())

    def test_selected_event_context_is_preserved_for_fresh_briefing_read(self):
        previous = self.turn('O que tenho amanhã?')
        self.preparation.respond.return_value = PreparationChatResult('Preparo da entrevista.', 'succeeded', 'prepare_briefing')
        result = self.turn('Me prepara para essa entrevista', previous)
        memory = self.preparation.respond.call_args.kwargs['memory']
        self.assertEqual(memory.selected_event_id, fixtures.EID)
        self.assertEqual(result.action, 'prepare_briefing')
        self.assertFalse(self.writes())

    def test_media_and_ordinary_queries_keep_existing_paths(self):
        self.turn(None, event_type='message_image')
        self.turn('O que tenho amanhã?')
        self.preparation.respond.assert_not_called()
        self.assertEqual([kind for kind, _ in self.model.calls], ['query'])

    def test_invalid_or_oversized_result_is_not_published(self):
        for result in (PreparationChatResult('X' * 3501, 'succeeded', 'summarize_agenda'),
                       PreparationChatResult('PRIVATE\x00INVALID', 'succeeded', 'summarize_agenda'),
                       object()):
            with self.subTest(kind=type(result).__name__):
                self.preparation.respond.return_value = result
                turn = self.turn('Resume minha semana')
                self.assertNotIn('PRIVATE', turn.reply)
                self.assertNotEqual(turn.reply, 'X' * 3501)
                self.assertIn(turn.status, {'failed', 'needs_clarification'})
        self.assertFalse(self.writes())


if __name__ == '__main__':
    unittest.main()
