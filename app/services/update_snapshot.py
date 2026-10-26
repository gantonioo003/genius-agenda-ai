"""Snapshot privado da versão e dos campos preservados antes de uma alteração."""
from dataclasses import dataclass, asdict
from datetime import datetime
import json
import unicodedata

from app.services.agenda import AgendaEvent
from app.services.event_details import EventDetails
from app.services.event_selection import validate_event_id
from app.services.interviews import CalendarOperationError
from app.services.time_rules import load_zone, validate_event_interval


class UpdateBindingError(CalendarOperationError):
    """A agenda deixou de corresponder ao destino reservado antes da escrita."""


class UpdateVersionError(CalendarOperationError):
    """A versão atual do evento não corresponde à versão vinculada ao pedido."""


@dataclass(frozen=True, repr=False)
class UpdateSnapshot:
    calendar_id: str
    calendar_name: str
    event: EventDetails
    version: str
    transparency: str

    def __repr__(self):
        return 'UpdateSnapshot(<private>)'


def _text(value, limit, *, nonempty=True, controls=False):
    if (not isinstance(value, str) or len(value) > limit
            or nonempty and not value.strip()
            or not controls and any(unicodedata.category(char).startswith('C') for char in value)):
        raise ValueError()
    value.encode('utf-8')
    return value


def validate_update_guards(calendar_id, version):
    if calendar_id is not None:
        _text(calendar_id, 1024)
        if any(char.isspace() for char in calendar_id):
            raise ValueError('A identidade vinculada da agenda é inválida.')
    if version is not None:
        _text(version, 2048)


def validate_update_snapshot(snapshot, event_id, time_zone):
    """Copiar somente os campos conhecidos; nenhuma credencial ou resposta bruta."""
    try:
        validate_event_id(event_id)
        load_zone(time_zone)
        if type(snapshot) is not UpdateSnapshot or type(snapshot.event) is not EventDetails:
            raise ValueError()
        validate_update_guards(snapshot.calendar_id, snapshot.version)
        if snapshot.calendar_id is None or snapshot.version is None:
            raise ValueError()
        _text(snapshot.calendar_name, 4000)
        if snapshot.transparency not in ('opaque', 'transparent'):
            raise ValueError()
        event = snapshot.event
        _text(event.title, 4000, nonempty=False, controls=True)
        _text(event.description, 256 * 1024, nonempty=False, controls=True)
        _text(event.location, 256 * 1024, nonempty=False, controls=True)
        from app.services.agenda_tools import minimal_event
        public = AgendaEvent(**{name: getattr(event, name) for name in (
            'event_id', 'title', 'start', 'end', 'all_day', 'time_zone', 'status', 'html_link')})
        if not public.title.strip():
            public = AgendaEvent(**{**public.__dict__, 'title': '(sem título)'})
        minimal_event(public, expected_id=event_id)
        if not event.all_day:
            if event.time_zone != time_zone:
                raise ValueError()
            validate_event_interval(datetime.fromisoformat(event.start), datetime.fromisoformat(event.end), time_zone)
        if len(json.dumps(asdict(event), ensure_ascii=True).encode('utf-8')) > 256 * 1024:
            raise ValueError()
        return UpdateSnapshot(snapshot.calendar_id, snapshot.calendar_name,
                              EventDetails(**asdict(event)), snapshot.version, snapshot.transparency)
    except (ValueError, TypeError, UnicodeError, KeyError, OverflowError):
        raise ValueError('A leitura da alteração não contém um estado válido.') from None
