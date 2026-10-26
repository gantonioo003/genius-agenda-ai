"""Percurso HTTP de recuperação antes de planejar/enviar, sem provedores reais."""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import unittest
from unittest.mock import patch

from app.reminder_store import ReminderStore,ReminderStoreError
from app.services.reminder_tick import reminder_tick_lock
import test_reminder_delivery_paths as fixtures


class ReminderRecoveryPathsTests(unittest.TestCase):
    def setUp(self):
        self.context=fixtures.ReminderDeliveryPathsTests('test_full_route_accepts_once_and_fresh_process_objects_do_not_repeat')
        self.context.setUp()
        self.addCleanup(self.context.doCleanups)

    def reserved(self,*,inbound=True):
        ctx=self.context
        if inbound:self.assertEqual(ctx.inbound()[0],200)
        self.assertEqual(ctx.request('tick')[0],200)
        record=next(row for row in ctx.store.list_reminders() if row.state=='pending')
        with reminder_tick_lock(ctx.store.path) as acquired:
            self.assertTrue(acquired)
            return ctx.store.reserve(record.key,config_revision=ctx.store.config().revision)

    def test_abandoned_presend_reservation_recovers_then_accepts_only_once(self):
        ctx=self.context;old=self.reserved()
        code,body=ctx.request('recover')
        self.assertEqual((code,body['data']['reservations_recovered']),(200,1))
        self.assertEqual(ctx.attempts,[])
        with self.assertRaises(ReminderStoreError):
            ctx.store.mark_sending(old)
        self.assertEqual(ctx.request('tick')[0],200)
        self.assertEqual(ctx.request('dispatch')[1]['data']['accepted_count'],1)
        ctx.store=ReminderStore(ctx.store.path);ctx.api=ctx.application()
        self.assertEqual(ctx.request('recover')[1]['data']['protected_count'],1)
        self.assertEqual(ctx.request('dispatch')[1]['data']['transport_attempts'],0)
        self.assertEqual(len(ctx.attempts),1)

    def test_crash_after_sending_marker_converts_to_uncertain_without_transport(self):
        ctx=self.context;old=self.reserved()
        with reminder_tick_lock(ctx.store.path) as acquired:
            self.assertTrue(acquired)
            ctx.store.begin_delivery(old,config_revision=ctx.store.config().revision,
                title=ctx.event.title,calendar_timezone='America/Recife',not_before=datetime.now(timezone.utc))
        code,body=ctx.request('recover')
        self.assertEqual((code,body['data']['uncertain_count'],body['data']['reservations_recovered']),(200,1,0))
        record=ctx.store.get(old.reminder.key)
        self.assertEqual((record.state,record.attempt_id),('uncertain',old.attempt_id))
        self.assertEqual(ctx.request('recover')[1]['data']['protected_count'],1)
        ctx.request('tick');ctx.request('dispatch')
        self.assertEqual(ctx.attempts,[])
        self.assertEqual(ctx.store.get(record.key),record)

    def test_recovered_reservation_still_needs_channel_and_new_event_read(self):
        ctx=self.context;old=self.reserved(inbound=False)
        self.assertEqual(ctx.request('recover')[1]['data']['reservations_recovered'],1)
        self.assertEqual(ctx.request('dispatch')[1]['data']['blocked_count'],1)
        self.assertEqual(ctx.attempts,[])
        ctx.inbound();ctx.outcome='cancelled'
        self.assertEqual(ctx.request('dispatch')[1]['data']['cancelled_count'],1)
        self.assertEqual(ctx.attempts,[])
        self.assertEqual(ctx.store.get(old.reminder.key).state,'cancelled')

    def test_unknown_local_commit_reply_does_not_invent_zero_or_send_twice(self):
        ctx=self.context;old=self.reserved();original=ctx.store.apply_recovery
        def lost(*args,**kwargs):
            original(*args,**kwargs)
            raise OSError('PRIVATE_LOCAL_REPLY_LOST')
        with patch.object(ctx.store,'apply_recovery',side_effect=lost):
            code,body=ctx.request('recover')
        self.assertEqual((code,body['mutation_state']),(503,'not_applicable'))
        self.assertIsNone(body['data'])
        self.assertNotIn('PRIVATE_LOCAL_REPLY_LOST',str(body))
        self.assertEqual(ctx.store.get(old.reminder.key).state,'pending')
        self.assertEqual(ctx.request('recover')[1]['data']['reservations_recovered'],0)
        self.assertEqual(ctx.request('dispatch')[1]['data']['accepted_count'],1)
        self.assertEqual(ctx.request('dispatch')[1]['data']['transport_attempts'],0)
        self.assertEqual(len(ctx.attempts),1)

    def test_expired_abandoned_reservation_is_not_replayed_after_restart(self):
        ctx=self.context
        now=datetime.now(timezone.utc);earlier=now-timedelta(minutes=5)
        ctx.store=ReminderStore(ctx.store.path,clock=lambda:earlier)
        ctx.event=replace(ctx.event,start_utc=earlier+timedelta(minutes=15))
        record=ctx.store.plan(ctx.event.event_id,ctx.event.start_utc,'imminent',
            title=ctx.event.title,calendar_timezone='America/Recife',config_revision=ctx.store.config().revision)
        with reminder_tick_lock(ctx.store.path) as acquired:
            self.assertTrue(acquired)
            ctx.store.reserve(record.key,config_revision=ctx.store.config().revision)
        ctx.store=ReminderStore(ctx.store.path);ctx.api=ctx.application()
        code,body=ctx.request('recover')
        self.assertEqual((code,body['data']['expired_count'],body['data']['reservations_recovered']),(200,1,0))
        self.assertEqual(ctx.store.get(record.key).state,'expired')
        self.assertEqual(ctx.request('dispatch')[1]['data']['transport_attempts'],0)
        self.assertEqual(ctx.attempts,[])


if __name__=='__main__':unittest.main()
