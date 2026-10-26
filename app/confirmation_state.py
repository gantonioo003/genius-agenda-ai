"""Consentimento explícito e limitado a uma proposta, sem efeitos externos."""
from dataclasses import dataclass, fields
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
import unicodedata
from uuid import UUID

from app.services.agenda import AgendaEvent
from app.services.event_selection import validate_event_id
from app.services.request_preparation import RequestPreparationService
from app.services.time_rules import load_zone, validate_interval

_HASH = re.compile(r'[0-9a-f]{64}\Z', re.ASCII)
_EVENT_KEYS = {'event_id', 'title', 'start', 'end', 'all_day', 'time_zone', 'status', 'html_link'}
_ERRORS = {
    'confirmation_not_found': (404, 'A proposta de confirmação não está disponível.'),
    'confirmation_expired': (409, 'A confirmação expirou. Solicite uma nova prévia.'),
    'confirmation_stale': (409, 'A proposta mudou. Confira uma nova prévia antes de aprovar.'),
    'confirmation_not_approved': (409, 'A proposta ainda não foi aprovada.'),
    'confirmation_consumed': (409, 'Esta aprovação já foi utilizada.'),
    'confirmation_invalid': (422, 'Os dados da confirmação são inválidos.'),
    'confirmation_unavailable': (503, 'O registro da confirmação está temporariamente indisponível.'),
    'preparation_required': (409, 'Prepare um pedido completo e selecione o evento antes de confirmar.'),
    'ambiguous_confirmation': (422, 'Informe uma confirmação explícita para esta proposta.'),
}

class ConfirmationError(RuntimeError):
    def __init__(self, code='confirmation_unavailable'):
        self.code = code if isinstance(code, str) and code in _ERRORS else 'confirmation_unavailable'
        self.http_status, message = _ERRORS[self.code]
        self.status_code = self.http_status
        super().__init__(message)

@dataclass(frozen=True, repr=False)
class ConfirmationProposal:
    proposal_id: str
    action: str
    parameters: dict
    request_hash: str
    calendar_id: str
    event_id: str | None
    event_version: str | None
    event: dict | None
    created_at: str
    expires_at: str
    state: str = 'pending'
    approved_at: str | None = None
    approval_message_hash: str | None = None
    consumed_at: str | None = None

    def __repr__(self):
        return 'ConfirmationProposal(<private>)'

@dataclass(frozen=True, repr=False)
class ConfirmationClaim:
    proposal_id: str
    action: str
    parameters: dict
    calendar_id: str
    event_id: str | None
    event_version: str | None
    request_hash: str

    def __repr__(self):
        return 'ConfirmationClaim(<private>)'

def confirmation_timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ConfirmationError('confirmation_invalid')
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() != timedelta(0) or parsed.isoformat() != value:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        raise ConfirmationError('confirmation_invalid') from None

def _text(value, limit=4000, *, multiline=True, nonempty=False):
    if not isinstance(value, str) or len(value) > limit or nonempty and not value.strip():
        raise ValueError()
    value.encode('utf-8')
    if any(unicodedata.category(char).startswith('C') and (not multiline or char not in '\t\r\n') for char in value):
        raise ValueError()
    return value

class _SelectionValidated(Exception):
    pass

class _ValidationGateway:
    def get_event(self, *_args, **_kwargs):
        raise _SelectionValidated()
    def list_events(self, *_args, **_kwargs):
        raise ValueError()

def canonical_parameters(action, parameters):
    """Valida apenas parâmetros prontos; nunca consulta nem aceita busca pendente."""
    try:
        if action not in {'create', 'edit', 'reschedule', 'delete'} or type(parameters) is not dict:
            raise ValueError()
        values = {name: _text(value) for name, value in parameters.items()}
        if any(type(name) is not str for name in values):
            raise ValueError()
        if len(json.dumps(values, ensure_ascii=False).encode('utf-8')) > 24 * 1024:
            raise ValueError()
        required = {'time_zone'}
        if action == 'create':
            required |= {'title', 'start', 'end'}
            if set(values) - (required | {'description'}) or not required <= values.keys():
                raise ValueError()
            values.setdefault('description', '')
        else:
            required.add('event_id')
            allowed = required | ({'start', 'end'} if action == 'reschedule' else
                                  {'title', 'description', 'location'} if action == 'edit' else set())
            if action == 'reschedule':
                required |= {'start', 'end'}
            if set(values) - allowed or not required <= values.keys():
                raise ValueError()
            validate_event_id(values['event_id'])
        load_zone(values['time_zone'])
        if 'title' in values:
            values['title'] = values['title'].strip()
        for name in ('start', 'end'):
            if name in values:
                if 'T' not in values[name]:
                    raise ValueError()
                values[name] = datetime.fromisoformat(values[name]).isoformat()
        try:
            prepared = RequestPreparationService(_ValidationGateway()).prepare({'action': action, **values})
        except _SelectionValidated:
            return dict(values)
        if prepared.status != 'ready' or prepared.parameters != values:
            raise ValueError()
        return dict(values)
    except (TypeError, ValueError, UnicodeError, KeyError, OverflowError):
        raise ConfirmationError('confirmation_invalid') from None

def _event(value, expected_id, time_zone):
    if type(value) is not dict or set(value) != _EVENT_KEYS:
        raise ValueError()
    if value['event_id'] != expected_id or value['time_zone'] != time_zone and value['all_day'] is not True:
        raise ValueError()
    validate_event_id(value['event_id'])
    _text(value['title'], nonempty=True)
    for name in ('start', 'end', 'time_zone'):
        _text(value[name], nonempty=True, multiline=False)
    if value['html_link'] is not None:
        _text(value['html_link'])
    if value['status'] not in ('confirmed', 'tentative') or type(value['all_day']) is not bool:
        raise ValueError()
    load_zone(value['time_zone'])
    if value['all_day']:
        from datetime import date
        start, end = date.fromisoformat(value['start']), date.fromisoformat(value['end'])
        if start.isoformat() != value['start'] or end.isoformat() != value['end'] or start >= end:
            raise ValueError()
    else:
        validate_interval(datetime.fromisoformat(value['start']), datetime.fromisoformat(value['end']))
    return dict(value)

def proposal_request_hash(action, parameters, calendar_id, event_id, event_version, event):
    try:
        raw = json.dumps({'action': action, 'parameters': parameters, 'calendar_id': calendar_id,
                          'event_id': event_id, 'event_version': event_version, 'event': event},
                         sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
        if len(raw) > 256 * 1024:
            raise ValueError()
        return sha256(b'genius-confirmation-request:v1\0' + raw).hexdigest()
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ConfirmationError('confirmation_invalid') from None

def validate_confirmation(proposal):
    try:
        if type(proposal) is not ConfirmationProposal:
            raise ValueError()
        parsed_id = UUID(proposal.proposal_id)
        if parsed_id.version != 4 or str(parsed_id) != proposal.proposal_id:
            raise ValueError()
        parameters = canonical_parameters(proposal.action, proposal.parameters)
        if parameters != proposal.parameters:
            raise ValueError()
        _text(proposal.calendar_id, 1024, multiline=False, nonempty=True)
        event = None
        if proposal.action == 'create':
            if proposal.event_id is not None or proposal.event_version is not None or proposal.event is not None:
                raise ValueError()
        else:
            if proposal.event_id != parameters['event_id']:
                raise ValueError()
            validate_event_id(proposal.event_id)
            _text(proposal.event_version, 2048, multiline=False, nonempty=True)
            event = _event(proposal.event, proposal.event_id, parameters['time_zone'])
            if proposal.action == 'reschedule' and event['all_day']:
                raise ValueError()
        created = confirmation_timestamp(proposal.created_at)
        expires = confirmation_timestamp(proposal.expires_at)
        if not timedelta(0) < expires - created <= timedelta(seconds=900):
            raise ValueError()
        if proposal.state not in {'pending', 'approved', 'consumed', 'revoked', 'expired', 'invalidated'}:
            raise ValueError()
        if (proposal.approved_at is None) != (proposal.approval_message_hash is None):
            raise ValueError()
        if proposal.approved_at is not None:
            approved = confirmation_timestamp(proposal.approved_at)
            if not created <= approved < expires or not isinstance(proposal.approval_message_hash, str) or not _HASH.fullmatch(proposal.approval_message_hash):
                raise ValueError()
        if proposal.state == 'pending' and proposal.approved_at is not None:
            raise ValueError()
        if proposal.state in {'approved', 'consumed'} and proposal.approved_at is None:
            raise ValueError()
        if proposal.state == 'consumed':
            consumed = confirmation_timestamp(proposal.consumed_at)
            if not approved <= consumed < expires:
                raise ValueError()
        elif proposal.consumed_at is not None:
            raise ValueError()
        if (not isinstance(proposal.request_hash, str) or not _HASH.fullmatch(proposal.request_hash)
                or proposal.request_hash != proposal_request_hash(proposal.action, parameters, proposal.calendar_id,
                    proposal.event_id, proposal.event_version, event)):
            raise ValueError()
        values = {item.name: getattr(proposal, item.name) for item in fields(ConfirmationProposal)}
        return ConfirmationProposal(**{**values, 'parameters': parameters, 'event': event})
    except (TypeError, ValueError, AttributeError, KeyError, UnicodeError, OverflowError):
        raise ConfirmationError('confirmation_invalid') from None

def encode_confirmation(proposal):
    canonical = validate_confirmation(proposal)
    return {item.name: getattr(canonical, item.name) for item in fields(ConfirmationProposal)}

def decode_confirmation(value):
    try:
        if type(value) is not dict or set(value) != {item.name for item in fields(ConfirmationProposal)}:
            raise ValueError()
        return validate_confirmation(ConfirmationProposal(**value))
    except (TypeError, ValueError):
        raise ConfirmationError('confirmation_invalid') from None
