from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from app.chat_formatting import zone_label, visible_zone_label
from app.services.whatsapp_agent import _event_line, _query_text
from app.services.reminder_delivery import reminder_notice_text
from app.interview_card import interview_card_caption, render_interview_card, ASSET_DIR


START = datetime(2030, 6, 19, 19, 55, tzinfo=timezone.utc)
END = START + timedelta(minutes=30)


class ImplicitRecifePresentationTests(unittest.TestCase):
    def test_original_zone_label_and_explicit_label_are_preserved(self):
        self.assertEqual(zone_label('America/Recife'), 'Horário de Recife.')
        self.assertEqual(visible_zone_label('America/Recife'), '')
        self.assertEqual(visible_zone_label('America/Recife', explicit=True), 'Horário de Recife.')
        self.assertEqual(visible_zone_label('America/Fortaleza'), 'Horário de Fortaleza.')

    def test_whatsapp_item_and_list_hide_only_default_zone(self):
        event = dict(title='QA16', start=START.isoformat(), end=END.isoformat(), all_day=False, time_zone='America/Recife')
        self.assertNotIn('Recife', _event_line(event))
        period = dict(start='2030-06-19T00:00:00-03:00', end='2030-06-20T00:00:00-03:00', time_zone='America/Recife')
        result = SimpleNamespace(status='succeeded', action='list', data={'events': [event], 'period': period})
        self.assertNotIn('Recife', _query_text(result))
        event['time_zone'] = period['time_zone'] = 'America/Fortaleza'
        self.assertIn('Fortaleza', _event_line(event))
        self.assertIn('Fortaleza', _query_text(result))

    def test_notice_hides_default_zone_and_keeps_other_zone(self):
        self.assertNotIn('Recife', reminder_notice_text('QA16', START, 'America/Recife', offset_minutes=15))
        self.assertIn('Fortaleza', reminder_notice_text('QA16', START, 'America/Fortaleza', offset_minutes=15))

    def test_card_caption_and_render_allow_blank_default_zone_block(self):
        self.assertNotIn('Recife', interview_card_caption('QA16', START, END, 'America/Recife'))
        self.assertIn('Fortaleza', interview_card_caption('QA16', START, END, 'America/Fortaleza'))
        with TemporaryDirectory() as folder:
            output = render_interview_card('QA16', START, END, 'America/Recife', Path(folder) / 'card.png',
                now=START - timedelta(hours=1), template_path=ASSET_DIR / 'interview-template-v1.json')
            self.assertTrue(output.is_file())


if __name__ == '__main__':
    unittest.main()
