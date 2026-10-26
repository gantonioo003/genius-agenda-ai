"""Compor dados factuais sobre o fundo aprovado; sem rede ou geração de imagem."""
from datetime import datetime, timezone
import json
from pathlib import Path
import unicodedata
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont


ASSET_DIR = Path(__file__).resolve().parents[1] / 'assets' / 'cards'
_WEEKDAYS = ('Segunda-feira', 'Terça-feira', 'Quarta-feira', 'Quinta-feira', 'Sexta-feira', 'Sábado', 'Domingo')
_CITIES = {'America/Recife': 'Recife', 'America/Fortaleza': 'Fortaleza', 'America/Sao_Paulo': 'São Paulo',
           'America/New_York': 'Nova York', 'Europe/Lisbon': 'Lisboa', 'Europe/London': 'Londres',
           'Asia/Tokyo': 'Tóquio'}
_KEYS = {'brand', 'headline', 'event_title', 'date', 'time', 'timezone', 'footer'}


class CardRenderError(ValueError):
    def __init__(self):
        super().__init__('Não foi possível compor o card com os dados e o layout informados.')


def _title(value):
    if type(value) is not str or not 1 <= len(value) <= 4000:
        raise ValueError()
    value.encode('utf-8')
    value = unicodedata.normalize('NFC', value)
    value = ' '.join(''.join(c for c in value if c.isspace() or not unicodedata.category(c).startswith('C')).split())
    if not value:
        raise ValueError()
    return value


def _aware(value):
    if type(value) is not datetime or value.utcoffset() is None:
        raise ValueError()
    return value.astimezone(timezone.utc)


def _clock(value):
    text = str(value.hour) + 'h'
    if value.second or value.microsecond:
        seconds = str(value.second)
        if value.microsecond:
            seconds += ',' + f'{value.microsecond:06d}'.rstrip('0')
        return text + f'{value.minute:02d}min' + seconds + 's'
    return text + (f'{value.minute:02d}' if value.minute else '')


def _ambiguous(value, zone):
    local, choices = value.replace(tzinfo=None), set()
    for fold in (0, 1):
        utc = local.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None) == local:
            choices.add(utc)
    return len(choices) > 1


def _labels(event_title, start, end, display_timezone, now):
    title = _title(event_title)
    if type(display_timezone) is not str:
        raise ValueError()
    zone = ZoneInfo(display_timezone)
    first, final, observed = _aware(start), _aware(end), _aware(now)
    if final <= first:
        raise ValueError()
    first, final = first.astimezone(zone), final.astimezone(zone)
    date_label = (_WEEKDAYS[first.weekday()] + ', ' + first.strftime('%d/%m/%Y') if first.date() == final.date()
                  else first.strftime('%d/%m/%Y') + ' a ' + final.strftime('%d/%m/%Y'))
    ambiguous = first.utcoffset() != final.utcoffset() or _ambiguous(first, zone) or _ambiguous(final, zone)
    def clock(value):
        text = _clock(value)
        if ambiguous:
            offset = value.strftime('%z')
            text += ' (UTC' + offset[:3] + ':' + offset[3:] + ')'
        return text
    return {'event_title': title, 'date': date_label, 'time': clock(first) + ' às ' + clock(final),
            'headline': 'Chegou o seu dia!' if first.date() == observed.astimezone(zone).date() else 'Sua entrevista está chegando',
            'timezone': ('' if display_timezone == 'America/Recife' else
                         'Horário de ' + _CITIES[display_timezone] if display_timezone in _CITIES else 'Horário local da agenda')}, ambiguous


def _font(size, weight):
    names = {'bold': ('segoeuib.ttf', 'DejaVuSans-Bold.ttf'),
             'semibold': ('seguisb.ttf', 'DejaVuSans-Bold.ttf'),
             'regular': ('segoeui.ttf', 'DejaVuSans.ttf')}
    windows, fallback = names.get(weight, names['regular'])
    for name in (str(Path('C:/Windows/Fonts') / windows), fallback):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            pass
    raise CardRenderError()


def _width(draw, value, font):
    left, _, right, _ = draw.textbbox((0, 0), value, font=font)
    return right - left


def _title_lines(draw, value, font, width, max_characters, max_lines):
    if len(value) > max_characters:
        value = value[:max_characters - 1].rstrip() + '…'
    lines = []
    while value and len(lines) < max_lines:
        fit = len(value)
        while fit and _width(draw, value[:fit], font) > width:
            fit -= 1
        if not fit:
            raise ValueError()
        cut = fit
        if fit < len(value) and ' ' in value[:fit]:
            cut = value.rfind(' ', 0, fit + 1)
        line, value = value[:cut].strip(), value[cut:].strip()
        if value and len(lines) == max_lines - 1:
            while line and _width(draw, line.rstrip('…') + '…', font) > width:
                line = line[:-1]
            line = line.rstrip('…').rstrip() + '…'
            value = ''
        lines.append(line)
    return lines


def render_interview_card(event_title, start: datetime, end: datetime, display_timezone: str,
                          output_path: Path, now: datetime | None = None, *, demonstration=False,
                          template_path: Path | None = None) -> Path:
    """Retornar PNG RGB 1254²; texto crítico nunca é cortado para caber."""
    try:
        if type(demonstration) is not bool:
            raise ValueError()
        labels, ambiguous = _labels(event_title, start, end, display_timezone,
                                    datetime.now(timezone.utc) if now is None else now)
        template_path = ASSET_DIR / 'interview-template-v1.json' if template_path is None else Path(template_path)
        template = json.loads(template_path.read_text(encoding='utf-8'))
        if (template.get('schema_version') != 1 or template['canvas'] != {
                'width': 1254, 'height': 1254, 'format': 'PNG', 'color_mode': 'RGB'}
                or template.get('alignment') != 'center' or type(template.get('blocks')) is not list
                or {block['key'] for block in template['blocks']} != _KEYS or len(template['blocks']) != len(_KEYS)):
            raise ValueError()
        # O template aprovado omite UTC; horas repetidas precisam da alternativa
        # textual, que mantém os offsets em vez de produzir um card ambíguo.
        if ambiguous and template.get('rendering_rules', {}).get('omit_iso_and_utc_in_visible_text'):
            raise ValueError()
        background = template_path.parent / template['background']
        output = Path(output_path)
        if output.suffix.lower() != '.png' or output.resolve() in {background.resolve(), template_path.resolve()}:
            raise ValueError()
        with Image.open(background) as source:
            if source.size != (1254, 1254):
                raise ValueError()
            canvas = source.convert('RGB')
        draw = ImageDraw.Draw(canvas)
        for block in template['blocks']:
            key = block['key']
            for field in ('x', 'y', 'width', 'height', 'font_size'):
                if type(block[field]) is not int or block[field] < (0 if field in {'x', 'y'} else 1):
                    raise ValueError()
            if block['x'] + block['width'] > 1254 or block['y'] + block['height'] > 1254:
                raise ValueError()
            font = _font(block['font_size'], block.get('font_weight', 'regular'))
            value = labels.get(key, block.get('text'))
            if key == 'timezone' and display_timezone == 'America/Recife':
                continue
            if key == 'footer' and demonstration:
                value = 'Demonstração • compromisso fictício'
            if type(value) is not str or not value:
                raise ValueError()
            lines = (_title_lines(draw, value, font, block['width'], block['max_characters'], block['max_lines'])
                     if key == 'event_title' else [value])
            if key != 'event_title' and block.get('max_lines') != 1:
                raise ValueError()
            line_height = block.get('line_height', block['height'])
            if type(line_height) is not int or line_height < 1 or len(lines) * line_height > block['height']:
                raise ValueError()
            color = template['colors']['primary' if key in {'brand', 'headline'} else 'accent' if key == 'time' else 'body']
            top = block['y'] + (block['height'] - len(lines) * line_height) / 2
            for index, line in enumerate(lines):
                left, upper, right, lower = draw.textbbox((0, 0), line, font=font)
                if right - left > block['width'] or lower - upper > line_height:
                    raise ValueError()
                x = block['x'] + (block['width'] - (right - left)) / 2 - left
                y = top + index * line_height + (line_height - (lower - upper)) / 2 - upper
                draw.text((x, y), line, font=font, fill=color)
        output.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output, format='PNG')
        return output
    except Exception:
        raise CardRenderError() from None


def interview_card_caption(event_title, start: datetime, end: datetime, display_timezone: str, *, demonstration=False) -> str:
    """Legenda factual independente do slogan temporal e sem datas ISO."""
    try:
        if type(demonstration) is not bool:
            raise ValueError()
        labels, _ = _labels(event_title, start, end, display_timezone, start)
        title = labels['event_title']
        if len(title) > 160:
            title = title[:159].rstrip() + '…'
        lines = ['Demonstração • compromisso fictício'] if demonstration else []
        return '\n'.join(lines + [title, labels['date'], labels['time']]
                         + ([labels['timezone']] if labels['timezone'] else []))
    except Exception:
        raise CardRenderError() from None
