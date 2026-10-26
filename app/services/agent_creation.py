"""Executar um rascunho de criação por comando explícito, com identidade durável."""
from dataclasses import dataclass, field
from datetime import datetime
import re

from app.agent_creation_store import AgentCreationError
from app.confirmation_state import ConfirmationError, canonical_parameters, validate_confirmation
from app.conversation_store import ConversationError, validate_memory
from app.google_calendar import CalendarSetupError
from app.services.agenda import AgendaEvent, CalendarPeriod
from app.services.agenda_tools import minimal_event
from app.services.confirmation_snapshot import validate_confirmation_snapshot
from app.services.conflicts import CalendarConflictError, ConflictReport, build_conflict_report
from app.services.event_selection import validate_event_id
from app.services.interviews import (
    CalendarBindingError, CreatedInterview, InterviewDraft, InterviewService,
    ReconciledCreation, validate_interview,
)
from app.services.time_rules import validate_event_interval

_OPERATION = re.compile(r'agent-create-[0-9a-f]{64}\Z', re.ASCII)
_CREATED_KEYS = {'event_id', 'html_link', 'calendar_name', 'reused', 'recovered'}
_ERRORS = {
    'creation_unavailable': 503, 'creation_unresolved': 409,
    'creation_calendar_changed': 409, 'creation_claim_conflict': 409,
    'operation_not_confirmed': 502, 'creation_previously_confirmed': 409,
    'calendar_setup_unavailable': 503, 'schedule_conflict': 409,
    'conversation_conflict': 409,
}


@dataclass(frozen=True)
class CreationResult:
    status: str
    mutation_state: str
    message: str
    data: dict | None = field(repr=False)
    operation_id: str | None = None
    http_status: int = 200
    error_code: str | None = None


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Texto de resultado inválido.')
    value.encode('utf-8')
    return value


def _created(value):
    if not isinstance(value, CreatedInterview):
        raise ValueError('Criação sem resultado válido.')
    validate_event_id(value.event_id)
    _text(value.html_link)
    _text(value.calendar_name)
    if type(value.reused) is not bool or type(value.recovered) is not bool:
        raise ValueError('Estado de criação inválido.')
    return {name: getattr(value, name) for name in _CREATED_KEYS}


def _conflict(report, draft):
    if (not isinstance(report, ConflictReport) or report.status != 'conflict'
            or not isinstance(report.conflicts, (list, tuple)) or not report.conflicts
            or report.time_zone != draft.time_zone):
        raise ValueError('Conflito não confirmado.')
    _text(report.calendar_name)
    start, end = datetime.fromisoformat(report.start), datetime.fromisoformat(report.end)
    validate_event_interval(start, end, report.time_zone)
    if start != draft.start or end != draft.end:
        raise ValueError('Intervalo de conflito divergente.')
    checked = build_conflict_report(CalendarPeriod(start, end, report.time_zone), report.calendar_name, report.conflicts)
    if len(checked.conflicts) != len(report.conflicts):
        raise ValueError('Identidades de conflito divergentes.')
    events = [minimal_event(event) for event in report.conflicts]
    if len({event['event_id'] for event in events}) != len(events):
        raise ValueError('Identidades de conflito repetidas.')
    return {'status': 'conflict', 'calendar_name': report.calendar_name,
            'start': report.start, 'end': report.end, 'time_zone': report.time_zone,
            'conflicts': events}


def validate_creation_result(result):
    """Projetar somente os estados e dados permitidos antes da fronteira HTTP."""
    try:
        if (type(result) is not CreationResult or type(result.http_status) is not int
                or len(_text(result.message)) > 8000
                or result.operation_id is not None and (not isinstance(result.operation_id, str)
                    or _OPERATION.fullmatch(result.operation_id) is None)):
            raise ValueError()
        if result.status == 'succeeded':
            if (result.mutation_state != 'confirmed' or result.operation_id is None
                    or result.error_code is not None or type(result.data) is not dict
                    or set(result.data) != _CREATED_KEYS):
                raise ValueError()
            value = CreatedInterview(**result.data)
            if _created(value) != result.data or result.http_status != (200 if value.reused else 201):
                raise ValueError()
        elif result.status == 'conflict':
            if (result.http_status != 409 or result.error_code != 'schedule_conflict'
                    or result.mutation_state != 'not_applied' or result.operation_id is None
                    or type(result.data) is not dict or set(result.data) !=
                        {'status', 'calendar_name', 'start', 'end', 'time_zone', 'conflicts'}
                    or type(result.data['conflicts']) is not list):
                raise ValueError()
            data = result.data
            events = tuple(AgendaEvent(**event) for event in data['conflicts'])
            draft = InterviewDraft('Validação de conflito', datetime.fromisoformat(data['start']),
                datetime.fromisoformat(data['end']), data['time_zone'])
            report = ConflictReport(data['status'], data['calendar_name'], data['start'], data['end'], data['time_zone'], events)
            if _conflict(report, draft) != data:
                raise ValueError()
        elif result.status == 'failed':
            if _ERRORS.get(result.error_code) != result.http_status:
                raise ValueError()
            if result.mutation_state not in {'unknown', 'not_applied', 'confirmed'}:
                raise ValueError()
            if result.mutation_state in {'unknown', 'confirmed'}:
                if (result.operation_id is None or type(result.data) is not dict
                        or set(result.data) != {'event_id'}):
                    raise ValueError()
                if result.data['event_id'] is not None:
                    validate_event_id(result.data['event_id'])
            elif result.data is not None:
                raise ValueError()
            if result.mutation_state == 'confirmed' and result.error_code != 'creation_previously_confirmed':
                raise ValueError()
        else:
            raise ValueError()
        return result
    except Exception:
        raise AgentCreationError('creation_unavailable') from None


def _failure(code, *, operation_id=None, mutation_state=None, event_id=None):
    if mutation_state is None:
        mutation_state = 'unknown' if code == 'operation_not_confirmed' else 'not_applied'
    messages = {
        'creation_unavailable': 'O registro da criação está temporariamente indisponível.',
        'creation_unresolved': 'Existe uma criação sem resultado confirmado. Confira seu estado antes de iniciar outra.',
        'creation_calendar_changed': 'A agenda mudou. Confira o destino antes de continuar.',
        'creation_claim_conflict': 'A criação recebeu outra atualização. Confira o estado antes de continuar.',
        'operation_not_confirmed': 'Não foi possível confirmar a criação. A operação não será reenviada automaticamente.',
        'creation_previously_confirmed': 'Esta operação já foi confirmada, mas o evento não pôde ser reconfirmado. Não foi recriado.',
        'calendar_setup_unavailable': 'O acesso à agenda está indisponível.',
        'conversation_conflict': 'O pedido mudou antes da execução. Confira os dados atuais.',
    }
    return validate_creation_result(CreationResult('failed', mutation_state, messages[code],
        {'event_id': event_id} if mutation_state in {'unknown', 'confirmed'} else None,
        operation_id, _ERRORS[code], code))


def _pending_parameters(memory):
    memory = validate_memory(memory)
    pending = memory.pending_request
    if pending is None or pending.awaiting or pending.candidates or pending.fields.get('action') != 'create':
        raise ConfirmationError('preparation_required')
    try:
        return canonical_parameters('create', {name: value for name, value in pending.fields.items() if name != 'action'})
    except Exception:
        raise ConfirmationError('preparation_required') from None


def _draft(parameters, operation_id):
    result = InterviewDraft(parameters['title'], datetime.fromisoformat(parameters['start']),
        datetime.fromisoformat(parameters['end']), parameters['time_zone'], parameters['description'])
    validate_interview(result, operation_id)
    return result


class _BoundGateway:
    def __init__(self, gateway, calendar_id):
        self.gateway, self.calendar_id = gateway, calendar_id
    def create_event(self, draft, *, operation_id):
        return self.gateway.create_event(draft, operation_id=operation_id, expected_calendar_id=self.calendar_id)


class AgentCreationService:
    """Comando explícito com identidade persistida e reconciliação somente de leitura."""
    def __init__(self, conversation_store, execution_store, gateway_factory):
        if not callable(gateway_factory):
            raise AgentCreationError('creation_invalid')
        self._context, self._ledger, self._gateway_factory = conversation_store, execution_store, gateway_factory

    def _finish(self, owner, conversation, record, state, result=None):
        return self._ledger.finish(owner, conversation, record.operation_id, record.attempt_id, state, result)

    def _known_finish(self, owner, conversation, record, state):
        try:
            self._finish(owner, conversation, record, state)
        except Exception:
            raise AgentCreationError('creation_unavailable') from None

    def _confirm(self, owner, conversation, record, data):
        try:
            self._finish(owner, conversation, record, 'confirmed', data)
            return data
        except Exception:
            # Uma consulta concorrente pode ter confirmado o mesmo evento antes
            # de o primeiro comando persistir seu recibo. A identidade imutável
            # do recibo deve coincidir integralmente antes de aproveitar isso.
            latest = self._ledger.get(owner, conversation, record.parameters)
            if (latest is None or latest.state != 'confirmed'
                    or latest.operation_id != record.operation_id
                    or latest.parameters != record.parameters
                    or latest.calendar_id != record.calendar_id
                    or type(latest.result) is not dict or set(latest.result) != _CREATED_KEYS):
                raise AgentCreationError('creation_unavailable') from None
            persisted = _created(CreatedInterview(**latest.result))
            if any(persisted[name] != data[name] for name in ('event_id', 'html_link', 'calendar_name')):
                raise AgentCreationError('creation_unavailable') from None
            return {**persisted, 'reused': True, 'recovered': True}

    @staticmethod
    def _confirmation(memory, parameters):
        if memory.confirmation is None:
            return None
        proposal = validate_confirmation(memory.confirmation)
        if proposal.action != 'create' or proposal.parameters != parameters:
            raise ConfirmationError('confirmation_stale')
        if proposal.state == 'consumed':
            raise ConfirmationError('confirmation_consumed')
        if proposal.state == 'expired':
            raise ConfirmationError('confirmation_expired')
        if proposal.state != 'approved':
            raise ConfirmationError('confirmation_not_approved')
        return proposal

    def _reconcile(self, owner, conversation, record):
        try:
            draft = _draft(record.parameters, record.operation_id)
            with self._gateway_factory() as gateway:
                reconciled = gateway.reconcile_creation(draft, operation_id=record.operation_id,
                    expected_calendar_id=record.calendar_id)
            if type(reconciled) is not ReconciledCreation or reconciled.status not in {'found', 'absent', 'unregistered'}:
                raise ValueError()
            if reconciled.status == 'found':
                data = {**_created(reconciled.created), 'reused': True, 'recovered': True}
                if record.result is not None and any(data[name] != record.result[name]
                        for name in ('event_id', 'html_link', 'calendar_name')):
                    raise ValueError()
                if record.state != 'confirmed':
                    data = self._confirm(owner, conversation, record, data)
                return validate_creation_result(CreationResult('succeeded', 'confirmed',
                    'Criação correspondente confirmada por consulta, sem nova gravação.', data,
                    record.operation_id, 200))
            if reconciled.created is not None:
                raise ValueError()
            if record.state == 'confirmed':
                return _failure('creation_previously_confirmed', operation_id=record.operation_id,
                    mutation_state='confirmed', event_id=record.result['event_id'])
        except CalendarBindingError:
            return _failure('creation_calendar_changed', operation_id=record.operation_id,
                mutation_state='unknown', event_id=record.result['event_id'] if record.result else None)
        except Exception:
            pass
        # Não mudar o estado running de outra tentativa concorrente: ela ainda
        # pode terminar com conflito ou outra falha comprovadamente sem escrita.
        return _failure('operation_not_confirmed', operation_id=record.operation_id,
            mutation_state='unknown', event_id=record.result['event_id'] if record.result else None)

    def execute(self, owner_key, conversation_id):
        snapshot = self._context.load(owner_key, conversation_id)
        parameters = _pending_parameters(snapshot.memory)
        active = self._ledger.active(owner_key, conversation_id)
        if active is not None and active.parameters != parameters:
            return _failure('creation_unresolved', operation_id=active.operation_id, mutation_state='unknown')
        previous = self._ledger.get(owner_key, conversation_id, parameters)
        if previous is not None and previous.state in {'running', 'unknown', 'confirmed'}:
            return self._reconcile(owner_key, conversation_id, previous)
        proposal = self._confirmation(snapshot.memory, parameters)
        try:
            with self._gateway_factory() as gateway:
                bound = validate_confirmation_snapshot(gateway.get_confirmation_snapshot(None,
                    time_zone=parameters['time_zone']), expected_id=None, time_zone=parameters['time_zone'])
        except CalendarSetupError:
            return _failure('calendar_setup_unavailable')
        except Exception:
            return _failure('creation_unavailable')
        if proposal is not None and proposal.calendar_id != bound.calendar_id:
            raise ConfirmationError('confirmation_stale')
        reservation = self._ledger.reserve(owner_key, conversation_id, parameters, bound.calendar_id)
        record = reservation.record
        if not reservation.claimed:
            return self._reconcile(owner_key, conversation_id, record)
        draft = _draft(parameters, record.operation_id)
        try:
            if proposal is not None:
                claim = self._context.consume_confirmation(owner_key, conversation_id, snapshot.revision,
                    proposal.proposal_id, proposal.request_hash, None)
                if claim.action != 'create' or claim.parameters != parameters or claim.calendar_id != bound.calendar_id:
                    raise ConfirmationError('confirmation_stale')
            else:
                current = self._context.load(owner_key, conversation_id)
                if current.revision != snapshot.revision or current.memory != snapshot.memory:
                    self._known_finish(owner_key, conversation_id, record, 'not_applied')
                    return _failure('conversation_conflict', operation_id=record.operation_id)
        except (ConversationError, ConfirmationError):
            self._known_finish(owner_key, conversation_id, record, 'not_applied')
            raise
        attempted = False
        try:
            with self._gateway_factory() as gateway:
                attempted = True
                created = InterviewService(_BoundGateway(gateway, bound.calendar_id)).create(draft, operation_id=record.operation_id)
                data = _created(created)
        except CalendarConflictError as error:
            try:
                data = _conflict(error.report, draft)
                self._known_finish(owner_key, conversation_id, record, 'conflict')
                return validate_creation_result(CreationResult('conflict', 'not_applied',
                    'Já existe um compromisso nesse intervalo.', data, record.operation_id, 409, 'schedule_conflict'))
            except Exception:
                return _failure('creation_unavailable', operation_id=record.operation_id)
        except CalendarBindingError:
            self._known_finish(owner_key, conversation_id, record, 'not_applied')
            return _failure('creation_calendar_changed', operation_id=record.operation_id)
        except Exception as error:
            if not attempted:
                self._known_finish(owner_key, conversation_id, record, 'not_applied')
                return _failure('calendar_setup_unavailable' if isinstance(error, CalendarSetupError) else 'creation_unavailable',
                    operation_id=record.operation_id)
            try:
                self._finish(owner_key, conversation_id, record, 'unknown')
            except Exception:
                pass
            return _failure('operation_not_confirmed', operation_id=record.operation_id)
        try:
            data = self._confirm(owner_key, conversation_id, record, data)
        except Exception:
            return _failure('operation_not_confirmed', operation_id=record.operation_id,
                mutation_state='unknown', event_id=data['event_id'])
        return validate_creation_result(CreationResult('succeeded', 'confirmed',
            'Entrevista confirmada na agenda.', data, record.operation_id, 200 if data['reused'] else 201))
