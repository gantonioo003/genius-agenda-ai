"""Criação HTTP com validação local e estados de mutação conservadores."""

from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from app.api_queries import QueryInputError, _calendar_name, _public_event, _strict_query
from app.api_security import security_envelope
from app.google_calendar import CalendarSetupError
from app.services.agenda import CalendarPeriod
from app.services.conflicts import CalendarConflictError, ConflictReport, build_conflict_report
from app.services.event_selection import validate_event_id
from app.services.interviews import CreatedInterview, InterviewDraft, InterviewService, validate_interview
from app.services.time_rules import load_zone, validate_event_interval, validate_interval


_EVENT_FIELDS = frozenset({"title", "start", "end", "time_zone", "description"})


def _utf8_text(value: str) -> str:
    # JSON permite escapes de surrogates isolados, que não são texto UTF-8 válido.
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError("Informe um texto Unicode válido.") from None
    return value


class EventBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: StrictStr
    start: StrictStr
    end: StrictStr
    time_zone: StrictStr
    description: StrictStr = ""

    @field_validator("title", "start", "end", "time_zone", "description")
    @classmethod
    def valid_text(cls, value: str) -> str:
        return _utf8_text(value)

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Informe um título para a entrevista.")
        return value


class CreateEventBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: StrictStr = Field(min_length=1, max_length=128)
    event: EventBody

    @field_validator("operation_id")
    @classmethod
    def valid_operation(cls, value: str) -> str:
        _utf8_text(value)
        if not value.strip():
            raise ValueError("Informe um identificador estável para esta operação.")
        return value


def _known_operation_id(body) -> str | None:
    value = body.get("operation_id") if isinstance(body, dict) else None
    if not isinstance(value, str) or not value.strip() or not 1 <= len(value) <= 128:
        return None
    try:
        return _utf8_text(value)
    except ValueError:
        return None


def _response(request: Request, *, operation_id: str | None, http_status: int,
              status: str, mutation_state: str, message: str, data=None,
              code: str | None = None, fields=()) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content=security_envelope(
            request, status=status, action="create", operation_id=operation_id,
            mutation_state=mutation_state, message=message, data=data,
            error={"code": code, "retryable": False, "fields": list(fields)} if code else None,
        ),
    )


def _invalid_input(request: Request, fields, *, operation_id: str | None = None):
    return _response(
        request, operation_id=operation_id, http_status=422, status="failed",
        mutation_state="not_applied", message="Informe os dados da entrevista no formato esperado.",
        code="invalid_request", fields=tuple(dict.fromkeys(fields)),
    )


def creation_validation_error_response(request: Request, errors, *, body=None):
    """Projetar somente nomes conhecidos, sem reproduzir erros ou entradas."""
    fields = []
    for entry in errors:
        location = entry.get("loc", ())
        if location[:2] == ("body", "event"):
            field = location[2] if len(location) == 3 else None
            safe_field = field if field in _EVENT_FIELDS else "event"
        elif location == ("body", "operation_id"):
            safe_field = "operation_id"
        elif location and location[0] == "query":
            safe_field = "query"
        else:
            safe_field = "body"
        if safe_field not in fields:
            fields.append(safe_field)
    return _invalid_input(request, fields or ["body"], operation_id=_known_operation_id(body))


async def creation_http_error_handler(request: Request, error):
    """Normalizar falhas de parsing JSON sem alterar erros de outras rotas."""
    route = request.scope.get("route")
    if error.status_code == 400 and getattr(route, "name", None) in ("api_v1_deletion_preview", "api_v1_delete_event"):
        from app.api_deletion import deletion_invalid_input
        return deletion_invalid_input(request, ["body"])
    if error.status_code == 400 and getattr(route, "name", None) in ("api_v1_reschedule_event", "api_v1_edit_event"):
        from app.api_updates import UPDATE_ACTIONS, update_invalid_input
        return update_invalid_input(request, UPDATE_ACTIONS[route.name], ["body"])
    if error.status_code == 400 and getattr(route, "name", None) == "api_v1_create_event":
        return _invalid_input(request, ["body"])
    return await http_exception_handler(request, error)


def _draft(body: CreateEventBody) -> InterviewDraft:
    values = {}
    fields = []
    for field in ("start", "end"):
        try:
            values[field] = datetime.fromisoformat(getattr(body.event, field))
        except ValueError:
            fields.append(field)
    try:
        load_zone(body.event.time_zone)
    except ValueError:
        fields.append("time_zone")
    if fields:
        raise QueryInputError(fields)
    try:
        validate_interval(values["start"], values["end"])
    except ValueError:
        raise QueryInputError(("start", "end")) from None
    draft = InterviewDraft(
        title=body.event.title, start=values["start"], end=values["end"],
        time_zone=body.event.time_zone, description=body.event.description,
    )
    try:
        validate_event_interval(draft.start, draft.end, draft.time_zone)
        validate_interview(draft, body.operation_id)
    except ValueError:
        raise QueryInputError(("start", "end", "time_zone")) from None
    return draft


def _public_created(created: CreatedInterview) -> dict:
    if not isinstance(created, CreatedInterview):
        raise ValueError("Confirmação inválida na resposta do provedor.")
    validate_event_id(created.event_id)
    _utf8_text(created.event_id)
    _calendar_name(created.calendar_name)
    _utf8_text(created.calendar_name)
    if not isinstance(created.html_link, str) or not created.html_link.strip():
        raise ValueError("Link inválido na resposta do provedor.")
    _utf8_text(created.html_link)
    if type(created.reused) is not bool or type(created.recovered) is not bool:
        raise ValueError("Estado inválido na resposta do provedor.")
    # Projeção explícita: subclasses não acrescentam dados privados ao HTTP.
    return {
        "event_id": created.event_id, "html_link": created.html_link,
        "calendar_name": created.calendar_name, "reused": created.reused,
        "recovered": created.recovered,
    }


def _public_conflict(report: ConflictReport, draft: InterviewDraft) -> dict:
    if (not isinstance(report, ConflictReport) or report.status != "conflict"
            or not isinstance(report.conflicts, (tuple, list)) or not report.conflicts):
        raise ValueError("Relatório de conflito inválido na resposta do provedor.")
    _calendar_name(report.calendar_name)
    _utf8_text(report.calendar_name)
    if report.time_zone != draft.time_zone:
        raise ValueError("Fuso divergente no relatório de conflito.")
    start, end = datetime.fromisoformat(report.start), datetime.fromisoformat(report.end)
    validate_event_interval(start, end, report.time_zone)
    if start != draft.start or end != draft.end:
        raise ValueError("Intervalo divergente no relatório de conflito.")
    events = [_public_event(event) for event in report.conflicts]
    for event in events:
        for value in event.values():
            if isinstance(value, str):
                _utf8_text(value)
    ids = [event["event_id"] for event in events]
    checked = build_conflict_report(
        CalendarPeriod(start, end, report.time_zone), report.calendar_name, report.conflicts,
    )
    if len(ids) != len(set(ids)) or len(checked.conflicts) != len(events):
        raise ValueError("Eventos inconsistentes no relatório de conflito.")
    return {
        "status": "conflict", "calendar_name": report.calendar_name,
        "start": report.start, "end": report.end, "time_zone": report.time_zone,
        "conflicts": events,
    }


def _provider_failure(request: Request, operation_id: str, *, setup: bool = False):
    return _response(
        request, operation_id=operation_id, http_status=503 if setup else 502,
        status="failed", mutation_state="not_applied" if setup else "unknown",
        message="O acesso à agenda está indisponível." if setup else "Não foi possível confirmar a criação da entrevista.",
        code="calendar_setup_unavailable" if setup else "operation_not_confirmed",
        data=None if setup else {"event_id": None},
    )


def register_creation_route(router: APIRouter) -> None:
    @router.post(
        "/events", name="api_v1_create_event", tags=["agenda"], status_code=201,
        summary="Criar uma entrevista com identificação estável da operação",
        responses={
            200: {"description": "Criação correspondente reutilizada e confirmada."},
            409: {"description": "Conflito de horário confirmado antes da escrita."},
            422: {"description": "Entrada inválida, sem reprodução dos valores recebidos."},
            502: {"description": "O resultado da criação não pôde ser confirmado."},
            503: {"description": "Configuração de acesso à agenda indisponível antes da operação."},
        },
    )
    def create_event(request: Request, body: CreateEventBody):
        try:
            _strict_query(request, set())
            draft = _draft(body)
        except QueryInputError as error:
            return _invalid_input(request, error.fields, operation_id=body.operation_id)
        operation_started = False
        conflict_data = None
        try:
            with request.app.state.calendar_gateway_factory() as gateway:
                operation_started = True
                try:
                    created = InterviewService(gateway).create(draft, operation_id=body.operation_id)
                except CalendarConflictError as error:
                    conflict_data = _public_conflict(error.report, draft)
                else:
                    data = _public_created(created)
        except CalendarSetupError:
            return _provider_failure(request, body.operation_id, setup=not operation_started)
        except Exception:
            # A operação pode ter escrito antes de falhar: não inventar ausência,
            # repetir POST, analisar mensagens ou expor exceções do provedor.
            return _provider_failure(request, body.operation_id)
        if conflict_data is not None:
            return _response(
                request, operation_id=body.operation_id, http_status=409,
                status="conflict", mutation_state="not_applied",
                message="Já existe um compromisso nesse intervalo.",
                data=conflict_data, code="schedule_conflict",
            )
        return _response(
            request, operation_id=body.operation_id, http_status=200 if data["reused"] else 201,
            status="succeeded", mutation_state="confirmed",
            message="Entrevista confirmada na agenda.", data=data,
        )
