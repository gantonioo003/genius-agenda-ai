"""Ensaios limitados com transporte substituído e ledger temporário real."""
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from app.sandbox_media_probe import IMAGE_CAPTION, IMAGE_URL, ProbeRunError, definition, preview, run_probe
from app.whatsapp_probe_ledger import ProbeLedger


PHONE = '15555550123'
OTHER = '15555550124'
FINGERPRINT = 'f' * 64


class MediaProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'probe.sqlite3'
        self.ledger = ProbeLedger(self.path)
        self.sender = Mock()
        self.sender.send_image.return_value = FINGERPRINT
        self.sender.send_template.return_value = FINGERPRINT

    def run_probe(self, kind='image', operation_id=None, phone=PHONE):
        return run_probe(kind, operation_id or str(uuid4()), recipient=phone, sender=self.sender, ledger=self.ledger)

    def test_preview_uses_fixed_public_definition_without_secret_or_network(self):
        result = preview('image', str(uuid4()))
        self.assertEqual(result['definition']['image']['link'], IMAGE_URL)
        self.assertFalse(result['execute'])
        self.assertEqual(result['network_requests'], 0)
        self.assertFalse(self.path.exists())
        self.assertNotIn(PHONE, json.dumps(result))
        self.sender.send_image.assert_not_called()

    def test_image_once_and_replay_after_restart_do_not_send_twice(self):
        operation = str(uuid4())
        self.assertFalse(self.run_probe(operation_id=operation).reused)
        self.ledger = ProbeLedger(self.path)
        self.assertTrue(self.run_probe(operation_id=operation).reused)
        self.sender.send_image.assert_called_once_with(PHONE, IMAGE_URL, IMAGE_CAPTION)
        self.sender.send_template.assert_not_called()

    def test_template_is_only_disclaimer_en_without_user_parameters(self):
        result = self.run_probe('template')
        self.assertEqual(result.case_name, 'template_disclaimer_v1')
        self.sender.send_template.assert_called_once_with(PHONE, 'disclaimer', 'en')
        self.sender.send_image.assert_not_called()

    def test_reusing_operation_for_other_kind_is_refused_before_send(self):
        operation = str(uuid4())
        self.run_probe('image', operation)
        with self.assertRaises(ProbeRunError):
            self.run_probe('template', operation)
        self.sender.send_image.assert_called_once()
        self.sender.send_template.assert_not_called()

    def test_changed_recipient_is_bound_and_cannot_reuse_acceptance(self):
        operation = str(uuid4())
        self.run_probe(operation_id=operation)
        with self.assertRaises(ProbeRunError):
            self.run_probe(operation_id=operation, phone=OTHER)
        self.sender.send_image.assert_called_once()

    def test_provider_timeout_blocks_retry_and_exposes_only_safe_state(self):
        operation = str(uuid4())
        self.sender.send_image.side_effect = TimeoutError('PRIVATE_' + PHONE)
        with self.assertRaises(ProbeRunError) as first:
            self.run_probe(operation_id=operation)
        self.assertTrue(first.exception.uncertain)
        self.assertNotIn(PHONE, str(first.exception))
        with self.assertRaises(ProbeRunError):
            self.run_probe(operation_id=operation)
        self.sender.send_image.assert_called_once()

    def test_http_rejection_records_only_numeric_status_and_no_retry(self):
        class Failure(Exception):
            http_status = 400
        self.sender.send_template.side_effect = Failure('PRIVATE_TEMPLATE_' + PHONE)
        with self.assertRaises(ProbeRunError) as caught:
            self.run_probe('template')
        self.assertEqual(caught.exception.http_status, 400)
        self.assertTrue(caught.exception.uncertain)
        self.assertNotIn(PHONE, str(caught.exception))

    def test_bad_confirmation_is_not_persisted_and_remains_blocked(self):
        operation = str(uuid4())
        self.sender.send_image.return_value = 'RAW_PRIVATE_ID'
        with self.assertRaises(ProbeRunError):
            self.run_probe(operation_id=operation)
        with self.assertRaises(ProbeRunError):
            self.run_probe(operation_id=operation)
        self.sender.send_image.assert_called_once()
        self.assertNotIn(b'RAW_PRIVATE_ID', self.path.read_bytes())

    def test_private_recipient_and_content_do_not_enter_ledger(self):
        self.run_probe()
        data = self.path.read_bytes()
        for text in (PHONE, IMAGE_URL, IMAGE_CAPTION):
            self.assertNotIn(text.encode('utf-8'), data)

    def test_accepted_provider_then_commit_failure_cannot_send_again(self):
        operation = str(uuid4())
        self.ledger.mark_accepted = Mock(side_effect=OSError('PRIVATE_COMMIT_FAILURE'))
        with self.assertRaises(ProbeRunError):
            self.run_probe(operation_id=operation)
        self.ledger = ProbeLedger(self.path)
        with self.assertRaises(ProbeRunError):
            self.run_probe(operation_id=operation)
        self.sender.send_image.assert_called_once()

    def test_invalid_inputs_do_not_create_ledger_or_send(self):
        for kind, operation, phone in [('video', str(uuid4()), PHONE), ('image', 'bad', PHONE), ('template', str(uuid4()), '+' + PHONE)]:
            with self.subTest(kind=kind, valid_operation=operation != 'bad'):
                with self.assertRaises(ProbeRunError):
                    self.run_probe(kind, operation, phone)
        self.assertFalse(self.path.exists())
        self.sender.send_image.assert_not_called()
        self.sender.send_template.assert_not_called()

    def test_modifying_preview_does_not_modify_the_fixed_case(self):
        sample = definition('template')
        sample['template']['name'] = 'arbitrary'
        self.assertEqual(definition('template')['template']['name'], 'disclaimer')

    def test_cli_preview_never_loads_settings_or_private_policy(self):
        from scripts.test_whatsapp_media import main
        with patch('sys.argv', ['test_whatsapp_media.py', '--kind', 'template', '--operation-id', str(uuid4())]), patch('pathlib.Path.read_bytes') as private_read, redirect_stdout(StringIO()) as output:
            self.assertEqual(main(), 0)
        private_read.assert_not_called()
        self.assertFalse(json.loads(output.getvalue())['execute'])


if __name__ == '__main__':
    unittest.main()
