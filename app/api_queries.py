"""Consultas HTTP da agenda, com validação local e projeção pública mínima."""

from contextlib import contextmanager
from datetime import date as Date, datetime
import re
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api_security import security_envelope
from app.google_calendar import CalendarSetupError, GoogleCalendarGateway, calendar_client
from app.services.agenda import (
    AgendaEvent, CalendarListing, day_period, month_period, week_period,
)
from app.services.event_selection import EventLookup, validate_event_id
from app.services.time_rules import load_zone, validate_interval


_BUILDERS = {"day": day_period, "week": week_period, "month": month_period}
_ROUTE_ACTIONS = {"api_v1_list_events": "list", "api_v1_get_event": "get"}
_SAFE_FIELDS = frozenset({"date", "period", "time_zone", "event_id"})
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", flags=re.ASCII)
_ERROR_RESPONSES = {
    422: {"description": "Entrada inválida, sem reprodução dos valores recebidos."},
    502: {"description": "A consulta não pôde ser concluída ou confirmou dados inválidos."},
    503: {"description": "A configuração de acesso à agenda está indisponível."},
}


@contextmanager
def default_calendar_gateway():
    """Abrir um cliente por requisição, depois da autenticação e validação."""
    with calendar_client(interactive=False) as client:
        yield GoogleCalendarGateway(client)


class QueryInputError(ValueError):
    def __init__(self, fields):
        super().__init__("Parâmetros de consulta inválidos.")
        self.fields = tuple(dict.fromkeys(fields))


def _response(request: Request, *, action: str | None, http_status: int = 200,
              status: str = "succeeded", message: str = "Consulta concluída.",
              data=None, code: str | None = None, fields=()) -> JSONResponse:
    error = {"code": code, "retryable": False, "fields": list(fields)} if code else None
    return JSONResponse(
        status_code=http_status,
        content=security_envelope(
            request, status=status, action=action, mutation_state="not_applicable",
            message=message, data=data, error=error,
        ),
    )


def _invalid_input(request: Request, action: str | None, fields) -> JSONResponse:
    return _response(
        request, action=action, http_status=422, status="failed",
        message="Informe os parâmetros da consulta no formato esperado.",
        code="invalid_request", fields=fields,
    )


async def query_validation_error_handler(request: Request, error: RequestValidationError):
    """Usar somente nomes permitidos; nunca devolver os inputs do Pydantic."""
    route = request.scope.get("route")
    if getattr(route, "name", None) in ("api_v1_deletion_preview", "api_v1_delete_event"):
        from app.api_deletion import deletion_validation_error_response
        return deletion_validation_error_response(request, error.errors())
    if getattr(route, "name", None) in ("api_v1_reschedule_event", "api_v1_edit_event"):
        from app.api_updates import update_validation_error_response
        return update_validation_error_response(request, error.errors())
    if getattr(route, "name", None) == "api_v1_create_event":
        from app.api_creation import creation_validation_error_response
        return creation_validation_error_response(request, error.errors(), body=error.body)
    action = _ROUTE_ACTIONS.get(getattr(route, "name", None))
    fields = []
    for entry in error.errors():
        location = entry.get("loc", ())
        field = location[-1] if location else None
        safe_field = field if field in _SAFE_FIELDS else "query"
        if safe_field not in fields:
            fields.append(safe_field)
    return _invalid_input(request, action, fields or ["query"])


def _strict_query(request: Request, allowed: set[str]) -> None:
    counts = {}
    fields = []
    for key, _value in request.query_params.multi_items():
        counts[key] = counts.get(key, 0) + 1
        safe_field = key if key in allowed else "query"
        if (key not in allowed or counts[key] > 1) and safe_field not in fields:
            fields.append(safe_field)
    if fields:
        raise QueryInputError(fields)


def _valid_zone(time_zone: str) -> None:
    try:
        load_zone(time_zone)
    except ValueError:
        raise QueryInputError(("time_zone",)) from None


def _listing_period(date: str, period: str, time_zone: str):
    fields = []
    reference = None
    if not _DATE.fullmatch(date):
        fields.append("date")
    else:
        try:
            reference = Date.fromisoformat(date)
        except ValueError:
            fields.append("date")
    if period not in _BUILDERS:
        fields.append("period")
    try:
        _valid_zone(time_zone)
    except QueryInputError:
        fields.append("time_zone")
    if fields:
        raise QueryInputError(fields)
    try:
        return _BUILDERS[period](reference, time_zone)
    except ValueError:
        raise QueryInputError(("date",)) from None


def _calendar_name(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Nome de agenda inválido na resposta do provedor.")
    return value


def _public_event(event: AgendaEvent, *, expected_id: str | None = None) -> dict:
    if not isinstance(event, AgendaEvent):
        raise ValueError("Evento inválido na resposta do provedor.")
    validate_event_id(event.event_id)
    if expected_id is not None and event.event_id != expected_id:
        raise ValueError("Identidade divergente na resposta do provedor.")
    if event.status not in ("confirmed", "tentative"):
        raise ValueError("Estado inválido na resposta do provedor.")
    if (not isinstance(event.title, str) or not event.title.strip()
            or not isinstance(event.start, str) or not isinstance(event.end, str)
            or type(event.all_day) is not bool
            or (event.html_link is not None and not isinstance(event.html_link, str))):
        raise ValueError("Dados inválidos na resposta do provedor.")
    load_zone(event.time_zone)
    if event.all_day:
        if not _DATE.fullmatch(event.start) or not _DATE.fullmatch(event.end):
            raise ValueError("Datas inválidas na resposta do provedor.")
        if Date.fromisoformat(event.end) <= Date.fromisoformat(event.start):
            raise ValueError("Intervalo inválido na resposta do provedor.")
    else:
        validate_interval(datetime.fromisoformat(event.start), datetime.fromisoformat(event.end))
    # A projeção explícita também protege subclasses como EventDetails.
    return {
        "event_id": event.event_id, "title": event.title,
        "start": event.start, "end": event.end, "all_day": event.all_day,
        "time_zone": event.time_zone, "status": event.status, "html_link": event.html_link,
    }


def _provider_failure(request: Request, action: str, *, setup: bool = False):
    return _response(
        request, action=action, http_status=503 if setup else 502, status="failed",
        message="O acesso à agenda está indisponível." if setup else "Não foi possível concluir a consulta da agenda.",
        code="calendar_setup_unavailable" if setup else "operation_failed",
    )


def register_query_routes(router: APIRouter) -> None:
    """Registrar somente leitura no router que já exige a chave da API."""

    @router.get(
        "/events", name="api_v1_list_events", tags=["agenda"],
        summary="Consultar eventos por dia, semana ou mês", responses=_ERROR_RESPONSES,
    )
    def list_events(
        request: Request,
        date: Annotated[str, Query(description="Data de referência, exatamente AAAA-MM-DD.")],
        period: Annotated[str, Query(description="day, week ou month; o fim é exclusivo.")] = "day",
        time_zone: Annotated[str, Query(description="Fuso IANA da consulta.")] = "America/Recife",
    ):
        try:
            _strict_query(request, {"date", "period", "time_zone"})
            selected_period = _listing_period(date, period, time_zone)
        except QueryInputError as error:
            return _invalid_input(request, "list", error.fields)
        try:
            with request.app.state.calendar_gateway_factory() as gateway:
                listing = gateway.list_events(selected_period)
                if not isinstance(listing, CalendarListing) or not isinstance(listing.events, (tuple, list)):
                    raise ValueError("Lista inválida na resposta do provedor.")
                data = {
                    "calendar_name": _calendar_name(listing.calendar_name),
                    "period": {
                        "start": selected_period.start.isoformat(), "end": selected_period.end.isoformat(),
                        "time_zone": selected_period.time_zone,
                    },
                    "events": [_public_event(event) for event in listing.events],
                }
                ids = [event["event_id"] for event in data["events"]]
                if len(ids) != len(set(ids)):
                    raise ValueError("Identidades repetidas na resposta do provedor.")
        except CalendarSetupError:
            return _provider_failure(request, "list", setup=True)
        except Exception:
            # Fronteira deliberada: nenhum erro bruto do SDK, OAuth ou banco
            # vira lista vazia ou é exposto ao cliente.
            return _provider_failure(request, "list")
        return _response(request, action="list", data=data)

    @router.get(
        "/events/{event_id:path}", name="api_v1_get_event", tags=["agenda"],
        summary="Consultar um evento pelo identificador completo", responses={
            **_ERROR_RESPONSES, 404: {"description": "Ausência validada do evento na agenda."},
        },
    )
    def get_event(
        request: Request, event_id: str,
        time_zone: Annotated[str, Query(description="Fuso IANA de exibição.")] = "America/Recife",
    ):
        try:
            _strict_query(request, {"time_zone"})
            _valid_zone(time_zone)
            try:
                validate_event_id(event_id)
            except ValueError:
                raise QueryInputError(("event_id",)) from None
        except QueryInputError as error:
            return _invalid_input(request, "get", error.fields)
        try:
            with request.app.state.calendar_gateway_factory() as gateway:
                lookup = gateway.get_event(event_id, time_zone=time_zone)
                if not isinstance(lookup, EventLookup):
                    raise ValueError("Identificação inválida na resposta do provedor.")
                data = {
                    "calendar_name": _calendar_name(lookup.calendar_name),
                    "event": _public_event(lookup.event, expected_id=event_id) if lookup.event is not None else None,
                }
        except CalendarSetupError:
            return _provider_failure(request, "get", setup=True)
        except Exception:
            return _provider_failure(request, "get")
        if data["event"] is None:
            return _response(
                request, action="get", http_status=404, status="not_found",
                message="O evento não foi encontrado.", data=data, code="event_not_found",
            )
        return _response(request, action="get", data=data)
