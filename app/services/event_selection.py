"""Identificação de eventos sem depender da IA ou de operações de escrita."""

from dataclasses import dataclass
from typing import Literal, Protocol
import unicodedata

from app.services.agenda import AgendaEvent, CalendarListing, CalendarPeriod, load_zone, validate_period
from app.services.interviews import CalendarOperationError


@dataclass(frozen=True)
class EventLookup:
    calendar_name: str
    event: AgendaEvent | None


@dataclass(frozen=True)
class EventSelection:
    status: Literal["selected", "needs_clarification", "not_found"]
    calendar_name: str
    event: AgendaEvent | None
    candidates: tuple[AgendaEvent, ...] = ()
    question: str | None = None


class EventSelectionGateway(Protocol):
    def list_events(self, period: CalendarPeriod) -> CalendarListing: ...
    def get_event(self, event_id: str, *, time_zone: str) -> EventLookup: ...


def normalized_title(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return " ".join("".join(char for char in decomposed if not unicodedata.combining(char)).split())


def validate_event_id(event_id: str) -> None:
    # O ID é opaco: preservar caixa e IDs de ocorrências recorrentes.
    if (not isinstance(event_id, str) or not event_id or len(event_id) > 1024
            or any(char.isspace() or unicodedata.category(char).startswith("C") for char in event_id)):
        raise ValueError("Informe o ID completo do evento, sem espaços, como retornado na consulta.")


def validate_selection(
    *, event_id: str | None, title: str | None,
    period: CalendarPeriod | None, time_zone: str,
) -> str | None:
    load_zone(time_zone)
    if event_id is not None:
        validate_event_id(event_id)
        if title is not None or period is not None:
            raise ValueError("Use o ID sozinho; título e período pertencem à busca por título.")
        return None
    if not isinstance(title, str) or not normalized_title(title):
        raise ValueError("Informe um título ou o ID do evento.")
    if period is None:
        raise ValueError("A busca por título precisa de uma data e período de referência.")
    validate_period(period)
    if period.time_zone != time_zone:
        raise ValueError("O fuso da seleção deve ser o mesmo do período consultado.")
    return normalized_title(title)


class EventSelectionService:
    def __init__(self, gateway: EventSelectionGateway):
        self.gateway = gateway

    def select(
        self, *, event_id: str | None = None, title: str | None = None,
        period: CalendarPeriod | None = None, time_zone: str = "America/Recife",
    ) -> EventSelection:
        needle = validate_selection(
            event_id=event_id, title=title, period=period, time_zone=time_zone,
        )
        if event_id is not None:
            lookup = self.gateway.get_event(event_id, time_zone=time_zone)
            return self._from_lookup(lookup, event_id)

        listing = self.gateway.list_events(period)
        matches = tuple(event for event in listing.events
                        if event.status != "cancelled" and needle in normalized_title(event.title))
        if not matches:
            return EventSelection(
                "not_found", listing.calendar_name, None,
                question="Não encontrei esse compromisso no período. Pode conferir a data e o título?",
            )
        if len(matches) > 1:
            return EventSelection(
                "needs_clarification", listing.calendar_name, None, candidates=matches,
                question="Encontrei mais de um compromisso. Qual deles? Informe o ID da opção desejada.",
            )

        # Não tratar uma lista anterior como confirmação de que o evento segue igual.
        previous = matches[0]
        lookup = self.gateway.get_event(previous.event_id, time_zone=time_zone)
        result = self._from_lookup(lookup, previous.event_id)
        if result.event is None:
            return result
        current = result.event
        fields = ("title", "start", "end", "all_day", "time_zone", "status")
        if any(getattr(current, field) != getattr(previous, field) for field in fields):
            return EventSelection(
                "needs_clarification", lookup.calendar_name, None, candidates=(current,),
                question="O compromisso mudou desde a busca. Confira os dados e informe o ID para selecioná-lo novamente.",
            )
        return result

    @staticmethod
    def _from_lookup(lookup: EventLookup, expected_id: str) -> EventSelection:
        if lookup.event is None or lookup.event.status == "cancelled":
            return EventSelection(
                "not_found", lookup.calendar_name, None,
                question="Esse evento não está disponível na agenda de teste. Pode consultar novamente e conferir o ID?",
            )
        if lookup.event.event_id != expected_id or lookup.event.status not in ("confirmed", "tentative"):
            raise CalendarOperationError("Não foi possível confirmar a identidade e o estado do evento.")
        return EventSelection("selected", lookup.calendar_name, lookup.event)
