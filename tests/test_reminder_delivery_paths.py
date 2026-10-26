"""Percurso callback→janela→planejamento→API→ledger com provedores fictícios."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from tempfile import TemporaryDirectory
import unittest

from app.main import create_app
from app.reminder_store import ReminderStore
from app.sandbox_webhook import create_app as create_receiver
from app.services.reminder_delivery import ReminderRecipient
from app.services.reminder_sync import ReminderEventLookup, ReminderLookupSnapshot
from app.services.reminders import ReminderCalendarEvent, ReminderCalendarSnapshot
from app.settings import Settings
from app.whatsapp_sandbox_sender import SandboxSendError
from test_api_security import InjectedDatabase, TOKEN, invoke_asgi
from test_sandbox_webhook import CONFIG, PHONE, CALLBACK, payload, invoke


class ReminderDeliveryPathsTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.store=ReminderStore(self.root/'reminders.sqlite3')
        self.binding=hmac.new(CALLBACK.encode(),b'genius-whatsapp-agent-sender:v1\0'+PHONE.encode(),sha256).hexdigest()
        self.store.configure('calendar-fixture',self.binding,enabled=True,permission_granted=True)
        self.event=ReminderCalendarEvent('event-fixture','Entrevista fictícia',
            datetime.now(timezone.utc)+timedelta(minutes=15,seconds=-20),
            datetime.now(timezone.utc)+timedelta(minutes=45),False,'confirmed')
        self.outcome='found'
        self.attempts=[]
        self.lost_response=False
        outer=self
        class Gateway:
            def snapshot(self,*args,**kwargs):
                return ReminderCalendarSnapshot('calendar-fixture','America/Recife',(outer.event,))
            def lookup_snapshot(self,ids,**kwargs):
                event=outer.event if outer.outcome=='found' else None
                return ReminderLookupSnapshot('calendar-fixture','America/Recife',
                    tuple(ReminderEventLookup(identity,outer.outcome,event) for identity in ids))
        @contextmanager
        def gateway(): yield Gateway()
        self.gateway=gateway
        class Sender:
            def send(self,recipient,text):
                states=outer.store.list_reminders()
                outer.assertEqual(sum(record.state=='sending' for record in states),1)
                outer.attempts.append((recipient,text))
                if outer.lost_response: raise SandboxSendError(uncertain=True)
                return 'e'*64
        self.sender=Sender()
        self.api=self.application()
        def forward(summary):
            return {'status':'received','api_reachable':True,'request_id':summary['request_id'],
                    'reply_state':'accepted' if summary['message_count'] else 'skipped'}
        self.receiver=create_receiver(CONFIG,allowed_sender=PHONE,forward=forward,
            receipts_path=self.root/'receipts.jsonl',diagnostics_path=self.root/'ingress.jsonl',
            inbox_path=self.root/'inbox.sqlite3',reminder_store=self.store)

    def application(self):
        return create_app(Settings(_env_file=None,api_auth_token=TOKEN,app_env='test'),InjectedDatabase(),
            reminder_store_factory=lambda:self.store,reminder_gateway_factory=self.gateway,
            reminder_recipient_binding_loader=lambda:self.binding,
            reminder_delivery_recipient_loader=lambda:ReminderRecipient(PHONE,self.binding),
            reminder_delivery_sender_factory=lambda:self.sender)

    def request(self,operation):
        result=asyncio.run(invoke_asgi(self.api,'/api/v1/reminders/'+operation,method='POST',body=b'{}',
            headers=[(b'content-type',b'application/json'),(b'authorization',f'Bearer {TOKEN}'.encode()),
                     (b'x-request-id',b'path-fixture-1')]))
        public=json.dumps(result)
        for secret in (PHONE,self.binding,self.event.event_id,self.event.title,CALLBACK,TOKEN):
            self.assertNotIn(secret,public)
        return result[0],result[2]

    def inbound(self,*,age=timedelta(),identity='new-channel-message'):
        value=payload(); message=value['entry'][0]['changes'][0]['value']['messages'][0]
        message['id']=identity
        message['timestamp']=str(int((datetime.now(timezone.utc)-age).timestamp()))
        return asyncio.run(invoke(self.receiver,json.dumps(value).encode()))

    def test_full_route_accepts_once_and_fresh_process_objects_do_not_repeat(self):
        self.assertEqual(self.inbound()[0],200)
        self.assertEqual(self.request('tick')[0],200)
        code,body=self.request('dispatch')
        self.assertEqual((code,body['mutation_state'],body['data']['accepted_count']),(200,'confirmed',1))
        self.assertEqual(self.attempts[0][0],PHONE)
        self.assertIn('Sua entrevista começa em 15 minutos.\nEntrevista fictícia\n',self.attempts[0][1])
        local=self.event.start_utc.astimezone(ZoneInfo('America/Recife'))
        clock=str(local.hour)+'h'+(f'{local.minute:02d}' if local.minute or local.second or local.microsecond else '')
        self.assertIn(local.strftime('%d/%m/%Y')+' às '+clock,self.attempts[0][1])
        self.assertNotIn('Horário de Recife.',self.attempts[0][1])
        self.store=ReminderStore(self.store.path); self.api=self.application()
        self.request('tick')
        self.assertEqual(self.request('dispatch')[1]['data']['transport_attempts'],0)
        self.assertEqual(len(self.attempts),1)

    def test_unknown_window_blocks_then_new_authorized_inbound_allows_same_identity(self):
        self.request('tick')
        result=self.request('dispatch')[1]
        self.assertEqual((result['data']['blocked_count'],result['data']['transport_attempts']),(1,0))
        key=next(record.key for record in self.store.list_reminders() if record.state=='blocked')
        self.inbound()
        self.assertEqual(self.request('dispatch')[1]['data']['accepted_count'],1)
        self.assertEqual(self.store.get(key).state,'accepted')

    def test_expired_inbound_and_improved_duplicate_cannot_authorize_transport(self):
        self.inbound(age=timedelta(hours=25))
        self.request('tick')
        self.assertEqual(self.request('dispatch')[1]['data']['blocked_count'],1)
        self.inbound()
        self.assertEqual(self.request('dispatch')[1]['data']['blocked_count'],1)
        self.assertEqual(self.attempts,[])

    def test_cancel_after_planning_prevents_sender_call(self):
        self.inbound(); self.request('tick'); self.outcome='cancelled'
        result=self.request('dispatch')[1]['data']
        self.assertEqual((result['cancelled_count'],result['transport_attempts']),(1,0))
        self.assertEqual(self.attempts,[])

    def test_lost_provider_reply_stays_uncertain_across_replanning_and_restart(self):
        self.inbound(); self.request('tick'); self.lost_response=True
        code,body=self.request('dispatch')
        self.assertEqual((code,body['mutation_state'],body['data']['uncertain_count']),(503,'unknown',1))
        self.store=ReminderStore(self.store.path); self.api=self.application(); self.lost_response=False
        self.request('tick'); self.request('dispatch')
        self.assertEqual(len(self.attempts),1)
        self.assertEqual(sum(record.state=='uncertain' for record in self.store.list_reminders()),1)


if __name__=='__main__': unittest.main()
