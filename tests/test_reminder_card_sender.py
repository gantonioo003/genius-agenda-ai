"""Cards automáticos com render/publicação/transporte fictícios; sem rede."""

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from app.reminder_card_sender import ReminderCardSender
from app.services.reminders import ReminderCalendarEvent
from app.whatsapp_sandbox_sender import SandboxSendError


NOW = datetime(2030, 6, 19, 12, tzinfo=timezone.utc)
ZONE = 'America/Recife'
LINK = 'https://example.com/card.png'
FINGERPRINT = 'a' * 64


class ReminderCardSenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.event = ReminderCalendarEvent('event-fictitious', 'Entrevista fictícia',
            NOW + timedelta(hours=1), NOW + timedelta(hours=2), False, 'confirmed')
        self.transport = Mock()
        self.transport.send.return_value = FINGERPRINT
        self.transport.send_image.return_value = FINGERPRINT
        self.publisher = Mock()
        self.publisher.publish.return_value = LINK
        self.renderer = Mock(return_value=Path(self.temp.name) / 'fake.png')
        self.sender = ReminderCardSender(self.transport, publisher=self.publisher,
            output_dir=self.temp.name, renderer=self.renderer)

    def prepare(self, **kwargs):
        values = dict(calendar_timezone=ZONE, offset_minutes=60, now=NOW)
        values.update(kwargs)
        return self.sender.prepare_reminder(self.event, **values)

    def send_prepared(self, prepared, **kwargs):
        values = dict(event=self.event, calendar_timezone=ZONE, offset_minutes=60, now=NOW)
        values.update(kwargs)
        return self.sender.send_prepared('5511999990000', 'Lembrete factual', prepared, **values)

    def test_prepare_renders_and_publishes_but_never_transports(self):
        prepared = self.prepare()
        self.assertIsNotNone(prepared)
        self.renderer.assert_called_once()
        self.publisher.publish.assert_called_once_with(self.renderer.return_value, now=NOW)
        self.transport.send.assert_not_called()
        self.transport.send_image.assert_not_called()
        self.assertNotIn(self.event.title, repr(prepared))
        self.assertEqual(prepared.expires_at, NOW + timedelta(minutes=10))
        self.assertEqual(self.send_prepared(prepared), FINGERPRINT)
        self.transport.send_image.assert_called_once_with('5511999990000', LINK, caption='Lembrete factual')

    def test_fifteen_minutes_keeps_text_without_render_or_publish(self):
        self.assertIsNone(self.prepare(offset_minutes=15))
        self.assertEqual(self.send_prepared(None, offset_minutes=15), FINGERPRINT)
        self.renderer.assert_not_called()
        self.publisher.publish.assert_not_called()
        self.transport.send.assert_called_once()
        self.transport.send_image.assert_not_called()

    def test_changed_title_start_end_or_zone_never_uses_stale_card(self):
        prepared = self.prepare()
        changes = ({'event': replace(self.event, title='Outro título')},
                   {'event': replace(self.event, start_utc=self.event.start_utc + timedelta(minutes=1))},
                   {'event': replace(self.event, end_utc=self.event.end_utc + timedelta(minutes=1))},
                   {'calendar_timezone': 'America/Fortaleza'})
        for changed in changes:
            with self.subTest(changed=tuple(changed)):
                self.assertEqual(self.send_prepared(prepared, **changed), FINGERPRINT)
        self.assertEqual(self.transport.send.call_count, 4)
        self.transport.send_image.assert_not_called()

    def test_expiry_and_clock_reversal_use_text(self):
        prepared = self.prepare()
        self.send_prepared(prepared, now=NOW + timedelta(minutes=10))
        self.send_prepared(prepared, now=NOW - timedelta(seconds=1))
        self.assertEqual(self.transport.send.call_count, 2)
        self.transport.send_image.assert_not_called()

    def test_render_or_hosting_failures_happen_before_transport_and_fall_back(self):
        self.renderer.side_effect = RuntimeError('render failed')
        self.assertIsNone(self.prepare())
        self.renderer.side_effect = None
        self.publisher.publish.side_effect = RuntimeError('hosting failed')
        self.assertIsNone(self.prepare())
        self.send_prepared(None)
        self.transport.send.assert_called_once()
        self.transport.send_image.assert_not_called()

    def test_safe_image_rejection_allows_one_text_fallback(self):
        prepared = self.prepare()
        self.transport.send_image.side_effect = SandboxSendError(uncertain=False)
        self.assertEqual(self.send_prepared(prepared), FINGERPRINT)
        self.transport.send_image.assert_called_once()
        self.transport.send.assert_called_once()

    def test_uncertain_image_rejection_propagates_without_text(self):
        prepared = self.prepare()
        self.transport.send_image.side_effect = SandboxSendError(uncertain=True)
        with self.assertRaises(SandboxSendError):
            self.send_prepared(prepared)
        self.transport.send.assert_not_called()

    def test_arbitrary_transport_error_propagates_without_fallback(self):
        prepared = self.prepare()
        self.transport.send_image.side_effect = RuntimeError('unknown outcome')
        with self.assertRaises(RuntimeError):
            self.send_prepared(prepared)
        self.transport.send.assert_not_called()

    def test_send_delegates_to_existing_transport(self):
        self.assertEqual(self.sender.send('5511999990000', 'Texto'), FINGERPRINT)
        self.transport.send.assert_called_once_with('5511999990000', 'Texto')


class ReminderCardFactoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Isolar a função sem importar Settings ou abrir configurações reais.
        path = Path(__file__).resolve().parents[1] / 'app/api_reminder_delivery.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        node = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == 'default_reminder_sender')
        namespace = {'PROJECT_ROOT': self.root}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
        self.factory = namespace['default_reminder_sender']
        self.base = Mock()
        self.publisher = Mock()
        media = ModuleType('app.card_media_store')
        media.CardMediaPublisher = self.publisher
        rendering = ModuleType('app.interview_card')
        rendering.render_interview_card = Mock()
        self.modules = {'app.card_media_store': media, 'app.interview_card': rendering}

    def config(self, enabled):
        return SimpleNamespace(reminder_cards_enabled=enabled, d360_api_key='fictitious')

    def test_disabled_factory_never_reads_private_configuration(self):
        with patch('app.whatsapp_sandbox_sender.SandboxSender', return_value=self.base), \
                patch.object(Path, 'read_text', side_effect=AssertionError('unexpected read')):
            self.assertIs(self.factory(self.config(False)), self.base)

    def test_enabled_factory_accepts_public_origin_and_private_output_locations(self):
        secrets = self.root / 'secrets'
        secrets.mkdir()
        (secrets / 'card-media-runtime.json').write_text(json.dumps({'origin': 'https://example.com'}), encoding='utf-8')
        with patch('app.whatsapp_sandbox_sender.SandboxSender', return_value=self.base), \
                patch.dict(sys.modules, self.modules):
            sender = self.factory(self.config(True))
        self.assertIsInstance(sender, ReminderCardSender)
        self.publisher.assert_called_once_with(cache_root=self.root / 'data/cards/published',
                                               origin='https://example.com', ttl_seconds=600, verify_public=True)
        self.assertEqual(sender._output, self.root / 'data/cards/rendered')
        self.assertEqual(sender._template, self.root / 'assets/cards/interview-template-v1.json')

    def test_invalid_origin_preserves_text_transport(self):
        secrets = self.root / 'secrets'
        secrets.mkdir()
        (secrets / 'card-media-runtime.json').write_text(json.dumps({'origin': 'http://127.0.0.1:8787'}), encoding='utf-8')
        with patch('app.whatsapp_sandbox_sender.SandboxSender', return_value=self.base), \
                patch.dict(sys.modules, self.modules):
            self.assertIs(self.factory(self.config(True)), self.base)
        self.publisher.assert_not_called()


if __name__ == '__main__':
    unittest.main()
