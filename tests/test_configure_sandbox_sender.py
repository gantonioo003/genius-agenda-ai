"""Fixtures de telefone ficticias; nenhuma leitura da configuracao real."""

import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import warnings
import io
from contextlib import redirect_stdout, redirect_stderr


path = Path(__file__).resolve().parents[1] / 'scripts/configure_sandbox_sender.py'
spec = importlib.util.spec_from_file_location('configure_sandbox_sender', path)
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)
PHONE = '15555550123'
OTHER = '15555550124'


class ConfigureSandboxSenderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.policy = self.root / configure.POLICY_FILE

    def test_formats_input_once_and_stores_only_canonical_private_phone(self):
        self.assertEqual(configure.configure_sender(self.root, '+1 (555) 555-0123'), 'created')
        self.assertEqual(json.loads(self.policy.read_text()), {'allowed_phone': PHONE})
        self.assertEqual([p for p in self.root.rglob('*') if p.is_file()], [self.policy])

    def test_same_phone_is_idempotent_and_preserves_file_bytes(self):
        configure.configure_sender(self.root, PHONE)
        original = self.policy.read_bytes()
        self.assertEqual(configure.configure_sender(self.root, '+1 555 555 0123'), 'unchanged')
        self.assertEqual(self.policy.read_bytes(), original)

    def test_different_phone_is_not_silently_replaced(self):
        configure.configure_sender(self.root, PHONE)
        original = self.policy.read_bytes()
        with self.assertRaises(ValueError) as error:
            configure.configure_sender(self.root, OTHER)
        self.assertNotIn(PHONE, str(error.exception))
        self.assertNotIn(OTHER, str(error.exception))
        self.assertEqual(self.policy.read_bytes(), original)

    def test_invalid_inputs_do_not_create_policy_or_reveal_input(self):
        cases = [None, True, 15555550123, '', '0' * 12, '1' * 16, '123456',
                 '1e555550123', '１5555550123', PHONE + '\n', '\t' + PHONE,
                 '++' + PHONE, 'file://' + PHONE, '+1555<script>', '9' * 100]
        for value in cases:
            with self.subTest(kind=type(value).__name__):
                with self.assertRaises(ValueError) as error:
                    configure.configure_sender(self.root, value)
                if isinstance(value, str) and len(value) >= 7:
                    self.assertNotIn(value, str(error.exception))
                self.assertFalse(self.policy.exists())

    def test_malformed_existing_policy_is_preserved_and_rejected(self):
        self.policy.parent.mkdir(parents=True)
        cases = [b'null', b'[]', b'{}', b'{', b'{"allowed_phone":123}',
                 b'{"allowed_phone":"+15555550123"}',
                 b'{"allowed_phone":"15555550123","extra":true}',
                 b'{"allowed_phone":"15555550123","allowed_phone":"15555550123"}']
        for value in cases:
            with self.subTest(length=len(value)):
                self.policy.write_bytes(value)
                with self.assertRaises(ValueError) as error:
                    configure.configure_sender(self.root, PHONE)
                self.assertNotIn(PHONE, str(error.exception))
                self.assertEqual(self.policy.read_bytes(), value)

    def test_main_stops_before_reading_unhidden_input(self):
        def unavailable(_prompt):
            warnings.warn('Hidden input unavailable', configure.getpass.GetPassWarning)
            raise AssertionError('Fallback input must never run')
        out, err = io.StringIO(), io.StringIO()
        with patch.object(configure.sys, 'argv', ['configure', '--apply']), \
             patch.object(configure.getpass, 'getpass', side_effect=unavailable), \
             patch.object(configure, 'configure_sender') as write, \
             redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(ValueError) as error:
                configure.main()
        write.assert_not_called()
        self.assertEqual(str(error.exception), 'Use um terminal com entrada oculta disponivel.')
        self.assertEqual(out.getvalue() + err.getvalue(), '')

    def test_main_success_never_prints_the_phone(self):
        out = io.StringIO()
        with patch.object(configure.sys, 'argv', ['configure', '--apply', '--project-root', str(self.root)]), \
             patch.object(configure.getpass, 'getpass', return_value=PHONE), redirect_stdout(out):
            configure.main()
        self.assertNotIn(PHONE, out.getvalue())
        self.assertEqual(json.loads(self.policy.read_text()), {'allowed_phone': PHONE})


if __name__ == '__main__':
    unittest.main()
