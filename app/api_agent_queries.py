"""Consulta autenticada por linguagem natural, sem autorizar mutações."""

import asyncio
from datetime import datetime
import json

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api_security import security_envelope
from app.api_queries import _public_event
from app.api_conversations import (
    conversation_error_response, conversation_owner_key, conversation_store,
    validate_conversation_id,
)
from app.conversation_store import ConversationError, validate_memory
from app.llm import ModelError
from app.services.agenda import AgendaEvent
from app.services.agent_preview import validate_preview_message
from app.services.agent_queries import default_agent_query_service
from app.services.agenda_tools import QueryResult
from app.services.time_rules import load_zone, validate_interval


_MAX_BODY_BYTES = 24 * 1024
_MODEL_ERRORS = {
    "llm_not_configured": (503, "O modelo de IA ainda não está configurado."),
    "llm_credentials_rejected": (503, "Não foi possível autenticar o modelo de IA."),
    "llm_rate_limited": (503, "O modelo de IA está temporariamente indisponível."),
    "llm_timeout": (504, "O modelo de IA não respondeu no prazo."),
    "llm_invalid_response": (502, "O modelo de IA retornou uma resposta inválida."),
    "llm_unavailable": (502, "Não foi possível interpretar a consulta de IA."),
}
_RESULT_ERRORS = {
    "invalid_request": 422,
    "event_not_found": 404,
    "operation_failed": 502,
    "calendar_setup_unavailable": 503,
}
_DATA_FIELDS = {
    "list": frozenset({"calendar_name", "period", "events", "execution_allowed"}),
    "get": frozenset({"calendar_name", "event", "execution_allowed"}),
    None: frozenset({"reason", "question", "execution_allowed"}),
}
_EVENT_FIELDS = frozenset({
    "event_id", "title", "start", "end", "all_day", "time_zone", "status", "html_link",
})


def _response(request: Request, *, status: str, action: str | None,
              message: str, data=None, error=None, http_status: int = 200) -> JSONResponse:
    safe_data = {**data, "execution_allowed": False} if data is not None else None
    return JSONResponse(status_code=http_status, content=security_envelope(
        request, status=status, action=action, mutation_state="not_applicable",
        operation_id=None, message=message, data=safe_data, error=error,
    ))


def _error(request: Request, *, code: str, http_status: int, message: str,
           fields=()) -> JSONResponse:
    return _response(
        request, status="failed", action=None, http_status=http_status, message=message,
        error={"code": code, "retryable": False, "fields": list(fields)},
    )


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError()


def _validate_event(event: object) -> None:
    if type(event) is not dict or set(event) != _EVENT_FIELDS:
        raise ValueError("Evento inválido para a consulta de IA.")
    # A mesma projeção das consultas HTTP verifica IDs, datas, intervalos,
    # estado e fuso sem permitir descrições, participantes ou objetos do SDK.
    _public_event(AgendaEvent(**event))


def _validate_data(result: QueryResult) -> None:
    data = result.data
    if result.status == "failed":
        if data is not None:
            raise ValueError("Dados inesperados em falha da consulta de IA.")
        return
    if type(data) is not dict or set(data) != _DATA_FIELDS[result.action]:
        raise ValueError("Dados inválidos para a consulta de IA.")
    if result.action is None:
        if (data["reason"] not in {"missing_information", "unsupported_request"}
                or not isinstance(data["question"], str) or not data["question"].strip()
                or len(data["question"]) > 8000):
            raise ValueError("Pergunta inválida para a consulta de IA.")
    else:
        if not isinstance(data["calendar_name"], str) or not data["calendar_name"].strip():
            raise ValueError("Agenda inválida para a consulta de IA.")
        if result.action == "list":
            period = data["period"]
            if (type(period) is not dict or set(period) != {"start", "end", "time_zone"}
                    or not isinstance(period["start"], str) or not isinstance(period["end"], str)
                    or type(data["events"]) is not list):
                raise ValueError("Período inválido para a consulta de IA.")
            load_zone(period["time_zone"])
            validate_interval(datetime.fromisoformat(period["start"]), datetime.fromisoformat(period["end"]))
            for event in data["events"]:
                _validate_event(event)
            event_ids = [event["event_id"] for event in data["events"]]
            if len(set(event_ids)) != len(event_ids):
                raise ValueError("Identidades repetidas na consulta de IA.")
        elif result.status == "not_found":
            if data["event"] is not None:
                raise ValueError("Evento inesperado em resultado ausente.")
        else:
            _validate_event(data["event"])
    # Valores JSON e UTF-8 somente: não serializar objetos/erros de SDKs.
    json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8", errors="strict")


def _validate_result(result: object) -> QueryResult:
    """Fechar o envelope mesmo quando o serviço for substituído por injeção."""
    if (not isinstance(result, QueryResult)
            or not isinstance(result.status, str)
            or result.status not in {"succeeded", "needs_clarification", "not_found", "failed"}
            or result.action is not None and not isinstance(result.action, str)
            or result.action not in {None, "list", "get"}
            or not isinstance(result.message, str) or not result.message.strip()
            or len(result.message) > 8000
            or type(result.http_status) is not int
            or (result.data is not None and type(result.data) is not dict)):
        raise ValueError("Resultado inválido para a consulta de IA.")
    result.message.encode("utf-8", errors="strict")
    if result.error_code is not None:
        if (not isinstance(result.error_code, str)
                or _RESULT_ERRORS.get(result.error_code) != result.http_status):
            raise ValueError("Erro inválido para a consulta de IA.")
        if result.error_code == "event_not_found":
            if result.status != "not_found" or result.action != "get":
                raise ValueError("Estado inválido para a consulta de IA.")
        elif result.status != "failed":
            raise ValueError("Estado inválido para a consulta de IA.")
    elif (result.http_status != 200
          or result.status not in {"succeeded", "needs_clarification"}):
        raise ValueError("Estado inválido para a consulta de IA.")
    if result.status == "succeeded" and result.action not in {"list", "get"}:
        raise ValueError("Ação inválida para a consulta de IA.")
    if result.status == "needs_clarification" and result.action is not None:
        raise ValueError("Ação inválida para a consulta de IA.")
    _validate_data(result)
    return result


def register_agent_query_routes(router):
    @router.post("/agent/query", name="api_v1_agent_query", tags=["agent"])
    async def query_agent(request: Request):
        # A autenticação herdada do router precede corpo, modelo e Google.
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
            body = json.loads(
                raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_invalid_constant,
            )
            if (type(body) is not dict or "message" not in body
                    or set(body) - {"message", "conversation_id"}):
                raise ValueError()
            message = validate_preview_message(
                body["message"], max_input_chars=request.app.state.settings.llm_max_input_chars,
            )
            conversation_id = (
                validate_conversation_id(body["conversation_id"])
                if "conversation_id" in body else None
            )
        except Exception:
            return _error(
                request, code="invalid_request", http_status=422,
                message="Informe somente uma mensagem válida para consultar a agenda.", fields=("message",),
            )

        def execute():
            # A existência, o dono e a validade temporal precedem inclusive a
            # construção do serviço, para não gastar uma chamada de modelo.
            store = None
            if conversation_id is not None:
                try:
                    store = conversation_store(request)
                    owner = conversation_owner_key(request.app.state.settings)
                    snapshot = store.load(owner, conversation_id)
                except ConversationError:
                    raise
                except Exception:
                    raise ConversationError("conversation_unavailable") from None
            factory = request.app.state.agent_query_service_factory
            service = (
                default_agent_query_service(
                    request.app.state.settings, request.app.state.calendar_gateway_factory,
                ) if factory is None else factory()
            )
            if conversation_id is None:
                return _validate_result(service.query(message))
            result, memory = service.query_with_context(message, snapshot.memory)
            result = _validate_result(result)
            try:
                memory = validate_memory(memory)
            except ConversationError:
                raise ModelError("llm_invalid_response") from None
            if result.status != "failed":
                # load/save são operações curtas independentes. Nenhum lock ou
                # transação permanece aberto durante modelo ou Google. O CAS
                # rejeita a perda de atualização, sem repetição automática.
                try:
                    store.save(owner, conversation_id, snapshot.revision, memory)
                except ConversationError:
                    raise
                except Exception:
                    raise ConversationError("conversation_unavailable") from None
            return result

        try:
            result = await run_in_threadpool(execute)
        except ConversationError as error:
            return conversation_error_response(request, error)
        except ModelError as error:
            code = error.code if isinstance(error.code, str) and error.code in _MODEL_ERRORS else "llm_unavailable"
            http_status, safe_message = _MODEL_ERRORS[code]
            return _error(request, code=code, http_status=http_status, message=safe_message)
        except Exception:
            http_status, safe_message = _MODEL_ERRORS["llm_unavailable"]
            return _error(request, code="llm_unavailable", http_status=http_status, message=safe_message)
        return _response(
            request, status=result.status, action=result.action, message=result.message,
            data=result.data, http_status=result.http_status,
            error=({"code": result.error_code, "retryable": False, "fields": []}
                   if result.error_code is not None else None),
        )
