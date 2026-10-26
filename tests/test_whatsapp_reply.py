"""Serviço e API de resposta com SQLite real temporário; sem conexão externa."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import json
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from app.database import Database
from app.main import create_app
from app.services.whatsapp_reply import ReplyService, ReplyUnavailable, TEST_REPLY, default_reply_service
from app.settings import Settings
from app.whatsapp_inbox import InboxEvent, InboxStore, make_event_key
from app.whatsapp_outbox import OutboxStore
from test_api_security import invoke_asgi


PHONE = '15555550123'
TOKEN = 'ApiTestToken_OnlyForTests_0123456789AbCdEf'
FINGERPRINT = 'a' * 64
ENDPOINT = '/api/v1/whatsapp/sandbox/reply'


class ReplyFixture(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inbox = InboxStore(self.root / 'inbox.sqlite3')
        self.outbox = OutboxStore(self.root / 'outbox.sqlite3')
        self.sender = Mock()
        self.sender.send.return_value = FINGERPRINT
        self.service = ReplyService(inbox=self.inbox, outbox=self.outbox, sender=self.sender, recipient=PHONE)

    def claim(self, *types):
        return self.inbox.claim(tuple(InboxEvent(make_event_key('message' if name.startswith('message_') else 'status', str(uuid4()), name), name) for name in types or ('message_text',)))


class ReplyServiceTests(ReplyFixture):
    def test_new_message_replies_once_and_pending_claim_replay_reuses(self):
        claim = self.claim()
        self.assertEqual(self.service.reply(claim.request_id).reply_state, 'accepted')
        replay = self.service.reply(claim.request_id)
        self.assertTrue(replay.reused)
        self.sender.send.assert_called_once_with(PHONE, TEST_REPLY)

    def test_status_only_does_not_open_outbox_or_send(self):
        result = self.service.reply(self.claim('status_read', 'status_delivered').request_id)
        self.assertEqual(result.reply_state, 'skipped')
        self.assertFalse(self.outbox.path.exists())
        self.sender.send.assert_not_called()

    def test_mixed_batch_multiple_messages_send_one_reply(self):
        claim = self.claim('message_text', 'message_image', 'status_read')
        self.service.reply(claim.request_id)
        self.sender.send.assert_called_once()

    def test_unknown_claim_does_not_reserve_or_send(self):
        with self.assertRaises(ReplyUnavailable) as caught:
            self.service.reply(str(uuid4()))
        self.assertEqual(caught.exception.http_status, 409)
        self.assertFalse(self.outbox.path.exists())
        self.sender.send.assert_not_called()

    def test_completed_inbox_cannot_start_new_reply(self):
        claim = self.claim()
        self.inbox.complete(claim)
        with self.assertRaises(ReplyUnavailable):
            self.service.reply(claim.request_id)
        self.sender.send.assert_not_called()

    def test_uncertain_inbox_cannot_start_reply(self):
        claim = self.claim()
        self.inbox.mark_uncertain(claim)
        with self.assertRaises(ReplyUnavailable):
            self.service.reply(claim.request_id)
        self.sender.send.assert_not_called()

    def test_lost_provider_response_remains_blocked_after_reopening_stores(self):
        claim = self.claim()
        self.sender.send.side_effect = TimeoutError('PRIVATE_ERROR')
        with self.assertRaises(ReplyUnavailable) as caught:
            self.service.reply(claim.request_id)
        self.assertTrue(caught.exception.uncertain)
        restarted = ReplyService(inbox=InboxStore(self.inbox.path), outbox=OutboxStore(self.outbox.path), sender=self.sender, recipient=PHONE)
        with self.assertRaises(ReplyUnavailable) as replay:
            restarted.reply(claim.request_id)
        self.assertEqual(replay.exception.code, 'reply_pending')
        self.assertNotIn('PRIVATE_ERROR', str(caught.exception))
        self.sender.send.assert_called_once()

    def test_accepted_reply_survives_service_restart_without_extra_send(self):
        claim = self.claim()
        self.service.reply(claim.request_id)
        restarted = ReplyService(inbox=InboxStore(self.inbox.path), outbox=OutboxStore(self.outbox.path), sender=self.sender, recipient=PHONE)
        self.assertTrue(restarted.reply(claim.request_id).reused)
        self.sender.send.assert_called_once()

    def test_invalid_send_confirmation_is_uncertain_and_blocked(self):
        claim = self.claim()
        self.sender.send.return_value = 'RAW_PRIVATE_PROVIDER_ID'
        with self.assertRaises(ReplyUnavailable):
            self.service.reply(claim.request_id)
        with self.assertRaises(ReplyUnavailable):
            self.service.reply(claim.request_id)
        self.sender.send.assert_called_once()
        data = self.outbox.path.read_bytes()
        self.assertNotIn(b'RAW_PRIVATE_PROVIDER_ID', data)
        self.assertNotIn(PHONE.encode(), data)
        self.assertNotIn(TEST_REPLY.encode(), data)

    def test_failed_accept_commit_does_not_allow_second_send(self):
        claim = self.claim()
        self.outbox.mark_accepted = Mock(side_effect=OSError('PRIVATE'))
        with self.assertRaises(ReplyUnavailable):
            self.service.reply(claim.request_id)
        with self.assertRaises(ReplyUnavailable):
            self.service.reply(claim.request_id)
        self.sender.send.assert_called_once()

    def test_parallel_api_commands_only_one_provider_call(self):
        claim = self.claim()
        entered, release = Event(), Event()
        def send(*_args):
            entered.set()
            self.assertTrue(release.wait(3))
            return FINGERPRINT
        self.sender.send.side_effect = send
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.service.reply, claim.request_id)
            self.assertTrue(entered.wait(3))
            try:
                with self.assertRaises(ReplyUnavailable):
                    pool.submit(self.service.reply, claim.request_id).result(3)
            finally:
                release.set()
            self.assertEqual(first.result(3).reply_state, 'accepted')
        self.sender.send.assert_called_once()

    def test_missing_key_does_not_load_private_policy(self):
        with patch('pathlib.Path.read_bytes') as private_read:
            with self.assertRaises(ReplyUnavailable):
                default_reply_service(Settings(_env_file=None, d360_api_key=None))
            private_read.assert_not_called()


class ReplyApiTests(ReplyFixture):
    def setUp(self):
        super().setUp()
        config = Settings(_env_file=None, api_auth_token=TOKEN, app_env='test')
        self.factory = Mock(return_value=self.service)
        self.app = create_app(config, Database(self.root / 'agenda.db'), sandbox_reply_service_factory=self.factory)

    def invoke(self, claim_id=None, *, headers=None, body=None, query=b''):
        claim_id = claim_id or str(uuid4())
        headers = headers if headers is not None else [(b'authorization', ('Bearer ' + TOKEN).encode()), (b'x-request-id', claim_id.encode()), (b'content-type', b'application/json')]
        body = json.dumps({'request_id': claim_id}).encode() if body is None else body
        return asyncio.run(invoke_asgi(self.app, ENDPOINT, method='POST', headers=headers, body=body, query=query))

    def test_api_accepts_message_claim_and_exposes_no_phone_or_provider_id(self):
        claim = self.claim()
        code, headers, body = self.invoke(claim.request_id)
        self.assertEqual(code, 200)
        self.assertEqual(headers['cache-control'], 'no-store')
        self.assertEqual(body['action'], 'whatsapp_test_reply')
        self.assertEqual(body['mutation_state'], 'confirmed')
        self.assertEqual(body['data'], {'authenticated': True, 'reply_state': 'accepted', 'reused': False})
        self.assertNotIn(PHONE, json.dumps(body))
        self.assertNotIn(FINGERPRINT, json.dumps(body))

    def test_api_status_only_skips_without_sending(self):
        code, _, body = self.invoke(self.claim('status_delivered').request_id)
        self.assertEqual(code, 200)
        self.assertEqual(body['data']['reply_state'], 'skipped')
        self.assertEqual(body['mutation_state'], 'not_applicable')
        self.sender.send.assert_not_called()

    def test_api_unauthorized_does_not_construct_service(self):
        code, _, _ = self.invoke(headers=[(b'content-type', b'application/json')], body=b'not-json')
        self.assertEqual(code, 401)
        self.factory.assert_not_called()

    def test_api_untrusted_input_is_rejected_before_service(self):
        claim_id = str(uuid4())
        invalid = [b'{}', b'{"request_id":123}', b'[]', b'not-json', json.dumps({'request_id': claim_id, 'to': PHONE}).encode(), json.dumps({'request_id': str(uuid4())}).encode(), ('{"request_id":"' + claim_id + '","request_id":"' + claim_id + '"}').encode(), b' ' * 1025]
        for body in invalid:
            with self.subTest(body_type=len(body)):
                self.assertEqual(self.invoke(claim_id, body=body)[0], 422)
        self.factory.assert_not_called()

    def test_api_query_is_rejected_before_service(self):
        self.assertEqual(self.invoke(query=b'force=true')[0], 422)
        self.factory.assert_not_called()

    def test_api_unknown_claim_never_sends(self):
        code, _, body = self.invoke()
        self.assertEqual(code, 409)
        self.assertFalse(body['error']['retryable'])
        self.sender.send.assert_not_called()

    def test_api_failure_does_not_leak_exception_and_no_retry(self):
        self.sender.send.side_effect = OSError('PRIVATE_ERROR_' + PHONE)
        claim = self.claim()
        code, _, body = self.invoke(claim.request_id)
        self.assertEqual(code, 502)
        self.assertEqual(body['mutation_state'], 'unknown')
        self.assertNotIn(PHONE, json.dumps(body))
        self.assertFalse(body['error']['retryable'])
        self.assertEqual(self.invoke(claim.request_id)[0], 409)
        self.sender.send.assert_called_once()


if __name__ == '__main__':
    unittest.main()
