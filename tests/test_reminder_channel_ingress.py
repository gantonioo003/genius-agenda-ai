"""Ingressos autorizados: observação durável sem renovar janela por replay."""

import asyncio
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.reminder_store import ReminderStore
from app.sandbox_webhook import create_app, main, PROJECT_ROOT
from app.whatsapp_inbox import make_event_key
from test_sandbox_webhook import CONFIG, PHONE, OTHER_PHONE, CALLBACK, payload, status_payload, invoke


NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


class ReminderChannelIngressTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.now = NOW
        self.path = self.root/'reminders.sqlite3'
        self.store = ReminderStore(self.path, clock=lambda: self.now)
        self.binding = hmac.new(CALLBACK.encode(), b'genius-whatsapp-agent-sender:v1\0'+PHONE.encode(), hashlib.sha256).hexdigest()
        self.forwarded = []
        def forward(summary):
            self.forwarded.append(deepcopy(summary))
            return {'status':'received','api_reachable':True,'request_id':summary['request_id'],
                    'reply_state':'accepted' if summary['message_count'] else 'skipped'}
        self.forward = forward
        self.app = self.application()

    def application(self, **kw):
        return create_app(CONFIG, allowed_sender=PHONE, forward=kw.pop('forward',self.forward),
            receipts_path=self.root/'receipts.jsonl', diagnostics_path=self.root/'ingress.jsonl',
            inbox_path=self.root/'inbox.sqlite3', reminder_store=kw.pop('reminder_store',self.store),
            clock=kw.pop('clock',lambda:self.now), **kw)

    def value(self, *, identity='private-message-1', at=None, kind='text'):
        result = payload()
        event = result['entry'][0]['changes'][0]['value']['messages'][0]
        event['id'] = identity
        event['timestamp'] = str(int((self.now if at is None else at).timestamp()))
        if kind != 'text':
            del event['text']
            event.update(type=kind, **{kind:{'id':'private-media'}})
        return result

    def request(self, value=None, *, app=None, **kw):
        return asyncio.run(invoke(app or self.app, json.dumps(self.value() if value is None else value).encode(), **kw))

    def count(self):
        if not self.path.exists(): return 0
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute('SELECT count(*) FROM channel_observations').fetchone()[0]

    def observation(self): return self.store.last_inbound(self.binding)

    def test_original_provider_instant_persists_before_forward_without_local_backfill(self):
        old = self.now-timedelta(hours=3)
        def forward(summary):
            row = self.observation()
            self.assertEqual(row.message_at, old)
            self.assertEqual(row.received_at,self.now)
            return self.forward(summary)
        self.assertEqual(self.request(self.value(at=old),app=self.application(forward=forward))[0],200)
        row=self.observation()
        self.assertEqual(row.provenance,'sandbox_callback_capacity')
        self.assertTrue(row.valid)
        self.assertIsNone(self.store.config())

    def test_callback_does_not_enable_profile_or_infer_permission(self):
        self.store.configure('calendar-fixture',self.binding,enabled=False,permission_granted=False)
        previous=self.store.config()
        self.request()
        self.assertEqual(self.store.config(),previous)
        self.assertFalse(self.store.config().permission_granted)

    def test_duplicate_with_new_timestamp_does_not_extend_first_observation(self):
        first=self.now-timedelta(hours=2)
        self.request(self.value(at=first))
        self.now+=timedelta(hours=1)
        self.assertEqual(self.request()[0],200)
        self.assertEqual(self.observation().message_at,first)
        self.assertEqual(self.count(),1)
        self.assertEqual(len(self.forwarded),1)

    def test_out_of_order_new_message_does_not_replace_latest_original_instant(self):
        self.request(self.value())
        self.request(self.value(identity='old-arrives-later',at=self.now-timedelta(hours=3)))
        self.assertEqual(self.observation().message_at,NOW)
        self.assertEqual(self.count(),2)

    def test_future_timestamp_cannot_open_or_later_repair_same_identity(self):
        self.request(self.value(at=self.now+timedelta(seconds=1)))
        self.assertIsNone(self.observation())
        self.assertEqual(self.count(),1)
        self.now+=timedelta(hours=1)
        self.request()
        self.assertIsNone(self.observation())

    def test_out_of_range_unix_is_recorded_invalid_and_never_local_now(self):
        for i,stamp in enumerate(['0','99999999999999999999']):
            value=self.value(identity=f'invalid-{i}')
            value['entry'][0]['changes'][0]['value']['messages'][0]['timestamp']=stamp
            self.assertEqual(self.request(value)[0],200)
        self.assertEqual(self.count(),2)
        self.assertIsNone(self.observation())

    def test_missing_or_non_numeric_timestamp_rejects_entire_payload_before_store(self):
        for stamp in [None,'abc',1234,'1.5','-1']:
            value=self.value()
            value['entry'][0]['changes'][0]['value']['messages'][0]['timestamp']=stamp
            self.assertEqual(self.request(value)[0],400)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.forwarded,[])

    def test_outgoing_delivery_status_cannot_open_window(self):
        self.assertEqual(self.request(status_payload())[0],200)
        self.assertEqual(self.count(),0)

    def test_nontext_first_identity_stays_invalid_after_text_replay(self):
        self.assertEqual(self.request(self.value(kind='image'))[0],200)
        self.assertIsNone(self.observation())
        self.request(self.value())
        self.assertIsNone(self.observation())
        self.assertEqual(self.count(),1)

    def test_partial_duplicate_batch_observes_only_new_id(self):
        self.request(self.value())
        self.now+=timedelta(seconds=5)
        second=self.value(identity='private-message-2')
        messages=second['entry'][0]['changes'][0]['value']['messages']
        messages.append(self.value()['entry'][0]['changes'][0]['value']['messages'][0])
        self.request(second)
        self.assertEqual(self.count(),2)
        self.assertEqual(self.observation().message_at,self.now)
        self.assertEqual(self.forwarded[-1]['message_count'],1)

    def test_mixed_unauthorized_sender_rejects_all_without_observation(self):
        value=self.value()
        events=value['entry'][0]['changes'][0]['value']['messages']
        bad=deepcopy(events[0]); bad.update(id='bad-private-id',**{'from':OTHER_PHONE})
        events.append(bad)
        self.assertEqual(self.request(value)[0],403)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.forwarded,[])

    def test_invalid_capability_query_and_content_type_never_observe(self):
        for options,code in [({'path':'/webhooks/360dialog/not-valid'},404),
                             ({'query':b'probe=1'},400),({'content_type':b'text/plain'},415)]:
            self.assertEqual(self.request(**options)[0],code)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.forwarded,[])

    def test_observation_failure_blocks_forward_and_replay_remains_uncertain(self):
        with patch.object(self.store,'observe_inbound',side_effect=RuntimeError('PRIVATE_FAILURE')):
            self.assertEqual(self.request()[0],503)
        self.assertEqual(self.request()[0],503)
        self.assertEqual(self.forwarded,[])
        self.assertEqual(self.count(),0)
        self.assertNotIn('PRIVATE_FAILURE',(self.root/'ingress.jsonl').read_text())

    def test_forward_failure_preserves_real_observation_but_replay_never_renews(self):
        def failed(_): raise RuntimeError('private')
        self.assertEqual(self.request(app=self.application(forward=failed))[0],503)
        first=self.observation()
        self.now+=timedelta(hours=3)
        self.assertEqual(self.request()[0],503)
        self.assertEqual(self.observation(),first)
        self.assertEqual(self.count(),1)

    def test_restart_reads_original_window_without_new_observation_or_backfill(self):
        self.request()
        self.now+=timedelta(hours=25)
        self.store=ReminderStore(self.path,clock=lambda:self.now)
        self.app=self.application()
        self.assertEqual(self.request()[0],200)
        self.assertEqual(self.observation().message_at,NOW)
        self.assertEqual(self.count(),1)

    def test_store_contains_no_text_phone_raw_id_or_capability(self):
        value=self.value()
        self.request(value)
        raw=self.path.read_bytes().decode('latin-1')
        event=value['entry'][0]['changes'][0]['value']['messages'][0]
        for private in [event['id'],event['text']['body'],PHONE,CALLBACK]:
            self.assertNotIn(private,raw)
            self.assertNotIn(private,json.dumps(self.forwarded))
        self.assertNotIn(self.binding,json.dumps(self.forwarded))
        self.assertEqual(self.observation().event_key,make_event_key('message',event['id'],'message_text'))

    def test_invalid_clock_is_unavailable_before_claim_or_observation(self):
        for bad in [None,NOW.replace(tzinfo=None),'2026-10-26']:
            self.assertEqual(self.request(app=self.application(clock=lambda:bad))[0],503)
        self.assertFalse(self.path.exists())
        self.assertFalse((self.root/'inbox.sqlite3').exists())

    def test_default_factory_keeps_isolated_legacy_tests_disconnected(self):
        self.assertEqual(self.request(app=self.application(reminder_store=None))[0],200)
        self.assertFalse(self.path.exists())

    def test_runtime_resolves_same_custom_reminder_path_as_api(self):
        raw_policy=json.dumps({'allowed_phone':PHONE}).encode()
        for path in [Path('private/reminders.sqlite3'),self.root/'custom-reminders.sqlite3']:
            with patch.object(Path,'read_bytes',side_effect=[json.dumps(CONFIG).encode(),raw_policy]), \
                 patch('app.settings.Settings',return_value=SimpleNamespace(reminder_database_path=path)), \
                 patch('app.sandbox_webhook.ReminderStore') as store, \
                 patch('app.sandbox_webhook.create_app') as creator, patch('uvicorn.run'):
                main()
            store.assert_called_once_with(path if path.is_absolute() else PROJECT_ROOT/path)
            self.assertIs(creator.call_args.kwargs['reminder_store'],store.return_value)


if __name__=='__main__': unittest.main()
