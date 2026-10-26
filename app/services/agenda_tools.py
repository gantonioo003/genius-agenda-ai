"""Ferramentas de consulta; validação local antes de abrir o conector."""

from dataclasses import dataclass, field
from datetime import date, datetime
import re

from app.agent_query_contract import QueryPlan
from app.google_calendar import CalendarSetupError
from app.llm import ModelError
from app.services.agenda import AgendaEvent, CalendarListing, day_period, week_period, month_period
from app.services.event_selection import EventLookup, validate_event_id
from app.services.time_rules import load_zone, validate_interval

_DATE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}\Z', re.ASCII)
_BUILDERS = {'day': day_period, 'week': week_period, 'month': month_period}


@dataclass(frozen=True)
class QueryResult:
    status: str
    action: str | None
    message: str
    data: dict | None = field(repr=False)
    http_status: int = 200
    error_code: str | None = None


def minimal_event(event, *, expected_id=None):
    """Projeção explícita: subclasses com descrição/local não ampliam a saída."""
    if not isinstance(event, AgendaEvent):
        raise ValueError('Evento inválido.')
    validate_event_id(event.event_id)
    if expected_id is not None and event.event_id != expected_id:
        raise ValueError('Identidade divergente.')
    if (not isinstance(event.title, str) or not event.title.strip()
            or not isinstance(event.start, str) or not isinstance(event.end, str)
            or type(event.all_day) is not bool or event.status not in ('confirmed', 'tentative')
            or (event.html_link is not None and not isinstance(event.html_link, str))):
        raise ValueError('Evento inválido.')
    load_zone(event.time_zone)
    for value in (event.title, event.start, event.end, event.html_link):
        if value is not None:
            value.encode('utf-8')
    if event.all_day:
        if (not _DATE.fullmatch(event.start) or not _DATE.fullmatch(event.end)
                or date.fromisoformat(event.end) <= date.fromisoformat(event.start)):
            raise ValueError('Intervalo inválido.')
    else:
        validate_interval(datetime.fromisoformat(event.start), datetime.fromisoformat(event.end))
    return {name: getattr(event, name) for name in ('event_id', 'title', 'start', 'end', 'all_day', 'time_zone', 'status', 'html_link')}


def calendar_name(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Agenda inválida.')
    value.encode('utf-8')
    return value


def _validated_arguments(plan):
    try:
        if not isinstance(plan, QueryPlan) or type(plan.arguments) is not dict:
            raise ValueError()
        args = plan.arguments
        if plan.name == 'ask_query':
            if set(args) != {'reason'} or args['reason'] not in ('missing_information', 'unsupported_request'):
                raise ValueError()
            return args, None
        fields = {'date', 'period', 'time_zone'} if plan.name == 'list_events' else {'event_id', 'time_zone'} if plan.name == 'get_event' else None
        if fields is None or set(args) != fields or any(not isinstance(value, str) for value in args.values()):
            raise ValueError()
        for value in args.values():
            value.encode('utf-8')
        load_zone(args['time_zone'])
        if plan.name == 'get_event':
            validate_event_id(args['event_id'])
            return args, None
        if not _DATE.fullmatch(args['date']) or args['period'] not in _BUILDERS:
            raise ValueError()
        period = _BUILDERS[args['period']](date.fromisoformat(args['date']), args['time_zone'])
        return args, period
    except Exception:
        raise ModelError('llm_invalid_response') from None


class AgendaReadTools:
    """Recebe uma factory de leitura; não expõe método de criação ou alteração."""

    def __init__(self, gateway_factory):
        self._gateway_factory = gateway_factory

    def execute(self, plan: QueryPlan) -> QueryResult:
        args, selected_period = _validated_arguments(plan)
        if plan.name == 'ask_query':
            question = ('Para consultar, informe uma data e o período (dia, semana ou mês), ou o ID do evento.'
                        if args['reason'] == 'missing_information' else
                        'Esta parte permite apenas consultar a agenda. Você quer consultar um período ou um evento pelo ID?')
            return QueryResult('needs_clarification', None, question,
                               {'reason': args['reason'], 'question': question, 'execution_allowed': False})
        action = 'list' if plan.name == 'list_events' else 'get'
        try:
            with self._gateway_factory() as gateway:
                if plan.name == 'list_events':
                    listing = gateway.list_events(selected_period)
                    if not isinstance(listing, CalendarListing) or not isinstance(listing.events, (tuple, list)):
                        raise ValueError('Listagem inválida.')
                    events = [minimal_event(event) for event in listing.events]
                    if len({event['event_id'] for event in events}) != len(events):
                        raise ValueError('Identidades repetidas.')
                    data = {'calendar_name': calendar_name(listing.calendar_name),
                            'period': {'start': selected_period.start.isoformat(), 'end': selected_period.end.isoformat(), 'time_zone': selected_period.time_zone},
                            'events': events, 'execution_allowed': False}
                else:
                    lookup = gateway.get_event(args['event_id'], time_zone=args['time_zone'])
                    if not isinstance(lookup, EventLookup):
                        raise ValueError('Consulta inválida.')
                    data = {'calendar_name': calendar_name(lookup.calendar_name),
                            'event': minimal_event(lookup.event, expected_id=args['event_id']) if lookup.event is not None else None,
                            'execution_allowed': False}
        except CalendarSetupError:
            return QueryResult('failed', action, 'O acesso à agenda está indisponível.', None, 503, 'calendar_setup_unavailable')
        except Exception:
            return QueryResult('failed', action, 'Não foi possível concluir a consulta da agenda.', None, 502, 'operation_failed')
        if action == 'get' and data['event'] is None:
            return QueryResult('not_found', 'get', 'O evento não foi encontrado.', data, 404, 'event_not_found')
        return QueryResult('succeeded', action, 'Consulta concluída com dados conferidos da agenda.', data)
