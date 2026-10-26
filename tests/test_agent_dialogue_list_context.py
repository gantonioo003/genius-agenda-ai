"""Título literal e contexto válido refazem busca; não adivinham ID/data."""
import unittest

import test_agent_dialogue as legacy
from app.conversation_store import QueryMemory
from app.dialogue_state import PendingRequest


TITLE = 'QA16 — Lembrete fictício'
MESSAGE = 'Altere a descrição do QA16 — Lembrete fictício para Teste real de qualidade pelo WhatsApp.'
NOTES = 'Teste real de qualidade pelo WhatsApp.'


class DialogueListContextTests(unittest.TestCase):
    setUp = legacy.AgentDialogueTests.setUp
    check_read_only = legacy.AgentDialogueTests.check_read_only

    def memory(self, *, listed=True):
        return QueryMemory(last_list={'date': '2030-06-17', 'period': 'day', 'time_zone': legacy.ZONE} if listed else None,
            selected_event_id='UnrelatedOldSelection' if listed else None, selected_time_zone=legacy.ZONE if listed else None,
            pending_request=PendingRequest({}, ('event',)))

    def set_events(self, count=1):
        self.gateway.events = tuple(legacy.event('FixtureExact' + str(i), title=TITLE) for i in range(count))
        self.gateway.lookups = {item.event_id: item for item in self.gateway.events}

    def test_exact_screenshot_request_recovers_missing_plan_fields_and_refetches_google(self):
        self.set_events()
        self.model.plan = legacy.plan('ask_dialogue', reason='unclear_answer', field='event')
        result, memory = self.service.respond(MESSAGE, self.memory())
        self.assertEqual((result.status, result.action), ('ready', 'edit'))
        self.assertEqual(result.data['parameters']['description'], NOTES)
        self.assertEqual(result.data['parameters']['event_id'], 'FixtureExact0')
        self.assertEqual([call[0] for call in self.gateway.calls], ['get', 'list', 'get'])
        self.assertEqual(next(call for call in self.gateway.calls if call[0] == 'list')[1].start.date().isoformat(), '2030-06-17')
        self.assertFalse(result.data['execution_allowed'])
        self.assertEqual(memory.pending_request.fields['description'], NOTES)

    def test_prepare_patch_missing_date_reuses_last_period_without_selecting_old_id(self):
        self.set_events()
        self.model.plan = legacy.plan(action='edit', search_title=TITLE, description='Descrição atualizada')
        memory = QueryMemory(last_list={'date': '2030-06-17', 'period': 'month', 'time_zone': legacy.ZONE})
        result, _ = self.service.respond('Altere a descrição de ' + TITLE + ' para Descrição atualizada', memory)
        self.assertEqual(result.status, 'ready')
        period = self.gateway.calls[0][1]
        self.assertEqual((period.start.date().isoformat(), period.end.date().isoformat()), ('2030-06-01', '2030-07-01'))

    def test_missing_or_expired_context_preserves_literal_title_and_asks_only_date(self):
        self.model.plan = legacy.plan('ask_dialogue', reason='unclear_answer', field='event')
        for connector in ('de', 'do', 'da'):
            message = f'Altere descrição {connector} {TITLE} para {NOTES}'
            result, memory = self.service.respond(message, self.memory(listed=False))
            self.assertEqual(result.status, 'needs_clarification')
            self.assertEqual(result.data['missing_fields'], ['date'])
            self.assertNotIn('título', result.message.lower())
            self.assertEqual(memory.pending_request.fields['search_title'], TITLE)
        self.assertEqual(self.factory.opened, 0)

    def test_two_fresh_candidates_keep_selection_question_without_guessing_first(self):
        self.set_events(2)
        self.model.plan = legacy.plan('ask_dialogue', reason='ambiguous_information', field='event')
        result, memory = self.service.respond(MESSAGE, self.memory())
        self.assertEqual((result.status, result.data['reason']), ('needs_clarification', 'event_selection'))
        self.assertEqual(len(result.data['candidates']), 2)
        self.assertEqual(len(memory.pending_request.candidates), 2)
        self.assertIsNone(result.data['parameters'])
        self.assertEqual([call[0] for call in self.gateway.calls], ['get', 'list'])

    def test_explicit_ambiguous_date_or_multiple_requests_never_borrows_context(self):
        self.model.plan = legacy.plan(action='edit', search_title=TITLE, description=NOTES, ambiguous_field='date')
        result, _ = self.service.respond(MESSAGE, self.memory())
        self.assertEqual(result.data['missing_fields'], ['date'])
        self.assertEqual(self.factory.opened, 0)
        self.model.plan = legacy.plan('ask_dialogue', reason='multiple_requests', field='request')
        result, _ = self.service.respond(MESSAGE + ' E exclua outro evento.', self.memory())
        self.assertEqual(result.data['reason'], 'multiple_requests')
        self.assertEqual(self.factory.opened, 0)

    def test_complete_literal_overrides_selected_hint_but_not_pending_multiple_candidates(self):
        self.set_events()
        self.model.plan = legacy.plan(action='edit', description=NOTES, use_selected_event=True)
        result, _ = self.service.respond(MESSAGE, self.memory())
        self.assertEqual(result.status, 'ready')
        self.assertEqual(result.data['parameters']['event_id'], 'FixtureExact0')
        self.assertEqual([call[0] for call in self.gateway.calls], ['get', 'list', 'get'])
        self.assertTrue(self.model.plan.arguments['use_selected_event'])
        # Uma ambiguidade já apresentada continua exigindo escolha explícita.
        from app.dialogue_state import CandidateReference
        memory = self.memory()
        memory = QueryMemory(last_list=memory.last_list, selected_event_id=memory.selected_event_id,
            selected_time_zone=memory.selected_time_zone,
            pending_request=PendingRequest({'action': 'edit', 'description': NOTES}, ('event',), (
                CandidateReference('candidate-one', 'a' * 64), CandidateReference('candidate-two', 'b' * 64))))
        self.gateway.calls.clear()
        self.model.plan = legacy.plan(mode='continue', description=NOTES, use_selected_event=True)
        result, _ = self.service.respond(MESSAGE, memory)
        self.assertEqual((result.status, result.data['reason']), ('needs_clarification', 'event_selection'))
        self.assertEqual(self.gateway.calls, [])


if __name__ == '__main__':
    unittest.main()
