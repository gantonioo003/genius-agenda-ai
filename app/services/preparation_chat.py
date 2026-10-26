"""Pedidos informativos explícitos em português, sem plano ou estado novo."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import re
from typing import Literal
import unicodedata

from app.conversation_store import QueryMemory, validate_memory
from app.chat_formatting import event_interval, visible_zone_label as zone_label
from app.services.availability import AvailabilityInputError, validate_request
from app.services.interview_briefing import BriefingInputError, validate_input as validate_briefing
from app.services.summary_context import SummaryInputError, validate_input as validate_summary
from app.services.time_rules import load_zone


MAX_TEXT = 3500
MAX_INPUT = 4000
_DATE = r'[0-9]{4}-[0-9]{2}-[0-9]{2}'
_DAY = rf'(?:{_DATE}|hoje|amanha)'
_STAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'
                    r'(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})', re.ASCII)
_PREFIXES = (
    ('suggest_reschedule', re.compile(r'^(?:sugira|sugestao de) horarios? (?:para )?(?:reagendar|remarcar)\b')),
    ('summarize_agenda', re.compile(r'^(?:resume|resuma|resumo)\b')),
    ('prepare_briefing', re.compile(r'^(?:me prepara|me prepare|briefing|checklist)\b')),
    ('find_free_slots', re.compile(r'^horarios? livres?\b')),
)
_SUMMARY = re.compile(rf'(?:resume|resuma|resumo)\s+'
    rf'(?:(?:a|minha|meu|o|da|do|de|desta|deste|dessa|desse|esta|este|essa|esse)\s+)?'
    rf'(?:(proxima|proximo)\s+)?(semana|mes)'
    rf'(?:\s+(?:(?:de|em)\s+)?({_DAY}))?[.!?]?')
_AVAILABILITY = re.compile(rf'horarios? livres?\s+(?:(?:em|de|no dia)\s+)?({_DAY})'
    rf'(?:\s+(?:a|ate)\s+({_DAY}))?\s+das\s+([0-9]{{2}}:[0-9]{{2}})'
    rf'\s+(?:as|a|ate)\s+([0-9]{{2}}:[0-9]{{2}})\s+(?:por|durante)\s+'
    rf'([0-9]{{1,4}})\s*(?:minutos?|min)'
    rf'(?:\s+(?:com\s+)?margem\s+(?:de\s+)?([0-9]{{1,3}})\s*(?:minutos?|min))?[.!?]?')
_DEICTIC = re.compile(r'(?:me prepara|me prepare|briefing|checklist)'
    r'(?:\s+(?:para\s+)?(?:essa entrevista|esta entrevista|esse evento|este evento|'
    r'a entrevista selecionada|o evento selecionado|a entrevista|o evento)|\s+dessa entrevista|\s+da entrevista)?[.!?]?')
_EXPLICIT_ID = re.compile(r'(?:me prepara|me prepare|briefing|checklist)\s+'
    r'(?:(?:(?:para|da|do)\s+)?(?:(?:a|o)\s+)?(?:entrevista|evento)\s+)?id\s+(\S+)', re.IGNORECASE)
_SUGGESTION = re.compile(r'(?:sugira|sugest[aã]o de) hor[aá]rios? (?:para )?(?:reagendar|remarcar) '
    r'(?:(?:essa|esta) entrevista|(?:esse|este) evento|id (\S+)) '
    rf'(?:(?:em|no dia) )?({_DATE}|hoje|amanh[aã])(?: (?:a|at[eé]) ({_DATE}|hoje|amanh[aã]))? '
    r'das ([0-9]{2}:[0-9]{2}) (?:[aà]s|a|at[eé]) ([0-9]{2}:[0-9]{2})'
    r'(?: (?:com )?margem (?:de )?([0-9]{1,3}) (?:minutos?|min))?[.!?]?', re.IGNORECASE)
_QUESTIONS = {
    'suggest_reschedule': 'Selecione a entrevista e informe a nova faixa. Exemplo: “Sugira horários para reagendar essa entrevista amanhã das 09:00 às 12:00”. A duração atual será mantida.',
    'summarize_agenda': 'Informe semana ou mês e uma referência clara. Exemplo: “Resumo da semana de 2030-06-19” ou “Resumo do próximo mês”.',
    'prepare_briefing': 'Qual entrevista devo preparar? Consulte a agenda e selecione um evento pelo ID; depois peça “me prepara para essa entrevista”. Contexto opcional vem após “Contexto:”.',
    'find_free_slots': 'Informe data, faixa e duração. Exemplo: “Horários livres amanhã das 09:00 às 12:00 por 30 minutos”, opcionalmente “margem 15 min”. Nenhuma reserva será criada.',
}
_FAILURES = {
    'suggest_reschedule': 'Não consegui confirmar opções de reagendamento agora. Nenhum compromisso foi alterado.',
    'summarize_agenda': 'Não foi possível confirmar os fatos atuais da agenda. Tente novamente mais tarde.',
    'prepare_briefing': 'Não foi possível confirmar os dados atuais da entrevista. Tente novamente mais tarde.',
    'find_free_slots': 'Não foi possível confirmar a ocupação da agenda; nenhum horário foi declarado livre.',
}


@dataclass(frozen=True, repr=False)
class PreparationChatResult:
    text: str
    status: Literal['succeeded', 'needs_clarification', 'failed']
    action: Literal['summarize_agenda', 'prepare_briefing', 'find_free_slots', 'suggest_reschedule']
    selected_event_id: str | None = None
    selected_time_zone: str | None = None

    def __repr__(self):
        return 'PreparationChatResult(<private>)'


class _Clarify(ValueError):
    pass


def _fold(value):
    return ''.join(char for char in unicodedata.normalize('NFD', value.casefold())
                   if not unicodedata.combining(char))


def _intent(message):
    if type(message) is not str:
        return None
    # Texto entre aspas, citações e negações não são uma intenção positiva.
    text = _fold(message.strip())
    instruction = re.split(r'\bcontexto\s*:', text, maxsplit=1)[0]
    instruction = re.sub(r'\bid\s+\S+', 'id', instruction)
    if re.search(r'\b(?:nao|nunca|jamais)\b', instruction):
        return None
    return next((action for action, pattern in _PREFIXES if pattern.match(text)), None)


def is_preparation_request(message) -> bool:
    """Classificação local por prefixo; não consulta fonte, relógio ou modelo."""
    return _intent(message) is not None


def _message(value):
    if type(value) is not str or not 1 <= len(value) <= MAX_INPUT:
        raise _Clarify()
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise _Clarify() from None
    if any(unicodedata.category(char).startswith('C') and char not in '\r\n\t' for char in value):
        raise _Clarify()
    return ' '.join(unicodedata.normalize('NFC', value).split())


def _text(value):
    if type(value) is not str or not value.strip() or len(value) > MAX_TEXT:
        raise ValueError()
    value.encode('utf-8')
    if any(unicodedata.category(char).startswith('C') and char not in '\r\n\t' for char in value):
        raise ValueError()
    return value


def _stamp(value, zone=None):
    if type(value) is not str or _STAMP.fullmatch(value) is None:
        raise ValueError()
    if not value.endswith('Z') and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
        raise ValueError()
    instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if zone is not None:
        local = instant.astimezone(zone)
        if local.replace(tzinfo=None) != instant.replace(tzinfo=None) or local.utcoffset() != instant.utcoffset():
            raise ValueError()
    return instant.astimezone(timezone.utc)


def _read_only(result):
    if (type(result) is not dict or type(result.get('schema_version')) is not int
            or result['schema_version'] != 1 or result.get('execution_allowed') is not False):
        raise ValueError()


def _ambiguous(instant, zone):
    local = instant.astimezone(zone).replace(tzinfo=None)
    choices = set()
    for fold in (0, 1):
        candidate = local.replace(tzinfo=zone, fold=fold)
        utc = candidate.astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None) == local:
            choices.add(utc)
    return len(choices) > 1


def _one_line(value, limit):
    value = ' '.join(_text(value).split())
    return value if len(value) <= limit else value[:limit - 1] + '…'


def _minutes(value):
    return f"{value} minuto" + ('' if value == 1 else 's')


def _availability_text(result, arguments, expected_zone):
    _read_only(result)
    if (result.get('reservation_created') is not False or result.get('scope') != 'authorized_calendar_only'
            or result.get('request') != arguments | {'step_minutes': 15}
            or type(result.get('count')) is not int or not 0 <= result['count'] <= 3
            or type(result.get('limit')) is not int or result['limit'] != 3
            or type(result.get('more_available')) is not bool
            or result['more_available'] and result['count'] < 3
            or type(result.get('slots')) is not list or len(result['slots']) != result['count']):
        raise ValueError()
    period, snapshot = result['period'], result['snapshot']
    if (type(period) is not dict or type(snapshot) is not dict or snapshot.get('complete') is not True
            or type(period.get('time_zone')) is not str
            or expected_zone is not None and period['time_zone'] != expected_zone):
        raise ValueError()
    zone = load_zone(period['time_zone'])
    lower, upper = _stamp(period['start'], zone), _stamp(period['end'], zone)
    observed = _stamp(snapshot['observed_at_utc'])
    if lower >= upper:
        raise ValueError()
    entries = []
    previous = None
    for index, slot in enumerate(result['slots'], 1):
        if (type(slot) is not dict or slot.get('ref') != 'S' + str(index)
                or slot.get('time_zone') != zone.key):
            raise ValueError()
        start, end = _stamp(slot['start'], zone), _stamp(slot['end'], zone)
        if (not lower <= start < end <= upper or start < observed
                or end - start != timedelta(minutes=arguments['duration_minutes'])
                or previous is not None and start <= previous):
            raise ValueError()
        previous = start
        entries.append((slot, start, end))
    lines = ['*Horários livres*']
    current_day = None
    for index, (slot, start, end) in enumerate(entries, 1):
        day = start.astimezone(zone).date()
        if day != current_day:
            lines += ['', day.strftime('%d/%m/%Y')]
            current_day = day
        label = event_interval(slot['start'], slot['end'], include_date=day != end.astimezone(zone).date(),
                               show_offset=_ambiguous(start, zone) or _ambiguous(end, zone))
        lines.append(f'{index}. *{label}*')
    if not result['slots']:
        first, last = date.fromisoformat(arguments['date_from']), date.fromisoformat(arguments['date_to'])
        dates = first.strftime('%d/%m/%Y')
        if last != first:
            dates += ' a ' + last.strftime('%d/%m/%Y')
        lines += ['', dates, 'Nenhuma opção futura na faixa informada foi encontrada nesta consulta.']
    lines += ['', zone_label(zone.key), f"Opções de {_minutes(arguments['duration_minutes'])}. Nenhuma reserva foi criada."]
    if arguments['buffer_minutes']:
        lines.append(f"Margem: {_minutes(arguments['buffer_minutes'])}.")
    if entries:
        lines.append('As opções são alternativas.')
    if result['more_available']:
        lines.append('Há mais alternativas na faixa consultada.')
    return _text('\n'.join(lines))


def _briefing_text(result, identity):
    # Conservar a validação anterior mesmo que o texto técnico não seja exibido.
    _text(result['text'])
    facts, context, checklist = result['facts'], result.get('provided_context'), result.get('checklist')
    if (type(facts) is not dict or facts.get('event_id') != identity
            or type(facts.get('all_day')) is not bool or facts.get('status') not in {'confirmed', 'tentative'}
            or type(facts.get('time_zone')) is not str or type(context) is not dict
            or context.get('verified_externally') is not False
            or type(context.get('truncated_fields')) is not list
            or any(type(field) is not str or field not in {'title', 'location', 'description', 'user_context'}
                   for field in context['truncated_fields'])
            or type(checklist) is not list or not 1 <= len(checklist) <= 6):
        raise ValueError()
    zone = load_zone(facts['time_zone'])
    title = _one_line(facts['title'], 160)
    location = facts.get('location')
    if location is not None:
        location = _one_line(location, 240)
    if facts['all_day']:
        if (type(facts.get('start')) is not str or type(facts.get('end')) is not str
                or re.fullmatch(_DATE, facts['start']) is None or re.fullmatch(_DATE, facts['end']) is None
                or date.fromisoformat(facts['start']) >= date.fromisoformat(facts['end'])):
            raise ValueError()
        show_offset = False
    else:
        start, end = _stamp(facts['start'], zone), _stamp(facts['end'], zone)
        if start >= end:
            raise ValueError()
        show_offset = _ambiguous(start, zone) or _ambiguous(end, zone)
    label = event_interval(facts['start'], facts['end'], facts['all_day'], show_offset=show_offset)
    lines = ['*Preparo da entrevista*', '', '*' + title + '*', label, zone_label(zone.key)]
    if facts['status'] == 'tentative':
        lines.append('Horário provisório.')
    lines.append('Local/link: ' + (location or 'não informado.'))
    notes = []
    for field, prefix in (('event_description', 'Evento: '), ('user_context', 'Você: ')):
        value = context.get(field)
        if value is not None:
            notes.append(prefix + _one_line(value, 240))
    if notes:
        lines += ['', '*Contexto informado*', *notes]
    else:
        lines += ['', 'Contexto adicional não informado.']
    lines += ['', '*Checklist sugerido*']
    lines += ['☐ ' + _one_line(item, 140) for item in checklist]
    if context['truncated_fields'] or any(len(' '.join(value.split())) > 240
            for value in (context.get('event_description'), context.get('user_context')) if type(value) is str):
        lines.append('Contexto resumido; confira os detalhes completos no evento ou nas suas notas.')
    return _text('\n'.join(lines))


class PreparationChatService:
    def __init__(self, *, summary_factory, briefing_factory, availability_factory,
                 time_zone_loader, clock=None, reschedule_factory=None):
        self._summary_factory = summary_factory
        self._briefing_factory = briefing_factory
        self._availability_factory = availability_factory
        self._time_zone_loader = time_zone_loader
        self._clock = clock if clock is not None else lambda: datetime.now(timezone.utc)
        self._reschedule_factory = reschedule_factory

    def _today(self):
        name = self._time_zone_loader()
        if type(name) is not str:
            raise ValueError()
        zone = load_zone(name)
        now = self._clock()
        if type(now) is not datetime or now.utcoffset() is None:
            raise ValueError()
        return now.astimezone(timezone.utc).astimezone(zone).date(), zone.key

    def _summary(self, message):
        match = _SUMMARY.fullmatch(_fold(message))
        if match is None:
            raise _Clarify()
        following, noun, reference = match.groups()
        if following and reference:
            raise _Clarify()
        kind, expected_zone = ('week' if noun == 'semana' else 'month'), None
        if reference is None or reference in {'hoje', 'amanha'}:
            today, expected_zone = self._today()
            day = today + timedelta(days=1) if reference == 'amanha' else today
            if following:
                day = day + timedelta(days=7) if kind == 'week' else (
                    date(day.year + 1, 1, 1) if day.month == 12 else date(day.year, day.month + 1, 1))
        else:
            _, day = validate_summary(kind, reference)
        validate_summary(kind, day.isoformat())
        result = self._summary_factory().summarize(period=kind, reference_date=day.isoformat())
        _read_only(result)
        load_zone(result['period']['time_zone'])
        if (result['period']['kind'] != kind or result['period']['reference_date'] != day.isoformat()
                or expected_zone is not None and result['period']['time_zone'] != expected_zone
                or result['snapshot']['complete'] is not True
                or result['summary']['mode'] not in {'factual', 'ai_organized'}):
            raise ValueError()
        return _text(result['summary']['text']), 'succeeded'

    def _briefing(self, message, memory):
        pieces = re.split(r'\bcontexto\s*:', message, maxsplit=1, flags=re.IGNORECASE)
        instruction, notes = pieces[0].strip(), pieces[1].strip() if len(pieces) == 2 else ''
        # A captura usa o texto original: nunca normalizar caixa ou conteúdo do ID.
        explicit = _EXPLICIT_ID.fullmatch(instruction)
        if explicit is not None:
            identity = explicit[1]
        elif _DEICTIC.fullmatch(_fold(instruction)):
            identity = memory.selected_event_id
        else:
            raise _Clarify()
        identity, notes = validate_briefing(identity, notes)
        if identity is None:
            raise _Clarify()
        result = self._briefing_factory().prepare(event_id=identity, user_context=notes)
        _read_only(result)
        if result.get('reservation_created') is not False or result.get('status') not in {'ready', 'needs_clarification'}:
            raise ValueError()
        if result['status'] == 'ready' and type(result.get('facts')) is not dict:
            raise ValueError()
        if result['status'] == 'needs_clarification' and result.get('facts') is not None:
            raise ValueError()
        return (_briefing_text(result, identity), 'succeeded') if result['status'] == 'ready' else (
            _text(result['text']), 'needs_clarification')

    def _availability(self, message):
        match = _AVAILABILITY.fullmatch(_fold(message))
        if match is None:
            raise _Clarify()
        first, last, start, end, duration, buffer = match.groups()
        expected_zone, today = None, None
        if first in {'hoje', 'amanha'} or last in {'hoje', 'amanha'}:
            today, expected_zone = self._today()
        def resolve(value):
            if value in {'hoje', 'amanha'}:
                return (today + timedelta(days=value == 'amanha')).isoformat()
            try:
                return date.fromisoformat(value).isoformat()
            except ValueError:
                raise _Clarify() from None
        arguments = {'date_from': resolve(first), 'date_to': resolve(last or first),
                     'window_start': start, 'window_end': end, 'duration_minutes': int(duration),
                     'buffer_minutes': int(buffer or '0')}
        validate_request(**arguments)
        result = self._availability_factory().find(**arguments)
        return _availability_text(result, arguments, expected_zone), 'succeeded'

    def _suggestion(self, message, memory):
        match = _SUGGESTION.fullmatch(message)
        if match is None or self._reschedule_factory is None:
            raise _Clarify()
        identity, first, last, start, end, buffer = match.groups()
        identity = identity or memory.selected_event_id
        identity, _ = validate_briefing(identity, '')
        if identity is None:
            raise _Clarify()
        first, last = _fold(first), _fold(last or first)
        today = self._today()[0] if first in {'hoje', 'amanha'} or last in {'hoje', 'amanha'} else None
        def resolve(value):
            if value in {'hoje', 'amanha'}:
                return (today + timedelta(days=value == 'amanha')).isoformat()
            try:
                return date.fromisoformat(value).isoformat()
            except ValueError:
                raise _Clarify() from None
        arguments = {'event_id': identity, 'date_from': resolve(first), 'date_to': resolve(last),
                     'window_start': start, 'window_end': end, 'buffer_minutes': int(buffer or '0')}
        validate_request(**{key: val for key, val in arguments.items() if key != 'event_id'}, duration_minutes=1)
        result = self._reschedule_factory().suggest(**arguments)
        _read_only(result)
        if result.get('reservation_created') is not False:
            raise ValueError()
        if result.get('status') == 'needs_clarification':
            if result.get('event') is not None or result.get('availability') is not None:
                raise ValueError()
            return PreparationChatResult(_text(result['question']), 'needs_clarification', 'suggest_reschedule')
        event = result['event']
        if (result.get('status') != 'ready' or event['event_id'] != identity
                or type(result.get('duration_minutes')) is not int):
            raise ValueError()
        params = {key: val for key, val in arguments.items() if key != 'event_id'} | {'duration_minutes': result['duration_minutes']}
        text = _availability_text(result['availability'], params, event['time_zone'])
        title = event['title'].replace('\n', ' ')[:160]
        text = text.replace('*Horários livres*', '*Sugestões para reagendar*\n' + title, 1)
        text += '\n\nPara mudar, informe a nova data, início e fim. Vou mostrar a prévia para você confirmar antes de alterar.'
        return PreparationChatResult(_text(text), 'succeeded', 'suggest_reschedule', identity, event['time_zone'])

    def respond(self, message, memory: QueryMemory) -> PreparationChatResult:
        action = _intent(message) or 'prepare_briefing'
        try:
            text = _message(message)
            if _intent(text) is None:
                raise _Clarify()
            memory = validate_memory(memory)
            if action == 'suggest_reschedule':
                return self._suggestion(text, memory)
            if action == 'summarize_agenda':
                text, status = self._summary(text)
            elif action == 'prepare_briefing':
                text, status = self._briefing(text, memory)
            else:
                text, status = self._availability(text)
            return PreparationChatResult(text, status, action)
        except (_Clarify, SummaryInputError, BriefingInputError, AvailabilityInputError):
            return PreparationChatResult(_QUESTIONS[action], 'needs_clarification', action)
        except Exception:
            return PreparationChatResult(_FAILURES[action], 'failed', action)
