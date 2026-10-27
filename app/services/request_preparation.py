"""Preparar pedidos e esclarecimentos; não executa operações de escrita."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from app.services.agenda import AgendaEvent, day_period, month_period, week_period
from app.services.event_details import DetailsChange, validate_details_change
from app.services.event_selection import EventSelectionGateway, EventSelectionService, normalized_title, validate_event_id
from app.services.interviews import CalendarOperationError, InterviewDraft, validate_interview
from app.services.time_rules import load_zone, validate_event_interval


@dataclass(frozen=True)
class RequestPreparation:
    action: str | None
    status: Literal["ready", "needs_clarification", "needs_correction"]
    reason: str | None = None
    question: str | None = None
    missing_fields: tuple[str, ...] = ()
    invalid_fields: tuple[str, ...] = ()
    event: AgendaEvent | None = None
    candidates: tuple[AgendaEvent, ...] = ()
    parameters: dict | None = None


_SELECTOR_FIELDS = {"event_id", "search_title", "date", "period"}
_ACTION_FIELDS = {
    "create": {"title", "start", "end", "description"},
    "list": {"date", "period"},
    "reschedule": _SELECTOR_FIELDS | {"start", "end"},
    "edit": _SELECTOR_FIELDS | {"title", "description", "location"},
    "delete": _SELECTOR_FIELDS,
}
_BUILDERS = {"day": day_period, "week": week_period, "month": month_period}
_LABELS = {
    "title": "o título do compromisso", "start": "a data e o horário de início",
    "end": "a data e o horário de término", "time_zone": "o fuso horário",
    "date": "a data de referência", "search_title": "o título do compromisso que devo procurar",
    "event_id": "o identificador completo do compromisso",
    "event": "qual compromisso usar (título ou identificador)",
    "changes": "o que deseja alterar: título, descrição ou local",
}


class RequestPreparationService:
    def __init__(self, gateway: EventSelectionGateway | None = None, *, default_time_zone: str = "America/Recife"):
        load_zone(default_time_zone)
        self.gateway = gateway
        self.default_time_zone = default_time_zone

    def prepare(self, request: Mapping) -> RequestPreparation:
        if not isinstance(request, Mapping) or any(not isinstance(key, str) for key in request):
            raise ValueError("O pedido deve ser um objeto com campos de texto.")
        action = request.get("action")
        if action is None or (isinstance(action, str) and not action.strip()):
            return RequestPreparation(None, "needs_clarification", "missing_information",
                                      "Você quer criar, consultar, reagendar, editar ou excluir um compromisso?",
                                      missing_fields=("action",))
        if not isinstance(action, str) or action not in _ACTION_FIELDS:
            return RequestPreparation(None, "needs_correction", "invalid_information",
                                      "Qual ação deseja fazer: criar, consultar, reagendar, editar ou excluir?",
                                      invalid_fields=("action",))

        missing, invalid = [], []
        allowed = _ACTION_FIELDS[action] | {"action", "time_zone"}
        unknown = sorted(set(request) - allowed)
        invalid.extend(unknown)
        parameters = {}

        def required_text(field):
            value = request.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                missing.append(field)
                return None
            if not isinstance(value, str):
                invalid.append(field)
                return None
            return value

        zone_name = request.get("time_zone", self.default_time_zone)
        if zone_name is None or (isinstance(zone_name, str) and not zone_name.strip()):
            missing.append("time_zone")
        else:
            try:
                load_zone(zone_name)
            except ValueError:
                invalid.append("time_zone")
            else:
                parameters["time_zone"] = zone_name

        def timestamp(field):
            value = required_text(field)
            if value is None:
                return None
            try:
                if "T" not in value:
                    raise ValueError
                parsed = datetime.fromisoformat(value)
                if parsed.utcoffset() is None:
                    raise ValueError
                parameters[field] = parsed.isoformat()
                return parsed
            except ValueError:
                invalid.append(field)
                return None

        if action == "create":
            title = required_text("title")
            if title is not None:
                parameters["title"] = title.strip()
            description = request.get("description")
            if description is None:
                description = ""
            if not isinstance(description, str):
                invalid.append("description")
            else:
                parameters["description"] = description

        schedule_error = None
        if action in ("create", "reschedule"):
            start, end = timestamp("start"), timestamp("end")
            if start is not None and end is not None and "time_zone" in parameters:
                try:
                    validate_event_interval(start, end, zone_name)
                except ValueError as exc:
                    invalid.extend(("start", "end"))
                    schedule_error = str(exc)
            if action == "create" and not missing and not invalid:
                validate_interview(InterviewDraft(parameters["title"], start, end, zone_name, parameters["description"]),
                                   "request-preparation-only")

        if action == "edit":
            change = {}
            for field in ("title", "description", "location"):
                value = request.get(field)
                if value is None:
                    continue  # None é omissão; texto vazio limpa descrição/local.
                if not isinstance(value, str) or (field == "title" and not value.strip()):
                    invalid.append(field)
                else:
                    change[field] = value.strip() if field == "title" else value
            if not change and not any(field in invalid for field in ("title", "description", "location")):
                missing.append("changes")
            parameters.update(change)

        selector = {}
        if action == "list" or action in ("reschedule", "edit", "delete"):
            has_id = action != "list" and request.get("event_id") is not None
            if has_id:
                event_id = required_text("event_id")
                if event_id is not None:
                    try:
                        validate_event_id(event_id)
                    except ValueError:
                        invalid.append("event_id")
                    else:
                        selector["event_id"] = event_id
                competing = [field for field in ("search_title", "date", "period") if request.get(field) is not None]
                if competing:
                    invalid.extend(["event_id", *competing])
            else:
                if action != "list":
                    title = required_text("search_title")
                    if title is not None:
                        if not normalized_title(title):
                            invalid.append("search_title")
                        else:
                            selector["title"] = title
                # A data limita uma busca somente quando foi informada. Um
                # período explícito ainda precisa dessa âncora para ser aplicado.
                needs_date = action == "list" or request.get("period") is not None
                day_text = required_text("date") if needs_date or request.get("date") is not None else None
                period_kind = request.get("period", "day")
                if not isinstance(period_kind, str) or period_kind not in _BUILDERS:
                    invalid.append("period")
                day = None
                if day_text is not None:
                    try:
                        day = date.fromisoformat(day_text)
                        if day.isoformat() != day_text:
                            raise ValueError
                    except ValueError:
                        invalid.append("date")
                        day = None
                if day is not None and "period" not in invalid and "time_zone" in parameters:
                    try:
                        selector["period"] = _BUILDERS[period_kind](day, zone_name)
                    except ValueError:
                        invalid.append("date")
                    else:
                        if action == "list":
                            parameters.update(date=day.isoformat(), period=period_kind)

        missing = tuple(dict.fromkeys(missing))
        invalid = tuple(dict.fromkeys(invalid))
        if missing or invalid:
            if invalid:
                if schedule_error:
                    question = f"{schedule_error} Pode conferir os horários e o fuso?"
                elif "event_id" in invalid and any(field in invalid for field in ("search_title", "date", "period")):
                    question = "Você quer usar o identificador exato ou procurar pelo título? Informe apenas uma dessas opções."
                elif unknown:
                    question = "Não consegui entender uma parte do pedido. Pode reformulá-lo usando os dados da ação desejada?"
                else:
                    labels = [_LABELS.get(field, {"period": "o período da consulta (dia, semana ou mês)",
                                                 "description": "a descrição como texto", "location": "o local como texto"}.get(field, field))
                              for field in invalid]
                    question = "Pode conferir " + ", ".join(labels) + "?"
                if missing:
                    question += " Informe também " + ", ".join(_LABELS[field] for field in missing) + "."
                return RequestPreparation(action, "needs_correction", "invalid_information", question,
                                          missing_fields=missing, invalid_fields=invalid)
            return RequestPreparation(action, "needs_clarification", "missing_information",
                                      "Para continuar, informe " + ", ".join(_LABELS[field] for field in missing) + ".",
                                      missing_fields=missing)

        if action in ("create", "list"):
            return RequestPreparation(action, "ready", parameters=parameters)
        if self.gateway is None:
            raise CalendarOperationError("O conector de consulta é necessário para conferir o evento.")
        selection = EventSelectionService(self.gateway).select(**selector, time_zone=zone_name)
        if selection.status != "selected":
            reason = "event_not_found" if selection.status == "not_found" else "event_selection"
            return RequestPreparation(action, "needs_clarification", reason, selection.question,
                                      candidates=selection.candidates)
        parameters["event_id"] = selection.event.event_id
        if action == "edit":
            validate_details_change(selection.event.event_id, DetailsChange(
                title=parameters.get("title"), description=parameters.get("description"),
                location=parameters.get("location")), time_zone=zone_name)
        return RequestPreparation(action, "ready", event=selection.event, parameters=parameters)
