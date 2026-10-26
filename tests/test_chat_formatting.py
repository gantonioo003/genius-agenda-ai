"""Apresentação legível sem perder datas, duração, estados ou omissões."""
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import Mock
import unittest

from app.chat_formatting import clock_time, event_interval, zone_label
from app.services.agenda_summary import AgendaSummaryService
from app.summary_plan import SummaryPlan, prepare_summary, render_summary, render_summary_whatsapp
import test_agenda_summary as fixtures


class ChatFormattingTests(unittest.TestCase):
    def test_hour_minutes_and_seconds_are_readable_without_rounding(self):
        self.assertEqual(clock_time(datetime(2030, 6, 19, 9, tzinfo=timezone.utc)), '9h')
        self.assertEqual(clock_time(datetime(2030, 6, 19, 9, 30, tzinfo=timezone.utc)), '9h30')
        self.assertEqual(clock_time(datetime(2030, 6, 19, 9, 0, 15, tzinfo=timezone.utc)), '9h00min15s')
        self.assertEqual(event_interval('2030-06-19T09:00:00-03:00', '2030-06-19T09:30:00-03:00'),
                         '19/06/2030, 9h às 9h30')

    def test_multiday_and_all_day_preserve_inclusive_display_end(self):
        self.assertEqual(event_interval('2030-06-19', '2030-06-20', True), '19/06/2030 — dia inteiro')
        self.assertEqual(event_interval('2030-06-19', '2030-06-22', True, include_date=False),
                         'dia inteiro, até 21/06/2030')
        self.assertIn('20/06/2030, 1h', event_interval('2030-06-19T23:00:00-03:00', '2030-06-20T01:00:00-03:00'))

    def test_offset_is_only_shown_when_needed_for_distinct_instants(self):
        regular = event_interval('2030-06-19T09:00:00-03:00', '2030-06-19T09:30:00-03:00')
        self.assertNotIn('UTC', regular)
        folded = event_interval('2026-11-01T01:30:00-04:00', '2026-11-01T01:15:00-05:00')
        self.assertIn('UTC-04:00', folded)
        self.assertIn('UTC-05:00', folded)
        self.assertEqual(zone_label('America/Recife'), 'Horário de Recife.')

    def test_whatsapp_summary_has_only_useful_groups_counts_and_friendly_time(self):
        context = fixtures.context()
        text = render_summary_whatsapp(prepare_summary(context), SummaryPlan('chronological', ('H1',)))
        self.assertIn('*Resumo da semana*', text)
        self.assertIn('*3 compromissos no período.*', text)
        self.assertNotIn('Horário de Recife.', text)
        self.assertIn('a confirmar', text)
        self.assertIn('dia inteiro', text)
        for technical in ('Consulta UTC', 'America/Recife', 'T14:', '-03:00', 'Provisórios: 0', '0 sem detalhes', 'Destaques factuais'):
            self.assertNotIn(technical, text)
        for entry in context['events']:
            self.assertIn(entry['title'], text)
        self.assertLess(len(text), 1500)

    def test_empty_summary_does_not_show_zero_statistics(self):
        text = render_summary_whatsapp(prepare_summary(fixtures.context(empty=True)))
        self.assertIn('Você não tem compromissos', text)
        self.assertNotIn('Total: 0', text)
        self.assertNotIn('UTC:', text)

    def test_factual_fallback_and_api_detailed_view_keep_original_contract(self):
        context = fixtures.context()
        collector, model_factory = Mock(), Mock(side_effect=RuntimeError('model unavailable'))
        collector.build.return_value = context
        chat = AgendaSummaryService(collector, model_factory, presentation='whatsapp').summarize(period='week', reference_date=fixtures.REFERENCE)
        detailed = AgendaSummaryService(collector, model_factory).summarize(period='week', reference_date=fixtures.REFERENCE)
        self.assertEqual(chat['summary']['mode'], 'factual')
        self.assertEqual(detailed['summary']['text'], render_summary(prepare_summary(context)))
        self.assertEqual(chat['totals'], detailed['totals'])
        self.assertEqual(chat['period'], detailed['period'])
        self.assertNotIn('Consulta UTC', chat['summary']['text'])

    def test_long_titles_are_explicitly_shortened_without_omitting_events(self):
        context = fixtures.context()
        for entry in context['events']:
            entry['title'] = 'T' * 160
            entry['title_truncated'] = False
        text = render_summary_whatsapp(prepare_summary(context))
        self.assertIn('Títulos abreviados.', text)
        self.assertEqual(text.count('• *'), 3)
        self.assertLessEqual(len(text), 3500)


if __name__ == '__main__':
    unittest.main()
