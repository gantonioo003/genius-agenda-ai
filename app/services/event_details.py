"""Edição de informações do evento, independente de Google e de modelo de IA."""

from dataclasses import dataclass
from typing import Literal, Protocol

from app.services.agenda import AgendaEvent, load_zone
from app.services.event_selection import validate_event_id


@dataclass(frozen=True)
class DetailsChange:
    title: str | None = None
    description: str | None = None
    location: str | None = None


@dataclass(frozen=True)
class EventDetails(AgendaEvent):
    description: str = ""
    location: str = ""


@dataclass(frozen=True)
class DetailsResult:
    status: Literal["preview", "updated", "unchanged"]
    calendar_name: str
    before: EventDetails
    after: EventDetails | None
    requested: dict[str, str]
    recovered: bool = False


class EventDetailsGateway(Protocol):
    def update_event_details(
        self, event_id: str, change: DetailsChange, *, time_zone: str, execute: bool,
    ) -> DetailsResult: ...


def validate_details_change(
    event_id: str, change: DetailsChange, *, time_zone: str = "America/Recife",
) -> dict[str, str]:
    validate_event_id(event_id)
    load_zone(time_zone)
    requested = {}
    for field in ("title", "description", "location"):
        value = getattr(change, field)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError("Título, descrição e local devem ser textos quando informados.")
        if field == "title":
            value = value.strip()
            if not value:
                raise ValueError("O título do evento não pode ficar vazio.")
        requested[field] = value
    if not requested:
        raise ValueError("Informe ao menos um campo: título, descrição ou local.")
    return requested


class EventDetailsService:
    def __init__(self, gateway: EventDetailsGateway):
        self.gateway = gateway

    def update(
        self, event_id: str, change: DetailsChange, *, time_zone: str = "America/Recife",
        execute: bool = False,
    ) -> DetailsResult:
        validate_details_change(event_id, change, time_zone=time_zone)
        return self.gateway.update_event_details(event_id, change, time_zone=time_zone, execute=execute)
