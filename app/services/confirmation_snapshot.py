"""Leitura mínima da agenda e versão do evento para vincular uma confirmação."""

from dataclasses import dataclass
from typing import Protocol
import unicodedata

from app.services.agenda import AgendaEvent
from app.services.event_selection import validate_event_id
from app.services.time_rules import load_zone


@dataclass(frozen=True, repr=False)
class ConfirmationSnapshot:
    calendar_id: str
    calendar_name: str
    event: AgendaEvent | None
    version: str | None

    def __repr__(self):
        return "ConfirmationSnapshot(<private>)"


class ConfirmationSnapshotGateway(Protocol):
    def get_confirmation_snapshot(
        self, event_id: str | None, *, time_zone: str,
    ) -> ConfirmationSnapshot: ...


def _text(value, *, limit):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(unicodedata.category(char).startswith("C") for char in value)):
        raise ValueError()
    value.encode("utf-8")
    return value


def validate_confirmation_snapshot(snapshot, expected_id, time_zone):
    """Retornar cópia fechada, sem descrições, locais ou dados extras do provedor."""
    try:
        load_zone(time_zone)
        if expected_id is not None:
            validate_event_id(expected_id)
        if type(snapshot) is not ConfirmationSnapshot:
            raise ValueError()
        calendar_id = _text(snapshot.calendar_id, limit=1024)
        if any(char.isspace() for char in calendar_id):
            raise ValueError()
        calendar_name = _text(snapshot.calendar_name, limit=4000)
        if snapshot.event is None:
            if snapshot.version is not None:
                raise ValueError()
            return ConfirmationSnapshot(calendar_id, calendar_name, None, None)
        if expected_id is None:
            raise ValueError()
        # Importar somente durante validação evita dependência circular com o
        # módulo Google, do qual a projeção de consultas também depende.
        from app.services.agenda_tools import minimal_event
        event = AgendaEvent(**minimal_event(snapshot.event, expected_id=expected_id))
        if not event.all_day and event.time_zone != time_zone:
            raise ValueError()
        version = _text(snapshot.version, limit=2048)
        return ConfirmationSnapshot(calendar_id, calendar_name, event, version)
    except (ValueError, TypeError, UnicodeError, KeyError):
        raise ValueError("A leitura da confirmação não contém um estado válido.") from None
