"""Consultas do painel local: sessão própria, projeções mínimas e nenhuma escrita."""

from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from datetime import date as Date, datetime, time, timezone
from hashlib import sha256
import base64
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

from fastapi import Depends, Request
from fastapi.responses import JSONResponse

from app.api_queries import _listing_period, _public_event, QueryInputError
from app.google_calendar import CalendarSetupError, GoogleCalendarGateway, calendar_client, resolve_calendar
from app.reminder_store import ReminderRecord, ReminderHistory, ReminderStore
from app.services.agenda import AgendaEvent
from app.services.event_selection import EventLookup, validate_event_id
from app.services.interview_briefing import _text
from app.services.summary_context import (
    MAX_EVENTS, MAX_PAGES, SummaryCalendarBinding, SummaryCalendarSnapshot,
    SummaryCollectionError, SummaryLimitError, SummarySetupError,
)
from app.services.time_rules import load_zone
from app.services.update_snapshot import validate_update_snapshot
from app.settings import PROJECT_ROOT
from app.summary_calendar import GoogleSummaryCalendarGateway


MAX_REMINDERS = 10000
STATES = frozenset({'pending', 'blocked', 'reserved', 'sending', 'accepted', 'uncertain', 'cancelled', 'expired'})
_HASH = re.compile(r'[0-9a-f]{64}\Z', re.ASCII)
_REASONS = {
    'planned': 'Aguardando o horário do aviso.', 'replanned': 'Aviso atualizado para o novo horário.',
    'reserved': 'O sistema reservou esta tentativa.', 'sending': 'A tentativa de envio foi iniciada.',
    'accepted': 'Envio aceito pelo provedor; entrega e leitura não foram confirmadas.',
    'uncertain': 'O resultado do envio precisa ser conferido antes de repetir.',
    'released': 'Tentativa liberada antes de iniciar o envio.',
    'reminder_expired': 'O prazo para este aviso terminou.',
    'event_cancelled': 'Compromisso removido ou cancelado.',
    'event_rescheduled': 'Aviso cancelado após reagendamento.',
    'event_changed': 'Compromisso alterado; aviso anterior cancelado.',
    'configuration_changed': 'Configuração dos avisos alterada.',
    'configuration_disabled': 'Os lembretes foram desativados.',
    'slot_disabled': 'Este tipo de aviso foi desativado.',
    'binding_changed': 'O vínculo do destinatário ou da agenda mudou.',
    'channel_window_unknown': 'A janela de conversa ainda não foi confirmada.',
    'channel_window_closed': 'A janela de conversa do canal está encerrada.',
    'channel_window_margin': 'Não há margem suficiente na janela de conversa.',
    'template_unavailable': 'O modelo de mensagem necessário não está disponível.',
    'permission_missing': 'O envio não está autorizado na configuração atual.',
    'recipient_mismatch': 'O destinatário configurado precisa ser conferido.',
    'invalid_format': 'O formato do aviso precisa ser conferido.',
    'invalid_event': 'Os dados do compromisso precisam ser conferidos.',
    'calendar_unavailable': 'Não foi possível conferir a agenda.',
}


@dataclass(frozen=True, repr=False)
class PanelCalendarBinding:
    calendar_id: str
    calendar_name: str
    time_zone: str


class PanelCalendarGateway:
    """Composição somente de leituras já usadas pelos serviços existentes."""

    def __init__(self, service):
        self.service = service
        self.summary = GoogleSummaryCalendarGateway(service)
        self.calendar = GoogleCalendarGateway(service)

    def binding(self):
        calendar = resolve_calendar(self.service)
        binding = self.summary.binding()
        if calendar.get('id') != binding.calendar_id or calendar.get('timeZone') != binding.time_zone:
            raise SummaryCollectionError()
        return _binding(PanelCalendarBinding(binding.calendar_id, calendar['summary'], binding.time_zone))

    def list_events(self, period, *, expected_binding):
        return self.summary.list_events(period, expected_binding=SummaryCalendarBinding(
            expected_binding.calendar_id, expected_binding.time_zone))

    def get_update_snapshot(self, event_id, *, time_zone):
        return self.calendar.get_update_snapshot(event_id, time_zone=time_zone)

    def get_event(self, event_id, *, time_zone):
        return self.calendar.get_event(event_id, time_zone=time_zone)


@contextmanager
def default_panel_calendar_gateway():
    with calendar_client(interactive=False) as service:
        yield PanelCalendarGateway(service)


async def _panel_session(request: Request):
    # Importação tardia permite manter esta fronteira independente da montagem.
    from app.panel_security import require_panel_session
    return await require_panel_session(request)


def _response(*, data=None, status=200, code=None, message=None):
    if message is None:
        message = 'Consulta concluída.' if code is None else 'Não foi possível concluir a consulta.'
    return JSONResponse(status_code=status, headers={'Cache-Control': 'no-store', 'Vary': 'Cookie'},
        content={'status': 'succeeded' if code is None else 'failed', 'message': message,
                 'data': data, 'error': None if code is None else {'code': code, 'retryable': False}})


def _invalid():
    return _response(status=422, code='invalid_request', message='Confira os parâmetros da consulta.')


def _arguments(request, allowed):
    if len(request.scope.get('query_string', b'')) > 4096:
        raise ValueError()
    result = {}
    for key, value in request.query_params.multi_items():
        if key not in allowed or key in result:
            raise ValueError()
        result[key] = value
    return result


def _now(request):
    clock = getattr(request.app.state, 'panel_clock', None)
    value = datetime.now(timezone.utc) if clock is None else clock()
    if type(value) is not datetime or value.utcoffset() is None:
        raise ValueError()
    return value.astimezone(timezone.utc)


def _binding(value):
    if type(value) is not PanelCalendarBinding:
        raise ValueError()
    validate_event_id(value.calendar_id)
    if value.calendar_id == 'primary':
        raise ValueError()
    load_zone(value.time_zone)
    if type(value.calendar_name) is not str or not value.calendar_name.strip() or len(value.calendar_name) > 4000:
        raise ValueError()
    return value


def _gateway_factory(request):
    return getattr(request.app.state, 'panel_calendar_gateway_factory', None) or default_panel_calendar_gateway


def _event(value, zone, *, details=False):
    normalized = (replace(value, title='(sem título)')
                  if isinstance(value, AgendaEvent) and isinstance(value.title, str) and not value.title.strip() else value)
    public = _public_event(normalized)
    title, title_cut = _text(public['title'], 4000)
    if public['all_day']:
        date, start, end = public['start'], None, None
        start_at, end_at = public['start'], public['end']
    else:
        first = datetime.fromisoformat(public['start']).astimezone(zone)
        last = datetime.fromisoformat(public['end']).astimezone(zone)
        date, start, end = first.date().isoformat(), first.strftime('%H:%M'), last.strftime('%H:%M')
        start_at, end_at = first.isoformat(), last.isoformat()
    result = {'id': public['event_id'], 'event_id': public['event_id'], 'title': title or '(sem título)',
              'date': date, 'start': start, 'end': end, 'start_at': start_at, 'end_at': end_at,
              'all_day': public['all_day'], 'time_zone': zone.key, 'status': public['status'],
              'location': None, 'description': None, 'html_link': None,
              'title_truncated': title_cut, 'details_loaded': details}
    if public['html_link']:
        link = urlsplit(public['html_link'])
        if link.scheme == 'https' and link.netloc and link.username is None and link.password is None:
            result['html_link'] = public['html_link']
    if details:
        result['description'], description_cut = _text(value.description, 256 * 1024, html=True)
        result['location'], location_cut = _text(value.location, 256 * 1024)
        result['details_truncated'] = description_cut or location_cut
    return result


def _period_input(request):
    args = _arguments(request, {'date', 'period', 'time_zone'})
    kind = args.get('period', 'day')
    # Validate lexical inputs before opening the provider; effective zone is server-bound later.
    if kind not in {'day', 'week', 'month'}:
        raise ValueError()
    if 'date' in args:
        _listing_period(args['date'], kind, 'UTC')
    if 'time_zone' in args:
        load_zone(args['time_zone'])
    return args, kind


def _read_snapshot(snapshot, binding, period):
    if (type(snapshot) is not SummaryCalendarSnapshot or snapshot.complete is not True
            or snapshot.binding != SummaryCalendarBinding(binding.calendar_id, binding.time_zone)
            or snapshot.period != period or type(snapshot.events) is not tuple
            or type(snapshot.pages_read) is not int or not 1 <= snapshot.pages_read <= MAX_PAGES
            or type(snapshot.raw_events_seen) is not int
            or not len(snapshot.events) <= snapshot.raw_events_seen <= MAX_EVENTS):
        raise ValueError()
    ids = [event.event_id for event in snapshot.events]
    if len(ids) != len(set(ids)):
        raise ValueError()
    return snapshot


class PanelReminderReader:
    """Reutiliza os leitores/validadores do ledger em conexões SQLite mode=ro.

    Não possui initialize, configure, tick ou qualquer método de escrita.
    Cada leitura mantém um snapshot de transação e fecha a conexão no retorno.
    """

    _get = staticmethod(ReminderStore._get)
    _record = staticmethod(ReminderStore._record)
    _config = staticmethod(ReminderStore._config)

    def __init__(self, path):
        self.path = Path(path).resolve()

    @contextmanager
    def _operation(self):
        if not self.path.is_file():
            raise RuntimeError()
        with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=1)) as connection:
            connection.row_factory = sqlite3.Row
            # Opção da conexão, sem alterar arquivo: exigida pelo validador do ledger.
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('BEGIN')
            ReminderStore._check(connection)
            yield connection

    def list_reminders(self, *, states=None, limit=100):
        return ReminderStore.list_reminders(self, states=states, limit=limit)

    def get(self, key):
        return ReminderStore.get(self, key)

    def history(self, key):
        return ReminderStore.history(self, key)

    def config(self):
        return ReminderStore.config(self)


def _store(request):
    factory = getattr(request.app.state, 'reminder_store_factory', None)
    if factory is not None:
        store = factory()
        return PanelReminderReader(store.path) if isinstance(store, ReminderStore) else store
    path = request.app.state.settings.reminder_database_path
    path = path if path.is_absolute() else PROJECT_ROOT / path
    return PanelReminderReader(path)


def _stamp(value):
    if type(value) is not datetime or value.utcoffset() is None:
        raise ValueError()
    return value.astimezone(timezone.utc).isoformat()


def _reminder(value, *, cards_enabled=False):
    if type(value) is not ReminderRecord or _HASH.fullmatch(value.key) is None or value.state not in STATES:
        raise ValueError()
    validate_event_id(value.event_id)
    if type(value.offset_minutes) is not int or not 1 <= value.offset_minutes <= 10080:
        raise ValueError()
    load_zone(value.calendar_timezone)
    title, _ = _text(value.title, 160)
    return {'id': value.key, 'event_id': value.event_id, 'title': title or 'Compromisso',
            'start_at': _stamp(value.start_at), 'due_at': _stamp(value.due_at),
            'time_zone': value.calendar_timezone, 'offset_minutes': value.offset_minutes,
            'format': 'card' if value.offset_minutes == 60 and cards_enabled else 'text',
            'format_is_planned': True,
            'state': value.state, 'reason': _REASONS.get(value.reason, 'O estado deste aviso precisa ser conferido.'),
            'updated_at': _stamp(value.updated_at)}


def _history(value):
    if (type(value) is not ReminderHistory or value.from_state is not None and value.from_state not in STATES
            or value.to_state not in STATES):
        raise ValueError()
    return {'from_state': value.from_state, 'to_state': value.to_state,
            'reason': _REASONS.get(value.reason, 'Mudança no estado do aviso.'), 'at': _stamp(value.at)}


def _cursor(value):
    if type(value) is not str or len(value) > 256 or not re.fullmatch(r'[A-Za-z0-9_-]+', value, re.ASCII):
        raise ValueError()
    raw = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
    result = json.loads(raw)
    if (type(result) is not dict or set(result) != {'offset', 'snapshot'}
            or type(result['offset']) is not int or not 0 <= result['offset'] < MAX_REMINDERS
            or type(result['snapshot']) is not str or _HASH.fullmatch(result['snapshot']) is None):
        raise ValueError()
    return result


def _reminder_arguments(request):
    args = _arguments(request, {'states', 'limit', 'cursor'})
    limit = args.get('limit', '25')
    if not re.fullmatch(r'[0-9]{1,2}', limit, re.ASCII) or not 1 <= int(limit) <= 50:
        raise ValueError()
    states = None
    if 'states' in args:
        states = args['states'].split(',')
        if not states or len(states) != len(set(states)) or set(states) - STATES:
            raise ValueError()
        states = tuple(sorted(states))
    return int(limit), states, _cursor(args['cursor']) if 'cursor' in args else None


def register_panel_read_routes(router):
    protected = [Depends(_panel_session)]

    @router.get('/connection/status', dependencies=protected, name='panel_connection_status')
    def connection_status(request: Request):
        try:
            _arguments(request, set())
        except ValueError:
            return _invalid()
        return _response(data={'authenticated': True, 'api': 'responding', 'mode': 'local_confirmed_agenda',
            'integrations': {'google_calendar': 'not_checked', 'gemini': 'not_checked',
                             'n8n': 'not_checked', 'whatsapp': 'not_checked'}})

    @router.get('/events', dependencies=protected, name='panel_list_events')
    def list_events(request: Request):
        try:
            args, kind = _period_input(request)
        except (ValueError, QueryInputError):
            return _invalid()
        try:
            with _gateway_factory(request)() as gateway:
                binding = _binding(gateway.binding())
                zone = load_zone(binding.time_zone)
                if 'time_zone' in args and args['time_zone'] != binding.time_zone:
                    return _invalid()
                now = _now(request)
                today = now.astimezone(zone).date().isoformat()
                reference = args.get('date', today)
                period = _listing_period(reference, kind, binding.time_zone)
                snapshot = _read_snapshot(gateway.list_events(period, expected_binding=binding), binding, period)
                entries = [_event(value, zone) for value in snapshot.events]
                def bounds(value):
                    if value['all_day']:
                        return (datetime.combine(Date.fromisoformat(value['start_at']), time.min, tzinfo=zone),
                                datetime.combine(Date.fromisoformat(value['end_at']), time.min, tzinfo=zone))
                    return datetime.fromisoformat(value['start_at']), datetime.fromisoformat(value['end_at'])
                if any(start.astimezone(timezone.utc) >= period.end.astimezone(timezone.utc)
                       or end.astimezone(timezone.utc) <= period.start.astimezone(timezone.utc)
                       for start, end in map(bounds, entries)):
                    raise ValueError()
                entries.sort(key=lambda value: (bounds(value)[0].astimezone(timezone.utc), value['id']))
                result = {'events': entries, 'calendar_name': binding.calendar_name, 'time_zone': binding.time_zone,
                    'reference_date': reference, 'today': today, 'collected_at': now.isoformat(),
                    'period': {'kind': kind, 'start': period.start.isoformat(), 'end': period.end.isoformat()},
                    'complete': True}
        except (CalendarSetupError, SummarySetupError):
            return _response(status=503, code='calendar_setup_unavailable', message='O acesso à agenda está indisponível.')
        except SummaryLimitError:
            return _response(status=503, code='calendar_limit_exceeded', message='O período excede os limites de consulta.')
        except Exception:
            return _response(status=502, code='calendar_collection_failed', message='Não foi possível confirmar os compromissos da agenda.')
        return _response(data=result)

    @router.get('/events/{event_id:path}', dependencies=protected, name='panel_get_event')
    def get_event(request: Request, event_id: str):
        try:
            _arguments(request, set())
            validate_event_id(event_id)
        except ValueError:
            return _invalid()
        try:
            with _gateway_factory(request)() as gateway:
                binding = _binding(gateway.binding())
                lookup = gateway.get_event(event_id, time_zone=binding.time_zone)
                if type(lookup) is not EventLookup or lookup.calendar_name != binding.calendar_name:
                    raise ValueError()
                if lookup.event is None:
                    return _response(status=404, code='event_not_found', message='O compromisso não foi encontrado.')
                snapshot = validate_update_snapshot(gateway.get_update_snapshot(event_id, time_zone=binding.time_zone), event_id, binding.time_zone)
                if snapshot.calendar_id != binding.calendar_id or snapshot.calendar_name != binding.calendar_name:
                    raise ValueError()
                if any(getattr(lookup.event, key) != getattr(snapshot.event, key) for key in ('event_id', 'title', 'start', 'end', 'all_day', 'time_zone', 'status')):
                    return _response(status=409, code='event_changed', message='O compromisso mudou durante a consulta. Atualize os dados.')
                result = {'event': _event(snapshot.event, load_zone(binding.time_zone), details=True),
                    'time_zone': binding.time_zone, 'calendar_name': binding.calendar_name, 'collected_at': _now(request).isoformat()}
        except (CalendarSetupError, SummarySetupError):
            return _response(status=503, code='calendar_setup_unavailable', message='O acesso à agenda está indisponível.')
        except Exception:
            return _response(status=502, code='event_collection_failed', message='Não foi possível conferir os detalhes do compromisso.')
        return _response(data=result)

    @router.get('/reminders', dependencies=protected, name='panel_list_reminders')
    def list_reminders(request: Request):
        try:
            limit, states, cursor = _reminder_arguments(request)
        except (ValueError, UnicodeError, json.JSONDecodeError):
            return _invalid()
        try:
            store = _store(request)
            records = store.list_reminders(states=states, limit=MAX_REMINDERS)
            if type(records) is not tuple or len(records) >= MAX_REMINDERS:
                return _response(status=503, code='reminder_limit_exceeded', message='O histórico excede o limite de consulta do painel.')
            cards_enabled = getattr(request.app.state.settings, 'reminder_cards_enabled', False) is True
            items = sorted((_reminder(value, cards_enabled=cards_enabled) for value in records), key=lambda value: (value['due_at'], value['id']))
            if len({value['id'] for value in items}) != len(items):
                raise ValueError()
            digest = sha256(json.dumps({'states': states, 'limit': limit, 'items': items}, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()
            if cursor is not None and cursor['snapshot'] != digest:
                return _response(status=409, code='collection_changed', message='Os avisos mudaram. Atualize a lista antes de continuar.')
            offset = 0 if cursor is None else cursor['offset']
            if offset > len(items):
                return _invalid()
            shown = items[offset:offset + limit]
            next_cursor = None
            if offset + limit < len(items):
                next_cursor = base64.urlsafe_b64encode(json.dumps({'offset': offset + limit, 'snapshot': digest}, separators=(',', ':')).encode()).decode().rstrip('=')
            counts = {state: sum(item['state'] == state for item in items) for state in sorted(STATES)}
            result = {'items': shown, 'next_cursor': next_cursor, 'complete': True,
                'total': len(items), 'count': len(shown), 'counts': counts, 'counts_scope': 'filtered', 'snapshot_at': _now(request).isoformat()}
        except Exception:
            return _response(status=503, code='reminders_unavailable', message='Não foi possível consultar os registros de lembretes.')
        return _response(data=result)

    @router.get('/reminders/{reminder_id}', dependencies=protected, name='panel_get_reminder')
    def get_reminder(request: Request, reminder_id: str):
        try:
            _arguments(request, set())
            if _HASH.fullmatch(reminder_id) is None:
                raise ValueError()
        except ValueError:
            return _invalid()
        try:
            store = _store(request)
            value = store.get(reminder_id)
            if value is None:
                return _response(status=404, code='reminder_not_found', message='O aviso não foi encontrado.')
            history = store.history(reminder_id)
            if type(history) is not tuple or len(history) > 1000:
                raise ValueError()
            current = store.get(reminder_id)
            if current != value:
                return _response(status=409, code='reminder_changed', message='O aviso mudou durante a consulta. Atualize os dados.')
            result = {'item': _reminder(value, cards_enabled=getattr(request.app.state.settings, 'reminder_cards_enabled', False) is True),
                      'history': [_history(entry) for entry in history], 'complete': True}
        except Exception:
            return _response(status=503, code='reminders_unavailable', message='Não foi possível conferir o histórico deste aviso.')
        return _response(data=result)
