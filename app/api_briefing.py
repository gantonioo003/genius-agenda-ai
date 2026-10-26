"""Briefing factual autenticado, sem IA, reserva ou alteração da agenda."""

import asyncio
import json

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api_security import security_envelope
from app.briefing_calendar import default_briefing_gateway
from app.services.interview_briefing import (
    BriefingInputError, BriefingSetupError, InterviewBriefingService, validate_input,
)


ALLOWED = frozenset({'event_id', 'user_context'})


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _constant(_value):
    raise ValueError()


def _response(request, *, result=None, code=None, fields=(), http_status=200):
    messages = {
        'invalid_request': 'Informe somente o identificador da entrevista e contexto opcional em JSON.',
        'calendar_setup_unavailable': 'O acesso à agenda está indisponível.',
        'briefing_collection_failed': 'Não foi possível confirmar os dados da entrevista; nenhum briefing foi produzido.',
    }
    message = messages[code] if code else ('Briefing factual disponível; nenhuma alteração foi executada.'
        if result['status'] == 'ready' else 'Selecione uma entrevista atual para preparar o briefing.')
    return JSONResponse(status_code=http_status, content=security_envelope(
        request, status='failed' if code else result['status'], action='prepare_briefing',
        operation_id=None, mutation_state='not_applicable', message=message, data=result,
        error={'code': code, 'retryable': False, 'fields': list(fields)} if code else None,
    ))


def _invalid(request, error):
    fields = tuple(field for field in error.fields if field in ALLOWED | {'query', 'body'})
    return _response(request, code='invalid_request', fields=fields or ('body',), http_status=422)


async def _body(request):
    if request.scope.get('query_string'):
        raise BriefingInputError(('query',))
    content_types = request.headers.getlist('content-type')
    if len(content_types) != 1 or content_types[0].split(';', 1)[0].strip().lower() != 'application/json':
        raise BriefingInputError(('body',))
    try:
        raw = bytearray()
        async with asyncio.timeout(2):
            async for chunk in request.stream():
                if len(raw) + len(chunk) > 8192:
                    raise ValueError()
                raw.extend(chunk)
        body = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique, parse_constant=_constant)
        if type(body) is not dict or set(body) - ALLOWED:
            raise ValueError()
    except Exception:
        raise BriefingInputError(('body',)) from None
    return validate_input(body.get('event_id'), body.get('user_context', ''))


def register_briefing_routes(router):
    @router.post('/preparation/briefing', name='api_v1_preparation_briefing', tags=['preparation'],
        summary='Preparar briefing factual e checklist de uma entrevista identificada',
        description=('Relê uma ocorrência da agenda autorizada. Sem identificador ou com evento '
                     'indisponível, pede esclarecimento. Contexto fornecido não é verificação externa. '
                     'Sem modelo, pesquisa, reserva, alteração ou envio.'),
        responses={422: {'description': 'JSON ou parâmetros inválidos.'},
                   502: {'description': 'Dados atuais não confirmados; nenhum briefing produzido.'},
                   503: {'description': 'Configuração da agenda indisponível.'}},
        openapi_extra={'requestBody': {'required': True, 'content': {'application/json': {'schema': {
            'type': 'object', 'additionalProperties': False,
            'properties': {
                'event_id': {'anyOf': [{'type': 'string', 'maxLength': 1024}, {'type': 'null'}]},
                'user_context': {'type': 'string', 'maxLength': 1200, 'default': ''},
            },
        }, 'example': {'event_id': 'evento-ficticio', 'user_context': 'Quero revisar minhas experiências.'}}}}},
    )
    async def prepare_briefing(request: Request):
        # A dependência do router autentica antes de ler o corpo.
        try:
            event_id, notes = await _body(request)
        except BriefingInputError as error:
            return _invalid(request, error)

        def execute():
            factory = request.app.state.briefing_gateway_factory or default_briefing_gateway
            return InterviewBriefingService(factory).prepare(event_id=event_id, user_context=notes)

        try:
            result = await run_in_threadpool(execute)
            if type(result) is not dict or result.get('status') not in ('ready', 'needs_clarification'):
                raise ValueError()
            return _response(request, result=result)
        except BriefingInputError as error:
            return _invalid(request, error)
        except BriefingSetupError:
            return _response(request, code='calendar_setup_unavailable', http_status=503)
        except Exception:
            return _response(request, code='briefing_collection_failed', http_status=502)
