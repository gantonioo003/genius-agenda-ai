"""CLI do resumo: IA controlada, fatos e metadados sem texto privado."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
import unittest
from unittest.mock import Mock, patch

from app.llm import ModelError
from app.settings import Settings
from app.summarize_agenda import main
from test_agenda_summary_api import Model
from test_summary_context_api import Gateway, PRIVATE


class SummarizeAgendaCliTests(unittest.TestCase):
    def setUp(self):
        self.gateway = Gateway()
        self.model = Model()
        self.factory = Mock(return_value=self.model)
        self.config = Settings(_env_file=None, app_env='test')
        self.clock = lambda: datetime(2030, 6, 19, 12, tzinfo=timezone.utc)

    def invoke(self, args):
        output, error = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = main(args, gateway_factory=self.gateway.factory, model_factory=self.factory,
                config=self.config, clock=self.clock)
        return code, output.getvalue(), error.getvalue()

    def test_ai_result_contains_rendered_facts_without_ids(self):
        code, output, error = self.invoke(['--period', 'week', '--date', '2030-06-19'])
        self.assertEqual((code, error), (0, ''))
        value = json.loads(output)['result']
        self.assertEqual(value['summary']['mode'], 'ai_organized')
        self.assertIn('Entrevista fictícia', value['summary']['text'])
        self.assertNotIn('PrivateEventMixedID', output)

    def test_metadata_only_excludes_summary_text_and_titles(self):
        code, output, error = self.invoke(['--period', 'month', '--date', '2030-06-19', '--metadata-only'])
        self.assertEqual((code, error), (0, ''))
        value = json.loads(output)
        self.assertTrue(value['metadata_only'])
        self.assertNotIn('text', value['result']['summary'])
        self.assertNotIn('Entrevista fictícia', output)
        self.assertEqual(value['result']['totals']['events'], 1)

    def test_empty_agenda_skips_model(self):
        self.gateway.events = ()
        code, output, _ = self.invoke(['--period', 'week', '--metadata-only'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)['result']['summary']['reason'], 'empty_period')
        self.factory.assert_not_called()

    def test_invalid_input_prevents_calendar_and_model(self):
        for args in [['--period', 'day'], ['--period', 'month', '--date', PRIVATE]]:
            code, output, error = self.invoke(args)
            self.assertEqual((code, output), (1, ''))
            self.assertEqual(json.loads(error)['error']['code'], 'invalid_request')
            self.assertNotIn(PRIVATE, error)
        self.assertEqual(self.gateway.opened, 0)
        self.factory.assert_not_called()

    def test_calendar_failure_does_not_fabricate_summary(self):
        self.gateway.failure = RuntimeError(PRIVATE)
        code, output, error = self.invoke(['--period', 'week'])
        self.assertEqual((code, output), (1, ''))
        self.assertEqual(json.loads(error)['error']['code'], 'agenda_collection_failed')
        self.assertNotIn(PRIVATE, error)
        self.factory.assert_not_called()

    def test_missing_credentials_is_successful_factual_fallback(self):
        self.factory.side_effect = ModelError('llm_not_configured')
        code, output, error = self.invoke(['--period', 'week', '--date', '2030-06-19'])
        self.assertEqual((code, error), (0, ''))
        self.assertEqual(json.loads(output)['result']['summary']['reason'], 'llm_not_configured')

    def test_context_budget_skip_does_not_construct_model(self):
        self.config = Settings(_env_file=None, app_env='test', llm_max_input_chars=1)
        code, output, error = self.invoke(['--period', 'week', '--date', '2030-06-19'])
        self.assertEqual((code, error), (0, ''))
        self.assertEqual(json.loads(output)['result']['summary']['reason'], 'context_budget_exceeded')
        self.factory.assert_not_called()

    def test_default_factory_receives_config_without_changing_provider(self):
        output = StringIO()
        with patch('app.summarize_agenda.configured_language_model', return_value=self.model) as configured, redirect_stdout(output):
            code = main(['--period', 'week', '--date', '2030-06-19'],
                gateway_factory=self.gateway.factory, config=self.config, clock=self.clock)
        self.assertEqual(code, 0)
        configured.assert_called_once_with(self.config)
