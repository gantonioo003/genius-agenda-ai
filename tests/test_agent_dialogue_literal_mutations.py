"""Reagendamento/exclusão literais continuam somente preparação e seleção fresca."""
from datetime import datetime, timezone
import unittest

import test_agent_dialogue as legacy
from app.conversation_store import QueryMemory
from app.dialogue_state import PendingRequest

TITLE = 'QA16 — Lembrete fictício'
MESSAGE = 'Reagende QA16 — Lembrete fictício para hoje, 26/10/2026, das 16h45 às 17h15, no horário de Recife.'


class LiteralMutationTests(unittest.TestCase):
    setUp = legacy.AgentDialogueTests.setUp
    check_read_only = legacy.AgentDialogueTests.check_read_only

    def ready(self, count=1):
        self.service._clock = lambda: datetime(2026, 10, 26, 19, 30, tzinfo=timezone.utc)
        self.gateway.events = tuple(legacy.event('LiteralFake' + str(i), title=TITLE,
            start='2026-10-26T16:55:00-03:00', end='2026-10-26T17:25:00-03:00') for i in range(count))
        self.gateway.lookups = {event.event_id: event for event in self.gateway.events}
        self.model.plan = legacy.plan('ask_dialogue', reason='ambiguous_information', field='event')
        return QueryMemory(last_list={'date': '2026-10-26', 'period': 'day', 'time_zone': legacy.ZONE},
            selected_event_id='UnrelatedSelection', selected_time_zone=legacy.ZONE,
            pending_request=PendingRequest({}, ('event',)))

    def test_exact_screenshot_reschedule_uses_explicit_times_and_fresh_title_lookup(self):
        result, _ = self.service.respond(MESSAGE, self.ready())
        self.assertEqual((result.status, result.action), ('ready', 'reschedule'))
        self.assertEqual(result.data['parameters']['event_id'], 'LiteralFake0')
        self.assertEqual(result.data['parameters']['start'], '2026-10-26T16:45:00-03:00')
        self.assertEqual(result.data['parameters']['end'], '2026-10-26T17:15:00-03:00')
        self.assertFalse(result.data['execution_allowed'])
        self.assertEqual([call[0] for call in self.gateway.calls], ['list', 'get'])

    def test_complete_reschedule_without_zone_survives_unavailable_model(self):
        from unittest.mock import patch
        from app.llm import ModelError
        memory = self.ready()
        message = MESSAGE.replace(', no horário de Recife', '')
        with patch.object(self.model, 'plan_dialogue', side_effect=ModelError('llm_unavailable')) as model:
            result, _ = self.service.respond(message, memory)
        model.assert_not_called()
        self.assertEqual((result.status, result.action), ('ready', 'reschedule'))
        self.assertEqual(result.data['parameters']['event_id'], 'LiteralFake0')
        self.assertEqual(result.data['parameters']['time_zone'], legacy.ZONE)
        self.assertFalse(result.data['execution_allowed'])
        self.assertEqual(self.gateway.writes, 0)

    def test_reschedule_full_fields_override_stale_selected_hint_and_model_time(self):
        memory = self.ready()
        self.model.plan = legacy.plan(action='reschedule', use_selected_event=True,
            start='2026-10-26T15:00:00-03:00', end='2026-10-26T15:30:00-03:00')
        result, _ = self.service.respond(MESSAGE.replace('16h45', '16:45').replace('17h15', '17:15'), memory)
        self.assertEqual(result.status, 'ready')
        self.assertEqual(result.data['parameters']['start'], '2026-10-26T16:45:00-03:00')
        self.assertEqual(result.data['parameters']['event_id'], 'LiteralFake0')

    def test_delete_literal_title_with_explicit_date_or_last_list_does_not_execute(self):
        for message in ['Exclua ' + TITLE + ' hoje, 26/10/2026.', 'Exclua ' + TITLE]:
            memory = self.ready()
            result, _ = self.service.respond(message, memory)
            self.assertEqual((result.status, result.action), ('ready', 'delete'))
            self.assertEqual(result.data['parameters']['event_id'], 'LiteralFake0')
            self.assertFalse(result.data['execution_allowed'])
            self.assertEqual(self.gateway.writes, 0)

    def moved_memory(self, count=1):
        from dataclasses import replace
        memory = self.ready(count)
        return replace(memory, last_list={'date': '2026-10-27', 'period': 'day', 'time_zone': legacy.ZONE},
                       selected_event_id='LiteralFake0')

    def test_delete_after_move_uses_current_day_and_still_refetches_unique_match(self):
        memory = self.moved_memory()
        self.model.failure = legacy.ModelError('llm_invalid_response')
        result, _ = self.service.respond('Exclua ' + TITLE + '.', memory)
        self.assertEqual((result.status, result.action), ('ready', 'delete'))
        self.assertEqual(result.data['parameters']['event_id'], 'LiteralFake0')
        self.assertFalse(result.data['execution_allowed'])
        self.assertEqual([call[0] for call in self.gateway.calls], ['get', 'list', 'get'])
        self.assertEqual(self.gateway.calls[1][1].start.date().isoformat(), '2026-10-26')

    def test_delete_after_move_does_not_break_same_day_duplicate_tie(self):
        memory = self.moved_memory(2)
        result, _ = self.service.respond('Exclua ' + TITLE, memory)
        self.assertEqual(result.data['reason'], 'event_selection')
        self.assertEqual(len(result.data['candidates']), 2)
        self.assertIsNone(result.data['parameters'])
        self.assertEqual(self.gateway.writes, 0)

    def test_explicit_delete_day_overrides_remembered_moved_event(self):
        memory = self.moved_memory()
        result, _ = self.service.respond('Exclua ' + TITLE + ' 27/10/2026.', memory)
        self.assertEqual(self.gateway.calls[0][0], 'list')
        self.assertEqual(self.gateway.calls[0][1].start.date().isoformat(), '2026-10-27')
        self.assertFalse(result.data['execution_allowed'])

    def test_unrelated_selection_never_changes_delete_search_day(self):
        from dataclasses import replace
        memory = self.moved_memory()
        self.gateway.lookups['LiteralFake0'] = replace(self.gateway.lookups['LiteralFake0'], title='Outro compromisso')
        result, _ = self.service.respond('Exclua ' + TITLE, memory)
        self.assertEqual(self.gateway.calls[1][1].start.date().isoformat(), '2026-10-27')
        self.assertNotEqual(result.status, 'ready')

    def test_failed_fresh_lookup_never_prepares_delete(self):
        memory = self.moved_memory()
        self.gateway.failure = RuntimeError('temporary failure')
        result, _ = self.service.respond('Exclua ' + TITLE, memory)
        self.assertEqual(result.status, 'failed')
        self.assertIsNone(result.data)
        self.assertEqual(self.gateway.writes, 0)

    def test_two_candidates_never_select_first_for_reschedule_or_delete(self):
        for message in [MESSAGE, 'Exclua ' + TITLE]:
            result, _ = self.service.respond(message, self.ready(2))
            self.assertEqual((result.status, result.data['reason']), ('needs_clarification', 'event_selection'))
            self.assertEqual(len(result.data['candidates']), 2)
            self.assertIsNone(result.data['parameters'])

    def test_invalid_hours_relative_date_or_dst_never_open_source(self):
        for message in [MESSAGE.replace('16h45', '25h45'), MESSAGE.replace('17h15', '15h15'),
            MESSAGE.replace('hoje, 26/10/2026', 'hoje, 27/10/2026'),
            'Reagende ' + TITLE + ' para 03/11/2030, das 1h30 às 2h30, no horário de America/New_York.']:
            memory = self.ready()
            before = self.factory.opened
            result, _ = self.service.respond(message, memory)
            self.assertEqual(result.status, 'needs_clarification')
            self.assertEqual(self.factory.opened, before)

    def test_negation_multiple_or_incomplete_requests_do_not_bypass_model_question(self):
        for message in ['Não ' + MESSAGE, MESSAGE + ' E exclua outro evento.',
                        'Reagende ' + TITLE + ' para amanhã às 16h45', 'Não exclua ' + TITLE, 'Exclua todos os eventos']:
            memory = self.ready()
            before = self.factory.opened
            result, _ = self.service.respond(message, memory)
            self.assertEqual(result.status, 'needs_clarification')
            self.assertEqual(self.factory.opened, before)


if __name__ == '__main__':
    unittest.main()
