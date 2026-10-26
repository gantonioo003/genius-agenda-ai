"""Conversas privadas e temporárias; memória nunca faz parte da resposta HTTP."""

import asyncio
import hashlib
import json
from uuid import UUID

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api_security import security_envelope
from app.conversation_store import ConversationError, ConversationStore
from app.settings import PROJECT_ROOT


_ERRORS = {
    "conversation_not_found": (404, "A conversa não está disponível. Inicie uma nova conversa."),
    "conversation_conflict": (409, "A conversa recebeu outra atualização. Envie seu pedido novamente."),
    "conversation_unavailable": (503, "A memória da conversa está temporariamente indisponível."),
    "conversation_capacity": (503, "Não foi possível iniciar outra conversa neste momento."),
    "invalid_conversation": (422, "Informe um identificador de conversa válido."),
}


def validate_conversation_id(value: object) -> str:
    """Aceitar apenas UUID4 canônico; o banco comprova que foi emitido pelo servidor."""
    if not isinstance(value, str):
        raise ValueError("Identificador de conversa inválido.")
    parsed = UUID(value)
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("Identificador de conversa inválido.")
    return value


def conversation_owner_key(config) -> str:
    # A API atual possui uma única identidade, autenticada por chave. Rotacioná-la
    # cria outro escopo e não permite ler conversas da identidade anterior.
    return hashlib.sha256(
        b"genius-agenda-ai:agent-conversation-owner:v1\x00"
        + config.api_auth_token.get_secret_value().encode("utf-8")
    ).hexdigest()


def default_conversation_store(config) -> ConversationStore:
    path = config.conversation_database_path
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return ConversationStore(path, ttl_seconds=config.conversation_ttl_seconds)


def conversation_store(request: Request):
    factory = request.app.state.conversation_store_factory
    return default_conversation_store(request.app.state.settings) if factory is None else factory()


def conversation_error_response(request: Request, error: Exception) -> JSONResponse:
    code = getattr(error, "code", None)
    if not isinstance(code, str) or code not in _ERRORS:
        code = "conversation_unavailable"
    http_status, message = _ERRORS[code]
    return JSONResponse(status_code=http_status, content=security_envelope(
        request, status="failed", action=None, mutation_state="not_applicable",
        operation_id=None, message=message,
        error={"code": code, "retryable": False, "fields": []},
    ))


def _invalid_request(request: Request) -> JSONResponse:
    return JSONResponse(status_code=422, content=security_envelope(
        request, status="failed", action=None, mutation_state="not_applicable",
        operation_id=None, message="Informe uma solicitação válida para a conversa.",
        error={"code": "invalid_request", "retryable": False, "fields": []},
    ))


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError()


async def _body(request: Request, maximum: int) -> bytes:
    raw = bytearray()
    async with asyncio.timeout(2):
        async for chunk in request.stream():
            if len(raw) + len(chunk) > maximum:
                raise ValueError()
            raw.extend(chunk)
    return bytes(raw)


def register_conversation_routes(router):
    @router.post("/agent/conversations", name="api_v1_agent_conversation_create", tags=["agent"])
    async def create_conversation(request: Request):
        try:
            content_types = request.headers.getlist("content-type")
            if (request.scope.get("query_string") or len(content_types) != 1
                    or content_types[0].split(";", 1)[0].strip().lower() != "application/json"):
                raise ValueError()
            body = json.loads(
                (await _body(request, 1024)).decode("utf-8"),
                object_pairs_hook=_unique, parse_constant=_invalid_constant,
            )
            if type(body) is not dict or body:
                raise ValueError()
        except Exception:
            return _invalid_request(request)

        def execute():
            config = request.app.state.settings
            issued = conversation_store(request).create(conversation_owner_key(config))
            return validate_conversation_id(issued)

        try:
            conversation_id = await run_in_threadpool(execute)
        except Exception as error:
            return conversation_error_response(request, error)
        return JSONResponse(status_code=201, content=security_envelope(
            request, status="succeeded", action="conversation_create", mutation_state="not_applicable",
            operation_id=None, message="Conversa iniciada.",
            data={"conversation_id": conversation_id,
                  "expires_in_seconds": request.app.state.settings.conversation_ttl_seconds,
                  "execution_allowed": False},
        ))

    @router.delete("/agent/conversations/{conversation_id}",
                   name="api_v1_agent_conversation_delete", tags=["agent"])
    async def delete_conversation(conversation_id: str, request: Request):
        try:
            validate_conversation_id(conversation_id)
            if request.scope.get("query_string") or await _body(request, 0):
                raise ValueError()
        except Exception:
            return _invalid_request(request)

        def execute():
            conversation_store(request).delete(
                conversation_owner_key(request.app.state.settings), conversation_id,
            )

        try:
            await run_in_threadpool(execute)
        except Exception as error:
            return conversation_error_response(request, error)
        return Response(status_code=204)
