"""Confirmação de exclusão por identidade e versão, sem dependência de IA."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
import re
from typing import Literal, Protocol

from app.services.agenda import AgendaEvent, load_zone
from app.services.event_selection import validate_event_id


@dataclass(frozen=True)
class DeletionResult:
    status: Literal["needs_confirmation", "deleted", "already_absent"]
    calendar_name: str
    event: AgendaEvent | None
    confirmation_token: str | None = None
    question: str | None = None


class EventDeletionGateway(Protocol):
    def delete_event(
        self, event_id: str, *, time_zone: str, execute: bool,
        confirmation_token: str | None,
    ) -> DeletionResult: ...


def validate_deletion(
    event_id: str, *, time_zone: str = "America/Recife", execute: bool = False,
    confirmation_token: str | None = None,
) -> None:
    validate_event_id(event_id)
    load_zone(time_zone)
    if execute:
        if not isinstance(confirmation_token, str) or not re.fullmatch(r"[0-9a-f]{64}", confirmation_token):
            raise ValueError("Para excluir, informe --execute e --confirm com o código completo da prévia.")
    elif confirmation_token is not None:
        raise ValueError("Use o código de confirmação somente junto com --execute.")


def deletion_confirmation(calendar_id: str, event_id: str, version: str) -> str:
    """Código de versão; não é credencial nem comprovação de consentimento humano."""
    payload = {"operation": "delete_event.v1", "calendar_id": calendar_id,
               "event_id": event_id, "version": version}
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True).encode("utf-8")).hexdigest()


def deletion_question(event: AgendaEvent) -> str:
    if event.all_day:
        start = date.fromisoformat(event.start).strftime("%d/%m/%Y")
        last = (date.fromisoformat(event.end) - timedelta(days=1)).strftime("%d/%m/%Y")
        when = f"em {start}, dia inteiro" if start == last else f"de {start} a {last}, dias inteiros"
    else:
        start = datetime.fromisoformat(event.start)
        end = datetime.fromisoformat(event.end)
        end_format = "%Hh%M" if start.date() == end.date() else "%d/%m/%Y às %Hh%M"
        when = f"de {start.strftime('%d/%m/%Y às %Hh%M')} até {end.strftime(end_format)}"
    return f'Confirma excluir "{event.title}" {when}? Confira os dados antes de confirmar.'


class EventDeletionService:
    def __init__(self, gateway: EventDeletionGateway):
        self.gateway = gateway

    def delete(
        self, event_id: str, *, time_zone: str = "America/Recife", execute: bool = False,
        confirmation_token: str | None = None,
    ) -> DeletionResult:
        validate_deletion(event_id, time_zone=time_zone, execute=execute,
                          confirmation_token=confirmation_token)
        return self.gateway.delete_event(event_id, time_zone=time_zone, execute=execute,
                                         confirmation_token=confirmation_token)
