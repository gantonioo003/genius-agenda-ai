"""CLI de sincronização, dados fictícios e SQLite real, sem provedores."""

from contextlib import redirect_stdout
from datetime import datetime,timedelta,timezone
from hashlib import sha256
import io,json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.reminder_store import ReminderStore
from app.services.reminders import ReminderCalendarEvent
from app.services.reminder_sync import ReminderEventLookup,ReminderLookupSnapshot
from app.sync_reminders import main


class Gateway:
    def __init__(self,start):self.start=start;self.calls=0
    def lookup_snapshot(self,event_ids,expected_calendar_id):
        self.calls+=1
        if event_ids!=('fixture-event',) or expected_calendar_id!='fixture-calendar':
            raise AssertionError('unexpected scope')
        event=ReminderCalendarEvent('fixture-event','Entrevista atualizada',self.start,
            self.start+timedelta(minutes=30),False,'confirmed')
        return ReminderLookupSnapshot('fixture-calendar','America/Recife',(
            ReminderEventLookup('fixture-event','found',event),))


class ReminderSyncCliTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory(dir=Path(__file__).resolve().parents[1]);self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/'private.sqlite3';self.store=ReminderStore(self.path)
        self.start=datetime.now(timezone.utc)+timedelta(hours=3)
    def invoke(self):
        stream=io.StringIO()
        with redirect_stdout(stream):code=main(['--database',str(self.path)])
        return code,json.loads(stream.getvalue())
    def configure(self,**kwargs):
        return self.store.configure('fixture-calendar','a'*64,**kwargs)
    def seed(self):
        config=self.configure(enabled=True,permission_granted=True)
        for slot in ['preparation','imminent']:
            self.store.plan('fixture-event',self.start,slot,title='Entrevista anterior',
                calendar_timezone='America/Recife',config_revision=config.revision)
    def test_unconfigured_does_not_read_policy_or_open_google(self):
        with patch('app.sync_reminders.load_recipient_binding',side_effect=AssertionError('policy')), \
             patch('app.google_calendar.calendar_client',side_effect=AssertionError('Google')):
            code,result=self.invoke()
        self.assertEqual((code,result['status'],result['messages_sent']),(0,'not_configured',0))
    def test_disabled_does_not_read_policy(self):
        self.configure()
        with patch('app.sync_reminders.load_recipient_binding',side_effect=AssertionError('policy')):
            code,result=self.invoke()
        self.assertEqual((code,result['status']),(0,'disabled'))
    def test_permission_required_does_not_read_policy(self):
        self.configure(enabled=True)
        with patch('app.sync_reminders.load_recipient_binding',side_effect=AssertionError('policy')):
            code,result=self.invoke()
        self.assertEqual((code,result['status']),(0,'permission_required'))
    def test_active_empty_validates_binding_but_does_not_open_google(self):
        self.configure(enabled=True,permission_granted=True)
        with patch('app.sync_reminders.load_recipient_binding',return_value='a'*64), \
             patch('app.google_calendar.calendar_client',side_effect=AssertionError('Google')):
            code,result=self.invoke()
        self.assertEqual((code,result['status'],result['events_checked']),(0,'synchronized',0))
    def test_reassigned_binding_fails_even_when_no_active_reminders(self):
        config=self.configure(enabled=True,permission_granted=True)
        with patch('app.sync_reminders.load_recipient_binding',return_value='b'*64):
            code,result=self.invoke()
        self.assertEqual((code,result['status']),(1,'error'))
        self.assertEqual(self.store.config(),config)
    def test_cli_reschedules_then_repeat_does_not_duplicate(self):
        self.seed();gateway=Gateway(self.start+timedelta(hours=2))
        with patch('app.sync_reminders.load_recipient_binding',return_value='a'*64), \
             patch('app.sync_reminders._LiveLookup',return_value=gateway):
            code,result=self.invoke()
            self.assertEqual((code,result['status'],result['cancelled_count']),(0,'synchronized',2))
            self.assertEqual(result['state_counts'],{'cancelled':2,'pending':2})
            keys=tuple(record.key for record in self.store.list_reminders())
            code,repeated=self.invoke()
        self.assertEqual((code,repeated['cancelled_count'],repeated['replanned_count']),(0,0,0))
        self.assertEqual(tuple(record.key for record in self.store.list_reminders()),keys)
        self.assertEqual(gateway.calls,2)
        for value in ['fixture-event','fixture-calendar','Entrevista',str(self.path)]:
            self.assertNotIn(value,json.dumps(result))
        self.assertEqual(repeated['messages_sent'],0)
    def test_lookup_failure_is_sanitized_and_does_not_mutate_store(self):
        self.seed();before=sha256(self.path.read_bytes()).hexdigest()
        gateway=Gateway(self.start)
        with patch('app.sync_reminders.load_recipient_binding',return_value='a'*64), \
             patch('app.sync_reminders._LiveLookup',return_value=gateway), \
             patch.object(gateway,'lookup_snapshot',side_effect=RuntimeError('PRIVATE_PROVIDER_TOKEN')):
            code,result=self.invoke()
        self.assertEqual((code,result['status'],result['messages_sent']),(1,'error',0))
        self.assertEqual(sha256(self.path.read_bytes()).hexdigest(),before)
        self.assertNotIn('PRIVATE_PROVIDER_TOKEN',json.dumps(result))


if __name__=='__main__':unittest.main()
