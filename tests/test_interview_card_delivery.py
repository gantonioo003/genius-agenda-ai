"""Transporte fictício e ledger SQLite real; nenhuma rede ou configuração privada."""

from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest
from unittest.mock import Mock
from uuid import uuid4

from app.interview_card_delivery import send_interview_card
from app.whatsapp_probe_ledger import ProbeLedger, ProbeLedgerError
from app.whatsapp_sandbox_sender import SandboxSendError


RECIPIENT = '5511999990000'
LINK = 'https://example.com/card.png'
TEXT = 'Entrevista fictícia - 15h. Boa preparação!'
FINGERPRINT = 'a' * 64


class InterviewCardDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'delivery.sqlite3'
        self.ledger = ProbeLedger(self.path)
        self.sender = Mock()
        self.sender.send_image.return_value = FINGERPRINT
        self.sender.send.return_value = FINGERPRINT
        self.operation = str(uuid4())

    def run_delivery(self, **overrides):
        values = dict(recipient=RECIPIENT, image_link=LINK, text=TEXT,
                      sender=self.sender, ledger=self.ledger)
        values.update(overrides)
        return send_interview_card(self.operation, **values)

    def states(self):
        with closing(sqlite3.connect(self.path)) as db:
            return dict(db.execute('SELECT state,COUNT(*) FROM probe_operations GROUP BY state'))

    def test_image_success_is_reserved_once_and_replay_does_not_send(self):
        result = self.run_delivery()
        self.assertEqual((result.state, result.channel, result.reused), ('accepted', 'image', False))
        self.sender.send_image.assert_called_once_with(RECIPIENT, LINK, caption=TEXT)
        self.sender.send.assert_not_called()
        repeated = self.run_delivery(ledger=ProbeLedger(self.path))
        self.assertEqual((repeated.state, repeated.channel, repeated.reused), ('accepted', None, True))
        self.sender.send_image.assert_called_once()
        self.assertEqual(self.states(), {'accepted': 1})

    def test_preflight_image_failure_sends_text_without_image_attempt(self):
        result = self.run_delivery(image_link=None)
        self.assertEqual((result.state, result.channel), ('accepted', 'text'))
        self.sender.send_image.assert_not_called()
        self.sender.send.assert_called_once_with(RECIPIENT, TEXT)

    def test_safe_image_failure_allows_one_text_fallback_and_replay_stays_reserved(self):
        self.sender.send_image.side_effect = SandboxSendError(uncertain=False)
        result = self.run_delivery()
        self.assertEqual((result.state, result.channel), ('accepted', 'text'))
        self.sender.send.assert_called_once_with(RECIPIENT, TEXT)
        self.assertTrue(self.run_delivery().reused)
        self.sender.send_image.assert_called_once()
        self.sender.send.assert_called_once()

    def test_uncertain_image_failure_never_falls_back_or_retries(self):
        self.sender.send_image.side_effect = SandboxSendError(uncertain=True)
        result = self.run_delivery()
        self.assertEqual((result.state, result.channel), ('uncertain', 'image'))
        self.sender.send.assert_not_called()
        self.assertEqual(self.states(), {'uncertain': 1})
        self.assertTrue(self.run_delivery().reused)
        self.sender.send_image.assert_called_once()

    def test_arbitrary_error_after_image_attempt_is_uncertain_not_safe_fallback(self):
        self.sender.send_image.side_effect = RuntimeError('PRIVATE_DIAGNOSTIC')
        result = self.run_delivery()
        self.assertEqual(result.state, 'uncertain')
        self.sender.send.assert_not_called()
        self.assertNotIn('PRIVATE', repr(result))
        self.assertEqual(self.states(), {'uncertain': 1})

    def test_failed_text_fallback_is_blocked_and_cannot_repeat(self):
        self.sender.send_image.side_effect = SandboxSendError(uncertain=False)
        self.sender.send.side_effect = SandboxSendError(uncertain=False)
        result = self.run_delivery()
        self.assertEqual((result.state, result.channel), ('failed', 'text'))
        self.assertEqual(self.states(), {'uncertain': 1})
        self.assertEqual(self.run_delivery().state, 'uncertain')
        self.sender.send.assert_called_once()

    def test_same_operation_cannot_change_payload_or_recipient_and_ledger_keeps_only_hashes(self):
        self.run_delivery()
        for changed in ({'recipient': '5511999990001'}, {'text': TEXT + '!'},
                        {'image_link': None}, {'image_link': LINK + '?v=2'}):
            with self.subTest(changed=tuple(changed)), self.assertRaises(ProbeLedgerError):
                self.run_delivery(**changed)
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute('SELECT case_name,payload_fingerprint,message_id_fingerprint FROM probe_operations').fetchone()
        self.assertEqual(row[0], 'interview_card.v1')
        self.assertEqual(len(row[1]), 64)
        self.assertEqual(row[2], FINGERPRINT)
        saved = self.path.read_bytes()
        for private in (RECIPIENT, TEXT, LINK):
            self.assertNotIn(private.encode('utf-8'), saved)
        self.sender.send_image.assert_called_once()

    def test_invalid_acceptance_is_uncertain_without_second_send(self):
        self.sender.send_image.return_value = 'untrusted-provider-output'
        self.assertEqual(self.run_delivery().state, 'uncertain')
        self.sender.send.assert_not_called()
        self.assertTrue(self.run_delivery().reused)
        self.sender.send_image.assert_called_once()


if __name__ == '__main__':
    unittest.main()
