"""Alternativas conservadoras de reagendamento; nenhuma reserva ou escrita."""
from datetime import date, datetime, timedelta, timezone
import re
import unicodedata

from app.services.availability import validate_request
from app.services.interview_briefing import validate_input as validate_briefing
from app.services.time_rules import load_zone, validate_event_interval


_DATE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', re.ASCII)
_STAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'
                    r'(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})', re.ASCII)


class SuggestionCollectionError(RuntimeError):
    def __init__(self):
        super().__init__('Não foi possível confirmar alternativas atuais para essa entrevista.')


def _clarification(question):
    return {'schema_version': 1, 'status': 'needs_clarification', 'question': question,
            'event': None, 'duration_minutes': None, 'availability': None,
            'execution_allowed': False, 'reservation_created': False}


def _read_only(value):
    if (type(value) is not dict or type(value.get('schema_version')) is not int
            or value['schema_version'] != 1 or value.get('execution_allowed') is not False
            or value.get('reservation_created') is not False):
        raise ValueError()


def _timestamp(value, zone):
    if type(value) is not str or _STAMP.fullmatch(value) is None:
        raise ValueError()
    if not value.endswith('Z') and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
        raise ValueError()
    instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
    local = instant.astimezone(zone)
    if (instant.replace(tzinfo=None) != local.replace(tzinfo=None)
            or instant.utcoffset() != local.utcoffset()):
        raise ValueError()
    return instant


def _event(facts, identity):
    if (type(facts) is not dict or facts.get('event_id') != identity
            or type(facts.get('title')) is not str or not facts['title'].strip() or len(facts['title']) > 160
            or any(unicodedata.category(char).startswith('C') for char in facts['title'])
            or type(facts.get('all_day')) is not bool or type(facts.get('time_zone')) is not str
            or facts.get('status') not in {'confirmed', 'tentative', 'cancelled'}):
        raise ValueError()
    facts['title'].encode('utf-8')
    zone = load_zone(facts['time_zone'])
    result = {key: facts[key] for key in ('event_id', 'title', 'start', 'end', 'time_zone', 'all_day', 'status')}
    if facts['all_day']:
        if (type(facts['start']) is not str or type(facts['end']) is not str
                or _DATE.fullmatch(facts['start']) is None or _DATE.fullmatch(facts['end']) is None
                or date.fromisoformat(facts['start']) >= date.fromisoformat(facts['end'])):
            raise ValueError()
        return result, None
    start, end = _timestamp(facts['start'], zone), _timestamp(facts['end'], zone)
    duration = validate_event_interval(start, end, facts['time_zone'])
    micros = (duration.days * 86400 + duration.seconds) * 1000000 + duration.microseconds
    minutes, remainder = divmod(micros, 60000000)
    return result, minutes if not remainder and 1 <= minutes <= 1440 else None


def _availability(result, arguments, event):
    _read_only(result)
    period, snapshot = result['period'], result['snapshot']
    if (type(period) is not dict or period.get('time_zone') != event['time_zone']
            or type(snapshot) is not dict or snapshot.get('complete') is not True
            or result.get('scope') != 'authorized_calendar_only'
            or result.get('request') != arguments | {'step_minutes': 15}
            or type(result.get('count')) is not int or not 0 <= result['count'] <= 3
            or type(result.get('slots')) is not list or len(result['slots']) != result['count']
            or type(result.get('limit')) is not int or result['limit'] != 3
            or type(result.get('more_available')) is not bool
            or result['more_available'] and result['count'] < 3):
        raise ValueError()
    zone = load_zone(event['time_zone'])
    lower, upper = _timestamp(period['start'], zone), _timestamp(period['end'], zone)
    if lower.astimezone(timezone.utc) >= upper.astimezone(timezone.utc):
        raise ValueError()
    previous = None
    for index, slot in enumerate(result['slots'], 1):
        if (type(slot) is not dict or slot.get('ref') != 'S' + str(index)
                or slot.get('time_zone') != event['time_zone']):
            raise ValueError()
        start = _timestamp(slot['start'], zone).astimezone(timezone.utc)
        end = _timestamp(slot['end'], zone).astimezone(timezone.utc)
        if (not lower.astimezone(timezone.utc) <= start < end <= upper.astimezone(timezone.utc)
                or end - start != timedelta(minutes=arguments['duration_minutes'])
                or previous is not None and start <= previous):
            raise ValueError()
        previous = start


class RescheduleSuggestionsService:
    def __init__(self, *, briefing_factory, availability_factory):
        self._briefing_factory = briefing_factory
        self._availability_factory = availability_factory

    def suggest(self, *, event_id=None, date_from, date_to, window_start, window_end, buffer_minutes=0):
        # Duração escolhida pelo usuário não faz parte deste contrato. A duração
        # provisória serve somente à validação da janela antes de abrir a fonte.
        arguments = {'date_from': date_from, 'date_to': date_to, 'window_start': window_start,
                     'window_end': window_end, 'buffer_minutes': buffer_minutes}
        validate_request(**arguments, duration_minutes=1)
        identity, _ = validate_briefing(event_id)
        if identity is None:
            return _clarification('Qual entrevista deve receber sugestões? Consulte a agenda e selecione o ID exato.')
        try:
            briefing = self._briefing_factory().prepare(event_id=identity)
            _read_only(briefing)
            if briefing.get('status') == 'needs_clarification':
                if briefing.get('facts') is not None:
                    raise ValueError()
                return _clarification('A entrevista não está disponível ou mudou. Consulte a agenda e selecione-a novamente.')
            if briefing.get('status') != 'ready':
                raise ValueError()
            event, minutes = _event(briefing['facts'], identity)
            if event['status'] == 'cancelled':
                return _clarification('Essa entrevista não está disponível. Consulte a agenda e selecione um evento atual.')
            if event['all_day']:
                return _clarification('O reagendamento atual aceita entrevistas com horário. Selecione uma ocorrência com início e fim definidos.')
            if minutes is None:
                return _clarification('A duração atual precisa ser de 1 a 1440 minutos inteiros. Confira a entrevista antes de pedir alternativas.')
            arguments['duration_minutes'] = minutes
            # A ocupação original permanece na fonte: não excluir o evento nem
            # filtrar intervalos. Isso pode suprimir alternativas sobrepostas.
            availability = self._availability_factory().find(**arguments)
            _availability(availability, arguments, event)
            return {'schema_version': 1, 'status': 'ready', 'question': None,
                    'event': event, 'duration_minutes': minutes, 'availability': availability,
                    'execution_allowed': False, 'reservation_created': False}
        except Exception:
            raise SuggestionCollectionError() from None
