"""Configuração reproduzível e comando de ensaio sem acesso a provedor real."""

from contextlib import redirect_stdout
import importlib.util
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.llm import ModelReply
from app.settings import Settings


class ModelConfigurationAndCommandTests(unittest.TestCase):
    def load_command(self):
        path = Path(__file__).resolve().parents[1] / 'scripts/test_llm.py'
        spec = importlib.util.spec_from_file_location('llm_connection_command', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_documented_environment_values_load_from_dotenv_without_type_errors(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / '.env'
            path.write_text('OPENAI_API_KEY=\nLLM_MODEL=gpt-5-mini\nLLM_TIMEOUT_SECONDS=30\nLLM_MAX_OUTPUT_TOKENS=600\nLLM_MAX_INPUT_CHARS=4000\n', encoding='utf-8')
            config = Settings(_env_file=path)
        self.assertIsNone(config.openai_api_key)
        self.assertEqual(config.llm_max_output_tokens, 600)
        self.assertEqual(config.llm_max_input_chars, 4000)
        self.assertEqual(config.llm_timeout_seconds, 30)

    def test_default_preview_does_not_read_configuration_or_construct_model(self):
        command = self.load_command()
        output = StringIO()
        with patch('app.settings.Settings', side_effect=AssertionError('Configuration must not load.')), patch('app.services.agent_preview.default_agent_preview_service', side_effect=AssertionError('Model must not run.')), redirect_stdout(output):
            result = command.main([])
        self.assertEqual(result, 0)
        data = json.loads(output.getvalue())
        self.assertEqual(data['mode'], 'preview')
        self.assertEqual(data['network_requests'], 0)

    def test_execute_without_key_reports_safe_code_and_constructs_no_adapter(self):
        command = self.load_command()
        output = StringIO()
        config = Settings(_env_file=None, openai_api_key=None)
        with patch('app.settings.Settings', return_value=config), patch('app.services.agent_preview.OpenAIResponsesModel', side_effect=AssertionError('No network adapter without key.')), redirect_stdout(output):
            result = command.main(['--execute'])
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(output.getvalue()), {'status': 'failed', 'code': 'llm_not_configured', 'execution_allowed': False})

    def test_execute_calls_only_fixed_synthetic_message(self):
        command = self.load_command()
        output = StringIO()
        with patch('app.services.agent_preview.default_agent_preview_service') as factory, redirect_stdout(output):
            factory.return_value.preview.return_value = ModelReply('Informe a data e o horário.', 'fake-model')
            result = command.main(['--execute'])
        self.assertEqual(result, 0)
        factory.return_value.preview.assert_called_once_with(command.TEST_MESSAGE)
        data = json.loads(output.getvalue())
        self.assertFalse(data['execution_allowed'])
        self.assertEqual(data['calendar_operations'], 0)
        self.assertEqual(data['whatsapp_sends'], 0)

    def test_unexpected_configuration_error_does_not_echo_details(self):
        command = self.load_command()
        output = StringIO()
        with patch('app.settings.Settings', side_effect=RuntimeError('PRIVATE_CONFIGURATION_DETAIL')), redirect_stdout(output):
            result = command.main(['--execute'])
        self.assertEqual(result, 1)
        self.assertNotIn('PRIVATE_CONFIGURATION_DETAIL', output.getvalue())
        self.assertEqual(json.loads(output.getvalue())['code'], 'llm_unavailable')
