"""Formulários do painel reutilizam preparação, confirmação e executores existentes."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from uuid import UUID

from app.agent_creation_store import AgentCreationError, AgentCreationStore
from app.agent_update_store import AgentUpdateError, AgentUpdateStore
from app.agent_deletion_store import AgentDeletionError, AgentDeletionStore
from app.confirmation_state import ConfirmationError
from app.conversation_store import ConversationError, ConversationStore, QueryMemory
from app.dialogue_state import PendingRequest
from app.panel_operation_store import PanelOperationError
from app.services.agent_creation import AgentCreationService, validate_creation_result
from app.services.agent_updates import AgentUpdateService, validate_update_result
from app.services.agent_deletion import AgentDeletionService, validate_deletion_result
from app.services.agenda import AgendaEvent, CalendarPeriod
from app.services.agenda_tools import minimal_event
from app.services.confirmations import ConfirmationService, validate_confirmation_result
from app.services.conflicts import build_conflict_report
from app.services.request_preparation import RequestPreparationService
from app.services.time_rules import load_zone, validate_event_interval
from app.services.update_snapshot import validate_update_snapshot
from app.settings import PROJECT_ROOT


_ACTIONS = {'create', 'edit', 'reschedule', 'delete'}
_LOCAL = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}\Z', re.ASCII)
_HASH = re.compile(r'[0-9a-f]{64}\Z', re.ASCII)
_FIELDS = {'create': {'title', 'start', 'end', 'description'},
           'edit': {'title', 'description', 'location'}, 'reschedule': {'start', 'end'}, 'delete': set()}


class PanelAgendaError(RuntimeError):
    def __init__(self, code='panel_agenda_unavailable', http_status=503,
                 message='Não foi possível conferir a operação. Tente novamente.', fields=()):
        self.code, self.http_status, self.message, self.fields = code, http_status, message, tuple(fields)
        super().__init__(message)


@dataclass(frozen=True, repr=False)
class PanelMutationResult:
    data: dict
    http_status: int = 200


def _error(error):
    if isinstance(error, PanelAgendaError):
        return error
    known = isinstance(error, (PanelOperationError, ConfirmationError, ConversationError,
                              AgentCreationError, AgentUpdateError, AgentDeletionError))
    return PanelAgendaError(error.code if known else 'panel_agenda_unavailable',
                            error.http_status if known else 503,
                            'Não foi possível conferir a operação. Atualize os dados antes de continuar.')


def _uuid(value):
    try:
        parsed = UUID(value)
        if parsed.version != 4 or str(parsed) != value:
            raise ValueError()
    except (TypeError, ValueError, AttributeError):
        raise PanelAgendaError('invalid_request', 422, 'Confira o identificador do pedido.') from None


def _civil(value, zone_name):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError()
    zone = load_zone(zone_name)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        back = parsed.astimezone(timezone.utc).astimezone(zone)
        if back.replace(tzinfo=None) != parsed.replace(tzinfo=None) or back.utcoffset() != parsed.utcoffset():
            raise ValueError()
        return parsed.isoformat()
    if _LOCAL.fullmatch(value) is None:
        raise ValueError()
    candidates = []
    for fold in (0, 1):
        local = parsed.replace(tzinfo=zone, fold=fold)
        back = local.astimezone(timezone.utc).astimezone(zone)
        if back.replace(tzinfo=None) == parsed and local.utcoffset() not in {c.utcoffset() for c in candidates}:
            candidates.append(local)
    if len(candidates) != 1:
        raise ValueError()
    return candidates[0].isoformat()


def _public_event(event):
    if event is None:
        return None
    public = minimal_event(event if event.title.strip() else replace(event, title='(sem título)'))
    for key in ('description', 'location'):
        value = getattr(event, key, '')
        if not isinstance(value, str) or len(value) > 12000:
            raise PanelAgendaError('event_details_too_large', 409,
                                  'Este compromisso tem detalhes extensos. Use o WhatsApp para alterá-lo.')
        value.encode('utf-8')
        public[key] = value
    return public


class PanelAgendaService:
    def __init__(self, operations, context, gateway_factory, *, zone_loader,
                 creation_store, update_store, deletion_store, clock=None):
        self.operations, self.context, self.gateway_factory = operations, context, gateway_factory
        self.zone_loader = zone_loader
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.confirmations = ConfirmationService(gateway_factory, clock=self.clock)
        self.creation = AgentCreationService(context, creation_store, gateway_factory)
        self.updates = AgentUpdateService(context, update_store, gateway_factory)
        self.deletion = AgentDeletionService(context, deletion_store, gateway_factory)

    def _get(self, owner, operation_id):
        _uuid(operation_id)
        try:
            record = self.operations.get(owner, operation_id)
        except PanelOperationError as error:
            raise _error(error) from None
        if record is None:
            raise PanelAgendaError('panel_operation_not_found', 404, 'Esta operação não está disponível neste navegador.')
        return record

    @staticmethod
    def _dto(record, *, state=None, before=None, after=None, message=None,
             mutation_state='not_applied', error=None, conflicts=()):
        return {'operation_id': record.operation_id, 'action': record.action,
                'state': state or record.state, 'before': before, 'after': after,
                'expires_at': datetime.fromtimestamp(record.expires_at, timezone.utc).isoformat(),
                'message': message or 'Confira os dados antes de confirmar.',
                'mutation_state': mutation_state, 'error': error, 'conflicts': list(conflicts)}

    def _result(self, record):
        data = dict(record.dto) if record.dto is not None else self._dto(record)
        data['expires_at'] = datetime.fromtimestamp(record.expires_at, timezone.utc).isoformat()
        if record.state in {'expired', 'cancelled', 'preparing', 'confirming'}:
            data.update(state=record.state, mutation_state='unknown' if record.state == 'confirming' else 'not_applied')
            data['message'] = {'expired': 'A prévia expirou. Confira os dados e gere outra.',
                               'cancelled': 'Operação cancelada. Nenhum compromisso foi alterado.',
                               'preparing': 'A prévia está sendo preparada. Confira o estado em instantes.',
                               'confirming': 'A confirmação está em andamento. Confira o estado antes de qualquer nova tentativa.'}[record.state]
        code = 202 if data['state'] in {'preparing', 'confirming'} else 409 if data['state'] in {'expired', 'invalidated', 'conflict'} else 502 if data['state'] == 'unknown' else 422 if data['state'] == 'failed' else 200
        return PanelMutationResult(data, code)

    def status(self, owner, operation_id):
        return self._result(self._get(owner, operation_id))

    @staticmethod
    def _input(owner, request_id, action, event_id, event):
        _uuid(request_id)
        if not isinstance(owner, str) or not _HASH.fullmatch(owner) or not isinstance(action, str) or action not in _ACTIONS:
            raise PanelAgendaError('invalid_request', 422, 'Confira os dados do pedido.')
        if action == 'create':
            if event_id is not None:
                raise PanelAgendaError('invalid_request', 422, 'A criação não aceita um compromisso existente.')
        else:
            from app.services.event_selection import validate_event_id
            try:
                validate_event_id(event_id)
            except ValueError:
                raise PanelAgendaError('invalid_request', 422, 'Selecione um compromisso da agenda.', ('event_id',)) from None
        if event is None:
            event = {}
        if type(event) is not dict or set(event) - _FIELDS[action]:
            raise PanelAgendaError('invalid_request', 422, 'Confira os campos do compromisso.')
        if action in {'create', 'reschedule'} and not {'start', 'end'} <= set(event):
            raise PanelAgendaError('invalid_request', 422, 'Informe início e término.', ('start', 'end'))
        if action == 'create' and 'title' not in event or action == 'edit' and not event:
            raise PanelAgendaError('invalid_request', 422, 'Informe os dados que deseja alterar.')
        for key, value in event.items():
            if not isinstance(value, str) or len(value) > (400 if key == 'title' else 4000):
                raise PanelAgendaError('invalid_request', 422, 'Confira os campos do compromisso.', (key,))
            try:
                value.encode('utf-8')
            except UnicodeError:
                raise PanelAgendaError('invalid_request', 422, 'Confira o texto informado.', (key,)) from None
            if key == 'title' and not value.strip():
                raise PanelAgendaError('invalid_request', 422, 'Informe um título.', ('title',))
        return dict(event)

    def _save_memory(self, owner, cid, previous, memory):
        if memory != previous.memory:
            self.context.save(owner, cid, previous.revision, memory)

    def _finish(self, owner, record, data, state):
        try:
            stored = self.operations.finish(owner, record.operation_id, state, data)
            return self._result(stored)
        except Exception:
            # A gravação no calendário pode ter terminado antes da falha local.
            return PanelMutationResult(self._dto(record, state='unknown', before=data.get('before'),
                after=None, message='Não foi possível confirmar o resultado. Consulte a agenda antes de tentar novamente.',
                mutation_state='unknown', error={'code': 'operation_not_confirmed', 'fields': []}), 502)

    def preview(self, owner, request_id, action, event_id=None, event=None):
        event = self._input(owner, request_id, action, event_id, event)
        digest = sha256(json.dumps({'action': action, 'event_id': event_id, 'event': event},
                                  ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        try:
            reserved = self.operations.reserve(owner, request_id, action, digest)
        except PanelOperationError as error:
            raise _error(error) from None
        record = reserved.record
        if not reserved.claimed:
            return self._result(record)
        before = after = None
        try:
            zone = self.zone_loader()
            load_zone(zone)
            fields = {'action': action, 'time_zone': zone, **event}
            if event_id is not None:
                fields['event_id'] = event_id
            if action in {'create', 'reschedule'}:
                for key in ('start', 'end'):
                    try:
                        fields[key] = _civil(event[key], zone)
                    except (ValueError, OverflowError):
                        raise PanelAgendaError('invalid_schedule', 422, 'Confira a data e o horário. Horários ambíguos precisam de deslocamento UTC explícito.', (key,)) from None
            with self.gateway_factory() as gateway:
                prepared = RequestPreparationService(gateway, default_time_zone=zone).prepare(fields)
            if prepared.status != 'ready':
                raise PanelAgendaError(prepared.reason or 'invalid_request', 422,
                                      prepared.question or 'Confira os dados do compromisso.',
                                      prepared.invalid_fields + prepared.missing_fields)
            cid = self.context.create(owner)
            record = self.operations.attach_context(owner, record.operation_id, cid)
            pending = QueryMemory(pending_request=PendingRequest({'action': action, **prepared.parameters}))
            self.context.save(owner, cid, 0, pending)
            result, memory = self.confirmations.preview(pending)
            validate_confirmation_result(result)
            if result.error_code or memory.confirmation is None:
                raise PanelAgendaError(result.error_code or 'preview_failed', result.http_status, result.message)
            snap = self.context.load(owner, cid)
            self._save_memory(owner, cid, snap, memory)
            proposal = memory.confirmation
            if event_id is not None:
                with self.gateway_factory() as gateway:
                    current = validate_update_snapshot(gateway.get_update_snapshot(event_id, time_zone=zone), event_id, zone)
                if current.calendar_id != proposal.calendar_id or current.version != proposal.event_version:
                    raise PanelAgendaError('event_changed', 409, 'O compromisso mudou durante a consulta. Atualize e gere outra prévia.')
                before = _public_event(current.event)
            if action == 'create':
                after = {'event_id': None, 'title': prepared.parameters['title'], 'start': prepared.parameters['start'],
                         'end': prepared.parameters['end'], 'time_zone': zone, 'description': prepared.parameters.get('description', ''),
                         'location': '', 'all_day': False, 'status': 'confirmed', 'html_link': None}
            elif action == 'delete':
                after = None
            else:
                after = dict(before)
                for key in _FIELDS[action]:
                    if key in prepared.parameters:
                        after[key] = prepared.parameters[key]
                if after == before:
                    raise PanelAgendaError('no_changes', 422, 'Os dados já estão assim. Informe uma alteração.')
            if action in {'create', 'reschedule'}:
                with self.gateway_factory() as gateway:
                    period = CalendarPeriod(datetime.fromisoformat(prepared.parameters['start']), datetime.fromisoformat(prepared.parameters['end']), zone)
                    report = gateway.check_conflicts(period, exclude_event_id=event_id)
                    checked = build_conflict_report(period, report.calendar_name, report.conflicts, exclude_event_id=event_id)
                    if report.status != checked.status or report.start != checked.start or report.end != checked.end or report.time_zone != zone:
                        raise ValueError()
                if checked.conflicts:
                    data = self._dto(record, state='conflict', before=before, after=after,
                                     message='Já existe um compromisso nesse intervalo.',
                                     error={'code': 'schedule_conflict', 'fields': ['start', 'end']},
                                     conflicts=[_public_event(item) for item in checked.conflicts])
                    return self._finish(owner, record, data, 'conflict')
            data = self._dto(record, state='pending', before=before, after=after,
                             message={'create': 'Confira a criação.', 'edit': 'Confira a edição.',
                                      'reschedule': 'Confira o reagendamento.', 'delete': 'Confirma excluir este compromisso?'}[action])
            data['expires_at'] = proposal.expires_at
            record = self.operations.attach_preview(owner, record.operation_id, proposal.proposal_id, proposal.expires_at, data)
            return self._result(record)
        except Exception as error:
            public = _error(error)
            data = self._dto(record, state='invalidated' if public.http_status == 409 else 'failed',
                             before=before, after=after, message=public.message,
                             error={'code': public.code, 'fields': list(public.fields)})
            finished = self._finish(owner, record, data, 'failed')
            return PanelMutationResult(finished.data, public.http_status)

    def cancel(self, owner, operation_id):
        record = self._get(owner, operation_id)
        try:
            cancelled = self.operations.cancel(owner, operation_id)
        except PanelOperationError as error:
            raise _error(error) from None
        if record.conversation_id and record.proposal_id:
            try:
                snapshot = self.context.load(owner, record.conversation_id)
                _, memory = self.confirmations.revoke(snapshot.memory, record.proposal_id)
                self._save_memory(owner, record.conversation_id, snapshot, memory)
            except Exception:
                pass  # The session-scoped gate is already cancelled and cannot execute.
        return self._result(cancelled)

    def confirm(self, owner, operation_id):
        record = self._get(owner, operation_id)
        try:
            claim = self.operations.claim_confirm(owner, operation_id)
        except PanelOperationError as error:
            raise _error(error) from None
        record = claim.record
        if not claim.claimed:
            return self._result(record)
        before = record.dto.get('before') if record.dto else None
        attempted = False
        try:
            if not record.conversation_id or not record.proposal_id:
                raise PanelAgendaError('preview_required', 409, 'Gere uma prévia antes de confirmar.')
            snapshot = self.context.load(owner, record.conversation_id)
            if snapshot.memory.confirmation is None or self.zone_loader() != snapshot.memory.confirmation.parameters['time_zone']:
                raise PanelAgendaError('calendar_changed', 409, 'A agenda mudou. Gere outra prévia.')
            approval, memory = self.confirmations.approve(snapshot.memory, record.proposal_id, 'sim')
            validate_confirmation_result(approval)
            self._save_memory(owner, record.conversation_id, snapshot, memory)
            if approval.error_code or memory.confirmation.state != 'approved':
                raise PanelAgendaError(approval.error_code or 'confirmation_invalid', approval.http_status, approval.message)
            attempted = True
            if record.action == 'create':
                result = validate_creation_result(self.creation.execute(owner, record.conversation_id))
            elif record.action == 'delete':
                result = validate_deletion_result(self.deletion.execute(owner, record.conversation_id))
            else:
                result = validate_update_result(self.updates.execute(owner, record.conversation_id, record.action))
            after = None
            state = 'succeeded' if result.status == 'succeeded' else 'conflict' if result.status == 'conflict' else 'unknown' if result.mutation_state == 'unknown' else 'failed'
            if state == 'succeeded' and record.action != 'delete':
                event_id = result.data['event_id'] if record.action == 'create' else result.data['after']['event_id']
                zone = memory.confirmation.parameters['time_zone']
                with self.gateway_factory() as gateway:
                    current = validate_update_snapshot(gateway.get_update_snapshot(event_id, time_zone=zone), event_id, zone)
                if current.calendar_id != memory.confirmation.calendar_id:
                    raise ValueError()
                after = _public_event(current.event)
                expected = record.dto['after']
                if any(after[key] != expected[key] for key in ('title', 'start', 'end', 'description', 'location', 'all_day')):
                    raise ValueError()
            code = result.error_code
            data = self._dto(record, state=state, before=before, after=after,
                             message={'create': 'Compromisso criado na agenda.', 'edit': 'Compromisso atualizado.',
                                      'reschedule': 'Compromisso reagendado.', 'delete': 'Compromisso excluído da agenda.'}[record.action] if state == 'succeeded' else result.message,
                             mutation_state=result.mutation_state,
                             error={'code': code, 'fields': []} if code else None,
                             conflicts=[_public_event(AgendaEvent(**item)) for item in result.data.get('conflicts', [])] if state == 'conflict' else ())
            return self._finish(owner, record, data, state)
        except Exception as error:
            public = _error(error)
            # Existing stale/expiry guards fail before writes, even after entering executor.
            safe_guard = public.code in {'confirmation_stale', 'confirmation_expired', 'confirmation_not_approved',
                                         'confirmation_consumed', 'conversation_conflict'}
            unknown = attempted and not safe_guard
            data = self._dto(record, state='unknown' if unknown else 'invalidated' if public.http_status == 409 else 'failed',
                             before=before, message='Não foi possível confirmar o resultado. Consulte a agenda antes de tentar novamente.' if unknown else public.message,
                             mutation_state='unknown' if unknown else 'not_applied',
                             error={'code': 'operation_not_confirmed' if unknown else public.code, 'fields': list(public.fields)})
            return self._finish(owner, record, data, 'unknown' if unknown else 'failed')


def default_panel_agenda_service(config, gateway_factory):
    from app.api_panel import default_panel_calendar_gateway
    from app.panel_operation_store import PanelOperationStore

    def path(value):
        return value if value.is_absolute() else PROJECT_ROOT / value

    def zone_loader():
        with default_panel_calendar_gateway() as gateway:
            return gateway.binding().time_zone

    return PanelAgendaService(PanelOperationStore(path(config.panel_operations_database_path)),
        ConversationStore(path(config.conversation_database_path), ttl_seconds=config.conversation_ttl_seconds),
        gateway_factory, zone_loader=zone_loader,
        creation_store=AgentCreationStore(path(config.agent_creation_database_path)),
        update_store=AgentUpdateStore(path(config.agent_update_database_path)),
        deletion_store=AgentDeletionStore(path(config.agent_deletion_database_path)))
