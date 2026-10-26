"""Fronteira autenticada para uma prévia isolada, sem executar ferramentas."""

import asyncio
import json

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api_security import security_envelope
from app.llm import ModelError
from app.services.agent_preview import (
    default_agent_preview_service, validate_preview_message, validate_preview_reply,
)


_MAX_BODY_BYTES = 24 * 1024
_MODEL_ERRORS = {
    "llm_not_configured": (503, "O modelo de IA ainda não está configurado."),
    "llm_credentials_rejected": (503, "Não foi possível autenticar o modelo de IA."),
    "llm_rate_limited": (503, "O modelo de IA está temporariamente indisponível."),
    "llm_timeout": (504, "O modelo de IA não respondeu no prazo."),
    "llm_invalid_response": (502, "O modelo de IA retornou uma resposta inválida."),
    "llm_unavailable": (502, "Não foi possível gerar a prévia de IA."),
}


def _error(request: Request, *, code: str, http_status: int, message: str,
           fields=()) -> JSONResponse:
    return JSONResponse(status_code=http_status, content=security_envelope(
        request, status="failed", action="agent_preview", mutation_state="not_applied",
        operation_id=None, message=message,
        error={"code": code, "retryable": False, "fields": list(fields)},
    ))


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def register_agent_routes(router):
    @router.post("/agent/preview", name="api_v1_agent_preview", tags=["agent"])
    async def preview_agent(request: Request):
        # A proteção Bearer herdada do router roda antes da leitura do corpo.
        try:
            content_types = request.headers.getlist("content-type")
            if (request.scope.get("query_string") or len(content_types) != 1
                    or content_types[0].split(";", 1)[0].strip().lower() != "application/json"):
                raise ValueError()
            raw = bytearray()
            async with asyncio.timeout(2):
                async for chunk in request.stream():
                    if len(raw) + len(chunk) > _MAX_BODY_BYTES:
                        raise ValueError()
                    raw.extend(chunk)
            body = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
            if not isinstance(body, dict) or set(body) != {"message"}:
                raise ValueError()
            message = validate_preview_message(
                body["message"], max_input_chars=request.app.state.settings.llm_max_input_chars,
            )
        except Exception:
            return _error(
                request, code="invalid_request", http_status=422,
                message="Informe somente uma mensagem válida para a prévia.", fields=("message",),
            )

        def execute():
            factory = request.app.state.agent_preview_service_factory
            service = (default_agent_preview_service(request.app.state.settings)
                       if factory is None else factory())
            return validate_preview_reply(service.preview(message))

        try:
            result = await run_in_threadpool(execute)
        except ModelError as error:
            # Código e status são escolhidos aqui; detalhes do provedor não saem.
            code = error.code if isinstance(error.code, str) and error.code in _MODEL_ERRORS else "llm_unavailable"
            http_status, safe_message = _MODEL_ERRORS[code]
            return _error(request, code=code, http_status=http_status, message=safe_message)
        except Exception:
            http_status, safe_message = _MODEL_ERRORS["llm_unavailable"]
            return _error(request, code="llm_unavailable", http_status=http_status, message=safe_message)
        return JSONResponse(status_code=200, content=security_envelope(
            request, status="succeeded", action="agent_preview", mutation_state="not_applied",
            operation_id=None, message="Prévia gerada; nenhuma operação de agenda foi executada.",
            data={
                "reply": result.text, "model": result.model,
                "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                "execution_allowed": False,
            },
        ))
