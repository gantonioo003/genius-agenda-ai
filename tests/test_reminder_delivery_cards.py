"""Card preparado antes da conferência final, sob a mesma reserva de envio."""
from datetime import timedelta
import unittest

import test_reminder_delivery as legacy
from app.services.reminder_delivery import ReminderRecipient, reminder_notice_text, reminder_text


class PreparedSender:
    def __init__(self, owner):
        self.owner, self.prepare_calls, self.prepared_calls, self.text_calls = owner, [], [], []
        self.hook = None
        self.failure = False
        self.trace = []
        self.prepared = object()

    def prepare_reminder(self, event, **kwargs):
        self.trace.append('prepare')
        self.prepare_calls.append((event, kwargs, self.owner.store.get(self.owner.record.key).state))
        if self.hook:
            self.hook()
        return self.prepared

    def send_prepared(self, recipient, text, prepared, **kwargs):
        self.trace.append('prepared_send')
        self.prepared_calls.append((recipient, text, prepared, kwargs, self.owner.store.get(self.owner.record.key).state))
        if self.failure:
            raise RuntimeError('private provider error')
        return legacy.FINGERPRINT

    def send(self, recipient, text):
        self.trace.append('text_send')
        self.text_calls.append((recipient, text, self.owner.store.get(self.owner.record.key).state))
        return legacy.FINGERPRINT


class ReminderDeliveryCardTests(unittest.TestCase):
    setUp = legacy.ReminderDeliveryTests.setUp
    event = legacy.ReminderDeliveryTests.event
    remote = legacy.ReminderDeliveryTests.remote
    configure = legacy.ReminderDeliveryTests.configure
    seed = legacy.ReminderDeliveryTests.seed
    inbound = legacy.ReminderDeliveryTests.inbound
    service = legacy.ReminderDeliveryTests.service
    public = legacy.ReminderDeliveryTests.public

    def ready(self, *, offset=60, inbound_age=timedelta(hours=1)):
        if offset == 15:
            config = self.configure()
            event = self.event()
            self.now += timedelta(minutes=45)
            self.record = self.store.plan(event.event_id, event.start_utc, 'imminent', title=event.title,
                calendar_timezone=legacy.ZONE, config_revision=config.revision)
        else:
            self.record = self.seed()
        self.inbound(age=inbound_age)
        self.adapter = PreparedSender(self)
        self.sender_factory.return_value = self.adapter

    def assert_no_send(self):
        self.assertEqual((self.adapter.prepared_calls, self.adapter.text_calls), ([], []))

    def test_notice_rounds_real_remaining_minutes_and_keeps_legacy_formatter(self):
        start = legacy.NOW + timedelta(minutes=15)
        first = reminder_notice_text('Entrevista', start, legacy.ZONE, offset_minutes=15, now=legacy.NOW)
        late = reminder_notice_text('Entrevista', start, legacy.ZONE, offset_minutes=15, now=legacy.NOW + timedelta(minutes=1))
        self.assertIn('começa em 15 minutos.', first)
        self.assertIn('começa em 14 minutos.', late)
        self.assertIn('17/06/2030 às 9h15', late)
        self.assertNotIn('Horário de Recife.', late)
        self.assertNotIn('America/', late)
        self.assertIn('(America/Recife)', reminder_text('Entrevista', start, legacy.ZONE))

    def test_60_prepares_reserved_then_final_get_then_single_sending_attempt(self):
        self.ready()
        def lookup(ids, **kwargs):
            self.adapter.trace.append('get')
            return self.remote(ids[0])
        self.gateway.lookup_snapshot.side_effect = lookup
        result = self.public(self.service().dispatch())
        self.assertEqual(self.adapter.trace, ['get', 'prepare', 'get', 'prepared_send'])
        self.assertEqual(self.adapter.prepare_calls[0][2], 'reserved')
        self.assertEqual(self.adapter.prepared_calls[0][4], 'sending')
        self.assertIs(self.adapter.prepared_calls[0][2], self.adapter.prepared)
        final = self.adapter.prepared_calls[0][3]
        self.assertEqual((final['event'].event_id, final['calendar_timezone'], final['offset_minutes'], final['now']),
                         ('event123', legacy.ZONE, 60, self.now))
        self.assertEqual((result['accepted_count'], result['transport_attempts']), (1, 1))
        self.assertEqual(self.adapter.text_calls, [])

    def test_15_uses_only_text_even_with_image_adapter_available(self):
        self.ready(offset=15)
        result = self.public(self.service().dispatch())
        self.assertEqual(self.adapter.prepare_calls, [])
        self.assertEqual(self.adapter.prepared_calls, [])
        self.assertEqual(len(self.adapter.text_calls), 1)
        self.assertIn('começa em 15 minutos.', self.adapter.text_calls[0][1])
        self.assertEqual(result['accepted_count'], 1)

    def test_send_only_legacy_sender_still_uses_one_attempt(self):
        self.ready()
        self.sender_factory.return_value = self.sender
        result = self.public(self.service().dispatch())
        self.sender.send.assert_called_once()
        self.assertEqual((result['accepted_count'], result['transport_attempts']), (1, 1))

    def test_prepare_failure_falls_back_to_text_after_final_validation(self):
        self.ready()
        def fail():
            raise RuntimeError('private render error')
        self.adapter.hook = fail
        result = self.public(self.service().dispatch())
        self.assertEqual(self.gateway.lookup_snapshot.call_count, 2)
        self.assertEqual((len(self.adapter.prepared_calls), len(self.adapter.text_calls)), (0, 1))
        self.assertEqual(self.adapter.text_calls[0][2], 'sending')
        self.assertEqual((result['accepted_count'], result['failed_count'], result['transport_attempts']), (1, 0, 1))

    def test_slow_preparation_cannot_cross_deadline_and_send(self):
        self.ready()
        self.adapter.hook = lambda: setattr(self, 'now', self.now + timedelta(seconds=120))
        result = self.public(self.service().dispatch())
        self.assert_no_send()
        self.assertEqual((result['expired_count'], result['transport_attempts']), (1, 0))
        self.assertEqual(self.store.get(self.record.key).state, 'expired')

    def test_channel_margin_reached_during_preparation_is_still_atomic(self):
        self.ready(inbound_age=timedelta(hours=24, seconds=-61))
        self.adapter.hook = lambda: setattr(self, 'now', self.now + timedelta(seconds=1))
        result = self.public(self.service().dispatch())
        self.assert_no_send()
        self.assertEqual((result['blocked_count'], result['transport_attempts']), (1, 0))

    def test_profile_revoked_during_preparation_prevents_transport(self):
        self.ready()
        self.adapter.hook = lambda: self.configure(enabled=False)
        result = self.public(self.service().dispatch())
        self.assert_no_send()
        self.assertEqual(result['transport_attempts'], 0)
        self.assertEqual(self.store.get(self.record.key).state, 'cancelled')

    def test_rescheduled_event_after_preparation_cancels_original_claim(self):
        self.ready()
        self.gateway.lookup_snapshot.side_effect = [self.remote(), self.remote(
            start_utc=legacy.NOW + timedelta(hours=2), end_utc=legacy.NOW + timedelta(hours=3))]
        result = self.public(self.service().dispatch())
        self.assert_no_send()
        self.assertEqual((result['cancelled_count'], result['transport_attempts']), (1, 0))

    def test_final_title_and_zone_are_passed_to_adapter_without_second_render(self):
        self.ready()
        self.gateway.lookup_snapshot.side_effect = [self.remote(), self.remote(title='Título atual', zone='UTC')]
        result = self.public(self.service().dispatch())
        self.assertEqual(len(self.adapter.prepare_calls), 1)
        sent = self.adapter.prepared_calls[0]
        self.assertEqual((sent[3]['event'].title, sent[3]['calendar_timezone']), ('Título atual', 'UTC'))
        self.assertIn('Título atual', sent[1])
        self.assertIn('Horário de UTC.', sent[1])
        self.assertEqual(result['accepted_count'], 1)

    def test_recipient_change_after_preparation_releases_without_transport(self):
        self.ready()
        self.recipient_loader.side_effect = [ReminderRecipient(legacy.PHONE, legacy.BINDING),
                                             ReminderRecipient('5511888888888', legacy.BINDING)]
        result = self.public(self.service().dispatch())
        self.assert_no_send()
        self.assertEqual((result['failed_count'], result['transport_attempts']), (1, 0))
        self.assertEqual(self.store.get(self.record.key).state, 'pending')

    def test_prepared_send_failure_is_uncertain_without_text_retry(self):
        self.ready()
        self.adapter.failure = True
        result = self.public(self.service().dispatch())
        self.assertEqual((len(self.adapter.prepared_calls), len(self.adapter.text_calls)), (1, 0))
        self.assertEqual((result['uncertain_count'], result['transport_attempts']), (1, 1))
        self.assertEqual(self.store.get(self.record.key).state, 'uncertain')


if __name__ == '__main__':
    unittest.main()
