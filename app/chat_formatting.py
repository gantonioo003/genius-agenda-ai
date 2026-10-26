"""Datas e horários para leitura humana no WhatsApp, sem alterar os fatos."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def clock_time(value):
    if type(value) is not datetime or value.utcoffset() is None:
        raise ValueError('Horário inválido para apresentação.')
    text = str(value.hour) + 'h'
    if value.second or value.microsecond:
        seconds = str(value.second)
        if value.microsecond:
            seconds += ',' + f'{value.microsecond:06d}'.rstrip('0')
        return text + f'{value.minute:02d}min' + seconds + 's'
    return text + (f'{value.minute:02d}' if value.minute else '')


def _offset(value):
    raw = value.strftime('%z')
    return 'UTC' + raw[:3] + ':' + raw[3:]


def event_interval(start, end, all_day=False, *, include_date=True, show_offset=False):
    if all_day:
        first, final = date.fromisoformat(start), date.fromisoformat(end)
        if final <= first:
            raise ValueError('Período inválido para apresentação.')
        last = final - timedelta(days=1)
        days = first.strftime('%d/%m/%Y')
        if first != last:
            days += ' a ' + last.strftime('%d/%m/%Y')
        return (days + ' — dia inteiro' if include_date else
                'dia inteiro' if first == last else 'dia inteiro, até ' + last.strftime('%d/%m/%Y'))
    first, last = datetime.fromisoformat(start.replace('Z', '+00:00')), datetime.fromisoformat(end.replace('Z', '+00:00'))
    if first.utcoffset() is None or last.utcoffset() is None or last <= first:
        raise ValueError('Período inválido para apresentação.')
    offset = show_offset or first.utcoffset() != last.utcoffset()
    lower = clock_time(first) + (' (' + _offset(first) + ')' if offset else '')
    upper = clock_time(last) + (' (' + _offset(last) + ')' if offset else '')
    if first.date() != last.date():
        return first.strftime('%d/%m/%Y') + ', ' + lower + ' até ' + last.strftime('%d/%m/%Y') + ', ' + upper
    return (first.strftime('%d/%m/%Y') + ', ' if include_date else '') + lower + ' às ' + upper


def zone_label(name):
    ZoneInfo(name)  # Não atribuir um lugar a um fuso inválido.
    cities = {'America/Recife': 'Recife', 'America/Fortaleza': 'Fortaleza',
              'America/Sao_Paulo': 'São Paulo', 'America/New_York': 'Nova York',
              'Europe/Lisbon': 'Lisboa', 'Europe/London': 'Londres',
              'Asia/Tokyo': 'Tóquio', 'UTC': 'UTC'}
    return 'Horário de ' + cities[name] + '.' if name in cities else 'Horário local da agenda.'


def visible_zone_label(name, *, explicit=False):
    """Ocultar só o fuso padrão implícito; manter validação e outros fusos."""
    label = zone_label(name)
    return label if explicit or name != 'America/Recife' else ''
