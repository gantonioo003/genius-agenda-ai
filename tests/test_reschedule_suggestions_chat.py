"""Sugestões legíveis, duração da origem e escolha seguida de confirmação."""
from unittest.mock import Mock, patch
import unittest

from app.conversation_store import ConversationError, QueryMemory
from app.services.availability import AvailabilityService
from app.services.preparation_chat import PreparationChatResult, PreparationChatService
from app.services.reschedule_suggestions import RescheduleSuggestionsService
import test_reschedule_suggestions as source
import test_whatsapp_agent as dialogue
import test_whatsapp_preparation as flows


class SuggestionChatTests(unittest.TestCase):
    def setUp(self):
        self.briefing, self.occupancy = source.BriefingFake(), source.OccupancySource()
        service = RescheduleSuggestionsService(briefing_factory=lambda: self.briefing,
            availability_factory=lambda: AvailabilityService(self.occupancy.factory, clock=lambda: source.NOW))
        self.factory = Mock(return_value=service)
        self.chat = PreparationChatService(summary_factory=Mock(), briefing_factory=Mock(), availability_factory=Mock(),
            time_zone_loader=lambda: source.ZONE, clock=lambda: source.NOW, reschedule_factory=self.factory)
        self.message = 'Sugira horários para reagendar id ' + source.IDENTITY + ' 2030-06-20 das 09:00 às 12:00'

    def test_explicit_event_keeps_case_and_derives_45_minutes_without_moving_it(self):
        result = self.chat.respond(self.message, QueryMemory())
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual(result.action, 'suggest_reschedule')
        self.assertEqual(result.selected_event_id, source.IDENTITY)
        self.assertEqual(result.selected_time_zone, source.ZONE)
        self.assertIn('*Sugestões para reagendar*', result.text)
        self.assertIn('9h às 9h45', result.text)
        self.assertIn('confirmar antes de alterar', result.text)
        self.assertNotIn('S1:', result.text)
        self.assertNotIn('America/Recife', result.text)

    def test_selected_event_and_relative_day_use_existing_context(self):
        result = self.chat.respond('Sugira horários para reagendar essa entrevista amanhã das 09:00 às 12:00',
            QueryMemory(selected_event_id=source.IDENTITY, selected_time_zone=source.ZONE))
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual(self.briefing.calls[0]['event_id'], source.IDENTITY)
        self.assertIn('18/06/2030', result.text)

    def test_missing_selection_ambiguous_window_or_client_duration_never_open_services(self):
        for message in ('Sugira horários para reagendar essa entrevista amanhã das 09:00 às 12:00',
                        self.message + ' por 30 minutos', self.message + ' e exclua a entrevista',
                        'Sugira horários para reagendar a primeira entrevista amanhã à tarde'):
            with self.subTest(message=message):
                result = self.chat.respond(message, QueryMemory())
                self.assertEqual(result.status, 'needs_clarification')
        self.factory.assert_not_called()

    def test_unavailable_target_returns_question_without_selected_event_or_options(self):
        self.briefing.status = 'needs_clarification'
        result = self.chat.respond(self.message, QueryMemory())
        self.assertEqual(result.status, 'needs_clarification')
        self.assertIsNone(result.selected_event_id)
        self.assertFalse(self.occupancy.queries)


class SuggestionConfirmationFlowTests(unittest.TestCase):
    setUp = flows.WhatsAppPreparationTests.setUp
    make_service = flows.WhatsAppPreparationTests.make_service
    factory = flows.WhatsAppPreparationTests.factory
    turn = flows.WhatsAppPreparationTests.turn
    writes = flows.WhatsAppPreparationTests.writes
    create_plan = flows.WhatsAppPreparationTests.create_plan

    def suggest(self):
        self.preparation.respond.return_value = PreparationChatResult(
            'Opções atuais. Nenhuma alteração feita.', 'succeeded', 'suggest_reschedule', dialogue.EID, dialogue.ZONE)
        return self.turn('Sugira horários para reagendar id ' + dialogue.EID + ' amanhã das 09:00 às 12:00')

    def test_suggestion_to_reschedule_to_confirmation_uses_existing_executor_once(self):
        suggested = self.suggest()
        memory = self.context.load(dialogue.OWNER, suggested.conversation_id).memory
        self.assertEqual(memory.selected_event_id, dialogue.EID)
        self.assertIsNone(suggested.authorization_action)
        self.assertFalse(self.writes())
        before = self.gateway.current
        self.model.plan = dialogue.plan(action='reschedule', use_selected_event=True, start=dialogue.START, end=dialogue.END)
        preview = self.turn('Reagende essa entrevista em 17/06/2030 das 15h às 15h30', suggested)
        self.assertEqual(preview.status, 'needs_confirmation')
        self.assertEqual(self.gateway.current, before)
        self.assertFalse(self.writes())
        confirmed = self.turn('sim', preview)
        self.assertEqual(confirmed.mutation_state, 'confirmed')
        self.assertEqual(self.gateway.current.start, dialogue.START)
        self.assertEqual(self.gateway.current.title, before.title)
        self.assertEqual(len(self.writes()), 1)

    def test_yes_after_suggestion_is_not_an_approval_or_a_write(self):
        suggested = self.suggest()
        result = self.turn('sim', suggested)
        self.assertNotEqual(result.mutation_state, 'confirmed')
        self.assertFalse(self.writes())

    def test_selection_save_conflict_does_not_publish_or_apply_the_suggestion(self):
        self.preparation.respond.return_value = PreparationChatResult(
            'PRIVATE_STALE_OPTIONS', 'succeeded', 'suggest_reschedule', dialogue.EID, dialogue.ZONE)
        original, calls = self.context.save, []
        def save(*args):
            calls.append(1)
            if len(calls) == 2:
                raise ConversationError('conversation_conflict')
            return original(*args)
        with patch.object(self.context, 'save', side_effect=save):
            result = self.turn('Sugira horários para reagendar id ' + dialogue.EID + ' amanhã das 09:00 às 12:00')
        self.assertEqual(result.status, 'failed')
        self.assertNotIn('PRIVATE_STALE_OPTIONS', result.reply)
        self.assertFalse(self.writes())


if __name__ == '__main__':
    unittest.main()
