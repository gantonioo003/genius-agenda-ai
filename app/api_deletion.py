"""Exclusão HTTP em duas etapas, vinculada à confirmação da versão consultada."""

from datetime import datetime
import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictStr, field_validator

from app.api_creation import _utf8_text
from app.api_queries import QueryInputError, _calendar_name, _public_event, _strict_query
from app.api_security import security_envelope
from app.google_calendar import CalendarSetupError
from app.services.event_deletion import DeletionResult, EventDeletionService, deletion_question
from app.services.event_selection import validate_event_id
from app.services.time_rules import load_zone, validate_event_interval


_TOKEN = re.compile(r"[0-9a-f]{64}", flags=re.ASCII)


class DeletionPreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time_zone: StrictStr

    @field_validator("time_zone")
    @classmethod
    def valid_text(cls, value: str) -> str:
        return _utf8_text(value)


class DeleteEventBody(DeletionPreviewBody):
    confirmation_token: StrictStr

    @field_validator("confirmation_token")
    @classmethod
    def valid_confirmation(cls, value: str) -> str:
        _utf8_text(value)
        if not _TOKEN.fullmatch(value):
            raise ValueError("Informe o código completo obtido na prévia de exclusão.")
        return value


def _response(request: Request, *, http_status: int, status: str,
              mutation_state: str, message: str, data=None, code=None, fields=()):
    return JSONResponse(status_code=http_status, content=security_envelope(
        request, status=status, action="delete", operation_id=None,
        mutation_state=mutation_state, message=message, data=data,
        error={"code": code, "retryable": False, "fields": list(fields)} if code else None,
    ))


def deletion_invalid_input(request: Request, fields):
    return _response(
        request, http_status=422, status="failed", mutation_state="not_applied",
        message="Informe os dados da exclusão no formato esperado.", code="invalid_request",
        fields=tuple(dict.fromkeys(fields)),
    )


def deletion_validation_error_response(request: Request, errors):
    """Projetar nomes permitidos; nunca expor valores, tokens ou erros brutos."""
    executing = request.scope["route"].name == "api_v1_delete_event"
    fields = []
    for entry in errors:
        location = entry.get("loc", ())
        if location == ("path", "event_id"):
            field = "event_id"
        elif location == ("body", "time_zone"):
            field = "time_zone"
        elif executing and location == ("body", "confirmation_token"):
            field = "confirmation_token"
        elif location and location[0] == "query":
            field = "query"
        else:
            field = "body"
        if field not in fields:
            fields.append(field)
    return deletion_invalid_input(request, fields or ["body"])


def _validate_input(event_id: str, time_zone: str) -> None:
    try:
        validate_event_id(event_id)
        _utf8_text(event_id)
    except ValueError:
        raise QueryInputError(("event_id",)) from None
    try:
        load_zone(time_zone)
    except ValueError:
        raise QueryInputError(("time_zone",)) from None


def _calendar(value) -> str:
    return _utf8_text(_calendar_name(value))


def _snapshot(event, event_id: str, time_zone: str) -> dict:
    data = _public_event(event, expected_id=event_id)
    for value in data.values():
        if isinstance(value, str):
            _utf8_text(value)
    if not event.all_day:
        if event.time_zone != time_zone:
            raise ValueError("Fuso de exibição divergente na resposta do provedor.")
        validate_event_interval(datetime.fromisoformat(event.start), datetime.fromisoformat(event.end), time_zone)
    return data


def _public_preview(result, event_id: str, time_zone: str) -> dict:
    if (not isinstance(result, DeletionResult)
            or result.status not in ("needs_confirmation", "already_absent")
            or type(result.recovered) is not bool or result.recovered):
        raise ValueError("Estado inválido na prévia de exclusão.")
    calendar_name = _calendar(result.calendar_name)
    if result.status == "already_absent":
        if any(value is not None for value in (result.event, result.confirmation_token, result.question)):
            raise ValueError("Ausência não confirmada na prévia de exclusão.")
        event = None
    else:
        event = _snapshot(result.event, event_id, time_zone)
        if (not isinstance(result.confirmation_token, str)
                or not _TOKEN.fullmatch(result.confirmation_token)
                or not isinstance(result.question, str)
                or not result.question.strip()
                or _utf8_text(result.question) != deletion_question(result.event)):
            raise ValueError("Confirmação inválida na prévia de exclusão.")
    return {
        "result": result.status, "calendar_name": calendar_name, "event": event,
        "confirmation_token": result.confirmation_token, "question": result.question,
    }


def _public_deleted(result, event_id: str, time_zone: str) -> dict:
    if (not isinstance(result, DeletionResult) or result.status not in ("deleted", "already_absent")
            or type(result.recovered) is not bool
            or result.confirmation_token is not None or result.question is not None):
        raise ValueError("Estado inválido na confirmação de exclusão.")
    calendar_name = _calendar(result.calendar_name)
    if result.status == "already_absent":
        if result.event is not None or result.recovered:
            raise ValueError("Ausência não confirmada na exclusão.")
    else:
        # Conferir a identidade sem incluir o snapshot ou o token na resposta.
        _snapshot(result.event, event_id, time_zone)
    return {
        "result": result.status, "calendar_name": calendar_name,
        "event_id": event_id, "recovered": result.recovered,
    }


def _provider_failure(request: Request, *, preview: bool, setup: bool = False):
    if setup:
        message, code = "O acesso à agenda está indisponível.", "calendar_setup_unavailable"
    elif preview:
        message, code = "Não foi possível concluir a prévia de exclusão.", "operation_failed"
    else:
        message, code = "Não foi possível confirmar a exclusão do evento.", "operation_not_confirmed"
    return _response(
        request, http_status=503 if setup else 502, status="failed",
        mutation_state="not_applicable" if preview else "not_applied" if setup else "unknown",
        message=message, code=code,
    )


def register_deletion_routes(router: APIRouter) -> None:
    @router.post(
        "/events/{event_id:path}/deletion-preview", name="api_v1_deletion_preview", tags=["agenda"],
        summary="Consultar a versão do evento e solicitar confirmação antes de excluir",
        responses={
            422: {"description": "Entrada inválida antes de consultar o provedor."},
            502: {"description": "A leitura da prévia não pôde ser concluída."},
            503: {"description": "Acesso à agenda indisponível."},
        },
    )
    def deletion_preview(request: Request, event_id: str, body: DeletionPreviewBody):
        try:
            _strict_query(request, set())
            _validate_input(event_id, body.time_zone)
        except QueryInputError as error:
            return deletion_invalid_input(request, error.fields)
        try:
            with request.app.state.calendar_gateway_factory() as gateway:
                result = EventDeletionService(gateway).delete(event_id, time_zone=body.time_zone, execute=False)
                data = _public_preview(result, event_id, body.time_zone)
        except CalendarSetupError:
            return _provider_failure(request, preview=True, setup=True)
        except Exception:
            return _provider_failure(request, preview=True)
        return _response(
            request, http_status=200,
            status="needs_confirmation" if data["result"] == "needs_confirmation" else "succeeded",
            mutation_state="not_applied",
            message="Confira o evento e confirme antes de excluir." if data["result"] == "needs_confirmation" else "O evento já está ausente da agenda.",
            data=data,
        )

    @router.post(
        "/events/{event_id:path}/delete", name="api_v1_delete_event", tags=["agenda"],
        summary="Excluir um evento após confirmação explícita da prévia e da versão",
        responses={
            422: {"description": "Entrada inválida antes da operação."},
            502: {"description": "O resultado da exclusão não pôde ser confirmado."},
            503: {"description": "Acesso à agenda indisponível antes da operação."},
        },
    )
    def delete_event(request: Request, event_id: str, body: DeleteEventBody):
        try:
            _strict_query(request, set())
            _validate_input(event_id, body.time_zone)
        except QueryInputError as error:
            return deletion_invalid_input(request, error.fields)
        operation_started = False
        try:
            with request.app.state.calendar_gateway_factory() as gateway:
                operation_started = True
                # O token recebido é encaminhado intacto; esta rota nunca cria uma prévia.
                result = EventDeletionService(gateway).delete(
                    event_id, time_zone=body.time_zone, execute=True,
                    confirmation_token=body.confirmation_token,
                )
                data = _public_deleted(result, event_id, body.time_zone)
        except CalendarSetupError:
            return _provider_failure(request, preview=False, setup=not operation_started)
        except Exception:
            # Código antigo/incorreto, escrita incerta e falha de cleanup são
            # conservadores. Não classificar pelo texto da exceção ou repetir DELETE.
            return _provider_failure(request, preview=False)
        return _response(
            request, http_status=200, status="succeeded", mutation_state="confirmed",
            message="Ausência do evento confirmada na agenda.", data=data,
        )
