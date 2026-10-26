"""Recuperação autenticada e local do ledger, sem consulta ou transporte."""

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api_reminders import _empty_json, default_reminder_store
from app.api_security import security_envelope
from app.services.reminder_recovery import ReminderRecoveryService, ReminderRecoverySummary


_COUNTERS = ("reservations_recovered", "expired_count", "uncertain_count", "protected_count")
_SKIP_REASONS = {"not_configured", "disabled", "permission_required", "busy"}


def _public_data(result):
    if type(result) is not ReminderRecoverySummary:
        raise ValueError()
    data = result.public_data()
    if (type(data) is not dict
            or set(data) != {"recovery_status", "skip_reason", *_COUNTERS, "messages_sent", "external_mutations"}
            or data["recovery_status"] not in {"completed", "skipped", "failed"}
            or any(type(data[key]) is not int or not 0 <= data[key] <= 9007199254740991 for key in _COUNTERS)
            or type(data["messages_sent"]) is not int or data["messages_sent"] != 0
            or type(data["external_mutations"]) is not int or data["external_mutations"] != 0):
        raise ValueError()
    if data["recovery_status"] == "skipped":
        if data["skip_reason"] not in _SKIP_REASONS or any(data[key] for key in _COUNTERS):
            raise ValueError()
    elif data["skip_reason"] is not None:
        raise ValueError()
    return data


def _response(request, *, result=None, invalid_field=None, unavailable=False):
    data, error = None, None
    if invalid_field is not None:
        http_status, status = 422, "failed"
        message = "Informe somente um objeto JSON vazio, sem parâmetros de consulta."
        error = {"code": "invalid_request", "retryable": False, "fields": [invalid_field]}
    elif unavailable:
        http_status, status = 503, "failed"
        message = "Não foi possível confirmar a recuperação local dos lembretes."
        error = {"code": "reminder_recovery_failed", "retryable": False, "fields": []}
    else:
        data = _public_data(result)
        failed = data["recovery_status"] == "failed"
        http_status = 503 if failed else 200
        status = "failed" if failed else "skipped" if data["recovery_status"] == "skipped" else "succeeded"
        message = ("Não foi possível concluir a recuperação local dos lembretes; consulte os contadores."
                   if failed else "Recuperação local dos lembretes dispensada."
                   if data["recovery_status"] == "skipped" else
                   "Recuperação local dos lembretes concluída, sem envio de mensagens.")
        if failed:
            error = {"code": "reminder_recovery_failed", "retryable": False, "fields": []}
    return JSONResponse(status_code=http_status, content=security_envelope(request,
        status=status, action="reminder_recovery", mutation_state="not_applicable",
        operation_id=None, message=message, data=data, error=error))


def register_reminder_recovery_routes(router):
    @router.post(
        "/reminders/recover", name="api_v1_reminder_recovery", tags=["reminders"],
        summary="Recuperar somente tentativas locais de lembretes que sejam seguras",
        description=("Recebe somente {} e usa o mesmo lock de SO do tick e do disparo. "
                     "Não consulta a agenda, não carrega destinatário nem envia mensagens. "
                     "Um resultado incerto de transporte continua protegido contra repetição. "
                     "Falhas posteriores a uma conclusão local podem preservar contadores confirmados; "
                     "uma falha inesperada da fronteira retorna data=null, sem inventar zero alterações."),
        responses={422: {"description": "Corpo ou consulta inválidos, sem reprodução da entrada."},
                   503: {"description": "Recuperação local não concluída ou não confirmada; sem retry automático."}},
        openapi_extra={"requestBody": {"required": True, "content": {"application/json": {
            "schema": {"type": "object", "properties": {}, "additionalProperties": False,
                       "maxProperties": 0}, "example": {},
        }}}},
    )
    async def recover_reminders(request: Request):
        if request.scope.get("query_string"):
            return _response(request, invalid_field="query")
        try:
            await _empty_json(request)
        except Exception:
            return _response(request, invalid_field="body")

        def recover():
            factory = request.app.state.reminder_store_factory
            store = (default_reminder_store(request.app.state.settings)
                     if factory is None else factory())
            return ReminderRecoveryService(store).recover()

        try:
            # Cancelar a espera HTTP não libera o lock do worker ainda ativo.
            # O resultado local só é confirmado depois da execução real.
            result = await run_in_threadpool(recover)
            return _response(request, result=result)
        except Exception:
            # Um commit pode ter ocorrido antes de uma exceção inesperada.
            # Não declarar rollback nem fabricar contadores nesse caso.
            return _response(request, unavailable=True)
