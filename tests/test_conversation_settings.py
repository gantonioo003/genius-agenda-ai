"""Configuração de retenção pelo ambiente, sem credenciais reais."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from app.settings import Settings


class ConversationSettingsTests(unittest.TestCase):
    def test_defaults_are_private_relative_database_and_half_hour(self):
        with patch.dict(os.environ, {}, clear=True):
            config = Settings(_env_file=None)
        self.assertEqual(config.conversation_database_path, Path('data/agent-conversations.sqlite3'))
        self.assertEqual(config.conversation_ttl_seconds, 1800)

    def test_dotenv_loads_ascii_integer_and_custom_path(self):
        with TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True):
            env = Path(temporary) / '.env'
            env.write_text('CONVERSATION_TTL_SECONDS=60\nCONVERSATION_DATABASE_PATH=data/custom-context.sqlite3\n', encoding='utf-8')
            config = Settings(_env_file=env)
        self.assertEqual(config.conversation_ttl_seconds, 60)
        self.assertEqual(config.conversation_database_path, Path('data/custom-context.sqlite3'))

    def test_ttl_rejects_noninteger_values_and_outside_limits(self):
        with patch.dict(os.environ, {}, clear=True):
            for invalid in (0, 59, 86401, True, 1800.0, '1.0', '６０', '-60', 'invalid'):
                with self.subTest(kind=type(invalid).__name__), self.assertRaises(ValidationError):
                    Settings(_env_file=None, conversation_ttl_seconds=invalid)
            self.assertEqual(Settings(_env_file=None, conversation_ttl_seconds=86400).conversation_ttl_seconds, 86400)
