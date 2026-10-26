"""Composição das novas consultas com serviços reais e fontes fictícias."""

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from app.conversation_store import ConversationStore, QueryMemory
from app.main import create_app
from app.preparation_chat_factory import default_preparation_chat_service
from app.services.summary_context import SummaryCalendarBinding, SummaryCalendarSnapshot
from app.settings import Settings
from app.whatsapp_agent_worker import default_whatsapp_agent_service
from test_api_security import TOKEN, InjectedDatabase
import test_interview_briefing as briefing_fixtures
import test_availability as availability_fixtures


class SummaryGateway:
    def __init__(self):
        self.closed = self.opened = 0
        self.selected_binding = SummaryCalendarBinding('fake-calendar', 'America/Recife')

    @contextmanager
    def factory(self):
        self.opened += 1
        try:
            yield self
        finally:
            self.closed += 1

    def binding(self):
        return self.selected_binding

    def list_events(self, period, *, expected_binding):
        return SummaryCalendarSnapshot(expected_binding, period, (), True, 1, 0)


class PreparationFactoryTests(unittest.TestCase):
    def setUp(self):
        self.summary = SummaryGateway()
        self.briefing = briefing_fixtures.BriefingFactory()
        self.available = availability_fixtures.FixtureGateway()
        self.model_factory = Mock(side_effect=AssertionError('An empty summary must not construct a model'))
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(config=Settings(_env_file=None, api_auth_token=TOKEN, app_env='test'),
            db=InjectedDatabase(), summary_calendar_gateway_factory=self.summary.factory,
            summary_model_factory=self.model_factory, briefing_gateway_factory=self.briefing,
            availability_gateway_factory=self.available.factory,
            conversation_store_factory=lambda: ConversationStore(Path(self.temp.name) / 'conversation.sqlite3'))
        self.service = default_preparation_chat_service(self.app)

    def test_actual_summary_services_empty_period_without_model(self):
        result = self.service.respond('Resume minha semana', memory=QueryMemory())
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual(result.action, 'summarize_agenda')
        self.assertTrue(result.text.strip())
        self.model_factory.assert_not_called()
        self.assertEqual(self.summary.opened, self.summary.closed)

    def test_actual_briefing_service_uses_selected_occurrence_and_generic_checklist(self):
        memory = QueryMemory(selected_event_id=briefing_fixtures.EVENT_ID, selected_time_zone=briefing_fixtures.ZONE)
        result = self.service.respond('Me prepara para essa entrevista. Contexto: Quero revisar APIs.', memory=memory)
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual(result.action, 'prepare_briefing')
        self.assertIn('Checklist sugerido', result.text)
        self.assertIn('Quero revisar APIs.', result.text)
        self.model_factory.assert_not_called()
        self.assertEqual(self.briefing.opened, self.briefing.closed)

    def test_actual_availability_service_is_rendered_as_unreserved_alternatives(self):
        result = self.service.respond('Horários livres 2030-06-19 das 09:00 às 12:00 por 30 minutos', memory=QueryMemory())
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual(result.action, 'find_free_slots')
        self.assertIn('9h às 9h30', result.text)
        self.assertIn('Nenhuma reserva', result.text)
        self.model_factory.assert_not_called()
        self.assertEqual(self.available.opened, self.available.closed)

    def test_default_worker_service_includes_preparation_without_new_schema_or_config(self):
        service = default_whatsapp_agent_service(self.app)
        turn = service.process('Resumo do mês')
        self.assertEqual(turn.status, 'succeeded')
        self.assertEqual(turn.action, 'summarize_agenda')
        self.assertEqual(turn.mutation_state, 'not_applicable')
        self.assertIsNone(turn.authorization_action)
        self.model_factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
