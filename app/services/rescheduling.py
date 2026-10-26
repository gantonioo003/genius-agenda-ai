"""Reagendamento independente do modelo de IA e do provedor de calendário."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from app.services.agenda import AgendaEvent
from app.services.event_selection import validate_event_id
from app.services.time_rules import validate_event_interval


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
    recovered: bool = False


class RescheduleGateway(Protocol):
    def reschedule_event(
        self, event_id: str, change: ScheduleChange, *, execute: bool,
    ) -> RescheduleResult: ...


def validate_schedule_change(event_id: str, change: ScheduleChange) -> None:
    validate_event_id(event_id)
    if not isinstance(change, ScheduleChange):
        raise ValueError("Informe os novos horários no formato esperado.")
    validate_event_interval(change.start, change.end, change.time_zone)


class RescheduleService:
    def __init__(self, gateway: RescheduleGateway):
        self.gateway = gateway

    def reschedule(
        self, event_id: str, change: ScheduleChange, *, execute: bool = False,
    ) -> RescheduleResult:
        validate_schedule_change(event_id, change)
        return self.gateway.reschedule_event(event_id, change, execute=execute)
