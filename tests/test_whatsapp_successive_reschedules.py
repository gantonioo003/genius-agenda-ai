"""Conversa local completa: consulta, duas mudanças confirmadas e exclusão."""

from dataclasses import replace
from datetime import datetime, timezone
import unittest

import test_whatsapp_agent as legacy
from app.agent_query_contract import QueryPlan
from app.services.agenda import CalendarListing


TITLE = 'Entrevista Projeto'
TOMORROW_START = '2026-10-27T19:32:00-03:00'
TOMORROW_END = '2026-10-27T20:02:00-03:00'
FIRST_START = '2026-10-26T19:32:00-03:00'
FIRST_END = '2026-10-26T20:02:00-03:00'
SECOND_START = '2026-10-26T18:48:00-03:00'
SECOND_END = '2026-10-26T19:18:00-03:00'


class PeriodFilteringGateway(legacy.Gateway):
    """O calendário fictício aplica o período, inclusive depois de mover o evento."""

    def list_events(self, period):
        listing = super().list_events(period)
        events = tuple(event for event in listing.events
                       if datetime.fromisoformat(event.start) < period.end
                       and datetime.fromisoformat(event.end) > period.start)
        return CalendarListing(listing.calendar_name, events)


class WhatsAppSuccessiveReschedulesTests(unittest.TestCase):
    # Reutiliza a composição real de SQLite, confirmação e executores, sem
    # herdar os demais testes nem substituir o serviço que processa os turnos.
    factory = legacy.WhatsAppAgentTests.factory
    make_service = legacy.WhatsAppAgentTests.make_service
    turn = legacy.WhatsAppAgentTests.turn
    writes = legacy.WhatsAppAgentTests.writes

    def setUp(self):
        legacy.WhatsAppAgentTests.setUp(self)
        self.now = datetime(2026, 10, 26, 19, 30, tzinfo=timezone.utc)
        self.gateway = PeriodFilteringGateway()
        self.gateway.current = replace(self.gateway.current, title=TITLE,
                                       start=TOMORROW_START, end=TOMORROW_END)
        self.model.query_plan = QueryPlan('list_events', {
            'date': '2026-10-27', 'period': 'day', 'time_zone': legacy.ZONE,
        }, 'fixture-query-model')
        self.service = self.make_service()

    def memory(self, turn):
        return self.context.load(legacy.OWNER, turn.conversation_id).memory

    def assert_reschedule_preview(self, turn, start, end, writes):
        self.assertEqual((turn.status, turn.action, turn.mutation_state),
                         ('needs_confirmation', 'reschedule', 'not_applied'))
        self.assertIn(TITLE, turn.reply)
        self.assertIn('sim', turn.reply)
        self.assertEqual(len(self.writes()), writes)
        proposal = self.memory(turn).confirmation
        self.assertEqual(proposal.state, 'pending')
        self.assertEqual(proposal.parameters['event_id'], legacy.EID)
        self.assertEqual(proposal.parameters['start'], start)
        self.assertEqual(proposal.parameters['end'], end)
        self.assertEqual(proposal.parameters['time_zone'], legacy.ZONE)

    def test_query_tomorrow_then_move_today_reschedule_again_and_delete(self):
        original = self.gateway.current
        listed = self.turn('O que tenho amanhã?')
        self.assertEqual((listed.status, listed.action), ('succeeded', 'list'))
        self.assertIn(TITLE, listed.reply)
        self.assertEqual(self.memory(listed).selected_event_id, legacy.EID)
        self.assertEqual(self.memory(listed).last_list['date'], '2026-10-27')
        self.assertFalse(self.writes())

        self.model.plan = legacy.plan(action='reschedule', search_title=TITLE,
                                      start=FIRST_START, end=FIRST_END)
        first = self.turn(
            'Reagende Entrevista Projeto para 26/10/2026 das 19h32 às 20h02.', listed)
        self.assert_reschedule_preview(first, FIRST_START, FIRST_END, writes=0)
        self.assertEqual(self.gateway.current, original)
        moved = self.turn('sim', first)
        self.assertEqual(moved.mutation_state, 'confirmed')
        self.assertEqual(self.gateway.current,
                         replace(original, start=FIRST_START, end=FIRST_END))
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(self.memory(moved).last_list['date'], '2026-10-27')
        self.assertEqual(self.memory(moved).selected_event_id, legacy.EID)

        # Reiniciar o serviço força o segundo pedido a usar o contexto persistido.
        self.service = self.make_service()
        calls_before_second = len(self.gateway.calls)
        self.model.plan = legacy.plan(action='reschedule', search_title=TITLE,
                                      start=SECOND_START, end=SECOND_END)
        second = self.turn(
            'Reagende Entrevista Projeto para 26/10/2026 das 18h48 às 19h18.', moved)
        self.assert_reschedule_preview(second, SECOND_START, SECOND_END, writes=1)
        self.assertEqual((self.gateway.current.start, self.gateway.current.end),
                         (FIRST_START, FIRST_END))
        second_calls = self.gateway.calls[calls_before_second:]
        self.assertEqual([call[0] for call in second_calls[:2]], ['search', 'lookup'])
        self.assertEqual([call for call in second_calls if call[0] == 'search'],
                         [('search', TITLE, legacy.ZONE)])
        self.assertFalse([call for call in second_calls if call[0] == 'list'])
        moved_again = self.turn('sim', second)
        self.assertEqual(moved_again.mutation_state, 'confirmed')
        self.assertEqual(self.gateway.current,
                         replace(original, start=SECOND_START, end=SECOND_END))
        self.assertEqual(len(self.writes()), 2)

        self.model.plan = legacy.plan(action='delete', search_title=TITLE)
        deletion = self.turn('Exclua Entrevista Projeto.', moved_again)
        self.assertEqual((deletion.status, deletion.action),
                         ('needs_confirmation', 'delete'))
        self.assertIn(TITLE, deletion.reply)
        self.assertEqual(len(self.writes()), 2)
        self.assertIsNotNone(self.gateway.current)
        self.assertEqual(self.memory(deletion).confirmation.parameters['event_id'], legacy.EID)
        deleted = self.turn('sim', deletion)
        self.assertEqual((deleted.action, deleted.mutation_state), ('delete', 'confirmed'))
        self.assertIsNone(self.gateway.current)
        self.assertEqual([call[0] for call in self.writes()], ['patch', 'patch', 'delete'])
        self.assertIsNone(self.memory(deleted).pending_request)
        self.assertIsNone(self.memory(deleted).selected_event_id)


if __name__ == '__main__':
    unittest.main()
