"""Reagendamento independente do modelo de IA e do provedor de calendário."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol

from app.services.agenda import AgendaEvent, load_zone
from app.services.event_selection import validate_event_id


@dataclass(frozen=True)
class ScheduleChange:
    start: datetime
    end: datetime
    time_zone: str


@dataclass(frozen=True)
class RescheduleResult:
    status: Literal["preview", "rescheduled", "unchanged"]
    calendar_name: str
    before: AgendaEvent
    after: AgendaEvent | None
    requested_start: str
    requested_end: str
    time_zone: str


class RescheduleGateway(Protocol):
    def reschedule_event(
        self, event_id: str, change: ScheduleChange, *, execute: bool,
    ) -> RescheduleResult: ...


def validate_schedule_change(event_id: str, change: ScheduleChange) -> None:
    validate_event_id(event_id)
    zone = load_zone(change.time_zone)
    for value in (change.start, change.end):
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("Início e fim precisam incluir data, hora e deslocamento UTC.")
        # Converter pelo UTC detecta horários inexistentes; o offset distingue horas ambíguas.
        try:
            local = value.astimezone(timezone.utc).astimezone(zone)
        except OverflowError:
            raise ValueError("A data está fora do intervalo de reagendamento suportado.") from None
        if (local.replace(tzinfo=None) != value.replace(tzinfo=None)
                or local.utcoffset() != value.utcoffset()):
            raise ValueError("Data, hora e deslocamento UTC devem corresponder ao fuso informado.")
    if change.end.astimezone(timezone.utc) <= change.start.astimezone(timezone.utc):
        raise ValueError("O fim deve ser posterior ao início do compromisso.")


class RescheduleService:
    def __init__(self, gateway: RescheduleGateway):
        self.gateway = gateway

    def reschedule(
        self, event_id: str, change: ScheduleChange, *, execute: bool = False,
    ) -> RescheduleResult:
        validate_schedule_change(event_id, change)
        return self.gateway.reschedule_event(event_id, change, execute=execute)
