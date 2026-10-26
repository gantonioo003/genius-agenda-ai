"""Composição local em PNG: limites, fuso, demonstração e dados obrigatórios."""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw

from app.interview_card import CardRenderError, _font, interview_card_caption, render_interview_card


START = datetime(2030, 6, 19, 12, tzinfo=timezone.utc)
END = datetime(2030, 6, 19, 12, 30, tzinfo=timezone.utc)


class InterviewCardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.template = self.root / 'template.json'
        self.output = self.root / 'card.png'
        Image.new('RGB', (1254, 1254), '#fff7ec').save(self.root / 'background.png')
        fields = [('brand', 590, 36, 26), ('headline', 650, 64, 52), ('event_title', 744, 116, 44),
                  ('date', 905, 52, 38), ('time', 977, 66, 54), ('timezone', 1075, 42, 28), ('footer', 1181, 33, 23)]
        blocks = [{'key': key, 'x': 100, 'y': y, 'width': 1054, 'height': height,
                   'font_size': size, 'max_lines': 1} for key, y, height, size in fields]
        blocks[0]['text'] = 'GENIUS AGENDA AI'
        blocks[-1]['text'] = 'Sua próxima conversa começa bem preparada.'
        blocks[1]['font_weight'] = blocks[4]['font_weight'] = 'bold'
        blocks[2].update(max_lines=2, max_characters=80, line_height=56, font_weight='semibold')
        self.data = {'schema_version': 1, 'canvas': {'width': 1254, 'height': 1254, 'format': 'PNG', 'color_mode': 'RGB'},
                     'alignment': 'center', 'background': 'background.png', 'blocks': blocks,
                     'colors': {'primary': '#244C3A', 'body': '#29352F', 'accent': '#B85B45'},
                     'rendering_rules': {'omit_iso_and_utc_in_visible_text': True}}
        self.save_template()

    def save_template(self):
        self.template.write_text(json.dumps(self.data), encoding='utf-8')

    def render(self, title='Entrevista técnica — empresa fictícia', **changes):
        values = {'start': START, 'end': END, 'display_timezone': 'America/Recife',
                  'output_path': self.output, 'now': START, 'template_path': self.template}
        return render_interview_card(title, **(values | changes))

    def capture(self, **changes):
        captured = []
        original = ImageDraw.ImageDraw.text
        def text(draw, position, value, *args, **kwargs):
            captured.append((value, kwargs['font'].size))
            return original(draw, position, value, *args, **kwargs)
        with patch.object(ImageDraw.ImageDraw, 'text', new=text):
            self.render(**changes)
        return captured

    def test_png_has_exact_canvas_rgb_and_preserves_upper_background(self):
        self.assertEqual(self.render(), self.output)
        with Image.open(self.output) as image, Image.open(self.root / 'background.png') as background:
            self.assertEqual((image.size, image.mode, image.format), ((1254, 1254), 'RGB', 'PNG'))
            self.assertIsNone(ImageChops.difference(image.crop((0, 0, 1254, 580)), background.crop((0, 0, 1254, 580))).getbbox())
            self.assertIsNotNone(ImageChops.difference(image, background).getbbox())

    def test_headline_uses_local_day_not_utc_or_host_date(self):
        today = self.capture(now=datetime(2030, 6, 20, 1, tzinfo=timezone.utc))
        self.assertIn(('Chegou o seu dia!', 52), today)
        future = self.capture(now=datetime(2030, 6, 19, 0, tzinfo=timezone.utc))
        self.assertIn(('Sua entrevista está chegando', 52), future)
        self.assertNotIn(('Chegou o seu dia!', 52), future)

    def test_caption_converts_zone_and_uses_portuguese_dates_without_iso(self):
        caption = interview_card_caption('Entrevista fictícia', START, END, 'Pacific/Kiritimati')
        self.assertIn('Quinta-feira, 20/06/2030', caption)
        self.assertIn('2h às 2h30', caption)
        self.assertNotIn('2030-06-', caption)
        self.assertNotIn('Recife', caption)

    def test_long_title_wraps_to_two_lines_with_visible_ellipsis(self):
        captured = self.capture(title='W' * 4000)
        title = [text for text, size in captured if size == 44]
        self.assertLessEqual(len(title), 2)
        self.assertTrue(title[-1].endswith('…'))
        self.assertLessEqual(sum(map(len, title)), 80)

    def test_critical_date_or_time_never_truncates_to_fit(self):
        for key in ('date', 'time'):
            block = next(block for block in self.data['blocks'] if block['key'] == key)
            block['width'] = 40
            self.save_template()
            with self.assertRaises(CardRenderError):
                self.render()
            self.assertFalse(self.output.exists())
            block['width'] = 1054

    def test_invalid_datetime_zone_title_or_now_is_rejected(self):
        for changes in [{'start': START.replace(tzinfo=None)}, {'end': START}, {'end': 'invalid'},
                        {'display_timezone': 'Invalid/PRIVATE'}, {'now': START.replace(tzinfo=None)}]:
            with self.subTest(changes=changes), self.assertRaises(CardRenderError):
                self.render(**changes)
        for title in ('', '\ud800', '   ', None):
            with self.subTest(title=repr(title)), self.assertRaises(CardRenderError):
                self.render(title=title)
        self.assertFalse(self.output.exists())

    def test_cross_day_interval_keeps_both_dates(self):
        caption = interview_card_caption('Fictícia', datetime(2030, 6, 20, 2, tzinfo=timezone.utc),
            datetime(2030, 6, 20, 3, 30, tzinfo=timezone.utc), 'America/Recife')
        self.assertIn('19/06/2030 a 20/06/2030', caption)
        self.assertIn('23h às 0h30', caption)

    def test_demonstration_is_explicit_in_footer_and_caption(self):
        captured = self.capture(demonstration=True)
        self.assertIn(('Demonstração • compromisso fictício', 23), captured)
        self.assertTrue(interview_card_caption('Fictícia', START, END, 'America/Recife', demonstration=True).startswith('Demonstração'))

    def test_dejavu_is_tried_when_segoe_is_unavailable(self):
        real_font = _font(23, 'regular')
        with patch('app.interview_card.ImageFont.truetype', side_effect=[OSError(), real_font]) as load:
            self.assertIs(_font(23, 'regular'), real_font)
        self.assertEqual(load.call_args_list[1].args[0], 'DejaVuSans.ttf')

    def test_ambiguous_dst_uses_factual_caption_instead_of_ambiguous_card(self):
        first = datetime.fromisoformat('2030-11-03T01:45:00-04:00')
        final = datetime.fromisoformat('2030-11-03T01:00:00-05:00')
        with self.assertRaises(CardRenderError):
            self.render(start=first, end=final, display_timezone='America/New_York')
        caption = interview_card_caption('Fictícia', first, final, 'America/New_York')
        self.assertIn('UTC-04:00', caption)
        self.assertIn('UTC-05:00', caption)

    def test_original_background_cannot_be_overwritten(self):
        original = (self.root / 'background.png').read_bytes()
        with self.assertRaises(CardRenderError):
            self.render(output_path=self.root / 'background.png')
        self.assertEqual((self.root / 'background.png').read_bytes(), original)

    def test_wrong_background_size_or_invalid_template_fails_without_output(self):
        Image.new('RGB', (100, 100)).save(self.root / 'background.png')
        with self.assertRaises(CardRenderError):
            self.render()
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
