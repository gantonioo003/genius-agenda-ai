"""Uma data no cabeçalho diário; intervalos e horários efetivos preservados."""
from types import SimpleNamespace
import unittest

from app.services.whatsapp_agent import _query_text, _event_line


def event(**changes):
    value = dict(title='QA16 — Lembrete fictício', start='2026-10-26T16:55:00-03:00',
                 end='2026-10-26T17:25:00-03:00', all_day=False, time_zone='America/Fortaleza')
    value.update(changes)
    return value


def result(events, end='2026-10-27T00:00:00-03:00'):
    return SimpleNamespace(status='succeeded', action='list', data={'events': events,
        'period': {'start': '2026-10-26T00:00:00-03:00', 'end': end, 'time_zone': 'America/Fortaleza'}})


class WhatsAppDayFormatTests(unittest.TestCase):
    def test_one_day_shows_date_once_and_only_times_in_item(self):
        text = _query_text(result([event()]))
        self.assertIn('*Sua agenda*\n26/10/2026\n', text)
        self.assertEqual(text.count('26/10/2026'), 1)
        self.assertIn('QA16 — Lembrete fictício — 16h55 às 17h25', text)
        self.assertIn('Horário de Fortaleza.', text)
        # Outros renders continuam mostrando a data por padrão.
        self.assertIn('26/10/2026, 16h55', _event_line(event()))

    def test_range_keeps_header_range_and_item_date(self):
        text = _query_text(result([event()], end='2026-11-02T00:00:00-03:00'))
        self.assertIn('26/10/2026 a 01/11/2026', text)
        self.assertIn('QA16 — Lembrete fictício — 26/10/2026, 16h55 às 17h25', text)

    def test_daily_all_day_and_empty_listing_do_not_repeat_date(self):
        text = _query_text(result([event(start='2026-10-26', end='2026-10-27', all_day=True)]))
        self.assertEqual(text.count('26/10/2026'), 1)
        self.assertIn('— dia inteiro', text)
        empty = _query_text(result([]))
        self.assertEqual(empty.count('26/10/2026'), 1)
        self.assertIn('compromissos em 26/10/2026', empty)

    def test_multiday_overlap_preserves_start_and_end_dates(self):
        text = _query_text(result([
            event(start='2026-10-25T23:00:00-03:00', end='2026-10-26T01:00:00-03:00'),
            event(title='Dia inteiro anterior', start='2026-10-25', end='2026-10-28', all_day=True),
        ]))
        self.assertIn('25/10/2026, 23h até 26/10/2026, 1h', text)
        self.assertIn('25/10/2026 a 27/10/2026 — dia inteiro', text)


if __name__ == '__main__':
    unittest.main()
