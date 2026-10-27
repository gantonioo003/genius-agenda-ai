"""Gemini escolhe a operação; o servidor resolve o nome sem exigir a data antiga."""
from dataclasses import replace
from datetime import datetime, timezone
import unittest
import test_agent_dialogue as legacy
from app.conversation_store import QueryMemory
from app.services.agenda import CalendarListing
from app.services.agent_dialogue import AgentDialogueService

TITLE='Entrevista Portfólio'
START='2026-10-28T10:00:00-03:00'
END='2026-10-28T10:30:00-03:00'

class SearchGateway(legacy.Gateway):
    def search_events_by_title(self, title, *, time_zone):
        self.calls.append(('search',title,time_zone))
        if self.failure is not None:
            raise self.failure
        return CalendarListing('Calendário fictício',self.events)

class TitleOnlyDialogueTests(unittest.TestCase):
    def setUp(self):
        self.model=legacy.Model()
        self.gateway=SearchGateway()
        self.factory=legacy.Factory(self.gateway)
        self.service=AgentDialogueService(self.model,self.factory,
            clock=lambda:datetime(2026,10,27,12,tzinfo=timezone.utc))
        self.event=legacy.event('ColdTitleFixture',title=TITLE,
            start='2026-10-29T15:00:00-03:00',end='2026-10-29T15:30:00-03:00')
        self.install(self.event)
        self.addCleanup(self.check_read_only)

    def install(self,*events):
        self.gateway.events=events
        self.gateway.lookups={event.event_id:event for event in events}

    def check_read_only(self):
        self.assertEqual(self.gateway.writes,0)
        self.assertEqual(self.factory.opened,self.factory.closed)

    def test_gemini_decides_every_operation_before_global_title_selection(self):
        cases=[('delete','Poderia fazer a exclusão de '+TITLE+'?',{}),
               ('edit','Quero mudar o local de '+TITLE+' para Sala B.',{'location':'Sala B'}),
               ('reschedule','Remarca '+TITLE+' para 28/10/2026 das 10h às 10h30.',{'start':START,'end':END})]
        for action,message,changes in cases:
            with self.subTest(action=action):
                self.gateway.calls.clear(); self.model.calls.clear()
                self.model.plan=legacy.plan(action=action,search_title=TITLE,**changes)
                result,_=self.service.respond(message,QueryMemory())
                self.assertEqual((result.status,result.action),('ready',action))
                self.assertEqual(len(self.model.calls),1)
                self.assertEqual([call[0] for call in self.gateway.calls],['search','get'])
                self.assertEqual(result.data['parameters']['event_id'],self.event.event_id)
                self.assertFalse(result.data['execution_allowed'])

    def test_old_query_and_selected_event_never_hide_same_title_on_other_dates(self):
        other=replace(self.event,event_id='DifferentDayFixture',start=START,end=END)
        self.install(self.event,other)
        memory=QueryMemory(last_list={'date':'2026-10-29','period':'day','time_zone':legacy.ZONE},
                           selected_event_id=self.event.event_id,selected_time_zone=legacy.ZONE)
        self.model.plan=legacy.plan(action='delete',search_title=TITLE,date='2026-10-29',period='day')
        result,_=self.service.respond('Apagar '+TITLE+'.',memory)
        self.assertEqual(result.data['reason'],'event_selection')
        self.assertEqual(len(result.data['candidates']),2)
        self.assertEqual([call[0] for call in self.gateway.calls],['search'])

    def test_explicit_named_delete_overrides_selected_hint_but_not_literal_id(self):
        other=replace(self.event,event_id='DifferentDayFixture',start=START,end=END)
        self.install(self.event,other)
        memory=QueryMemory(selected_event_id=self.event.event_id,selected_time_zone=legacy.ZONE)
        self.model.plan=legacy.plan(action='delete',use_selected_event=True)
        result,_=self.service.respond('Exclua '+TITLE+'.',memory)
        self.assertEqual(result.data['reason'],'event_selection')
        self.assertEqual(len(result.data['candidates']),2)
        self.gateway.calls.clear()
        self.model.plan=legacy.plan(action='delete',event_id=self.event.event_id)
        result,_=self.service.respond('Exclua '+self.event.event_id,QueryMemory())
        self.assertEqual(result.status,'ready')
        self.assertEqual([call[0] for call in self.gateway.calls],['get'])

    def test_weekday_in_title_does_not_create_hidden_date_filter(self):
        first=replace(self.event,title='Reunião de segunda')
        self.install(first,replace(first,event_id='AnotherWeekdayFixture',start=START,end=END))
        self.model.plan=legacy.plan(action='edit',search_title=first.title,date='2026-10-29',
                                   period='day',location='Sala B')
        result,_=self.service.respond('Atualiza o local de Reunião de segunda para Sala B.',QueryMemory())
        self.assertEqual(result.data['reason'],'event_selection')
        self.assertEqual([call[0] for call in self.gateway.calls],['search'])

    def test_missing_new_end_asks_end_without_asking_original_date(self):
        self.model.plan=legacy.plan(action='reschedule',search_title=TITLE,start=START)
        result,_=self.service.respond('Muda '+TITLE+' para 28/10/2026 às 10h.',QueryMemory())
        self.assertEqual(result.data['missing_fields'],['end'])
        self.assertEqual(self.gateway.calls,[])

    def test_explicit_user_source_date_still_filters(self):
        self.model.plan=legacy.plan(action='delete',search_title=TITLE,date='2026-10-29',period='day')
        result,_=self.service.respond('Quero remover '+TITLE+' de 29/10/2026.',QueryMemory())
        self.assertEqual(result.status,'ready')
        self.assertEqual([call[0] for call in self.gateway.calls],['list','get'])

    def test_name_not_found_asks_name_without_demanding_id_or_date(self):
        self.install()
        self.model.plan=legacy.plan(action='delete',search_title=TITLE)
        result,_=self.service.respond('Remover '+TITLE+'.',QueryMemory())
        self.assertEqual(result.data['reason'],'event_not_found')
        self.assertIsInstance(result.message,str)
        self.assertNotIn('identificador',result.message.lower())
        self.assertNotIn('data',result.message.lower())

if __name__=='__main__': unittest.main()
