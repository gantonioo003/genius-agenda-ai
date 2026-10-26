"""Excluir apenas a proposta aprovada e reconciliar repetições sem novo DELETE."""
from dataclasses import dataclass, field
import re

from app.agent_deletion_store import AgentDeletionError
from app.api_deletion import _public_deleted
from app.confirmation_state import ConfirmationError, canonical_parameters, validate_confirmation
from app.conversation_store import ConversationError, validate_memory
from app.google_calendar import CalendarSetupError
from app.services.agenda_tools import minimal_event
from app.services.confirmation_snapshot import validate_confirmation_snapshot
from app.services.event_deletion import (
    DeletionBindingError, DeletionVersionError, EventDeletionService, deletion_confirmation,
)
from app.services.event_selection import validate_event_id

_OPERATION = re.compile(r'agent-delete-[0-9a-f]{64}\Z', re.ASCII)
_DATA_KEYS = {'result', 'calendar_name', 'event_id', 'recovered'}
_CODES = {'delete_unavailable': 503, 'delete_unresolved': 409, 'delete_calendar_changed': 409,
    'delete_snapshot_changed': 409, 'delete_claim_conflict': 409, 'delete_previously_confirmed': 409,
    'calendar_setup_unavailable': 503, 'event_read_failed': 502, 'operation_not_confirmed': 502}

@dataclass(frozen=True)
class AgentDeletionResult:
    status: str
    mutation_state: str
    message: str
    data: dict | None = field(repr=False)
    operation_id: str | None = None
    http_status: int = 200
    error_code: str | None = None


def _data(value, expected_id=None):
    if (type(value) is not dict or set(value) != _DATA_KEYS
            or value['result'] not in {'deleted', 'already_absent'}
            or not isinstance(value['calendar_name'], str) or not value['calendar_name'].strip()
            or len(value['calendar_name']) > 4000 or type(value['recovered']) is not bool
            or value['result'] == 'already_absent' and value['recovered']):
        raise ValueError()
    value['calendar_name'].encode('utf-8')
    validate_event_id(value['event_id'])
    if expected_id is not None and value['event_id'] != expected_id:
        raise ValueError()
    return dict(value)


def validate_deletion_result(result):
    try:
        if (type(result) is not AgentDeletionResult or type(result.http_status) is not int
                or not isinstance(result.message, str) or not result.message.strip()
                or len(result.message) > 8000 or result.operation_id is not None and
                    (not isinstance(result.operation_id, str) or _OPERATION.fullmatch(result.operation_id) is None)):
            raise ValueError()
        result.message.encode('utf-8')
        if result.status == 'succeeded':
            if (result.http_status != 200 or result.mutation_state != 'confirmed'
                    or result.operation_id is None or result.error_code is not None):
                raise ValueError()
            _data(result.data)
        elif result.status == 'failed':
            if (_CODES.get(result.error_code) != result.http_status
                    or result.mutation_state not in {'not_applied', 'unknown', 'confirmed'}):
                raise ValueError()
            if result.mutation_state == 'not_applied':
                if result.data is not None:
                    raise ValueError()
            elif (result.operation_id is None or type(result.data) is not dict
                    or set(result.data) != {'event_id'}):
                raise ValueError()
            else:
                validate_event_id(result.data['event_id'])
            if result.mutation_state == 'confirmed' and result.error_code != 'delete_previously_confirmed':
                raise ValueError()
        else:
            raise ValueError()
        return result
    except Exception:
        raise AgentDeletionError('deletion_unavailable') from None


def _failure(code, *, record=None, mutation_state='not_applied'):
    messages = {
        'delete_unavailable': 'O registro da exclusão está indisponível.',
        'delete_unresolved': 'Há uma exclusão sem resultado confirmado. Confira seu estado antes de iniciar outra.',
        'delete_calendar_changed': 'A agenda deixou de corresponder ao destino aprovado.',
        'delete_snapshot_changed': 'O evento mudou. Prepare e confirme uma nova prévia antes de excluir.',
        'delete_claim_conflict': 'Esta proposta já recebeu uma tentativa. Confira seu estado antes de preparar outra.',
        'delete_previously_confirmed': 'A ausência foi confirmada anteriormente, mas a consulta atual não a reconfirmou. Nenhuma exclusão foi repetida.',
        'calendar_setup_unavailable': 'O acesso à agenda está indisponível.',
        'event_read_failed': 'Não foi possível conferir o evento antes da exclusão.',
        'operation_not_confirmed': 'Não foi possível confirmar a exclusão. Nenhum DELETE será reenviado automaticamente.',
    }
    return validate_deletion_result(AgentDeletionResult('failed', mutation_state, messages[code],
        {'event_id': record.parameters['event_id']} if mutation_state != 'not_applied' else None,
        record.operation_id if record is not None else None, _CODES[code], code))


def _parameters(memory):
    pending = validate_memory(memory).pending_request
    if pending is None or pending.awaiting or pending.candidates or pending.fields.get('action') != 'delete':
        raise ConfirmationError('preparation_required')
    try:
        return canonical_parameters('delete', {name: value for name, value in pending.fields.items() if name != 'action'})
    except Exception:
        raise ConfirmationError('preparation_required') from None


class _BoundGateway:
    def __init__(self, gateway, record):
        self.gateway, self.record = gateway, record
    def delete_event(self, event_id, *, time_zone, execute, confirmation_token):
        return self.gateway.delete_event(event_id, time_zone=time_zone, execute=execute,
            confirmation_token=confirmation_token, expected_calendar_id=self.record.calendar_id,
            expected_version=self.record.event_version)


class AgentDeletionService:
    def __init__(self, conversation_store, execution_store, gateway_factory):
        if not callable(gateway_factory):
            raise AgentDeletionError('deletion_invalid')
        self._context, self._ledger, self._gateway_factory = conversation_store, execution_store, gateway_factory

    def _finish(self, owner, cid, record, state, data=None):
        return self._ledger.finish(owner, cid, record.operation_id, record.attempt_id, state, data)

    def _known_finish(self, owner, cid, record, state):
        try:
            self._finish(owner, cid, record, state)
        except Exception:
            raise AgentDeletionError('deletion_unavailable') from None

    def _confirm(self, owner, cid, record, data):
        data = _data(data, record.parameters['event_id'])
        try:
            self._finish(owner, cid, record, 'confirmed', data)
            return data
        except Exception:
            latest = self._ledger.get(owner, cid, record.proposal_id)
            if (latest is None or latest.state != 'confirmed' or latest.operation_id != record.operation_id
                    or latest.proposal_id != record.proposal_id or latest.parameters != record.parameters
                    or latest.calendar_id != record.calendar_id or latest.event_version != record.event_version
                    or latest.event != record.event or latest.request_hash != record.request_hash):
                raise AgentDeletionError('deletion_unavailable') from None
            # Uma consulta concorrente pode registrar already_absent antes de
            # o escritor salvar deleted. Ambos representam a ausência observada.
            return _data(latest.result, record.parameters['event_id'])

    @staticmethod
    def _read(gateway, parameters):
        return validate_confirmation_snapshot(gateway.get_confirmation_snapshot(parameters['event_id'],
            time_zone=parameters['time_zone']), parameters['event_id'], parameters['time_zone'])

    def _reconcile(self, owner, cid, record):
        try:
            with self._gateway_factory() as gateway:
                current = self._read(gateway, record.parameters)
            if current.calendar_id == record.calendar_id and current.event is None:
                data = {'result': 'already_absent', 'calendar_name': current.calendar_name,
                        'event_id': record.parameters['event_id'], 'recovered': False}
                if record.state != 'confirmed':
                    data = self._confirm(owner, cid, record, data)
                return validate_deletion_result(AgentDeletionResult('succeeded', 'confirmed',
                    'Ausência do evento conferida por consulta, sem novo DELETE.', data, record.operation_id))
        except Exception:
            pass
        if record.state == 'confirmed':
            return _failure('delete_previously_confirmed', record=record, mutation_state='confirmed')
        return _failure('operation_not_confirmed', record=record, mutation_state='unknown')

    def execute(self, owner, cid):
        context = self._context.load(owner, cid)
        parameters = _parameters(context.memory)
        active = self._ledger.active(owner, cid)
        if active is not None:
            if active.parameters != parameters:
                return _failure('delete_unresolved', record=active, mutation_state='unknown')
            return self._reconcile(owner, cid, active)
        proposal = context.memory.confirmation
        if proposal is None:
            raise ConfirmationError('confirmation_not_approved')
        proposal = validate_confirmation(proposal)
        if proposal.action != 'delete' or proposal.parameters != parameters:
            raise ConfirmationError('confirmation_stale')
        previous = self._ledger.get(owner, cid, proposal.proposal_id)
        if previous is not None:
            if previous.parameters != parameters:
                raise ConfirmationError('confirmation_stale')
            if previous.state in {'running', 'unknown', 'confirmed'}:
                return self._reconcile(owner, cid, previous)
            return _failure('delete_claim_conflict', record=previous)
        if proposal.state == 'consumed':
            raise ConfirmationError('confirmation_consumed')
        if proposal.state == 'expired':
            raise ConfirmationError('confirmation_expired')
        if proposal.state != 'approved':
            raise ConfirmationError('confirmation_not_approved')
        try:
            with self._gateway_factory() as gateway:
                bound = self._read(gateway, parameters)
        except CalendarSetupError:
            return _failure('calendar_setup_unavailable')
        except Exception:
            return _failure('event_read_failed')
        if bound.calendar_id != proposal.calendar_id:
            raise ConfirmationError('confirmation_stale')
        if bound.event is not None and (bound.version != proposal.event_version or minimal_event(bound.event) != proposal.event):
            raise ConfirmationError('confirmation_stale')
        reservation = self._ledger.reserve(owner, cid, proposal)
        record = reservation.record
        if not reservation.claimed:
            if record.state in {'running', 'unknown', 'confirmed'}:
                return self._reconcile(owner, cid, record)
            return _failure('delete_claim_conflict', record=record)
        try:
            claim = self._context.consume_confirmation(owner, cid, context.revision,
                proposal.proposal_id, proposal.request_hash, proposal.event_version)
            if (claim.action != 'delete' or claim.parameters != parameters or claim.calendar_id != record.calendar_id
                    or claim.event_id != parameters['event_id'] or claim.event_version != record.event_version
                    or claim.proposal_id != record.proposal_id or claim.request_hash != record.request_hash):
                raise ConfirmationError('confirmation_stale')
        except (ConversationError, ConfirmationError):
            self._known_finish(owner, cid, record, 'not_applied')
            raise
        attempted = False
        try:
            with self._gateway_factory() as gateway:
                attempted = True
                if bound.event is not None:
                    token = deletion_confirmation(record.calendar_id, parameters['event_id'], record.event_version)
                    try:
                        result = EventDeletionService(_BoundGateway(gateway, record)).delete(parameters['event_id'],
                            time_zone=parameters['time_zone'], execute=True, confirmation_token=token)
                    except (DeletionBindingError, DeletionVersionError) as error:
                        self._known_finish(owner, cid, record, 'not_applied')
                        return _failure('delete_calendar_changed' if isinstance(error, DeletionBindingError)
                            else 'delete_snapshot_changed', record=record)
                    data = _public_deleted(result, parameters['event_id'], parameters['time_zone'])
                    if result.status == 'deleted' and minimal_event(result.event) != record.event:
                        raise ValueError()
                else:
                    data = {'result': 'already_absent', 'calendar_name': bound.calendar_name,
                            'event_id': parameters['event_id'], 'recovered': False}
                after = self._read(gateway, parameters)
                if after.calendar_id != record.calendar_id or after.event is not None:
                    raise ValueError()
                data = _data(data, parameters['event_id'])
        except Exception as error:
            if not attempted:
                self._known_finish(owner, cid, record, 'not_applied')
                return _failure('calendar_setup_unavailable' if isinstance(error, CalendarSetupError)
                    else 'delete_unavailable', record=record)
            try:
                self._finish(owner, cid, record, 'unknown')
            except Exception:
                pass
            return _failure('operation_not_confirmed', record=record, mutation_state='unknown')
        try:
            data = self._confirm(owner, cid, record, data)
        except Exception:
            return _failure('operation_not_confirmed', record=record, mutation_state='unknown')
        return validate_deletion_result(AgentDeletionResult('succeeded', 'confirmed',
            'Ausência do evento aprovado confirmada na agenda.', data, record.operation_id))
