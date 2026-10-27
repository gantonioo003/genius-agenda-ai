"""Reagendamentos sucessivos selecionam o estado atual e preservam o destino."""

from dataclasses import replace
from datetime import datetime, timezone
import unittest

import test_agent_dialogue as legacy
from app.conversation_store import QueryMemory
from app.services.agenda import CalendarListing
from app.services.agent_dialogue import AgentDialogueService


TITLE = 'Entrevista Projeto'
EVENT_ID = 'SuccessiveRescheduleFixture'
OLD_START = '2026-10-26T19:32:00-03:00'
OLD_END = '2026-10-26T20:02:00-03:00'
TARGET_START = '2026-10-26T18:48:00-03:00'
TARGET_END = '2026-10-26T19:18:00-03:00'
MESSAGE = 'Reagende Entrevista Projeto para hoje, 26/10/2026, das 18h48 às 19h18.'


class PeriodFilteringGateway(legacy.Gateway):
    """A consulta vazia de amanhã não pode devolver o evento movido para hoje."""

    def list_events(self, period):
        listing = super().list_events(period)
        events = tuple(item for item in listing.events
                       if datetime.fromisoformat(item.start) < period.end
                       and datetime.fromisoformat(item.end) > period.start)
        return CalendarListing(listing.calendar_name, events)


class SuccessiveRescheduleTests(unittest.TestCase):
    def setUp(self):
        self.model = legacy.Model()
        self.gateway = PeriodFilteringGateway()
        self.factory = legacy.Factory(self.gateway)
        self.service = AgentDialogueService(self.model, self.factory,
            clock=lambda: datetime(2026, 10, 26, 19, 30, tzinfo=timezone.utc))
        self.install_events(legacy.event(EVENT_ID, title=TITLE, start=OLD_START, end=OLD_END))
        self.addCleanup(self.check_read_only)

    def check_read_only(self):
        self.assertEqual(self.gateway.writes, 0)
        self.assertEqual(self.factory.opened, self.factory.closed)

    def install_events(self, *events):
        self.gateway.events = events
        self.gateway.lookups = {item.event_id: item for item in events}

    def memory(self, *, selected=True):
        # O primeiro reagendamento já foi concluído: o evento está no dia 26,
        # enquanto o contexto da consulta original continua no dia 27.
        return QueryMemory(last_list={'date': '2026-10-27', 'period': 'day', 'time_zone': legacy.ZONE},
            selected_event_id=EVENT_ID if selected else None,
            selected_time_zone=legacy.ZONE if selected else None)

    def assert_search_day(self, expected):
        periods = [call[1] for call in self.gateway.calls if call[0] == 'list']
        self.assertEqual(len(periods), 1)
        self.assertEqual(periods[0].start.date().isoformat(), expected)
        self.assertEqual((periods[0].end - periods[0].start).total_seconds(), 86400)

    def assert_global_search(self):
        self.assertEqual([call for call in self.gateway.calls if call[0] == 'search'],
                         [('search', TITLE, legacy.ZONE)])
        self.assertFalse([call for call in self.gateway.calls if call[0] == 'list'])

    def assert_ready_target(self, result, start=TARGET_START, end=TARGET_END, event_id=EVENT_ID):
        self.assertEqual((result.status, result.action), ('ready', 'reschedule'))
        self.assertEqual(result.data['parameters']['event_id'], event_id)
        self.assertEqual(result.data['parameters']['start'], start)
        self.assertEqual(result.data['parameters']['end'], end)
        self.assertFalse(result.data['execution_allowed'])

    def pending_after_not_found(self):
        # O evento só passa a existir depois da primeira busca global vazia.
        events = self.gateway.events
        self.install_events()
        try:
            result, memory = self.service.respond(MESSAGE, self.memory(selected=False))
        finally:
            self.install_events(*events)
        self.assertEqual(result.data['reason'], 'event_not_found')
        self.assertEqual(memory.pending_request.awaiting, ('event',))
        self.assertEqual(memory.pending_request.fields['start'], TARGET_START)
        self.assertEqual(memory.pending_request.fields['end'], TARGET_END)
        self.gateway.calls.clear()
        return memory

    def test_second_reschedule_searches_globally_and_keeps_requested_times(self):
        self.model.failure = legacy.ModelError('llm_unavailable')
        result, _ = self.service.respond(MESSAGE, self.memory())
        self.assert_ready_target(result)
        self.assert_global_search()
        self.assertEqual([call[0] for call in self.gateway.calls], ['search', 'get'])
        self.assertEqual(len(self.model.calls), 1)

    def test_destination_day_does_not_become_source_search_day(self):
        message = 'Reagende Entrevista Projeto para 28/10/2026, das 18h48 às 19h18.'
        result, _ = self.service.respond(message, self.memory())
        self.assert_ready_target(result, TARGET_START.replace('-26T', '-28T'),
                                 TARGET_END.replace('-26T', '-28T'))
        self.assert_global_search()

    def test_model_produced_edit_searches_globally_after_move(self):
        self.model.plan = legacy.plan(action='edit', search_title=TITLE, location='Sala B')
        result, memory = self.service.respond(
            'Quero atualizar o local de Entrevista Projeto para Sala B.', self.memory())
        self.assertEqual((result.status, result.action), ('ready', 'edit'))
        self.assertEqual(result.data['parameters']['event_id'], EVENT_ID)
        self.assertEqual(memory.pending_request.fields['location'], 'Sala B')
        self.assert_global_search()
        self.assertEqual([call[0] for call in self.gateway.calls], ['search', 'get'])
        self.assertFalse(result.data['execution_allowed'])

    def test_model_produced_reschedule_searches_globally_after_move(self):
        self.model.plan = legacy.plan(action='reschedule', search_title=TITLE,
            start=TARGET_START, end=TARGET_END)
        result, _ = self.service.respond(
            'Pode remarcar Entrevista Projeto para 26/10/2026 das 18h48 às 19h18?', self.memory())
        self.assert_ready_target(result)
        self.assert_global_search()

    def test_current_day_duplicates_still_require_explicit_selection(self):
        first = self.gateway.events[0]
        self.install_events(first, replace(first, event_id='DuplicateRescheduleFixture'))
        result, memory = self.service.respond(MESSAGE, self.memory())
        self.assertEqual((result.status, result.data['reason']),
                         ('needs_clarification', 'event_selection'))
        self.assertEqual(len(result.data['candidates']), 2)
        self.assertEqual(len(memory.pending_request.candidates), 2)
        self.assertIsNone(result.data['parameters'])
        self.assertEqual(memory.pending_request.fields['start'], TARGET_START)
        self.assertEqual(memory.pending_request.fields['end'], TARGET_END)
        self.assert_global_search()
        self.assertEqual([call[0] for call in self.gateway.calls], ['search'])

    def test_failed_global_search_never_prepares_reschedule(self):
        self.gateway.failure = RuntimeError('fixture lookup failure')
        result, _ = self.service.respond(MESSAGE, self.memory())
        self.assertEqual((result.status, result.action), ('failed', 'reschedule'))
        self.assertIsNone(result.data)
        self.assertEqual([call[0] for call in self.gateway.calls], ['search'])

    def test_explicit_source_date_overrides_remembered_moved_event_day(self):
        source = replace(self.gateway.events[0], event_id='ExplicitSourceFixture',
            start=OLD_START.replace('-26T', '-27T'), end=OLD_END.replace('-26T', '-27T'))
        self.install_events(self.gateway.events[0], source)
        start, end = TARGET_START.replace('-26T', '-28T'), TARGET_END.replace('-26T', '-28T')
        self.model.plan = legacy.plan(action='reschedule', search_title=TITLE,
            date='2026-10-27', period='day', start=start, end=end)
        result, _ = self.service.respond(
            'Quero reagendar Entrevista Projeto de 27/10/2026 para 28/10/2026 das 18h48 às 19h18.',
            self.memory())
        self.assert_ready_target(result, start, end, event_id=source.event_id)
        self.assert_search_day('2026-10-27')
        self.assertEqual([call[0] for call in self.gateway.calls], ['list', 'get'])

    def test_unrelated_remembered_event_never_restricts_global_search(self):
        memory = replace(self.memory(), selected_event_id='UnrelatedFixture')
        self.gateway.lookups['UnrelatedFixture'] = legacy.event('UnrelatedFixture', title='Outro compromisso')
        result, memory = self.service.respond(MESSAGE, memory)
        self.assert_ready_target(result)
        self.assert_global_search()
        self.assertEqual([call[0] for call in self.gateway.calls], ['search', 'get'])
        self.assertEqual(memory.pending_request.fields['start'], TARGET_START)
        self.assertEqual(memory.pending_request.fields['end'], TARGET_END)

    def test_pasted_current_event_line_identifies_event_without_replacing_desired_times(self):
        memory = self.pending_after_not_found()
        self.model.plan = legacy.plan(mode='continue', search_title=TITLE,
            date='2026-10-26', period='day', start=OLD_START, end=OLD_END)
        result, updated = self.service.respond(
            'Entrevista Projeto — hoje, 26/10/2026, das 19h32 às 20h02.', memory)
        self.assert_ready_target(result)
        self.assertEqual(updated.pending_request.fields['start'], TARGET_START)
        self.assertEqual(updated.pending_request.fields['end'], TARGET_END)
        self.assert_search_day('2026-10-26')

    def test_explicit_time_correction_during_event_clarification_can_change_destination(self):
        memory = self.pending_after_not_found()
        start = TARGET_START.replace('18:48', '18:50')
        end = TARGET_END.replace('19:18', '19:20')
        self.model.plan = legacy.plan(mode='continue', search_title=TITLE,
            date='2026-10-26', period='day', start=start, end=end)
        result, _ = self.service.respond(
            'Quero reagendar Entrevista Projeto de 26/10/2026 para as 18h50 às 19h20.', memory)
        self.assert_ready_target(result, start, end)

    def test_complete_new_request_replaces_previous_pending_destination(self):
        memory = self.pending_after_not_found()
        # Uma seleção fresca pode surgir de uma leitura entre as mensagens.
        memory = replace(memory, selected_event_id=EVENT_ID, selected_time_zone=legacy.ZONE)
        message = MESSAGE.replace('18h48', '18h50').replace('19h18', '19h20')
        result, _ = self.service.respond(message, memory)
        self.assert_ready_target(result, TARGET_START.replace('18:48', '18:50'),
                                 TARGET_END.replace('19:18', '19:20'))
        self.assert_global_search()

    def test_monthly_context_never_hides_cross_day_title_ambiguity(self):
        first = self.gateway.events[0]
        self.install_events(first, replace(first, event_id='OtherDayRescheduleFixture',
            start=OLD_START.replace('2026-10-26', '2027-03-27'), end=OLD_END.replace('2026-10-26', '2027-03-27')))
        memory = replace(self.memory(),
            last_list={'date': '2026-10-26', 'period': 'month', 'time_zone': legacy.ZONE})
        result, updated = self.service.respond(MESSAGE, memory)
        self.assertEqual((result.status, result.data['reason']),
                         ('needs_clarification', 'event_selection'))
        self.assertEqual(len(result.data['candidates']), 2)
        self.assertEqual(len(updated.pending_request.candidates), 2)
        self.assertIsNone(result.data['parameters'])
        self.assertEqual(updated.pending_request.fields['start'], TARGET_START)
        self.assertEqual(updated.pending_request.fields['end'], TARGET_END)
        self.assert_global_search()

    def test_pasted_event_title_with_change_word_does_not_replace_desired_times(self):
        memory = self.pending_after_not_found()
        source = replace(self.gateway.events[0], title='Mudança de equipe')
        self.install_events(source)
        self.model.plan = legacy.plan(mode='continue', search_title=source.title,
            date='2026-10-26', period='day', start=OLD_START, end=OLD_END)
        result, updated = self.service.respond(
            'Mudança de equipe — hoje, 26/10/2026, das 19h32 às 20h02.', memory)
        self.assert_ready_target(result)
        self.assertEqual(updated.pending_request.fields['start'], TARGET_START)
        self.assertEqual(updated.pending_request.fields['end'], TARGET_END)

    def test_corrigindo_message_can_explicitly_correct_desired_times(self):
        memory = self.pending_after_not_found()
        start = TARGET_START.replace('18:48', '18:50')
        end = TARGET_END.replace('19:18', '19:20')
        self.model.plan = legacy.plan(mode='continue', search_title=TITLE,
            date='2026-10-26', period='day', start=start, end=end)
        result, _ = self.service.respond(
            'Corrigindo o horário: Entrevista Projeto em 26/10/2026, das 18h50 às 19h20.', memory)
        self.assert_ready_target(result, start, end)

    def test_invalid_zone_is_corrected_before_any_selected_event_lookup(self):
        self.model.plan = legacy.plan(action='reschedule', search_title=TITLE,
            start=TARGET_START, end=TARGET_END, time_zone='Invalid/Zone')
        result, _ = self.service.respond(
            'Pode remarcar Entrevista Projeto para 26/10/2026 das 18h48 às 19h18 no fuso Invalid/Zone?',
            self.memory())
        self.assertEqual((result.status, result.action), ('needs_correction', 'reschedule'))
        self.assertIn('time_zone', result.data['invalid_fields'])
        self.assertEqual(self.gateway.calls, [])
        self.assertEqual(self.factory.opened, 0)

    def test_missing_end_asks_for_end_before_any_unavailable_calendar_lookup(self):
        self.model.plan = legacy.plan(action='reschedule', search_title=TITLE, start=TARGET_START)
        self.gateway.failure = RuntimeError('fixture source unavailable')
        result, updated = self.service.respond(
            'Pode remarcar Entrevista Projeto para 26/10/2026 às 18h48?', self.memory())
        self.assertEqual((result.status, result.action), ('needs_clarification', 'reschedule'))
        self.assertIn('end', result.data['missing_fields'])
        self.assertEqual(updated.pending_request.fields['start'], TARGET_START)
        self.assertEqual(self.gateway.calls, [])
        self.assertEqual(self.factory.opened, 0)

    def test_pasted_current_event_line_keeps_destination_even_if_model_starts_new_request(self):
        memory = self.pending_after_not_found()
        self.model.plan = legacy.plan(mode='start', action='reschedule', search_title=TITLE,
            date='2026-10-26', period='day', start=OLD_START, end=OLD_END)
        result, updated = self.service.respond(
            'Entrevista Projeto — hoje, 26/10/2026, das 19h32 às 20h02.', memory)
        self.assert_ready_target(result)
        self.assertEqual(updated.pending_request.fields['start'], TARGET_START)
        self.assertEqual(updated.pending_request.fields['end'], TARGET_END)
        self.assert_search_day('2026-10-26')


if __name__ == '__main__':
    unittest.main()
