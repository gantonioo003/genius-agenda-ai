"""Editar e reagendar o pedido preparado com versão vinculada e resultado durável."""
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import re

from app.agent_update_store import AgentUpdateError
from app.api_creation import _public_conflict
from app.api_updates import _public_details, _public_reschedule, _public_update
from app.confirmation_state import ConfirmationError, canonical_parameters, validate_confirmation
from app.conversation_store import ConversationError, validate_memory
from app.google_calendar import CalendarSetupError
from app.services.agenda import AgendaEvent
from app.services.agenda_tools import minimal_event
from app.services.conflicts import CalendarConflictError, ConflictReport
from app.services.event_details import DetailsChange, DetailsResult, EventDetails, EventDetailsService
from app.services.event_selection import validate_event_id
from app.services.rescheduling import RescheduleResult, RescheduleService, ScheduleChange
from app.services.update_snapshot import UpdateBindingError, UpdateVersionError, validate_update_snapshot

_OPERATION = re.compile(r'agent-update-[0-9a-f]{64}\Z', re.ASCII)
_ACTIONS = {'edit', 'reschedule'}
_EDIT_KEYS = {'result', 'calendar_name', 'before', 'after', 'requested', 'recovered'}
_SCHEDULE_KEYS = {'result', 'calendar_name', 'before', 'after', 'requested_start', 'requested_end', 'time_zone', 'recovered'}
_EVENT_KEYS = ('event_id', 'title', 'start', 'end', 'all_day', 'time_zone', 'status', 'html_link')
_CODES = {'update_unavailable': 503, 'update_unresolved': 409, 'update_calendar_changed': 409,
    'update_snapshot_changed': 409, 'update_claim_conflict': 409, 'operation_not_confirmed': 502,
    'update_previously_confirmed': 409, 'calendar_setup_unavailable': 503, 'event_read_failed': 502,
    'schedule_conflict': 409, 'conversation_conflict': 409, 'update_event_unsupported': 409}

@dataclass(frozen=True)
class UpdateResult:
    status: str
    action: str
    mutation_state: str
    message: str
    data: dict | None = field(repr=False)
    operation_id: str | None = None
    http_status: int = 200
    error_code: str | None = None

def _change(parameters):
    return ScheduleChange(datetime.fromisoformat(parameters['start']),
                          datetime.fromisoformat(parameters['end']), parameters['time_zone'])

def _requested(parameters):
    return {name: parameters[name] for name in ('title', 'description', 'location') if name in parameters}

def _agenda(event):
    values = {name: getattr(event, name) for name in _EVENT_KEYS}
    if not values['title'].strip():
        values['title'] = '(sem título)'
    return AgendaEvent(**values)

def _validate_data(action, data):
    if type(data) is not dict:
        raise ValueError()
    if action == 'edit':
        if set(data) != _EDIT_KEYS or type(data['requested']) is not dict:
            raise ValueError()
        before, after = EventDetails(**data['before']), EventDetails(**data['after'])
        projected = _public_update(DetailsResult(data['result'], data['calendar_name'], before, after,
            data['requested'], data['recovered']), before.event_id, data['requested'], before.time_zone)
    elif action == 'reschedule':
        if set(data) != _SCHEDULE_KEYS:
            raise ValueError()
        before, after = AgendaEvent(**data['before']), AgendaEvent(**data['after'])
        change = ScheduleChange(datetime.fromisoformat(data['requested_start']),
            datetime.fromisoformat(data['requested_end']), data['time_zone'])
        projected = _public_reschedule(RescheduleResult(data['result'], data['calendar_name'], before,
            after, data['requested_start'], data['requested_end'], data['time_zone'], data['recovered']),
            before.event_id, change)
    else:
        raise ValueError()
    if projected != data:
        raise ValueError()
    return data

def validate_update_result(result):
    try:
        if (type(result) is not UpdateResult or result.action not in _ACTIONS
                or not isinstance(result.message, str) or not result.message.strip()
                or len(result.message) > 8000 or type(result.http_status) is not int
                or result.operation_id is not None and (not isinstance(result.operation_id, str)
                    or _OPERATION.fullmatch(result.operation_id) is None)):
            raise ValueError()
        result.message.encode('utf-8')
        if result.status == 'succeeded':
            if (result.mutation_state != 'confirmed' or result.http_status != 200
                    or result.operation_id is None or result.error_code is not None):
                raise ValueError()
            _validate_data(result.action, result.data)
        elif result.status == 'conflict':
            if (result.action != 'reschedule' or result.http_status != 409
                    or result.error_code != 'schedule_conflict' or result.mutation_state != 'not_applied'
                    or result.operation_id is None or type(result.data) is not dict
                    or set(result.data) != {'status', 'calendar_name', 'start', 'end', 'time_zone', 'conflicts'}
                    or type(result.data['conflicts']) is not list):
                raise ValueError()
            data = result.data
            report = ConflictReport(data['status'], data['calendar_name'], data['start'], data['end'],
                data['time_zone'], tuple(AgendaEvent(**event) for event in data['conflicts']))
            change = ScheduleChange(datetime.fromisoformat(data['start']), datetime.fromisoformat(data['end']), data['time_zone'])
            if _public_conflict(report, change) != data:
                raise ValueError()
        elif result.status == 'failed':
            if (_CODES.get(result.error_code) != result.http_status
                    or result.mutation_state not in {'not_applied', 'unknown', 'confirmed'}):
                raise ValueError()
            if result.mutation_state == 'not_applied':
                if result.data is not None:
                    raise ValueError()
            else:
                if (result.operation_id is None or type(result.data) is not dict
                        or set(result.data) != {'event_id'}):
                    raise ValueError()
                validate_event_id(result.data['event_id'])
            if result.mutation_state == 'confirmed' and result.error_code != 'update_previously_confirmed':
                raise ValueError()
        else:
            raise ValueError()
        return result
    except Exception:
        raise AgentUpdateError('update_unavailable') from None

def _failure(action, code, *, record=None, mutation_state='not_applied'):
    messages = {'update_unavailable': 'O registro da alteração está indisponível.',
        'update_unresolved': 'Há uma alteração sem resultado confirmado. Confira seu estado antes de iniciar outra.',
        'update_calendar_changed': 'A agenda deixou de corresponder ao destino da alteração.',
        'update_snapshot_changed': 'O evento mudou. Confira seu estado antes de preparar outra alteração.',
        'update_claim_conflict': 'A alteração recebeu outra atualização. Confira o estado antes de continuar.',
        'operation_not_confirmed': 'Não foi possível confirmar a alteração. Nenhuma gravação será reenviada automaticamente.',
        'update_previously_confirmed': 'A alteração foi confirmada anteriormente, mas o estado atual não a reconfirmou. Ela não foi reaplicada.',
        'calendar_setup_unavailable': 'O acesso à agenda está indisponível.',
        'event_read_failed': 'Não foi possível conferir o evento antes da alteração.',
        'conversation_conflict': 'O pedido mudou antes da execução. Confira os dados atuais.',
        'update_event_unsupported': 'Este evento precisa de uma operação própria para o reagendamento.'}
    return validate_update_result(UpdateResult('failed', action, mutation_state, messages[code],
        {'event_id': record.parameters['event_id']} if mutation_state != 'not_applied' else None,
        record.operation_id if record else None, _CODES[code], code))

def _parameters(memory, action):
    pending = validate_memory(memory).pending_request
    if pending is None or pending.awaiting or pending.candidates or pending.fields.get('action') != action:
        raise ConfirmationError('preparation_required')
    try:
        return canonical_parameters(action, {name: value for name, value in pending.fields.items() if name != 'action'})
    except Exception:
        raise ConfirmationError('preparation_required') from None

def _matches(before, current, action, parameters, transparency):
    if current.calendar_id != before.calendar_id or current.transparency != transparency:
        return False
    original = before.event
    expected = asdict(original)
    if action == 'edit':
        expected.update(_requested(parameters))
    else:
        expected.update(start=parameters['start'], end=parameters['end'])
    observed = asdict(current.event)
    for name in expected:
        if name in {'start', 'end'} and not original.all_day:
            if datetime.fromisoformat(expected[name]) != datetime.fromisoformat(observed[name]):
                return False
        elif expected[name] != observed[name]:
            return False
    return True

def _record_snapshot(record, calendar_name):
    from app.services.update_snapshot import UpdateSnapshot
    return validate_update_snapshot(UpdateSnapshot(record.calendar_id, calendar_name, EventDetails(**record.before),
        record.event_version, record.transparency), record.parameters['event_id'], record.parameters['time_zone'])

def _observed_data(record, current):
    before = EventDetails(**record.before)
    if record.action == 'edit':
        requested = _requested(record.parameters)
        changed = any(getattr(before, field) != value for field, value in requested.items())
        return _public_update(DetailsResult('updated' if changed else 'unchanged', current.calendar_name,
            before, current.event, requested, changed), before.event_id, requested, record.parameters['time_zone'])
    change = _change(record.parameters)
    changed = datetime.fromisoformat(before.start) != change.start or datetime.fromisoformat(before.end) != change.end
    return _public_reschedule(RescheduleResult('rescheduled' if changed else 'unchanged', current.calendar_name,
        _agenda(before), _agenda(current.event), change.start.isoformat(), change.end.isoformat(),
        change.time_zone, changed), before.event_id, change)

class _BoundGateway:
    def __init__(self, gateway, snapshot):
        self.gateway, self.snapshot = gateway, snapshot
    def update_event_details(self, event_id, change, *, time_zone, execute):
        return self.gateway.update_event_details(event_id, change, time_zone=time_zone, execute=execute,
            expected_calendar_id=self.snapshot.calendar_id, expected_version=self.snapshot.version)
    def reschedule_event(self, event_id, change, *, execute):
        return self.gateway.reschedule_event(event_id, change, execute=execute,
            expected_calendar_id=self.snapshot.calendar_id, expected_version=self.snapshot.version)

class AgentUpdateService:
    def __init__(self, conversation_store, execution_store, gateway_factory):
        if not callable(gateway_factory):
            raise AgentUpdateError('update_invalid')
        self._context, self._ledger, self._gateway_factory = conversation_store, execution_store, gateway_factory
    def _finish(self, owner, cid, record, state, data=None):
        return self._ledger.finish(owner, cid, record.operation_id, record.attempt_id, state, data)
    def _known_finish(self, owner, cid, record, state):
        try:
            self._finish(owner, cid, record, state)
        except Exception:
            raise AgentUpdateError('update_unavailable') from None
    def _confirm(self, owner, cid, record, data):
        try:
            self._finish(owner, cid, record, 'confirmed', data)
            return data
        except Exception:
            latest = self._ledger.get(owner, cid, record.action, record.parameters)
            if (latest is None or latest.state != 'confirmed' or latest.operation_id != record.operation_id
                    or latest.action != record.action or latest.parameters != record.parameters
                    or latest.calendar_id != record.calendar_id or latest.event_version != record.event_version
                    or latest.before != record.before or latest.transparency != record.transparency):
                raise AgentUpdateError('update_unavailable') from None
            persisted = _validate_data(record.action, latest.result)
            if {key: value for key, value in data.items() if key != 'recovered'} != {key: value for key, value in persisted.items() if key != 'recovered'}:
                raise AgentUpdateError('update_unavailable') from None
            return persisted
    @staticmethod
    def _proposal(memory, action, parameters):
        if memory.confirmation is None:
            return None
        proposal = validate_confirmation(memory.confirmation)
        if proposal.action != action or proposal.parameters != parameters:
            raise ConfirmationError('confirmation_stale')
        if proposal.state == 'consumed':
            raise ConfirmationError('confirmation_consumed')
        if proposal.state == 'expired':
            raise ConfirmationError('confirmation_expired')
        if proposal.state != 'approved':
            raise ConfirmationError('confirmation_not_approved')
        return proposal
    def _reconcile(self, owner, cid, record, action):
        try:
            with self._gateway_factory() as gateway:
                current = validate_update_snapshot(gateway.get_update_snapshot(record.parameters['event_id'],
                    time_zone=record.parameters['time_zone']), record.parameters['event_id'], record.parameters['time_zone'])
            if current.calendar_id != record.calendar_id:
                if record.state == 'confirmed':
                    return _failure(action, 'update_previously_confirmed', record=record, mutation_state='confirmed')
                return _failure(action, 'update_calendar_changed', record=record, mutation_state='unknown')
            before = _record_snapshot(record, current.calendar_name)
            if _matches(before, current, record.action, record.parameters, record.transparency):
                data = _observed_data(record, current)
                if record.state != 'confirmed':
                    data = self._confirm(owner, cid, record, data)
                return validate_update_result(UpdateResult('succeeded', action, 'confirmed',
                    'O estado solicitado foi conferido por consulta, sem nova gravação.', data, record.operation_id))
        except Exception:
            pass
        if record.state == 'confirmed':
            return _failure(action, 'update_previously_confirmed', record=record, mutation_state='confirmed')
        return _failure(action, 'operation_not_confirmed', record=record, mutation_state='unknown')
    def execute(self, owner, cid, action):
        if action not in _ACTIONS:
            raise AgentUpdateError('update_invalid')
        context = self._context.load(owner, cid)
        parameters = _parameters(context.memory, action)
        active = self._ledger.active(owner, cid)
        if active is not None and (active.action != action or active.parameters != parameters):
            return _failure(action, 'update_unresolved', record=active, mutation_state='unknown')
        previous = self._ledger.get(owner, cid, action, parameters)
        if previous is not None and previous.state in {'running', 'unknown', 'confirmed'}:
            return self._reconcile(owner, cid, previous, action)
        proposal = self._proposal(context.memory, action, parameters)
        try:
            with self._gateway_factory() as gateway:
                bound = validate_update_snapshot(gateway.get_update_snapshot(parameters['event_id'],
                    time_zone=parameters['time_zone']), parameters['event_id'], parameters['time_zone'])
        except CalendarSetupError:
            return _failure(action, 'calendar_setup_unavailable')
        except Exception:
            return _failure(action, 'event_read_failed')
        if action == 'reschedule' and bound.event.all_day:
            return _failure(action, 'update_event_unsupported')
        if proposal is not None:
            display = bound.event if bound.event.title.strip() else replace(bound.event, title='(sem título)')
            if (proposal.calendar_id != bound.calendar_id or proposal.event_id != parameters['event_id']
                    or proposal.event_version != bound.version or proposal.event != minimal_event(display)):
                raise ConfirmationError('confirmation_stale')
        reservation = self._ledger.reserve(owner, cid, action, parameters, bound)
        record = reservation.record
        if not reservation.claimed:
            return self._reconcile(owner, cid, record, action)
        try:
            if proposal is not None:
                claim = self._context.consume_confirmation(owner, cid, context.revision,
                    proposal.proposal_id, proposal.request_hash, bound.version)
                if (claim.action != action or claim.parameters != parameters or claim.calendar_id != bound.calendar_id
                        or claim.event_version != bound.version or claim.event_id != parameters['event_id']):
                    raise ConfirmationError('confirmation_stale')
            else:
                current = self._context.load(owner, cid)
                if current.revision != context.revision or current.memory != context.memory:
                    self._known_finish(owner, cid, record, 'not_applied')
                    return _failure(action, 'conversation_conflict', record=record)
        except (ConversationError, ConfirmationError):
            self._known_finish(owner, cid, record, 'not_applied')
            raise
        attempted = False
        try:
            with self._gateway_factory() as gateway:
                wrapped = _BoundGateway(gateway, bound)
                attempted = True
                if action == 'edit':
                    requested = _requested(parameters)
                    result = EventDetailsService(wrapped).update(parameters['event_id'], DetailsChange(**requested),
                        time_zone=parameters['time_zone'], execute=True)
                    data = _public_update(result, parameters['event_id'], requested, parameters['time_zone'])
                    if data['before'] != _public_details(bound.event, parameters['event_id']):
                        raise ValueError()
                else:
                    change = _change(parameters)
                    result = RescheduleService(wrapped).reschedule(parameters['event_id'], change, execute=True)
                    data = _public_reschedule(result, parameters['event_id'], change)
                    if data['before'] != minimal_event(_agenda(bound.event)):
                        raise ValueError()
                after = validate_update_snapshot(gateway.get_update_snapshot(parameters['event_id'],
                    time_zone=parameters['time_zone']), parameters['event_id'], parameters['time_zone'])
                if not _matches(bound, after, action, parameters, bound.transparency):
                    raise ValueError()
                expected_after = _public_details(after.event, parameters['event_id']) if action == 'edit' else minimal_event(_agenda(after.event))
                if data['after'] != expected_after:
                    raise ValueError()
        except (UpdateBindingError, UpdateVersionError) as error:
            self._known_finish(owner, cid, record, 'not_applied')
            return _failure(action, 'update_calendar_changed' if isinstance(error, UpdateBindingError) else 'update_snapshot_changed', record=record)
        except CalendarConflictError as error:
            try:
                if action != 'reschedule':
                    raise ValueError()
                data = _public_conflict(error.report, _change(parameters))
                if any(event['event_id'] == parameters['event_id'] for event in data['conflicts']):
                    raise ValueError()
                self._known_finish(owner, cid, record, 'conflict')
                return validate_update_result(UpdateResult('conflict', action, 'not_applied',
                    'Já existe um compromisso nesse intervalo.', data, record.operation_id, 409, 'schedule_conflict'))
            except Exception:
                return _failure(action, 'update_unavailable', record=record)
        except Exception as error:
            if not attempted:
                self._known_finish(owner, cid, record, 'not_applied')
                return _failure(action, 'calendar_setup_unavailable' if isinstance(error, CalendarSetupError) else 'update_unavailable', record=record)
            try:
                self._finish(owner, cid, record, 'unknown')
            except Exception:
                pass
            return _failure(action, 'operation_not_confirmed', record=record, mutation_state='unknown')
        try:
            data = self._confirm(owner, cid, record, data)
        except Exception:
            return _failure(action, 'operation_not_confirmed', record=record, mutation_state='unknown')
        return validate_update_result(UpdateResult('succeeded', action, 'confirmed',
            'Alteração do evento conferida na agenda.', data, record.operation_id))
