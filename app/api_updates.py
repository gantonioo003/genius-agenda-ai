"""Edição e reagendamento HTTP com validação prévia e confirmação do resultado."""

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictStr, field_validator

from app.api_creation import _public_conflict, _utf8_text
from app.api_queries import QueryInputError, _calendar_name, _public_event, _strict_query
from app.api_security import security_envelope
from app.google_calendar import CalendarSetupError
from app.services.agenda import AgendaEvent
from app.services.conflicts import CalendarConflictError
from app.services.event_details import (
    DetailsChange, DetailsResult, EventDetails, EventDetailsService, validate_details_change,
)
from app.services.event_selection import validate_event_id
from app.services.rescheduling import (
    RescheduleResult, RescheduleService, ScheduleChange, validate_schedule_change,
)
from app.services.time_rules import load_zone, validate_event_interval, validate_interval


UPDATE_ACTIONS = {"api_v1_reschedule_event": "reschedule", "api_v1_edit_event": "edit"}
_DETAIL_FIELDS = ("title", "description", "location")


class ScheduleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: StrictStr
    end: StrictStr
    time_zone: StrictStr

    @field_validator("start", "end", "time_zone")
    @classmethod
    def valid_text(cls, value: str) -> str:
        return _utf8_text(value)


class DetailsChangesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: StrictStr | None = None
    description: StrictStr | None = None
    location: StrictStr | None = None

    @field_validator("title", "description", "location")
    @classmethod
    def valid_text(cls, value: str | None) -> str | None:
        return None if value is None else _utf8_text(value)


class UpdateDetailsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: DetailsChangesBody
    time_zone: StrictStr

    @field_validator("time_zone")
    @classmethod
    def valid_text(cls, value: str) -> str:
        return _utf8_text(value)


def _response(request: Request, *, action: str, http_status: int, status: str,
              mutation_state: str, message: str, data=None, code=None, fields=()):
    return JSONResponse(status_code=http_status, content=security_envelope(
        request, status=status, action=action, operation_id=None,
        mutation_state=mutation_state, message=message, data=data,
        error={"code": code, "retryable": False, "fields": list(fields)} if code else None,
    ))


def update_invalid_input(request: Request, action: str, fields):
    return _response(
        request, action=action, http_status=422, status="failed", mutation_state="not_applied",
        message="Informe os dados da alteração no formato esperado.", code="invalid_request",
        fields=tuple(dict.fromkeys(fields)),
    )


def update_validation_error_response(request: Request, errors):
    """Projetar somente nomes permitidos, sem entradas ou exceções do parser."""
    action = UPDATE_ACTIONS[request.scope["route"].name]
    fields = []
    for entry in errors:
        location = entry.get("loc", ())
        if location == ("path", "event_id"):
            field = "event_id"
        elif location == ("body", "time_zone"):
            field = "time_zone"
        elif action == "reschedule" and location in (("body", "start"), ("body", "end")):
            field = location[-1]
        elif action == "edit" and location[:2] == ("body", "changes"):
            field = location[2] if len(location) == 3 and location[2] in _DETAIL_FIELDS else "changes"
        elif location and location[0] == "query":
            field = "query"
        else:
            field = "body"
        if field not in fields:
            fields.append(field)
    return update_invalid_input(request, action, fields or ["body"])


def _validate_identity(event_id: str) -> None:
    try:
        validate_event_id(event_id)
        _utf8_text(event_id)
    except ValueError:
        raise QueryInputError(("event_id",)) from None


def _validate_zone(time_zone: str) -> None:
    try:
        load_zone(time_zone)
    except ValueError:
        raise QueryInputError(("time_zone",)) from None


def _schedule_change(event_id: str, body: ScheduleBody) -> ScheduleChange:
    _validate_identity(event_id)
    _validate_zone(body.time_zone)
    values, fields = {}, []
    for field in ("start", "end"):
        try:
            values[field] = datetime.fromisoformat(getattr(body, field))
        except ValueError:
            fields.append(field)
    if fields:
        raise QueryInputError(fields)
    try:
        validate_interval(values["start"], values["end"])
    except ValueError:
        raise QueryInputError(("start", "end")) from None
    change = ScheduleChange(values["start"], values["end"], body.time_zone)
    try:
        validate_schedule_change(event_id, change)
    except ValueError:
        raise QueryInputError(("start", "end", "time_zone")) from None
    return change


def _details_change(event_id: str, body: UpdateDetailsBody):
    _validate_identity(event_id)
    _validate_zone(body.time_zone)
    change = DetailsChange(body.changes.title, body.changes.description, body.changes.location)
    if change.title is not None and not change.title.strip():
        raise QueryInputError(("title",))
    try:
        requested = validate_details_change(event_id, change, time_zone=body.time_zone)
    except ValueError:
        raise QueryInputError(("changes",)) from None
    return change, requested


def _safe_event(event, event_id: str) -> dict:
    data = _public_event(event, expected_id=event_id)
    for value in data.values():
        if isinstance(value, str):
            _utf8_text(value)
    return data


def _public_details(event, event_id: str) -> dict:
    if not isinstance(event, EventDetails):
        raise ValueError("Snapshot de informações inválido.")
    if not isinstance(event.title, str):
        raise ValueError("Título inválido no snapshot de informações.")
    raw_title = _utf8_text(event.title)
    # A edição compara o título real, inclusive quando o evento não tem título.
    # O rótulo serve apenas para reutilizar a validação dos demais campos.
    public_event = AgendaEvent(
        event_id=event.event_id, title=raw_title if raw_title.strip() else "(sem título)",
        start=event.start, end=event.end, all_day=event.all_day,
        time_zone=event.time_zone, status=event.status, html_link=event.html_link,
    )
    data = _safe_event(public_event, event_id)
    data["title"] = raw_title
    if not event.all_day:
        validate_event_interval(datetime.fromisoformat(event.start), datetime.fromisoformat(event.end), event.time_zone)
    for field in ("description", "location"):
        value = getattr(event, field)
        if not isinstance(value, str):
            raise ValueError("Informação do evento inválida.")
        data[field] = _utf8_text(value)
    return data


def _calendar(value) -> str:
    return _utf8_text(_calendar_name(value))


def _schedule_matches(event, change: ScheduleChange) -> bool:
    return all(datetime.fromisoformat(value).astimezone(timezone.utc) == expected.astimezone(timezone.utc)
               for value, expected in ((event.start, change.start), (event.end, change.end)))


def _public_reschedule(result, event_id: str, change: ScheduleChange) -> dict:
    if (not isinstance(result, RescheduleResult) or result.status not in ("rescheduled", "unchanged")
            or type(result.recovered) is not bool or result.time_zone != change.time_zone):
        raise ValueError("Resultado de reagendamento inválido.")
    before, after = _safe_event(result.before, event_id), _safe_event(result.after, event_id)
    for event in (result.before, result.after):
        if event.all_day or event.time_zone != change.time_zone:
            raise ValueError("Snapshot de horário divergente.")
        validate_event_interval(datetime.fromisoformat(event.start), datetime.fromisoformat(event.end), change.time_zone)
    if not isinstance(result.requested_start, str) or not isinstance(result.requested_end, str):
        raise ValueError("Intervalo solicitado inválido.")
    start, end = datetime.fromisoformat(result.requested_start), datetime.fromisoformat(result.requested_end)
    validate_event_interval(start, end, change.time_zone)
    if start != change.start or end != change.end or not _schedule_matches(result.after, change):
        raise ValueError("O resultado não confirma o horário solicitado.")
    already_requested = _schedule_matches(result.before, change)
    if ((result.status == "unchanged") != already_requested
            or (result.status == "unchanged" and (before != after or result.recovered))):
        raise ValueError("Estado de reagendamento não confirmado.")
    return {
        "result": result.status, "calendar_name": _calendar(result.calendar_name),
        "before": before, "after": after, "requested_start": result.requested_start,
        "requested_end": result.requested_end, "time_zone": result.time_zone, "recovered": result.recovered,
    }


def _public_update(result, event_id: str, requested: dict[str, str], time_zone: str) -> dict:
    if (not isinstance(result, DetailsResult) or result.status not in ("updated", "unchanged")
            or type(result.recovered) is not bool or not isinstance(result.requested, dict)
            or result.requested != requested):
        raise ValueError("Resultado de edição inválido.")
    before, after = _public_details(result.before, event_id), _public_details(result.after, event_id)
    for field in ("start", "end", "all_day", "time_zone"):
        if before[field] != after[field]:
            raise ValueError("A edição não preservou o horário do evento.")
    if not before["all_day"] and before["time_zone"] != time_zone:
        raise ValueError("Fuso de exibição divergente.")
    if any(after[field] != requested.get(field, before[field]) for field in _DETAIL_FIELDS):
        raise ValueError("A edição não confirmou as informações solicitadas.")
    already_requested = all(before[field] == value for field, value in requested.items())
    if ((result.status == "unchanged") != already_requested
            or (result.status == "unchanged" and (before != after or result.recovered))):
        raise ValueError("Estado de edição não confirmado.")
    return {
        "result": result.status, "calendar_name": _calendar(result.calendar_name),
        "before": before, "after": after, "requested": dict(requested), "recovered": result.recovered,
    }


def _provider_failure(request: Request, action: str, *, setup: bool = False):
    return _response(
        request, action=action, http_status=503 if setup else 502, status="failed",
        mutation_state="not_applied" if setup else "unknown",
        message="O acesso à agenda está indisponível." if setup else "Não foi possível confirmar a alteração do evento.",
        code="calendar_setup_unavailable" if setup else "operation_not_confirmed",
    )


_ERROR_RESPONSES = {
    422: {"description": "Entrada inválida antes da operação, sem reproduzir valores."},
    502: {"description": "O resultado da alteração não pôde ser confirmado."},
    503: {"description": "Acesso à agenda indisponível antes da operação."},
}


def register_update_routes(router: APIRouter) -> None:
    # O sufixo /schedule é reservado; registrá-lo antes da edição com ID opaco.
    @router.patch(
        "/events/{event_id:path}/schedule", name="api_v1_reschedule_event", tags=["agenda"],
        summary="Reagendar um evento pelo identificador completo",
        responses={**_ERROR_RESPONSES, 409: {"description": "Conflito confirmado antes da escrita."}},
    )
    def reschedule_event(request: Request, event_id: str, body: ScheduleBody):
        try:
            _strict_query(request, set())
            change = _schedule_change(event_id, body)
        except QueryInputError as error:
            return update_invalid_input(request, "reschedule", error.fields)
        operation_started, conflict_data = False, None
        try:
            with request.app.state.calendar_gateway_factory() as gateway:
                operation_started = True
                try:
                    result = RescheduleService(gateway).reschedule(event_id, change, execute=True)
                except CalendarConflictError as error:
                    conflict_data = _public_conflict(error.report, change)
                    if any(event["event_id"] == event_id for event in conflict_data["conflicts"]):
                        raise ValueError("O evento reagendado não pode conflitar consigo mesmo.")
                else:
                    data = _public_reschedule(result, event_id, change)
        except CalendarSetupError:
            return _provider_failure(request, "reschedule", setup=not operation_started)
        except Exception:
            return _provider_failure(request, "reschedule")
        if conflict_data is not None:
            return _response(
                request, action="reschedule", http_status=409, status="conflict", mutation_state="not_applied",
                message="Já existe um compromisso nesse intervalo.", data=conflict_data, code="schedule_conflict",
            )
        return _response(
            request, action="reschedule", http_status=200, status="succeeded", mutation_state="confirmed",
            message="Horário do evento confirmado.", data=data,
        )

    @router.patch(
        "/events/{event_id:path}", name="api_v1_edit_event", tags=["agenda"],
        summary="Editar título, descrição e local de um evento", responses=_ERROR_RESPONSES,
    )
    def edit_event(request: Request, event_id: str, body: UpdateDetailsBody):
        try:
            _strict_query(request, set())
            change, requested = _details_change(event_id, body)
        except QueryInputError as error:
            return update_invalid_input(request, "edit", error.fields)
        operation_started = False
        try:
            with request.app.state.calendar_gateway_factory() as gateway:
                operation_started = True
                result = EventDetailsService(gateway).update(
                    event_id, change, time_zone=body.time_zone, execute=True,
                )
                data = _public_update(result, event_id, requested, body.time_zone)
        except CalendarSetupError:
            return _provider_failure(request, "edit", setup=not operation_started)
        except Exception:
            return _provider_failure(request, "edit")
        return _response(
            request, action="edit", http_status=200, status="succeeded", mutation_state="confirmed",
            message="Informações do evento confirmadas.", data=data,
        )
