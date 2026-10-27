"""Diálogo de esclarecimento com modelo e calendário controlados, sem escritas."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
import unittest

from app.conversation_store import QueryMemory
from app.dialogue_contract import DIALOGUE_TOOLS, DialoguePlan
from app.dialogue_state import PendingRequest
from app.google_calendar import CalendarSetupError
from app.llm import ModelError
from app.services.agenda import AgendaEvent, CalendarListing
from app.services.agent_dialogue import AgentDialogueService, DialogueResult, validate_dialogue_result
from app.services.event_selection import EventLookup, normalized_title


ZONE = 'America/Recife'
NOW = datetime(2030, 6, 17, 2, 0, tzinfo=timezone.utc)
START = '2030-06-17T10:00:00-03:00'
END = '2030-06-17T10:30:00-03:00'
PRIVATE_TITLE = 'PRIVATE_DRAFT_TITLE'
PRIVATE_DESCRIPTION = 'PRIVATE_DRAFT_DESCRIPTION'
PRIVATE_LOCATION = 'PRIVATE_DRAFT_LOCATION'


def plan(name='prepare_request', **values):
    if name == 'prepare_request':
        schema = next(item['parameters'] for item in DIALOGUE_TOOLS if item['name'] == name)
        args = {name: None for name in schema['required']}
        args.update(mode='start', use_selected_event=False)
        args.update(values)
    else:
        args = values
    return DialoguePlan(name, args, 'fake-dialogue-model', 10, 4)


def event(identifier='FirstOpaqueID', **values):
    return replace(AgendaEvent(identifier, 'Entrevista', START, END, False, ZONE, 'confirmed'), **values)


class Model:
    def __init__(self):
        self.plan = plan()
        self.calls = []
        self.failure = None

    def plan_dialogue(self, *, instructions, message):
        self.calls.append({'instructions': instructions, 'message': message})
        if self.failure is not None:
            raise self.failure
        return self.plan


class Gateway:
    def __init__(self):
        self.events = ()
        self.lookups = {}
        self.calls = []
        self.failure = None
        self.writes = 0

    def list_events(self, period):
        self.calls.append(('list', period))
        if self.failure is not None:
            raise self.failure
        return CalendarListing('Calendário fictício', self.events)

    def search_events_by_title(self, title, *, time_zone):
        self.calls.append(('search', title, time_zone))
        if self.failure is not None:
            raise self.failure
        needle = normalized_title(title)
        return CalendarListing('Calendário fictício', tuple(
            item for item in self.events if needle in normalized_title(item.title)))

    def get_event(self, identifier, *, time_zone):
        self.calls.append(('get', identifier, time_zone))
        if self.failure is not None:
            raise self.failure
        return EventLookup('Calendário fictício', self.lookups.get(identifier))

    def _write(self, *_args, **_kwargs):
        self.writes += 1
        raise AssertionError('Preparar não autoriza operações de escrita.')

    create_event = _write
    reschedule_event = _write
    update_event_details = _write
    delete_event = _write
    send_message = _write


class Factory:
    def __init__(self, gateway):
        self.gateway = gateway
        self.opened = 0
        self.closed = 0
        self.failure = None

    @contextmanager
    def __call__(self):
        self.opened += 1
        try:
            if self.failure is not None:
                raise self.failure
            yield self.gateway
        finally:
            self.closed += 1


class AgentDialogueTests(unittest.TestCase):
    def setUp(self):
        self.model = Model()
        self.gateway = Gateway()
        self.factory = Factory(self.gateway)
        self.service = AgentDialogueService(self.model, self.factory, clock=lambda: NOW)
        self.addCleanup(self.check_read_only)

    def check_read_only(self):
        self.assertEqual(self.gateway.writes, 0)
        self.assertEqual(self.factory.opened, self.factory.closed)

    def turn(self, message, memory=None, **values):
        self.model.plan = plan(**values)
        return self.service.respond(message, memory or QueryMemory())

    def pending_create(self):
        result, memory = self.turn('Criar uma entrevista.', action='create', title=PRIVATE_TITLE,
            description=PRIVATE_DESCRIPTION)
        self.assertEqual(result.status, 'needs_clarification')
        return memory

    def pending_candidates(self, count=2, **overrides):
        self.gateway.events = tuple(event(f'CandidateID{index}') for index in range(count))
        self.gateway.lookups = {item.event_id: item for item in self.gateway.events}
        return self.turn('Alterar as entrevistas de 17/06/2030.', action='edit', search_title='Entrevista',
            date='2030-06-17', location=PRIVATE_LOCATION, **overrides)

    def test_create_resumes_only_missing_fields_across_three_turns(self):
        previous = self.pending_create()
        self.assertEqual(previous.pending_request.awaiting, ('start', 'end'))
        middle, memory = self.turn('Dia 17 às 10h.', previous, mode='continue', start=START)
        self.assertEqual(middle.data['missing_fields'], ['end'])
        final, memory = self.turn('Termina às 10h30.', memory, mode='continue', end=END)
        self.assertEqual(final.status, 'ready')
        self.assertEqual(final.data['parameters'], {'title': PRIVATE_TITLE,
            'description': PRIVATE_DESCRIPTION, 'start': START, 'end': END, 'time_zone': ZONE})
        self.assertFalse(final.data['execution_allowed'])
        self.assertEqual(memory.pending_request.awaiting, ())
        self.assertEqual(self.factory.opened, 0)

    def test_known_create_date_is_preserved_until_times_complete_the_request(self):
        result, memory = self.turn('Marcar entrevista Nexus em 17 de junho de 2030.',
            action='create', title='Entrevista Nexus', date='2030-06-17')
        self.assertEqual(result.status, 'needs_clarification')
        self.assertEqual(result.data['missing_fields'], ['start', 'end'])
        self.assertEqual(memory.pending_request.fields['date'], '2030-06-17')
        result, final = self.turn('Das 10h às 10h30.', memory, mode='continue', start=START, end=END)
        self.assertEqual(result.status, 'ready')
        summary = json.loads(self.model.calls[-1]['instructions'].split('Resumo do pedido validado (JSON): ', 1)[1])
        self.assertEqual(summary['schedule']['date'], '2030-06-17')
        self.assertEqual(result.data['parameters']['title'], 'Entrevista Nexus')
        self.assertEqual(result.data['parameters']['start'], START)
        self.assertEqual(result.data['parameters']['end'], END)
        self.assertNotIn('date', final.pending_request.fields)
        self.assertEqual(self.factory.opened, 0)

    def test_invalid_create_date_is_correctable_without_opening_calendar(self):
        result, memory = self.turn('Marcar entrevista com data inválida.',
            action='create', title='Entrevista Nexus', date='2030-02-30')
        self.assertEqual(result.status, 'needs_correction')
        self.assertEqual(result.data['invalid_fields'], ['date'])
        self.assertEqual(memory.pending_request.fields['date'], '2030-02-30')
        result, final = self.turn('Corrigindo: 17 de junho de 2030, 10h às 10h30.', memory,
            mode='continue', date='2030-06-17', start=START, end=END)
        self.assertEqual(result.status, 'ready')
        self.assertEqual(final.pending_request.fields['title'], 'Entrevista Nexus')
        self.assertEqual(self.factory.opened, 0)

    def test_reschedule_date_remains_a_selector_and_is_not_removed_as_anchor(self):
        self.gateway.events = (event('RescheduleID'),)
        self.gateway.lookups['RescheduleID'] = self.gateway.events[0]
        result, final = self.turn('Reagendar a entrevista de 17/06/2030.', action='reschedule',
            search_title='Entrevista', date='2030-06-17', start=START, end=END)
        self.assertEqual(result.status, 'ready')
        self.assertEqual(result.data['parameters']['event_id'], 'RescheduleID')
        self.assertEqual([call[0] for call in self.gateway.calls], ['list', 'get'])

    def test_new_create_date_does_not_reuse_absolute_times_from_previous_ready_draft(self):
        result, memory = self.turn('Criar entrevista no dia 17 às 10h.', action='create',
            title=PRIVATE_TITLE, start=START, end=END)
        self.assertEqual(result.status, 'ready')
        result, memory = self.turn('Mude a data para 18 de junho.', memory,
            mode='continue', date='2030-06-18')
        self.assertEqual(result.status, 'needs_clarification')
        self.assertEqual(result.data['missing_fields'], ['start', 'end'])
        self.assertNotIn('start', memory.pending_request.fields)
        self.assertNotIn('end', memory.pending_request.fields)
        self.assertEqual(memory.pending_request.fields['date'], '2030-06-18')
        result, final = self.turn('Das 10h às 10h30.', memory, mode='continue',
            start='2030-06-18T10:00:00-03:00', end='2030-06-18T10:30:00-03:00')
        self.assertEqual(result.status, 'ready')
        self.assertEqual(result.data['parameters']['start'], '2030-06-18T10:00:00-03:00')
        self.assertEqual(final.pending_request.fields['title'], PRIVATE_TITLE)
        self.assertEqual(self.factory.opened, 0)

    def test_new_create_date_retains_only_time_fields_provided_in_current_patch(self):
        _, memory = self.turn('Criar entrevista.', action='create', title=PRIVATE_TITLE, start=START, end=END)
        result, memory = self.turn('Dia 18 às 11h.', memory, mode='continue', date='2030-06-18',
            start='2030-06-18T11:00:00-03:00')
        self.assertEqual(result.data['missing_fields'], ['end'])
        self.assertEqual(memory.pending_request.fields['start'], '2030-06-18T11:00:00-03:00')
        self.assertNotIn('end', memory.pending_request.fields)

    def test_ambiguous_create_date_invalidates_all_absolute_times_including_current_patch(self):
        _, memory = self.turn('Criar entrevista.', action='create', title=PRIVATE_TITLE, start=START, end=END)
        result, memory = self.turn('Dia 8/9, das 10h às 10h30.', memory, mode='continue',
            ambiguous_field='date', date='2030-09-08', start=START, end=END)
        self.assertEqual(result.data['reason'], 'ambiguous_information')
        for name in ('date', 'start', 'end'):
            self.assertNotIn(name, memory.pending_request.fields)
        self.assertEqual(memory.pending_request.fields['title'], PRIVATE_TITLE)
        self.assertEqual(self.factory.opened, 0)

    def test_invalid_interval_remains_correctable_without_losing_known_fields(self):
        result, memory = self.turn('Entrevista com horários invertidos.', action='create',
            title=PRIVATE_TITLE, start=END, end=START)
        self.assertEqual(result.status, 'needs_correction')
        self.assertEqual(result.data['invalid_fields'], ['start', 'end'])
        final, updated = self.turn('Corrigindo o início para 10h.', memory, mode='continue', start=START,
                                   end='2030-06-17T11:00:00-03:00')
        self.assertEqual(final.status, 'ready')
        self.assertEqual(updated.pending_request.fields['title'], PRIVATE_TITLE)
        self.assertEqual(self.factory.opened, 0)

    def test_invalid_zone_is_correctable_and_default_is_explicit(self):
        result, memory = self.turn('Criar.', action='create', title='Entrevista', start=START,
            end=END, time_zone='Invalid/SecretZone')
        self.assertEqual(result.status, 'needs_correction')
        self.assertEqual(result.data['invalid_fields'], ['time_zone'])
        result, updated = self.turn('Use Recife.', memory, mode='continue', time_zone=ZONE)
        self.assertEqual(result.status, 'ready')
        self.assertEqual(updated.pending_request.fields['time_zone'], ZONE)

    def test_ambiguous_information_asks_fixed_question_and_preserves_draft(self):
        memory = self.pending_create()
        self.model.plan = plan('ask_dialogue', reason='ambiguous_information', field='start')
        result, updated = self.service.respond('Às 8.', memory)
        self.assertEqual(result.data['question'], 'Qual é a data e o horário exatos de início?')
        self.assertEqual(updated.pending_request.fields, memory.pending_request.fields)
        self.assertEqual(updated.pending_request.awaiting, ('start',))
        self.assertNotIn('8', updated.pending_request.fields)
        self.assertEqual(self.factory.opened, 0)

    def test_initial_ambiguity_preserves_action_and_title_for_next_answer(self):
        result, memory = self.turn('Marcar entrevista Nexus sexta às 8.', action='create',
            title='Entrevista Nexus', ambiguous_field='start')
        self.assertEqual(result.status, 'needs_clarification')
        self.assertEqual(result.data['reason'], 'ambiguous_information')
        self.assertEqual(memory.pending_request.fields,
            {'action': 'create', 'title': 'Entrevista Nexus', 'time_zone': ZONE})
        self.assertEqual(memory.pending_request.awaiting, ('start',))
        self.assertEqual(self.factory.opened, 0)
        result, final = self.turn('17 de junho de 2030, das 10h às 10h30.', memory,
            mode='continue', start=START, end=END)
        self.assertEqual(result.status, 'ready')
        self.assertEqual(final.pending_request.fields['title'], 'Entrevista Nexus')
        self.assertEqual(final.pending_request.fields['action'], 'create')
        self.assertEqual(self.factory.opened, 0)

    def test_ambiguous_field_discards_old_and_new_value_without_losing_others(self):
        result, memory = self.turn('Criar entrevista completa.', action='create', title=PRIVATE_TITLE,
            start=START, end=END)
        self.assertEqual(result.status, 'ready')
        result, updated = self.turn('Mude para às 8.', memory, mode='continue',
            start='2030-06-17T08:00:00-03:00', ambiguous_field='start')
        self.assertEqual(result.data['reason'], 'ambiguous_information')
        self.assertNotIn('start', updated.pending_request.fields)
        self.assertEqual(updated.pending_request.fields['end'], END)
        self.assertEqual(updated.pending_request.fields['title'], PRIVATE_TITLE)
        self.assertEqual(updated.pending_request.awaiting, ('start',))
        self.assertEqual(self.factory.opened, 0)

    def test_ambiguous_zone_cannot_silently_be_replaced_by_default_on_next_turn(self):
        result, memory = self.turn('Criar entrevista com fuso incerto.', action='create',
            title=PRIVATE_TITLE, start=START, end=END, time_zone='UTC', ambiguous_field='time_zone')
        self.assertNotIn('time_zone', memory.pending_request.fields)
        self.assertEqual(result.data['reason'], 'ambiguous_information')
        result, memory = self.turn('Título atualizado.', memory, mode='continue', title='Novo título')
        self.assertEqual(result.data['missing_fields'], ['time_zone'])
        self.assertNotIn('time_zone', memory.pending_request.fields)
        self.assertEqual(self.factory.opened, 0)
        result, final = self.turn('Use Recife.', memory, mode='continue', time_zone=ZONE)
        self.assertEqual(result.status, 'ready')
        self.assertEqual(final.pending_request.fields['title'], 'Novo título')

    def test_ambiguous_continue_without_patch_replaces_awaiting_field(self):
        memory = self.pending_create()
        result, updated = self.turn('Não sei dizer se é manhã ou noite.', memory,
            mode='continue', ambiguous_field='start')
        self.assertEqual(result.data['reason'], 'ambiguous_information')
        self.assertEqual(updated.pending_request.fields, memory.pending_request.fields)
        self.assertEqual(updated.pending_request.awaiting, ('start',))

    def test_additional_question_does_not_forget_explicitly_unresolved_zone(self):
        result, memory = self.turn('Criar entrevista, o fuso é incerto.', action='create',
            title=PRIVATE_TITLE, start=START, end=END, ambiguous_field='time_zone')
        result, memory = self.turn('Também não sei se o início é manhã ou noite.', memory,
            mode='continue', ambiguous_field='start')
        self.assertEqual(memory.pending_request.awaiting, ('start', 'time_zone'))
        result, memory = self.turn('Início às 10h.', memory, mode='continue', start=START)
        self.assertEqual(result.status, 'needs_clarification')
        self.assertEqual(result.data['missing_fields'], ['time_zone'])
        self.assertNotIn('time_zone', memory.pending_request.fields)
        self.assertEqual(self.factory.opened, 0)

    def test_summary_omits_previous_sensitive_values_and_raw_messages(self):
        fields = {'action': 'edit', 'title': PRIVATE_TITLE, 'description': PRIVATE_DESCRIPTION,
            'location': PRIVATE_LOCATION, 'event_id': 'PrivateGoogleID', 'time_zone': ZONE}
        memory = QueryMemory(selected_event_id='PrivateReadID', selected_time_zone=ZONE,
                             pending_request=PendingRequest(fields, ('changes',)))
        self.model.plan = plan('ask_dialogue', reason='unclear_answer', field='changes')
        self.service.respond('Outra coisa.', memory)
        instructions = self.model.calls[-1]['instructions']
        for value in (PRIVATE_TITLE, PRIVATE_DESCRIPTION, PRIVATE_LOCATION, 'PrivateGoogleID', 'PrivateReadID'):
            self.assertNotIn(value, instructions)
        summary = json.loads(instructions.split('Resumo do pedido validado (JSON): ', 1)[1])
        self.assertEqual(summary['known_fields'], sorted(fields))
        self.assertEqual(summary['pending_action'], 'edit')
        self.assertTrue(summary['has_selected_event'])
        self.assertEqual(summary['schedule'], {'time_zone': ZONE})
        self.assertIn('Data civil de referência: 2030-06-16', instructions)

    def test_invalid_temporal_text_never_enters_summary(self):
        memory = QueryMemory(pending_request=PendingRequest({'action': 'create',
            'start': 'PRIVATE_MALFORMED_TIME', 'time_zone': 'Invalid/PRIVATE_ZONE',
            'date': 'PRIVATE_DATE', 'period': 'PRIVATE_PERIOD'}))
        self.model.plan = plan('ask_dialogue', reason='ambiguous_information', field='start')
        self.service.respond('Corrigir.', memory)
        instructions = self.model.calls[-1]['instructions']
        self.assertNotIn('PRIVATE_', instructions)

    def test_multiple_requests_preserve_original_and_ask_one_at_a_time(self):
        memory = self.pending_create()
        self.model.plan = plan('ask_dialogue', reason='multiple_requests', field='request')
        result, updated = self.service.respond('Crie e apague outra.', memory)
        self.assertIn('um pedido por vez', result.message)
        self.assertEqual(updated.pending_request.fields, memory.pending_request.fields)

    def test_new_intent_explicit_start_replaces_previous_draft(self):
        memory = self.pending_create()
        result, updated = self.turn('Consultar junho.', memory, mode='start', action='list',
            date='2030-06-17', period='month')
        self.assertEqual(result.status, 'ready')
        self.assertEqual(updated.pending_request.fields['action'], 'list')
        self.assertNotIn('title', updated.pending_request.fields)
        self.assertEqual(self.factory.opened, 0)

    def test_continue_different_action_does_not_overwrite_previous_draft(self):
        memory = self.pending_create()
        result, updated = self.turn('Excluir isso.', memory, mode='continue', action='delete')
        self.assertEqual(result.status, 'needs_clarification')
        self.assertEqual(result.data['reason'], 'ambiguous_information')
        self.assertEqual(updated.pending_request.fields, memory.pending_request.fields)

    def test_empty_continuation_and_acknowledgements_do_not_authorize_or_invent(self):
        memory = self.pending_create()
        for text in ('Sim', 'ok', 'confirmo', 'pode fazer', 'Continue'):
            with self.subTest(text=text):
                result, updated = self.turn(text, memory, mode='continue', action='create')
                self.assertEqual(result.status, 'needs_clarification')
                self.assertEqual(result.data['reason'], 'unclear_answer')
                self.assertEqual(updated.pending_request.fields, memory.pending_request.fields)
                self.assertFalse(result.data['execution_allowed'])
        self.assertEqual(self.factory.opened, 0)

    def test_ack_even_with_hallucinated_patch_is_only_a_question(self):
        memory = self.pending_create()
        result, updated = self.turn('sim', memory, mode='continue', start=START, end=END)
        self.assertEqual(result.status, 'needs_clarification')
        self.assertEqual(updated.pending_request.fields, memory.pending_request.fields)

    def test_continue_without_pending_asks_without_google(self):
        result, memory = self.turn('Às 10h.', mode='continue', start=START)
        self.assertEqual(result.data['reason'], 'missing_information')
        self.assertNotIn('start', memory.pending_request.fields)
        self.assertEqual(self.factory.opened, 0)

    def test_cancel_discards_only_pending_preserving_read_context(self):
        read = QueryMemory(last_list={'date': '2030-06-17', 'period': 'day', 'time_zone': ZONE},
            selected_event_id='ReadSelectionID', selected_time_zone=ZONE,
            pending_request=self.pending_create().pending_request)
        self.model.plan = plan('cancel_request')
        result, updated = self.service.respond('Cancelar pedido.', read)
        self.assertEqual(result.status, 'cancelled')
        self.assertIsNone(updated.pending_request)
        self.assertEqual(updated.last_list, read.last_list)
        self.assertEqual(updated.selected_event_id, read.selected_event_id)
        self.assertFalse(result.data['execution_allowed'])

    def test_two_candidates_require_choice_and_only_then_fresh_get(self):
        first, memory = self.pending_candidates()
        self.assertEqual(first.status, 'needs_clarification')
        self.assertEqual(first.data['reason'], 'event_selection')
        self.assertEqual(len(first.data['candidates']), 2)
        self.assertEqual([call[0] for call in self.gateway.calls], ['list'])
        self.model.plan = plan('select_candidate', choice=2)
        final, updated = self.service.respond('a segunda', memory)
        self.assertEqual(final.status, 'ready')
        self.assertEqual(final.data['parameters']['event_id'], 'CandidateID1')
        self.assertEqual(self.gateway.calls[-1], ('get', 'CandidateID1', ZONE))
        self.assertEqual(updated.pending_request.fields['location'], PRIVATE_LOCATION)
        self.assertEqual(updated.pending_request.candidates, ())

    def test_changed_search_cannot_reuse_candidates_from_previous_search(self):
        _, memory = self.pending_candidates()
        result, memory = self.turn('Procure outra entrevista, com término incerto.', memory,
            mode='continue', search_title='Outra entrevista', ambiguous_field='end')
        self.assertEqual(result.data['reason'], 'ambiguous_information')
        self.assertEqual(memory.pending_request.candidates, ())
        self.assertEqual(memory.pending_request.fields['search_title'], 'Outra entrevista')
        self.model.plan = plan('select_candidate', choice=2)
        result, memory = self.service.respond('2', memory)
        self.assertEqual(result.status, 'needs_clarification')
        self.assertEqual(result.data['reason'], 'missing_information')
        self.assertEqual([call[0] for call in self.gateway.calls], ['list'])

    def test_selector_ambiguity_and_changed_zone_discard_old_candidate_references(self):
        _, previous = self.pending_candidates()
        variants = (
            {'ambiguous_field': 'date', 'search_title': 'Outra entrevista'},
            {'ambiguous_field': 'period'},
            {'ambiguous_field': 'time_zone'},
            {'ambiguous_field': 'end', 'time_zone': 'UTC'},
            {'ambiguous_field': 'end', 'period': 'week'},
            {'ambiguous_field': 'end', 'date': '2030-06-18'},
        )
        for changes in variants:
            with self.subTest(changes=changes):
                message = ('Procure na semana de 17/06/2030.' if changes.get('period') == 'week'
                           else 'Procure em 18/06/2030.' if changes.get('date') == '2030-06-18'
                           else 'Uma informação mudou.')
                result, updated = self.turn(message, previous, mode='continue', **changes)
                self.assertEqual(result.status, 'needs_clarification')
                self.assertEqual(updated.pending_request.candidates, ())
        for field in ('event', 'date', 'period', 'time_zone', 'action'):
            with self.subTest(ask_field=field):
                self.model.plan = plan('ask_dialogue', reason='ambiguous_information', field=field)
                result, updated = self.service.respond('Essa informação ficou incerta.', previous)
                self.assertEqual(updated.pending_request.candidates, ())
        self.assertEqual([call[0] for call in self.gateway.calls], ['list'])

    def test_new_intent_does_not_inherit_candidate_references_even_when_search_matches(self):
        _, previous = self.pending_candidates()
        result, updated = self.turn('Agora quero reagendar a entrevista.', previous, mode='start',
            action='reschedule', search_title='Entrevista', date='2030-06-17', ambiguous_field='end')
        self.assertEqual(result.status, 'needs_clarification')
        self.assertEqual(updated.pending_request.candidates, ())
        self.assertEqual([call[0] for call in self.gateway.calls], ['list'])

    def test_candidate_references_survive_only_a_question_with_unchanged_selection(self):
        _, previous = self.pending_candidates()
        result, updated = self.turn('Ainda preciso escolher o novo título.', previous,
            mode='continue', ambiguous_field='title')
        self.assertEqual(result.status, 'needs_clarification')
        self.assertEqual(updated.pending_request.candidates, previous.pending_request.candidates)

    def test_old_read_selection_cannot_bypass_current_candidate_options(self):
        _, pending = self.pending_candidates()
        memory = replace(pending, selected_event_id='OldReadID', selected_time_zone=ZONE)
        self.gateway.lookups['OldReadID'] = event('OldReadID')
        result, updated = self.turn('Use esse evento.', memory, mode='continue', use_selected_event=True)
        self.assertEqual(result.status, 'needs_clarification')
        self.assertEqual(result.data['reason'], 'event_selection')
        self.assertEqual(updated.pending_request.candidates, pending.pending_request.candidates)
        self.assertEqual(updated.pending_request.fields, pending.pending_request.fields)
        self.assertEqual([call[0] for call in self.gateway.calls], ['list'])
        result, updated = self.turn('Começar outro pedido com esse evento.', memory,
            mode='start', action='delete', use_selected_event=True)
        self.assertEqual(result.status, 'ready')
        self.assertEqual(result.data['parameters']['event_id'], 'OldReadID')
        self.assertEqual(self.gateway.calls[-1], ('get', 'OldReadID', ZONE))

    def test_confirmed_missing_read_selection_clears_read_identity_and_preserves_last_period(self):
        memory = QueryMemory(last_list={'date': '2030-06-17', 'period': 'day', 'time_zone': ZONE},
            selected_event_id='MissingReadID', selected_time_zone=ZONE)
        result, updated = self.turn('Editar esse evento.', memory, action='edit',
            use_selected_event=True, location=PRIVATE_LOCATION)
        self.assertEqual(result.data['reason'], 'event_not_found')
        self.assertIsNone(updated.selected_event_id)
        self.assertIsNone(updated.selected_time_zone)
        self.assertEqual(updated.last_list, memory.last_list)
        self.assertNotIn('event_id', updated.pending_request.fields)
        self.assertEqual(updated.pending_request.fields['location'], PRIVATE_LOCATION)
        self.assertEqual(self.gateway.calls, [('get', 'MissingReadID', ZONE)])

    def test_candidate_selection_rejects_time_and_unoffered_number_without_google(self):
        _, memory = self.pending_candidates()
        for message, choice in (('Às 2h', 2), ('amanhã às 10h', 1), ('segunda', 2), ('opção 3', 3), ('a primeira', 2)):
            with self.subTest(message=message):
                self.model.plan = plan('select_candidate', choice=choice)
                result, updated = self.service.respond(message, memory)
                self.assertEqual(result.data['reason'], 'unclear_answer')
                self.assertEqual(updated.pending_request.candidates, memory.pending_request.candidates)
        self.assertEqual([call[0] for call in self.gateway.calls], ['list'])

    def test_changed_candidate_requires_new_selection_then_fresh_get(self):
        _, memory = self.pending_candidates()
        self.gateway.lookups['CandidateID1'] = event('CandidateID1', title='Entrevista alterada')
        self.model.plan = plan('select_candidate', choice=2)
        result, updated = self.service.respond('opção 2', memory)
        self.assertEqual(result.data['reason'], 'event_changed')
        self.assertEqual(result.status, 'needs_clarification')
        self.assertEqual(len(updated.pending_request.candidates), 1)
        self.assertNotIn('event_id', updated.pending_request.fields)
        self.model.plan = plan('select_candidate', choice=1)
        result, final = self.service.respond('1', updated)
        self.assertEqual(result.status, 'ready')
        self.assertEqual(final.pending_request.fields['event_id'], 'CandidateID1')
        self.assertEqual([call[0] for call in self.gateway.calls], ['list', 'get', 'get'])

    def test_missing_selected_candidate_clears_identity_but_retains_changes(self):
        _, memory = self.pending_candidates()
        del self.gateway.lookups['CandidateID0']
        self.model.plan = plan('select_candidate', choice=1)
        result, updated = self.service.respond('1', memory)
        self.assertEqual(result.data['reason'], 'event_not_found')
        self.assertEqual(updated.pending_request.candidates, ())
        self.assertNotIn('event_id', updated.pending_request.fields)
        self.assertNotIn('search_title', updated.pending_request.fields)
        self.assertEqual(updated.pending_request.fields['location'], PRIVATE_LOCATION)

    def test_no_matches_asks_without_selecting_and_over_limit_does_not_expose_options(self):
        for count, reason in ((0, 'event_not_found'), (21, 'too_many_candidates')):
            with self.subTest(count=count):
                result, memory = self.pending_candidates(count=count)
                self.assertEqual(result.data['reason'], reason)
                self.assertEqual(result.data['candidates'], [])
                self.assertEqual(memory.pending_request.candidates, ())
                self.assertNotIn('event_id', memory.pending_request.fields)

    def test_exact_id_has_to_appear_in_current_message(self):
        with self.assertRaises(ModelError) as raised:
            self.turn('Editar o evento.', action='edit', event_id='InventedID', location='Novo local')
        self.assertEqual(raised.exception.code, 'llm_invalid_response')
        self.assertEqual(self.factory.opened, 0)

    def test_literal_id_is_freshly_read_with_no_write(self):
        self.gateway.lookups['LiteralID'] = event('LiteralID')
        result, memory = self.turn('Excluir LiteralID.', action='delete', event_id='LiteralID')
        self.assertEqual(result.status, 'ready')
        self.assertEqual(result.action, 'delete')
        self.assertEqual(self.gateway.calls, [('get', 'LiteralID', ZONE)])
        self.assertFalse(result.data['execution_allowed'])
        self.assertNotIn('confirmed', memory.pending_request.fields)

    def test_selected_read_identity_resolved_locally_and_reread(self):
        self.gateway.lookups['LocalReadID'] = event('LocalReadID')
        read = QueryMemory(selected_event_id='LocalReadID', selected_time_zone=ZONE)
        result, updated = self.turn('Editar esse compromisso.', read, action='edit',
            use_selected_event=True, location='Novo local')
        self.assertEqual(result.status, 'ready')
        self.assertEqual(result.data['parameters']['event_id'], 'LocalReadID')
        self.assertEqual(self.gateway.calls, [('get', 'LocalReadID', ZONE)])
        self.assertNotIn('LocalReadID', self.model.calls[-1]['instructions'])
        self.assertEqual(updated.selected_event_id, read.selected_event_id)

    def test_missing_selected_context_preserves_patch_and_asks_which_event(self):
        result, memory = self.turn('Editar esse compromisso.', action='edit',
            use_selected_event=True, location='Novo local')
        self.assertEqual(result.data['missing_fields'], ['event'])
        self.assertEqual(memory.pending_request.fields['location'], 'Novo local')
        self.assertEqual(self.factory.opened, 0)

    def test_competing_id_and_search_are_not_resolved_automatically(self):
        result, memory = self.turn('Editar LiteralID da entrevista.', action='edit',
            event_id='LiteralID', search_title='Entrevista', date='2030-06-17', location='Novo local')
        self.assertEqual(result.status, 'needs_correction')
        self.assertIn('event_id', result.data['invalid_fields'])
        self.assertEqual(self.factory.opened, 0)

    def test_incomplete_update_validates_before_opening_calendar(self):
        result, memory = self.turn('Reagendar ExactID para às 10h.', action='reschedule',
            event_id='ExactID', start=START)
        self.assertEqual(result.data['missing_fields'], ['end'])
        self.assertEqual(self.factory.opened, 0)

    def test_operational_failures_preserve_memory_and_hide_diagnostics(self):
        memory = self.pending_create()
        for error, expected_status in ((RuntimeError('PRIVATE_DIAGNOSTIC'), 502),
                (CalendarSetupError('PRIVATE_SETUP_DIAGNOSTIC'), 503)):
            with self.subTest(error=type(error).__name__):
                self.factory.failure = error
                result, updated = self.turn('Editar ExactID.', memory, mode='start', action='edit',
                    event_id='ExactID', location='Novo local')
                self.assertEqual(result.status, 'failed')
                self.assertEqual(result.http_status, expected_status)
                self.assertEqual(updated, memory)
                self.assertNotIn('PRIVATE_', str(result))

    def test_model_failure_is_propagated_without_touching_calendar_or_memory(self):
        memory = self.pending_create()
        self.model.failure = ModelError('llm_timeout', http_status=504)
        with self.assertRaises(ModelError):
            self.service.respond('Completar depois.', memory)
        self.assertEqual(memory.pending_request.fields['title'], PRIVATE_TITLE)
        self.assertEqual(self.factory.opened, 0)

    def test_two_conversations_do_not_share_pending_drafts(self):
        first = self.pending_create()
        result, second = self.turn('Consultar um dia.', action='list')
        self.assertEqual(second.pending_request.fields['action'], 'list')
        self.assertNotIn('title', second.pending_request.fields)
        self.assertEqual(first.pending_request.fields['title'], PRIVATE_TITLE)

    def test_injected_plan_validation_rejects_extra_types_and_metadata(self):
        good = plan(action='create', title='Teste')
        variants = [replace(good, name='delete_event'), replace(good, model='PRIVATE MODEL'),
            replace(good, input_tokens=True), replace(good, arguments={**good.arguments, 'execute': True}),
            replace(good, arguments={**good.arguments, 'mode': 'approve'}),
            replace(good, arguments={**good.arguments, 'use_selected_event': 1}),
            replace(good, arguments={**good.arguments, 'title': 'x' * 4001}),
            plan('select_candidate', choice=True), plan('select_candidate', choice=0),
            plan('ask_dialogue', reason='PRIVATE_REASON', field='event')]
        for invalid in variants:
            with self.subTest(invalid=invalid):
                self.model.plan = invalid
                with self.assertRaises(ModelError):
                    self.service.respond('Olá.', QueryMemory())
        self.assertEqual(self.factory.opened, 0)

    def test_output_validator_rejects_mutation_flags_and_unexpected_data(self):
        result, _ = self.turn('Consultar junho.', action='list', date='2030-06-17', period='month')
        self.assertIs(validate_dialogue_result(result), result)
        for modified in ({**result.data, 'execution_allowed': True}, {**result.data, 'token': 'private'},
                         {**result.data, 'parameters': {**result.data['parameters'], 'execute': 'true'}},
                         {**result.data, 'event': {'event_id': 'invented'}}):
            with self.subTest(data=modified):
                with self.assertRaises(ModelError):
                    validate_dialogue_result(replace(result, data=modified))


if __name__ == '__main__':
    unittest.main()
