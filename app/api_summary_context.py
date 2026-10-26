"""Coleta autenticada do contexto factual de semana/mês, sem chamar IA."""

from fastapi import Request
from fastapi.responses import JSONResponse

from app.api_security import security_envelope
from app.services.summary_context import (
    SummaryContextService, SummaryInputError,
    SummaryLimitError, SummarySetupError, validate_input,
)
from app.summary_calendar import default_summary_calendar_gateway


def _response(request, *, data=None, code=None, fields=(), http_status=200):
    messages = {
        'invalid_request': 'Informe semana ou mês e uma data de referência válida, sem outros parâmetros.',
        'calendar_setup_unavailable': 'O acesso à agenda está indisponível.',
        'agenda_context_limit_exceeded': 'O período excede os limites de leitura ou contexto. Consulte um período menor.',
        'agenda_collection_failed': 'Não foi possível concluir a coleta da agenda. Nenhum contexto parcial foi confirmado.',
    }
    return JSONResponse(status_code=http_status, content=security_envelope(
        request, status='failed' if code else 'succeeded', action='collect_agenda_context',
        operation_id=None, mutation_state='not_applicable',
        message=messages.get(code, 'Contexto factual da agenda coletado, sem geração de texto por IA.'),
        data=data, error={'code': code, 'retryable': False, 'fields': list(fields)} if code else None,
    ))


def _arguments(request):
    if len(request.scope.get('query_string', b'')) > 1024:
        raise SummaryInputError(('query',))
    counts = {}
    for key, _value in request.query_params.multi_items():
        counts[key] = counts.get(key, 0) + 1
        if key not in {'period', 'date'} or counts[key] > 1:
            raise SummaryInputError(('query',))
    period = request.query_params.get('period')
    reference_date = request.query_params.get('date')
    validate_input(period, reference_date)
    return period, reference_date


def register_summary_context_routes(router):
    @router.get(
        '/agenda/context', name='api_v1_agenda_context', tags=['preparation'],
        summary='Coletar fatos da semana ou do mês para os futuros resumos',
        description=('Somente leitura. Fuso da agenda autorizada; date ausente usa hoje nesse fuso. '
                     'Fonte completa ou falha, sem LLM, memória de conversa ou escrita de evento.'),
        responses={
            422: {'description': 'Parâmetros inválidos; nenhuma entrada bruta é reproduzida.'},
            502: {'description': 'Coleta não concluída ou resposta inválida.'},
            503: {'description': 'Acesso indisponível ou limite de leitura/contexto excedido.'},
        },
        openapi_extra={'parameters': [
            {'in': 'query', 'name': 'period', 'required': True, 'schema': {'type': 'string', 'enum': ['week', 'month']}},
            {'in': 'query', 'name': 'date', 'required': False, 'schema': {'type': 'string', 'format': 'date'},
             'description': 'Data de referência AAAA-MM-DD; ausente usa hoje no fuso efetivo da agenda.'},
        ]},
    )
    def collect_agenda_context(request: Request):
        # Autenticação do router acontece antes de validar ou abrir o gateway.
        try:
            period, reference_date = _arguments(request)
        except SummaryInputError as error:
            fields = tuple('date' if field == 'reference_date' else field
                           for field in error.fields if field in {'reference_date', 'period', 'query'})
            return _response(request, code='invalid_request', fields=fields or ('query',), http_status=422)
        try:
            factory = request.app.state.summary_calendar_gateway_factory or default_summary_calendar_gateway
            context = SummaryContextService(factory).build(period=period, reference_date=reference_date)
        except SummarySetupError:
            return _response(request, code='calendar_setup_unavailable', http_status=503)
        except SummaryLimitError:
            return _response(request, code='agenda_context_limit_exceeded', http_status=503)
        except Exception:
            return _response(request, code='agenda_collection_failed', http_status=502)
        return _response(request, data=context)
