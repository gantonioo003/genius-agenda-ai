"""Consentimento de proposta exata; nenhuma operação de escrita é executada aqui."""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import re
import unicodedata
from uuid import UUID, uuid4

from app.confirmation_state import (
    ConfirmationError, ConfirmationProposal, canonical_parameters,
    proposal_request_hash, validate_confirmation,
)
from app.conversation_store import QueryMemory, validate_memory
from app.google_calendar import CalendarSetupError
from app.services.agenda import AgendaEvent
from app.services.agenda_tools import minimal_event
from app.services.confirmation_snapshot import validate_confirmation_snapshot


_STATES = {'pending', 'approved', 'revoked', 'expired', 'invalidated', 'consumed'}
_ACTIONS = {'create', 'edit', 'reschedule', 'delete'}
_DATA_FIELDS = {'proposal_id', 'state', 'action', 'event', 'parameters', 'expires_at',
                'approved_at', 'question', 'execution_allowed'}
_EVENT_FIELDS = {'event_id', 'title', 'start', 'end', 'all_day', 'time_zone', 'status', 'html_link'}
_ERROR_STATUS = {
    'confirmation_not_found': 404, 'confirmation_expired': 409,
    'confirmation_stale': 409, 'confirmation_not_approved': 409,
    'confirmation_consumed': 409, 'confirmation_invalid': 422,
    'confirmation_unavailable': 503, 'preparation_required': 409,
    'ambiguous_confirmation': 422, 'calendar_setup_unavailable': 503,
    'operation_failed': 502,
}
_FAILURES = {
    'calendar_setup_unavailable': 'O acesso à agenda está indisponível.',
    'operation_failed': 'Não foi possível conferir a proposta na agenda.',
}


@dataclass(frozen=True)
class ConfirmationResult:
    status: str
    action: str | None
    message: str
    data: dict | None = field(repr=False)
    http_status: int = 200
    error_code: str | None = None


def _uuid(value):
    if not isinstance(value, str):
        raise ValueError()
    parsed = UUID(value)
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError()
    return value


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError()
    result = datetime.fromisoformat(value)
    if (result.utcoffset() != timedelta(0) or result.tzinfo is None
            or result.isoformat() != value):
        raise ValueError()
    return result


def _normalized_confirmation(message):
    if not isinstance(message, str) or len(message) > 4000:
        return None
    normalized = ''.join(char for char in unicodedata.normalize('NFKD', message.casefold())
                         if not unicodedata.combining(char))
    normalized = ' '.join(normalized.strip(' \t\r\n.!').replace(',', ' ').split())
    return normalized if normalized in {'sim', 'confirmo', 'sim confirmo'} else None


def is_confirmation_message(message) -> bool:
    """Reconhecer uma aprovação inequívoca, sem pedir ao LLM para autorizá-la."""
    return _normalized_confirmation(message) is not None


def is_rejection_message(message) -> bool:
    """Uma recusa explícita do pedido atual também independe do modelo."""
    if not isinstance(message, str) or len(message) > 4000:
        return False
    normalized = ''.join(char for char in unicodedata.normalize('NFKD', message.casefold())
                         if not unicodedata.combining(char))
    normalized = ' '.join(normalized.strip(' \t\r\n.!').split())
    return normalized in {'nao', 'nao confirmo', 'cancela', 'cancelar'}


def _question(action):
    labels = {'create': 'criação', 'edit': 'edição', 'reschedule': 'reagendamento', 'delete': 'exclusão'}
    return ('Confirma esta proposta de ' + labels[action]
            + '? Confira o evento e os parâmetros apresentados antes de responder sim ou confirmo.')


def _public(proposal):
    return {
        'proposal_id': proposal.proposal_id, 'state': proposal.state, 'action': proposal.action,
        'event': None if proposal.event is None else dict(proposal.event),
        'parameters': dict(proposal.parameters), 'expires_at': proposal.expires_at,
        'approved_at': proposal.approved_at,
        'question': _question(proposal.action) if proposal.state == 'pending' else None,
        'execution_allowed': False,
    }


def _result(proposal, *, code=None, http_status=200):
    status = 'needs_confirmation' if proposal.state == 'pending' else proposal.state
    messages = {
        'pending': 'Proposta apresentada; a confirmação ainda não foi registrada.',
        'approved': 'Confirmação registrada somente para esta proposta; nenhuma operação foi executada.',
        'revoked': 'Confirmação revogada; nenhum compromisso foi alterado.',
        'expired': 'A proposta expirou. Solicite uma nova prévia antes de confirmar.',
        'invalidated': 'A proposta mudou ou deixou de corresponder ao pedido. Solicite uma nova prévia.',
        'consumed': 'A confirmação já foi consumida e não pode ser usada novamente.',
    }
    return validate_confirmation_result(ConfirmationResult(
        status, proposal.action, messages[proposal.state], _public(proposal), http_status, code,
    ))


def _failure(error, memory):
    return (ConfirmationResult('failed', None, str(error), None, error.http_status, error.code), memory)


def _provider_failure(code, memory):
    return (ConfirmationResult('failed', None, _FAILURES[code], None, _ERROR_STATUS[code], code), memory)


def validate_confirmation_result(result):
    """Fechar o envelope público e impedir que versões/hashes privados escapem."""
    try:
        if (type(result) is not ConfirmationResult or type(result.http_status) is not int
                or result.action is not None and result.action not in _ACTIONS
                or not isinstance(result.message, str) or not result.message.strip()
                or len(result.message) > 8000):
            raise ValueError()
        result.message.encode('utf-8')
        if result.error_code is not None:
            if _ERROR_STATUS.get(result.error_code) != result.http_status:
                raise ValueError()
        elif result.http_status != 200:
            raise ValueError()
        if result.status == 'failed':
            if result.data is not None or result.error_code is None:
                raise ValueError()
            return result
        data = result.data
        state = 'pending' if result.status == 'needs_confirmation' else result.status
        if (state not in _STATES or type(data) is not dict or set(data) != _DATA_FIELDS
                or data['state'] != state or data['action'] != result.action
                or data['execution_allowed'] is not False or result.action not in _ACTIONS):
            raise ValueError()
        _uuid(data['proposal_id'])
        expires = _timestamp(data['expires_at'])
        if data['approved_at'] is not None and _timestamp(data['approved_at']) >= expires:
            raise ValueError()
        if state in {'approved', 'consumed'} and data['approved_at'] is None:
            raise ValueError()
        if data['question'] != (_question(result.action) if state == 'pending' else None):
            raise ValueError()
        parameters = canonical_parameters(result.action, data['parameters'])
        if parameters != data['parameters']:
            raise ValueError()
        if result.action == 'create':
            if data['event'] is not None:
                raise ValueError()
        else:
            event = data['event']
            if type(event) is not dict or set(event) != _EVENT_FIELDS:
                raise ValueError()
            minimal_event(AgendaEvent(**event), expected_id=parameters['event_id'])
        return result
    except Exception:
        raise ConfirmationError('confirmation_unavailable') from None


def _draft(memory):
    pending = memory.pending_request
    if pending is None or pending.awaiting or pending.candidates:
        raise ConfirmationError('preparation_required')
    fields = dict(pending.fields)
    action = fields.pop('action', None)
    if action not in _ACTIONS:
        raise ConfirmationError('preparation_required')
    try:
        parameters = canonical_parameters(action, fields)
    except Exception:
        raise ConfirmationError('preparation_required') from None
    return action, parameters


class ConfirmationService:
    """Confirmações com prazo fixo; consome-se no store, antes do futuro executor."""

    def __init__(self, gateway_factory, *, clock=None, ttl_seconds=300):
        if (not callable(gateway_factory) or clock is not None and not callable(clock)
                or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 900):
            raise ConfirmationError('confirmation_invalid')
        self._gateway_factory = gateway_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._ttl_seconds = ttl_seconds

    def _now(self):
        try:
            now = self._clock()
            if not isinstance(now, datetime) or now.utcoffset() is None:
                raise ValueError()
            return now.astimezone(timezone.utc)
        except Exception:
            raise ConfirmationError('confirmation_unavailable') from None

    def _read(self, parameters):
        event_id = parameters.get('event_id')
        with self._gateway_factory() as gateway:
            snapshot = gateway.get_confirmation_snapshot(event_id, time_zone=parameters['time_zone'])
        return validate_confirmation_snapshot(snapshot, expected_id=event_id, time_zone=parameters['time_zone'])

    @staticmethod
    def _binding(action, parameters, snapshot):
        event = None if snapshot.event is None else minimal_event(snapshot.event)
        return proposal_request_hash(action, parameters, snapshot.calendar_id,
                                     parameters.get('event_id'), snapshot.version, event), event

    @staticmethod
    def _lookup(memory, proposal_id):
        try:
            _uuid(proposal_id)
        except (ValueError, TypeError, AttributeError):
            raise ConfirmationError('confirmation_invalid') from None
        proposal = memory.confirmation
        if proposal is None or proposal.proposal_id != proposal_id:
            raise ConfirmationError('confirmation_not_found')
        return validate_confirmation(proposal)

    @staticmethod
    def _invalidate(memory, proposal):
        changed = replace(proposal, state='invalidated')
        return (_result(changed, code='confirmation_stale', http_status=409),
                replace(memory, confirmation=changed))

    def _expired(self, memory, proposal, *, read_only=False, now=None):
        now = self._now() if now is None else now
        if now < _timestamp(proposal.created_at):
            raise ConfirmationError('confirmation_unavailable')
        if proposal.state in {'pending', 'approved'} and now >= _timestamp(proposal.expires_at):
            changed = replace(proposal, state='expired')
            return (_result(changed, code=None if read_only else 'confirmation_expired',
                            http_status=200 if read_only else 409), replace(memory, confirmation=changed))
        if proposal.state == 'expired' and not read_only:
            return _result(proposal, code='confirmation_expired', http_status=409), memory
        return None

    def preview(self, memory):
        memory = validate_memory(memory)
        try:
            action, parameters = _draft(memory)
            old = memory.confirmation
            if old is not None and old.state == 'consumed':
                return _result(old, code='confirmation_consumed', http_status=409), memory
            snapshot = self._read(parameters)
            if action != 'create' and snapshot.event is None:
                if old is not None:
                    return self._invalidate(memory, old)
                raise ConfirmationError('confirmation_stale')
            if action == 'reschedule' and snapshot.event.all_day:
                # O executor atual não converte compromissos de dia inteiro.
                raise ConfirmationError('preparation_required')
            fingerprint, event = self._binding(action, parameters, snapshot)
            now = self._now()
            if (old is not None and old.state in {'pending', 'approved'}
                    and old.request_hash == fingerprint and now < _timestamp(old.expires_at)):
                if now < _timestamp(old.created_at):
                    raise ConfirmationError('confirmation_unavailable')
                return _result(old), memory
            proposal = validate_confirmation(ConfirmationProposal(
                proposal_id=str(uuid4()), action=action, parameters=parameters,
                request_hash=fingerprint, calendar_id=snapshot.calendar_id,
                event_id=parameters.get('event_id'), event_version=snapshot.version,
                event=event, created_at=now.isoformat(),
                expires_at=(now + timedelta(seconds=self._ttl_seconds)).isoformat(),
            ))
            return _result(proposal), replace(memory, confirmation=proposal)
        except ConfirmationError as error:
            return _failure(error, memory)
        except CalendarSetupError:
            return _provider_failure('calendar_setup_unavailable', memory)
        except Exception:
            return _provider_failure('operation_failed', memory)

    def approve(self, memory, proposal_id, message):
        memory = validate_memory(memory)
        try:
            proposal = self._lookup(memory, proposal_id)
            if proposal.state == 'consumed':
                return _result(proposal, code='confirmation_consumed', http_status=409), memory
            expired = self._expired(memory, proposal)
            if expired is not None:
                return expired
            if proposal.state not in {'pending', 'approved'}:
                raise ConfirmationError('confirmation_not_approved')
            normalized = _normalized_confirmation(message)
            if normalized is None:
                raise ConfirmationError('ambiguous_confirmation')
            try:
                action, parameters = _draft(memory)
            except ConfirmationError:
                return self._invalidate(memory, proposal)
            if action != proposal.action or parameters != proposal.parameters:
                return self._invalidate(memory, proposal)
            snapshot = self._read(parameters)
            if action != 'create' and snapshot.event is None:
                return self._invalidate(memory, proposal)
            if action == 'reschedule' and snapshot.event.all_day:
                return self._invalidate(memory, proposal)
            fingerprint, _event = self._binding(action, parameters, snapshot)
            if fingerprint != proposal.request_hash:
                return self._invalidate(memory, proposal)
            approval_time = self._now()
            expired = self._expired(memory, proposal, now=approval_time)
            if expired is not None:
                return expired
            if proposal.state == 'approved':
                return _result(proposal), memory
            approved = validate_confirmation(replace(
                proposal, state='approved', approved_at=approval_time.isoformat(),
                approval_message_hash=sha256(b'genius-confirmation-message:v1\0' + normalized.encode('utf-8')).hexdigest(),
            ))
            return _result(approved), replace(memory, confirmation=approved)
        except ConfirmationError as error:
            return _failure(error, memory)
        except CalendarSetupError:
            return _provider_failure('calendar_setup_unavailable', memory)
        except Exception:
            return _provider_failure('operation_failed', memory)

    def revoke(self, memory, proposal_id):
        memory = validate_memory(memory)
        try:
            proposal = self._lookup(memory, proposal_id)
            if proposal.state == 'consumed':
                return _result(proposal, code='confirmation_consumed', http_status=409), memory
            expired = self._expired(memory, proposal)
            if expired is not None:
                return expired
            if proposal.state == 'revoked':
                return _result(proposal), memory
            changed = validate_confirmation(replace(proposal, state='revoked'))
            return _result(changed), replace(memory, confirmation=changed)
        except ConfirmationError as error:
            return _failure(error, memory)

    def status(self, memory, proposal_id):
        memory = validate_memory(memory)
        try:
            proposal = self._lookup(memory, proposal_id)
            expired = self._expired(memory, proposal, read_only=True)
            if expired is not None:
                return expired
            if proposal.state in {'pending', 'approved'}:
                try:
                    action, parameters = _draft(memory)
                except ConfirmationError:
                    action, parameters = None, None
                if action != proposal.action or parameters != proposal.parameters:
                    changed = replace(proposal, state='invalidated')
                    return _result(changed), replace(memory, confirmation=changed)
            return _result(proposal), memory
        except ConfirmationError as error:
            return _failure(error, memory)
