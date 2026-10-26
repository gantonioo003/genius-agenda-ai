"""Consulta autenticada de horários livres; não reserva nem altera eventos."""

import asyncio
import json

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api_security import security_envelope
from app.availability_calendar import default_availability_gateway
from app.services.availability import (
    AvailabilityService, AvailabilityInputError,
    AvailabilityLimitError, AvailabilitySetupError, validate_request,
)


REQUIRED = frozenset({'date_from', 'date_to', 'window_start', 'window_end', 'duration_minutes'})
ALLOWED = REQUIRED | {'buffer_minutes'}


class _HttpInputError(ValueError):
    def __init__(self, field):
        super().__init__('Entrada HTTP inválida.')
        self.fields = (field,)


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError()
        value[key] = item
    return value


def _constant(_value):
    raise ValueError()


def _response(request, *, result=None, code=None, fields=(), http_status=200):
    messages = {
        'invalid_request': 'Informe datas, faixa de horário e duração válidas; a faixa deve existir sem ambiguidade no fuso da agenda.',
        'calendar_setup_unavailable': 'O acesso à agenda está indisponível.',
        'availability_limit_exceeded': 'O período excede o limite de leitura. Consulte um período menor.',
        'availability_collection_failed': 'Não foi possível confirmar a ocupação da agenda; nenhum horário foi declarado livre.',
    }
    message = messages[code] if code else ('Opções livres nesta agenda no instante consultado; nenhuma reserva foi criada.'
        if result['count'] else 'Nenhuma opção futura na faixa consultada; nenhuma reserva foi criada.')
    return JSONResponse(status_code=http_status, content=security_envelope(
        request, status='failed' if code else 'succeeded', action='find_free_slots',
        mutation_state='not_applicable', operation_id=None, message=message, data=result,
        error={'code': code, 'retryable': False, 'fields': list(fields)} if code else None,
    ))


def _invalid(request, error):
    fields = tuple(field for field in error.fields if field in ALLOWED | {'query', 'body'})
    return _response(request, code='invalid_request', fields=fields or ('body',), http_status=422)


async def _body(request):
    if request.scope.get('query_string'):
        raise _HttpInputError('query')
    content_types = request.headers.getlist('content-type')
    if len(content_types) != 1 or content_types[0].split(';', 1)[0].strip().lower() != 'application/json':
        raise _HttpInputError('body')
    try:
        raw = bytearray()
        async with asyncio.timeout(2):
            async for chunk in request.stream():
                if len(raw) + len(chunk) > 4096:
                    raise ValueError()
                raw.extend(chunk)
        body = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique, parse_constant=_constant)
        if type(body) is not dict or set(body) - ALLOWED or REQUIRED - set(body):
            raise ValueError()
    except Exception:
        raise _HttpInputError('body') from None
    validate_request(**body)
    return body


def register_availability_routes(router):
    @router.post('/agenda/availability', name='api_v1_agenda_availability', tags=['preparation'],
        summary='Calcular até três opções de horário livres na agenda autorizada',
        description=('Somente leitura, sem IA ou reserva. Datas inclusivas por até 31 dias; '
                     'faixa diária explícita, duração real e margem opcional. Fuso da agenda. '
                     'Os horários são alternativas e podem se sobrepor entre si.'),
        responses={422: {'description': 'JSON, intervalo ou faixa civil inválidos/ambíguos.'},
                   502: {'description': 'Ocupação não confirmada; nenhum horário declarado livre.'},
                   503: {'description': 'Configuração indisponível ou limite de leitura excedido.'}},
        openapi_extra={'requestBody': {'required': True, 'content': {'application/json': {'schema': {
            'type': 'object', 'required': sorted(REQUIRED), 'additionalProperties': False,
            'properties': {
                'date_from': {'type': 'string', 'format': 'date'}, 'date_to': {'type': 'string', 'format': 'date'},
                'window_start': {'type': 'string', 'pattern': '^(?:[01][0-9]|2[0-3]):[0-5][0-9]$'},
                'window_end': {'type': 'string', 'pattern': '^(?:(?:[01][0-9]|2[0-3]):[0-5][0-9]|24:00)$'},
                'duration_minutes': {'type': 'integer', 'minimum': 1, 'maximum': 1440},
                'buffer_minutes': {'type': 'integer', 'minimum': 0, 'maximum': 240, 'default': 0},
            },
        }, 'example': {'date_from': '2030-06-19', 'date_to': '2030-06-19',
            'window_start': '09:00', 'window_end': '12:00', 'duration_minutes': 30}}}}},
    )
    async def find_free_slots(request: Request):
        try:
            arguments = await _body(request)
        except (AvailabilityInputError, _HttpInputError) as error:
            return _invalid(request, error)

        def execute():
            factory = request.app.state.availability_gateway_factory or default_availability_gateway
            return AvailabilityService(factory).find(**arguments)

        try:
            result = await run_in_threadpool(execute)
        except AvailabilityInputError as error:
            return _invalid(request, error)
        except AvailabilitySetupError:
            return _response(request, code='calendar_setup_unavailable', http_status=503)
        except AvailabilityLimitError:
            return _response(request, code='availability_limit_exceeded', http_status=503)
        except Exception:
            return _response(request, code='availability_collection_failed', http_status=502)
        return _response(request, result=result)
