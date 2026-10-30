"""Session-bound panel commands: preview first, explicit approval, existing executors."""

import asyncio
import json
from uuid import UUID

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.panel_security import require_panel_session


MAX_BODY = 24000
_ACTIONS = {'create', 'edit', 'reschedule', 'delete'}


def _reply(data=None, *, status=200, code=None, message=None, fields=(), unknown=False):
    return JSONResponse(status_code=status, headers={'Cache-Control': 'no-store', 'Vary': 'Cookie'}, content={
        'status': 'error' if code or status >= 400 else 'ok', 'data': data,
        'message': message or (data.get('message') if isinstance(data, dict) else None),
        'mutation_state': 'unknown' if unknown else 'not_applied' if code else (data or {}).get('mutation_state', 'not_applied'),
        'error': ({'code': code, 'fields': list(fields), 'retryable': False} if code else (data or {}).get('error')),
    })


def _uuid(value):
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError()
    parsed = UUID(value)
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError()
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _constant(_value):
    raise ValueError()


async def _body(request):
    if request.scope.get('query_string'):
        raise ValueError()
    types = request.headers.getlist('content-type')
    if len(types) != 1 or types[0].split(';', 1)[0].strip().lower() != 'application/json':
        raise ValueError()
    raw = bytearray()
    async with asyncio.timeout(2):
        async for chunk in request.stream():
            if len(raw) + len(chunk) > MAX_BODY:
                raise ValueError()
            raw.extend(chunk)
    body = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique, parse_constant=_constant)
    if type(body) is not dict:
        raise ValueError()
    return body


def _preview_body(body):
    if not {'request_id', 'action'} <= body.keys() or body.keys() - {'request_id', 'action', 'event_id', 'event'}:
        raise ValueError()
    _uuid(body['request_id'])
    action = body['action']
    if not isinstance(action, str) or action not in _ACTIONS:
        raise ValueError()
    if action == 'create':
        if 'event_id' in body:
            raise ValueError()
    else:
        from app.services.event_selection import validate_event_id
        validate_event_id(body.get('event_id'))
    if action == 'delete':
        if 'event' in body:
            raise ValueError()
        return body
    event = body.get('event')
    if type(event) is not dict:
        raise ValueError()
    allowed = {'title', 'start', 'end', 'description'} if action == 'create' else {'start', 'end'} if action == 'reschedule' else {'title', 'description', 'location'}
    required = {'title', 'start', 'end'} if action == 'create' else {'start', 'end'} if action == 'reschedule' else set()
    if event.keys() - allowed or not required <= event.keys() or not event:
        raise ValueError()
    for key, value in event.items():
        if not isinstance(value, str) or len(value) > (4000 if key in {'description', 'location'} else 400):
            raise ValueError()
        value.encode('utf-8')
    return body


def _service(request):
    factory = getattr(request.app.state, 'panel_agenda_service_factory', None)
    if factory is not None:
        return factory()
    from app.services.panel_agenda import default_panel_agenda_service
    return default_panel_agenda_service(request.app.state.settings, request.app.state.calendar_gateway_factory)


def _execute(request, session_hash, command, payload):
    from app.services.panel_agenda import PanelAgendaError, PanelMutationResult
    try:
        service = _service(request)
        result = (service.preview(session_hash, **payload) if command == 'preview'
                  else getattr(service, command)(session_hash, payload))
        if not isinstance(result, PanelMutationResult) or not isinstance(result.data, dict) or not 200 <= result.http_status <= 599:
            raise ValueError()
        return _reply(result.data, status=result.http_status)
    except PanelAgendaError as error:
        return _reply(status=error.http_status, code=error.code, message=error.message, fields=error.fields)
    except Exception:
        unknown = command in {'confirm', 'status'}
        return _reply(status=503, code='panel_operation_unavailable', unknown=unknown,
                      message=('Não foi possível confirmar o resultado. Confira a operação antes de tentar novamente.'
                               if unknown else 'Não foi possível preparar esta ação. Tente novamente mais tarde.'))


def register_panel_agenda_routes(router):
    @router.post('/agenda/preview', name='panel_agenda_preview', tags=['panel'])
    async def preview(request: Request):
        session = await require_panel_session(request)
        try:
            body = _preview_body(await _body(request))
        except Exception:
            return _reply(status=422, code='invalid_request', message='Confira os campos do compromisso e tente novamente.')
        return await run_in_threadpool(_execute, request, session.id_hash, 'preview', body)

    @router.get('/agenda/{operation_id}', name='panel_agenda_operation', tags=['panel'])
    async def status(request: Request, operation_id: str):
        session = await require_panel_session(request)
        try:
            _uuid(operation_id)
            if request.scope.get('query_string'):
                raise ValueError()
        except ValueError:
            return _reply(status=422, code='invalid_request', message='Esta operação não está disponível.')
        return await run_in_threadpool(_execute, request, session.id_hash, 'status', operation_id)

    def command_route(command):
        @router.post('/agenda/{operation_id}/' + command, name='panel_agenda_' + command, tags=['panel'])
        async def mutation(request: Request, operation_id: str):
            session = await require_panel_session(request)
            try:
                _uuid(operation_id)
                if await _body(request):
                    raise ValueError()
            except Exception:
                return _reply(status=422, code='invalid_request', message='Confira a prévia antes de continuar.')
            return await run_in_threadpool(_execute, request, session.id_hash, command, operation_id)
    command_route('confirm')
    command_route('cancel')
