"""Fronteira autenticada de um pulso de lembretes, com saída só de agregados."""

import asyncio
from contextlib import contextmanager
import json

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api_security import security_envelope
from app.plan_reminders import load_recipient_binding
from app.reminder_store import ReminderStore
from app.services.reminder_tick import ReminderTickService, ReminderTickSummary
from app.settings import PROJECT_ROOT


def default_reminder_store(config):
    path = config.reminder_database_path
    return ReminderStore(path if path.is_absolute() else PROJECT_ROOT / path)


@contextmanager
def default_reminder_gateway():
    # Importar/abrir somente depois de validar o perfil e o vínculo privado.
    from app.google_calendar import calendar_client
    from app.reminder_calendar import GoogleReminderCalendarGateway
    with calendar_client(interactive=False) as client:
        yield GoogleReminderCalendarGateway(client)


def default_recipient_binding():
    return load_recipient_binding(PROJECT_ROOT)


def _response(request, *, result=None, invalid_field=None):
    if invalid_field is not None:
        http_status, status = 422, "failed"
        message = "Informe somente um objeto JSON vazio, sem parâmetros de consulta."
        data = None
        error = {"code": "invalid_request", "retryable": False, "fields": [invalid_field]}
    else:
        data = result.public_data()
        failed = result.tick_status in {"failed", "partial"}
        http_status = 503 if failed else 200
        status = "failed" if failed else "skipped" if result.tick_status == "skipped" else "succeeded"
        message = ("A sincronização local foi concluída; a verificação terminou parcialmente."
                   if result.tick_status == "partial" else
                   "Não foi possível concluir a verificação dos lembretes." if failed else
                   "Verificação dos lembretes dispensada." if result.tick_status == "skipped" else
                   "Verificação local dos lembretes concluída, sem envio de mensagens.")
        error = ({"code": "reminder_tick_partial" if result.tick_status == "partial" else "reminder_tick_failed",
                  "retryable": False, "fields": []} if failed else None)
    return JSONResponse(status_code=http_status, content=security_envelope(request,
        status=status, action="reminder_tick", mutation_state="not_applicable",
        operation_id=None, message=message, data=data, error=error))


async def _empty_json(request):
    types = request.headers.getlist("content-type")
    if (len(types) != 1
            or types[0].split(";", 1)[0].strip().lower() != "application/json"):
        raise ValueError()
    raw = bytearray()
    async with asyncio.timeout(2):
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 1024:
                raise ValueError()
            raw.extend(chunk)
    body = json.loads(raw.decode("utf-8"))
    if type(body) is not dict or body:
        raise ValueError()


def register_reminder_routes(router):
    @router.post(
        "/reminders/tick", name="api_v1_reminder_tick", tags=["reminders"],
        summary="Sincronizar e gerar avisos locais, sem enviar mensagens",
        description=("Pulso sem parâmetros, sem LLM e sem envio. A sincronização precede a geração. "
                     "Um lock de SO evita ticks simultâneos para a mesma base; não há retry automático. "
                     "Falha da geração pode preservar uma sincronização local já concluída. "
                     "local_mutation_state=applied confirma a conclusão das duas fases locais, "
                     "inclusive sem novas linhas; partial conserva a primeira fase concluída."),
        responses={422: {"description": "Corpo ou consulta inválidos, sem reprodução da entrada."},
                   503: {"description": "Verificação indisponível ou parcialmente concluída localmente."}},
        openapi_extra={"requestBody": {"required": True, "content": {"application/json": {
            "schema": {"type": "object", "properties": {}, "additionalProperties": False,
                       "maxProperties": 0}, "example": {},
        }}}},
    )
    async def tick_reminders(request: Request):
        # A dependência do router autentica antes de ler/parsing do corpo.
        if request.scope.get("query_string"):
            return _response(request, invalid_field="query")
        try:
            await _empty_json(request)
        except Exception:
            return _response(request, invalid_field="body")

        def tick():
            store_factory = request.app.state.reminder_store_factory
            store = (default_reminder_store(request.app.state.settings)
                     if store_factory is None else store_factory())
            gateway_factory = request.app.state.reminder_gateway_factory or default_reminder_gateway
            binding_loader = request.app.state.reminder_recipient_binding_loader or default_recipient_binding
            return ReminderTickService(store, gateway_factory, binding_loader).tick()

        try:
            # Sem wait_for/timeout do worker: cancelar a espera não cancela um
            # cliente síncrono em voo. O lock dura até o trabalho real terminar.
            result = await run_in_threadpool(tick)
            return _response(request, result=result)
        except Exception:
            return _response(request, result=ReminderTickSummary("failed"))
