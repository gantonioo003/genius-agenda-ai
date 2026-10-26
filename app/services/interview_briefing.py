"""Briefing e checklist compactos a partir de uma ocorrência relida; só leitura."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from html.parser import HTMLParser
import re
import unicodedata

from app.services.event_selection import EventSelectionService, validate_event_id
from app.services.time_rules import load_zone
from app.services.update_snapshot import validate_update_snapshot


@dataclass(frozen=True, repr=False)
class BriefingBinding:
    calendar_id: str
    time_zone: str


class BriefingInputError(ValueError):
    def __init__(self, fields):
        super().__init__('Informe o identificador da entrevista e contexto opcional em texto.')
        self.fields = tuple(field for field in dict.fromkeys(fields)
                            if field in {'event_id', 'user_context', 'body', 'query'})


class BriefingCollectionError(RuntimeError):
    def __init__(self):
        super().__init__('Não foi possível confirmar os dados atuais da entrevista.')


class BriefingSetupError(BriefingCollectionError):
    pass


def validate_input(event_id=None, user_context=''):
    fields = []
    if event_id is not None:
        try:
            if type(event_id) is not str or len(event_id) > 1024:
                raise ValueError()
            event_id.encode('utf-8')
            if not event_id.strip():
                event_id = None
            else:
                validate_event_id(event_id)
        except (ValueError, UnicodeError):
            fields.append('event_id')
    try:
        if type(user_context) is not str or len(user_context) > 1200:
            raise ValueError()
        user_context.encode('utf-8')
    except (ValueError, UnicodeError):
        fields.append('user_context')
    if fields:
        raise BriefingInputError(fields)
    return event_id, user_context


class _PlainHTML(HTMLParser):
    """Extrair somente texto; nenhum link, anexo ou instrução é executado."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.hidden += 1
        elif tag in {'br', 'p', 'div', 'li'}:
            self.parts.append(' ')

    def handle_endtag(self, tag):
        if tag in {'script', 'style'} and self.hidden:
            self.hidden -= 1
        elif tag in {'p', 'div', 'li'}:
            self.parts.append(' ')

    def handle_data(self, value):
        if not self.hidden:
            self.parts.append(value)


def _text(value, limit, *, html=False):
    if type(value) is not str:
        raise ValueError()
    value.encode('utf-8')
    if html:
        parser = _PlainHTML()
        parser.feed(value)
        parser.close()
        value = ''.join(parser.parts)
    value = unicodedata.normalize('NFC', value)
    value = ''.join(' ' if char.isspace() else char for char in value
                    if char.isspace() or not unicodedata.category(char).startswith('C'))
    value = ' '.join(value.split())
    truncated = len(value) > limit
    return (value[:limit - 1] + '…' if truncated else value), truncated


def _clarification(question, *, reason, missing_fields=()):
    return {'schema_version': 1, 'status': 'needs_clarification', 'reason': reason,
            'question': question, 'text': question, 'facts': None, 'provided_context': None,
            'missing_fields': list(missing_fields), 'checklist': [], 'mode': 'factual_template',
            'execution_allowed': False, 'reservation_created': False}


def _instant(value):
    if type(value) is not str or re.fullmatch(
        r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'
        r'(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})', value, re.ASCII) is None:
        raise ValueError()
    if not value.endswith('Z') and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
        raise ValueError()
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def _compose(event, binding, notes):
    zone = load_zone(binding.time_zone)
    title, title_cut = _text(event.title, 160)
    title = title or '(sem título)'
    location, location_cut = _text(event.location, 240)
    description, description_cut = _text(event.description, 600, html=True)
    notes, notes_cut = _text(notes, 600)
    if event.all_day:
        start, end = date.fromisoformat(event.start), date.fromisoformat(event.end)
        if start.isoformat() != event.start or end.isoformat() != event.end or end <= start:
            raise ValueError()
        timing = f'{start:%d/%m/%Y}: dia inteiro (fim exclusivo {end:%d/%m/%Y})'
        start_text, end_text = event.start, event.end
    else:
        start, end = _instant(event.start), _instant(event.end)
        if end <= start:
            raise ValueError()
        start, end = start.astimezone(zone), end.astimezone(zone)
        start_text, end_text = start.isoformat(), end.isoformat()
        timing = f'{start:%d/%m/%Y %H:%M} ({start:%z}) até {end:%d/%m/%Y %H:%M} ({end:%z})'
    checklist = [
        'Revise a descrição da vaga e os requisitos que recebeu.',
        'Prepare uma apresentação breve da sua trajetória.',
        'Separe exemplos de experiências e resultados.',
        'Anote perguntas sobre a vaga e a equipe.',
        'Confirme local ou link e teste o acesso ou planeje o deslocamento.',
        'Deixe currículo, anotações e materiais necessários à mão.',
    ]
    missing = []
    if not location:
        missing.append('location')
    if not description and not notes:
        missing.append('context')
    cuts = {name: cut for name, cut in [('title', title_cut), ('location', location_cut),
            ('description', description_cut), ('user_context', notes_cut)]}
    context = {'event_description': description or None, 'user_context': notes or None,
               'verified_externally': False, 'truncated_fields': [key for key, cut in cuts.items() if cut]}
    facts = {'event_id': event.event_id, 'title': title, 'start': start_text, 'end': end_text,
             'all_day': event.all_day, 'time_zone': binding.time_zone, 'status': event.status,
             'location': location or None}
    lines = ['Preparo da entrevista', '', 'Dados da agenda:', title, timing,
             'Fuso: ' + binding.time_zone,
             'Estado: ' + ('provisório' if event.status == 'tentative' else 'confirmado'),
             'Local/link cadastrado: ' + (location or 'não informado'),
             '', 'Contexto fornecido (não verificado externamente):']
    if description:
        lines.append('Descrição cadastrada: ' + description)
    if notes:
        lines.append('Suas notas: ' + notes)
    if not description and not notes:
        lines.append('Nenhum contexto adicional informado.')
    lines += ['', 'O que conferir:']
    if not location:
        lines.append('Confirme o local ou link com a organização.')
    lines.append('Empresa, cargo e etapa não são inferidos do título; confira esses dados no material recebido.')
    if any(cuts.values()):
        lines.append('Textos longos foram abreviados; consulte o conteúdo completo no evento ou nas suas notas.')
    lines += ['', 'Checklist sugerido (genérico):']
    lines += [f'{index}. {item}' for index, item in enumerate(checklist, 1)]
    text = '\n'.join(lines)
    if len(text) > 3500:
        raise ValueError()
    return {'schema_version': 1, 'status': 'ready', 'reason': None, 'question': None,
            'facts': facts, 'provided_context': context, 'missing_fields': missing,
            'checklist': checklist, 'text': text, 'mode': 'factual_template',
            'execution_allowed': False, 'reservation_created': False}


class InterviewBriefingService:
    def __init__(self, gateway_factory):
        self._gateway_factory = gateway_factory

    def prepare(self, *, event_id=None, user_context=''):
        event_id, notes = validate_input(event_id, user_context)
        if event_id is None:
            return _clarification('Qual entrevista devo preparar? Selecione um evento ou informe o identificador completo.',
                                  reason='missing_event', missing_fields=('event_id',))
        try:
            with self._gateway_factory() as gateway:
                binding = gateway.binding()
                if type(binding) is not BriefingBinding:
                    raise ValueError()
                validate_event_id(binding.calendar_id)
                if binding.calendar_id == 'primary':
                    raise ValueError()
                load_zone(binding.time_zone)
                selection = EventSelectionService(gateway).select(event_id=event_id, time_zone=binding.time_zone)
                if selection.status != 'selected':
                    result = _clarification('Essa entrevista não está disponível. Consulte a agenda e selecione um evento atual.',
                                            reason='event_not_found', missing_fields=('event_id',))
                else:
                    snapshot = validate_update_snapshot(gateway.get_update_snapshot(event_id, time_zone=binding.time_zone),
                                                        event_id, binding.time_zone)
                    if snapshot.calendar_id != binding.calendar_id:
                        raise ValueError()
                    fields = ('event_id', 'start', 'end', 'all_day', 'status', 'time_zone')
                    prior_title = selection.event.title or '(sem título)'
                    current_title = snapshot.event.title or '(sem título)'
                    if prior_title != current_title or any(getattr(selection.event, key) != getattr(snapshot.event, key) for key in fields):
                        result = _clarification('A entrevista mudou durante a consulta. Consulte a agenda e selecione o evento novamente.',
                                                reason='event_changed', missing_fields=('event_id',))
                    else:
                        result = _compose(snapshot.event, binding, notes)
            return result
        except BriefingSetupError:
            raise
        except Exception:
            raise BriefingCollectionError() from None
