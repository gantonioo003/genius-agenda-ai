"""Disparo autenticado de avisos vencidos; transporte decidido só pelo domínio."""

from hashlib import sha256
import hmac

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api_reminders import _empty_json, default_reminder_gateway, default_reminder_store
from app.api_security import security_envelope
from app.services.reminder_delivery import (
    ReminderDeliveryService, ReminderDeliverySummary, ReminderRecipient,
)
from app.settings import PROJECT_ROOT


_COUNTERS = ("reminders_considered", "expired_count", "cancelled_count", "blocked_count",
             "accepted_count", "uncertain_count", "failed_count", "transport_attempts")
_SKIP_REASONS = {"not_configured", "disabled", "permission_required", "busy"}


def default_reminder_recipient():
    """Derivar telefone e HMAC juntos, de uma única leitura de cada arquivo.

    Não ler o telefone novamente depois de calcular um vínculo separado. O
    serviço repete esta função antes do envio e recusa mudança do par privado.
    """
    from app.sandbox_webhook import load_allowed_sender, _config_values, _decode_json
    try:
        policy_raw = (PROJECT_ROOT / "secrets/whatsapp-sandbox-security.json").read_bytes()
        config_raw = (PROJECT_ROOT / "secrets/whatsapp-sandbox-local.json").read_bytes()
        recipient = load_allowed_sender(policy_raw)
        token, _ = _config_values(_decode_json(config_raw))
        binding = hmac.new(token.encode("ascii"),
            b"genius-whatsapp-agent-sender:v1\0" + recipient.encode("ascii"), sha256).hexdigest()
        return ReminderRecipient(recipient, binding)
    except Exception:
        raise ValueError("O vínculo privado dos lembretes está indisponível.") from None


def default_reminder_sender(config):
    from app.whatsapp_sandbox_sender import SandboxSender, _public_image_link
    base = SandboxSender(config.d360_api_key)
    if getattr(config, 'reminder_cards_enabled', False) is not True:
        return base
    try:
        import json
        from urllib.parse import urlsplit
        from app.card_media_store import CardMediaPublisher
        from app.interview_card import render_interview_card
        from app.reminder_card_sender import ReminderCardSender
        settings = json.loads((PROJECT_ROOT / 'secrets/card-media-runtime.json').read_text(encoding='utf-8'))
        origin = settings.get('origin') if type(settings) is dict else None
        if not isinstance(origin, str):
            return base
        parsed = urlsplit(origin)
        if (parsed.path not in {'', '/'} or parsed.query or parsed.fragment
                or not _public_image_link(origin.rstrip('/') + '/card-media/check.png')):
            return base
        origin = origin.rstrip('/')
        publisher = CardMediaPublisher(cache_root=PROJECT_ROOT / 'data/cards/published',
                                       origin=origin, ttl_seconds=600, verify_public=True)
        return ReminderCardSender(base, publisher=publisher,
            renderer=render_interview_card, output_dir=PROJECT_ROOT / 'data/cards/rendered',
            template_path=PROJECT_ROOT / 'assets/cards/interview-template-v1.json', ttl_seconds=600)
    except Exception:
        # Preparação/configuração indisponível não impede o lembrete textual.
        return base


def _public_data(result):
    if type(result) is not ReminderDeliverySummary:
        raise ValueError()
    data = result.public_data()
    if (type(data) is not dict or set(data) != {"dispatch_status", "skip_reason", *_COUNTERS}
            or data["dispatch_status"] not in {"completed", "skipped", "failed", "partial"}
            or any(type(data[key]) is not int or data[key] < 0 for key in _COUNTERS)
            or data["transport_attempts"] != data["accepted_count"] + data["uncertain_count"]):
        raise ValueError()
    if data["dispatch_status"] == "skipped":
        if data["skip_reason"] not in _SKIP_REASONS or any(data[key] != 0 for key in _COUNTERS):
            raise ValueError()
    elif data["skip_reason"] is not None:
        raise ValueError()
    if data["dispatch_status"] == "completed" and (data["failed_count"] or data["uncertain_count"]):
        raise ValueError()
    if data["dispatch_status"] in {"failed", "partial"} and not (data["failed_count"] or data["uncertain_count"]):
        raise ValueError()
    return data


def _mutation_state(data):
    if data["uncertain_count"] > 0 or data["transport_attempts"] > data["accepted_count"]:
        return "unknown"
    if data["transport_attempts"] == data["accepted_count"] and data["accepted_count"] > 0:
        return "confirmed"
    if data["transport_attempts"] == 0 and data["accepted_count"] == 0:
        return "not_applied"
    raise ValueError()


def _response(request, *, result=None, invalid_field=None, unavailable=False, uncertain=False):
    data, error = None, None
    mutation_state = "unknown" if uncertain else "not_applied"
    if invalid_field is not None:
        http_status, status = 422, "failed"
        message = "Informe somente um objeto JSON vazio, sem parâmetros de consulta."
        error = {"code": "invalid_request", "retryable": False, "fields": [invalid_field]}
    elif unavailable:
        http_status, status = 503, "failed"
        message = "Não foi possível confirmar o processamento dos lembretes."
        error = {"code": "reminder_dispatch_failed", "retryable": False, "fields": []}
    else:
        data = _public_data(result)
        mutation_state = _mutation_state(data)
        failed = data["dispatch_status"] in {"failed", "partial"}
        http_status = 503 if failed else 200
        status = "failed" if failed else "skipped" if data["dispatch_status"] == "skipped" else "succeeded"
        message = ("O processamento dos lembretes terminou parcialmente; consulte os contadores."
                   if data["dispatch_status"] == "partial" else
                   "Não foi possível concluir o processamento dos lembretes." if failed else
                   "Processamento dos lembretes dispensado." if data["dispatch_status"] == "skipped" else
                   "Processamento concluído; aceites do provedor constam nos contadores.")
        if failed:
            error = {"code": "reminder_dispatch_partial" if data["dispatch_status"] == "partial"
                     else "reminder_dispatch_failed", "retryable": False, "fields": []}
    return JSONResponse(status_code=http_status, content=security_envelope(request,
        status=status, action="reminder_dispatch", mutation_state=mutation_state,
        operation_id=None, message=message, data=data, error=error))


def register_reminder_delivery_routes(router):
    @router.post(
        "/reminders/dispatch", name="api_v1_reminder_dispatch", tags=["reminders"],
        summary="Processar o envio autorizado dos lembretes vencidos",
        description=("Recebe somente {}. Revalida evento, configuração, destinatário, canal e relógio "
                     "antes do transporte e preserva reservas/estados incertos, sem retry automático. "
                     "accepted_count confirma somente aceite pelo provedor; não confirma entrega ou leitura. "
                     "A resposta contém exclusivamente estados fixos e contadores agregados."),
        responses={422: {"description": "Corpo ou consulta inválidos, sem reprodução da entrada."},
                   503: {"description": "Falha total, parcial ou resultado de transporte incerto; sem retry automático."}},
        openapi_extra={"requestBody": {"required": True, "content": {"application/json": {
            "schema": {"type": "object", "properties": {}, "additionalProperties": False,
                       "maxProperties": 0}, "example": {},
        }}}},
    )
    async def dispatch_reminders(request: Request):
        # A dependência herdada autentica antes de ler o corpo ou abrir fábricas.
        if request.scope.get("query_string"):
            return _response(request, invalid_field="query")
        try:
            await _empty_json(request)
        except Exception:
            return _response(request, invalid_field="body")

        def dispatch():
            state = request.app.state
            try:
                store = (default_reminder_store(state.settings) if state.reminder_store_factory is None
                         else state.reminder_store_factory())
                gateway_factory = state.reminder_gateway_factory or default_reminder_gateway
                recipient_loader = state.reminder_delivery_recipient_loader or default_reminder_recipient
                sender_factory = state.reminder_delivery_sender_factory or (lambda: default_reminder_sender(state.settings))
                service = ReminderDeliveryService(store, gateway_factory, recipient_loader, sender_factory)
            except Exception:
                return None, False
            try:
                return service.dispatch(), True
            except Exception:
                # Uma falha inesperada depois de entrar no domínio pode ocorrer
                # após transporte: não fabricar zero aceites ou ausência de envio.
                return None, True

        try:
            # Cancelar a espera HTTP não cancela o worker. O domínio conserva o
            # lock do tick até terminar de processar a execução real.
            result, started = await run_in_threadpool(dispatch)
            if result is None:
                return _response(request, unavailable=True, uncertain=started)
            try:
                return _response(request, result=result)
            except Exception:
                return _response(request, unavailable=True, uncertain=True)
        except Exception:
            return _response(request, unavailable=True, uncertain=True)
