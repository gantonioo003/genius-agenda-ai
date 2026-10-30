"""Resumo factual organizado por IA, com fallback e sem alterar a agenda."""

import asyncio
import json

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api_security import security_envelope
from app.language_model import configured_language_model
from app.services.agenda_summary import AgendaSummaryService
from app.services.summary_context import (
    SummaryCollectionError, SummaryContextService, SummaryInputError,
    SummaryLimitError, SummarySetupError, validate_input,
)
from app.summary_calendar import default_summary_calendar_gateway


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
        'invalid_request': 'Informe somente o período e uma data de referência válida em JSON.',
        'calendar_setup_unavailable': 'O acesso à agenda está indisponível.',
        'agenda_context_limit_exceeded': 'O período excede os limites de leitura ou contexto. Consulte um período menor.',
        'agenda_collection_failed': 'Não foi possível confirmar os fatos da agenda; nenhum resumo foi produzido.',
        'agenda_summary_failed': 'Não foi possível concluir o resumo da agenda.',
    }
    if code:
        message = messages[code]
    elif result['summary']['mode'] == 'ai_organized':
        message = 'Resumo organizado com IA a partir dos fatos conferidos da agenda.'
    else:
        message = 'Resumo factual disponível; organização por IA não aplicada.'
    return JSONResponse(status_code=http_status, content=security_envelope(
        request, status='failed' if code else 'succeeded', action='summarize_agenda',
        operation_id=None, mutation_state='not_applicable', message=message, data=result,
        error={'code': code, 'retryable': False, 'fields': list(fields)} if code else None,
    ))


async def _body(request):
    if request.scope.get('query_string'):
        raise SummaryInputError(('query',))
    content_types = request.headers.getlist('content-type')
    if len(content_types) != 1 or content_types[0].split(';', 1)[0].strip().lower() != 'application/json':
        raise SummaryInputError(('body',))
    raw = bytearray()
    try:
        async with asyncio.timeout(2):
            async for chunk in request.stream():
                if len(raw) + len(chunk) > 4096:
                    raise ValueError()
                raw.extend(chunk)
        body = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique, parse_constant=_constant)
        if type(body) is not dict or 'period' not in body or set(body) - {'period', 'date'}:
            raise ValueError()
    except Exception:
        raise SummaryInputError(('body',)) from None
    if 'date' in body and type(body['date']) is not str:
        raise SummaryInputError(('reference_date',))
    validate_input(body['period'], body.get('date'))
    return body['period'], body.get('date')


def register_agenda_summary_routes(router, *, presentation="detailed"):
    if presentation not in {"detailed", "whatsapp"}:
        raise ValueError("Apresentação do resumo inválida.")
    @router.post('/agenda/summary', name='api_v1_agenda_summary', tags=['preparation'],
        summary='Organizar a agenda de semana/mês em resumo fundamentado',
        description=('Coleta atual da agenda autorizada e uma tentativa de organização por IA. '
                     'Falha do modelo usa texto factual; falha de leitura não produz resumo. '
                     'Sem memória de conversa, escrita ou envio WhatsApp.'),
        responses={422: {'description': 'JSON ou parâmetros inválidos.'},
                   502: {'description': 'Fatos não confirmados ou falha interna do resumo.'},
                   503: {'description': 'Configuração de agenda indisponível ou limites excedidos.'}},
        openapi_extra={'requestBody': {'required': True, 'content': {'application/json': {'schema': {
            'type': 'object', 'required': ['period'], 'additionalProperties': False,
            'properties': {'period': {'type': 'string', 'enum': ['week', 'month']},
                           'date': {'type': 'string', 'format': 'date'}},
        }, 'example': {'period': 'week', 'date': '2030-06-19'}}}}},
    )
    async def summarize_agenda(request: Request):
        # A proteção do router autentica antes de ler o corpo ou acessar serviços.
        try:
            period, reference_date = await _body(request)
        except SummaryInputError as error:
            fields = tuple('date' if field == 'reference_date' else field
                           for field in error.fields if field in {'reference_date', 'period', 'query', 'body'})
            return _response(request, code='invalid_request', fields=fields or ('body',), http_status=422)

        def execute():
            state = request.app.state
            context_service = SummaryContextService(state.summary_calendar_gateway_factory or default_summary_calendar_gateway)
            factory = state.summary_model_factory
            if factory is None:
                factory = lambda: configured_language_model(state.settings)
            return AgendaSummaryService(context_service, factory,
                max_input_chars=state.settings.llm_max_input_chars, presentation=presentation).summarize(period=period, reference_date=reference_date)

        try:
            result = await run_in_threadpool(execute)
        except SummarySetupError:
            return _response(request, code='calendar_setup_unavailable', http_status=503)
        except SummaryLimitError:
            return _response(request, code='agenda_context_limit_exceeded', http_status=503)
        except SummaryCollectionError:
            return _response(request, code='agenda_collection_failed', http_status=502)
        except Exception:
            return _response(request, code='agenda_summary_failed', http_status=502)
        return _response(request, result=result)
