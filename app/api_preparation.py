"""Preparação HTTP autenticada: perguntas e parâmetros, sem execução de escrita."""

import asyncio
import json

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api_security import security_envelope
from app.google_calendar import CalendarSetupError
from app.services.agenda_tools import minimal_event
from app.services.request_preparation import RequestPreparationService

_FIELDS = {'action', 'time_zone', 'title', 'start', 'end', 'description', 'location', 'event_id', 'search_title', 'date', 'period'}
_RESULT_FIELDS = _FIELDS | {'event', 'changes'}


class _SelectionRequired(Exception):
    pass


class _PreparationInputError(Exception):
    pass


class _ValidationGateway:
    def list_events(self, *_args, **_kwargs):
        raise _SelectionRequired()

    def get_event(self, *_args, **_kwargs):
        raise _SelectionRequired()

    def search_events_by_title(self, *_args, **_kwargs):
        raise _SelectionRequired()


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError()
        value[key] = item
    return value


def _response(request, *, http_status=200, status='failed', action=None, message='Não foi possível preparar o pedido.', data=None, code=None, fields=()):
    return JSONResponse(status_code=http_status, content=security_envelope(
        request, status=status, action=action, mutation_state='not_applied', operation_id=None,
        message=message, data=data,
        error={'code': code, 'retryable': False, 'fields': list(fields)} if code else None,
    ))


def register_preparation_route(router):
    @router.post('/requests/prepare', name='api_v1_prepare_request', tags=['agenda'])
    async def prepare_request(request: Request):
        try:
            content_types = request.headers.getlist('content-type')
            if request.scope.get('query_string') or len(content_types) != 1 or content_types[0].split(';', 1)[0].strip().lower() != 'application/json':
                raise ValueError()
            raw = bytearray()
            async with asyncio.timeout(2):
                async for chunk in request.stream():
                    if len(raw) + len(chunk) > 24 * 1024:
                        raise ValueError()
                    raw.extend(chunk)
            body = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique)
            if not isinstance(body, dict) or set(body) != {'request'} or type(body['request']) is not dict:
                raise ValueError()
            payload = body['request']
            if set(payload) - _FIELDS or any(value is not None and not isinstance(value, str) for value in payload.values()):
                raise ValueError()
            for value in payload.values():
                if isinstance(value, str):
                    if len(value) > 4000:
                        raise ValueError()
                    value.encode('utf-8')
        except Exception:
            return _response(request, http_status=422, code='invalid_request', message='Informe somente um pedido válido para preparação.', fields=('request',))

        def execute():
            # A primeira passagem valida tudo e só sinaliza se seleção é necessária.
            try:
                result = RequestPreparationService(_ValidationGateway()).prepare(payload)
            except ValueError:
                raise _PreparationInputError() from None
            except _SelectionRequired:
                with request.app.state.calendar_gateway_factory() as gateway:
                    result = RequestPreparationService(gateway).prepare(payload)
            data = {
                'reason': result.reason, 'question': result.question,
                'missing_fields': [name for name in result.missing_fields if name in _RESULT_FIELDS],
                'invalid_fields': [name for name in result.invalid_fields if name in _RESULT_FIELDS],
                'event': minimal_event(result.event) if result.event is not None else None,
                'candidates': [minimal_event(event) for event in result.candidates],
                'parameters': result.parameters if result.status == 'ready' else None,
            }
            return result.status, result.action, data

        try:
            status, action, data = await run_in_threadpool(execute)
        except CalendarSetupError:
            return _response(request, http_status=503, code='calendar_setup_unavailable', message='O acesso à agenda está indisponível.')
        except _PreparationInputError:
            return _response(request, http_status=422, code='invalid_request', message='Confira os dados do pedido.', fields=('request',))
        except Exception:
            return _response(request, http_status=502, code='operation_failed', message='Não foi possível concluir a preparação do pedido.')
        return _response(request, status=status, action=action, data=data,
                         message=data['question'] or 'Pedido preparado; nenhuma operação de escrita foi executada.')
