"""Conflitos por interseção de intervalos, sem dependência do provedor ou da IA."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Literal

from app.services.agenda import AgendaEvent, CalendarPeriod, load_zone, validate_period
from app.services.event_selection import validate_event_id
from app.services.interviews import CalendarOperationError
from app.services.time_rules import validate_interval


@dataclass(frozen=True)
class ConflictReport:
    status: Literal["available", "conflict"]
    calendar_name: str
    start: str
    end: str
    time_zone: str
    conflicts: tuple[AgendaEvent, ...] = ()


class CalendarConflictError(CalendarOperationError):
    def __init__(self, report: ConflictReport):
        self.report = report
        names = "; ".join(event.title for event in report.conflicts[:3])
        super().__init__(f"Há conflito com {len(report.conflicts)} compromisso(s): {names}. Escolha outro intervalo.")


def build_conflict_report(
    period: CalendarPeriod, calendar_name: str, events: Iterable[AgendaEvent], *,
    exclude_event_id: str | None = None,
) -> ConflictReport:
    validate_period(period)
    if exclude_event_id is not None:
        validate_event_id(exclude_event_id)
    start, end = period.start.astimezone(timezone.utc), period.end.astimezone(timezone.utc)
    found = {}
    for event in events:
        validate_event_id(event.event_id)
        if event.status == "cancelled":
            continue
        if event.status not in ("confirmed", "tentative"):
            raise ValueError("Estado desconhecido na verificação de conflitos.")
        if event.event_id == exclude_event_id:
            continue
        if event.all_day:
            zone = load_zone(event.time_zone)
            event_start = datetime.combine(date.fromisoformat(event.start), time.min, tzinfo=zone)
            event_end = datetime.combine(date.fromisoformat(event.end), time.min, tzinfo=zone)
        else:
            event_start, event_end = datetime.fromisoformat(event.start), datetime.fromisoformat(event.end)
        validate_interval(event_start, event_end)
        event_start, event_end = event_start.astimezone(timezone.utc), event_end.astimezone(timezone.utc)
        if event_start < end and event_end > start:
            previous = found.get(event.event_id)
            if previous is not None and previous[1] != event:
                raise ValueError("O evento mudou durante a consulta de conflitos.")
            found[event.event_id] = (event_start, event)
    conflicts = tuple(event for _, event in sorted(found.values(), key=lambda entry: (entry[0], entry[1].event_id)))
    return ConflictReport("conflict" if conflicts else "available", calendar_name,
                          period.start.isoformat(), period.end.isoformat(), period.time_zone, conflicts)


def require_available(report: ConflictReport) -> None:
    if report.status not in ("available", "conflict") or bool(report.conflicts) != (report.status == "conflict"):
        raise ValueError("Resultado de disponibilidade inválido.")
    if report.status == "conflict":
        raise CalendarConflictError(report)
