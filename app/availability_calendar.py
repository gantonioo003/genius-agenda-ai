"""Ocupação completa da agenda autorizada, sem conteúdo textual dos eventos.

Os limites contam itens brutos e páginas lógicas, inclusive eventos que não
bloqueiam horário. Nenhum prefixo de uma coleta incompleta é retornado.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import re
import unicodedata

from app.google_calendar import CalendarSetupError, calendar_client, resolve_calendar
from app.services.agenda import CalendarPeriod, validate_period
from app.services.availability import (
    MAX_EVENTS, MAX_PAGES, AvailabilityBinding, AvailabilityEvent,
    AvailabilitySnapshot, AvailabilityCollectionError, AvailabilityLimitError,
    AvailabilitySetupError,
)
from app.services.event_selection import validate_event_id
from app.services.time_rules import load_zone


_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z", re.ASCII)
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})\Z", re.ASCII,
)


def _identity(value):
    validate_event_id(value)
    value.encode("utf-8")
    return value


def _zone(value):
    if (type(value) is not str or not 1 <= len(value) <= 128
            or any(char.isspace() or unicodedata.category(char).startswith("C") for char in value)):
        raise ValueError()
    value.encode("utf-8")
    return load_zone(value)


def _bound(value):
    if type(value) is not datetime or value.utcoffset() is None:
        raise ValueError()
    instant = value.astimezone(timezone.utc)
    local = instant.astimezone(value.tzinfo)
    if local.replace(tzinfo=None) != value.replace(tzinfo=None) or local.utcoffset() != value.utcoffset():
        raise ValueError()
    return instant


def _civil_boundary(day, zone):
    """Primeiro instante do dia civil, inclusive meia-noite ambígua ou pulada.

    Em um gap, os dois folds cercam a transição. A busca UTC encontra sua
    fronteira exata, sem presumir que o salto tem uma hora. Se a transição
    eliminar a data inteira, a conversão final pertence a outro dia e falha.
    """
    naive = datetime.combine(day, time.min)
    candidates = sorted({naive.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
                         for fold in (0, 1)})
    exact = [instant for instant in candidates
             if instant.astimezone(zone).replace(tzinfo=None) == naive]
    if exact:
        return min(exact)
    left, right = candidates[0], candidates[-1]
    if left.astimezone(zone).date() >= day or right.astimezone(zone).date() < day:
        raise ValueError()
    while right - left > timedelta(microseconds=1):
        middle = left + (right - left) // 2
        if middle.astimezone(zone).date() < day:
            left = middle
        else:
            right = middle
    if right.astimezone(zone).date() != day:
        raise ValueError()
    return right


def _part(value, calendar_zone):
    if type(value) is not dict or ("date" in value) == ("dateTime" in value):
        raise ValueError()
    declared_zone = _zone(value["timeZone"]) if "timeZone" in value else None
    if "date" in value:
        text = value["date"]
        if type(text) is not str or _DATE.fullmatch(text) is None:
            raise ValueError()
        if declared_zone is not None and declared_zone.key != calendar_zone.key:
            raise ValueError()
        return _civil_boundary(date.fromisoformat(text), calendar_zone), True
    text = value["dateTime"]
    if type(text) is not str or _TIMESTAMP.fullmatch(text) is None:
        raise ValueError()
    if not text.endswith("Z") and (int(text[-5:-3]) > 23 or int(text[-2:]) > 59):
        # fromisoformat aceita e normaliza offsets como +01:60; a fonte precisa
        # ser recusada antes de essa informação se perder.
        raise ValueError()
    # O IANA de origem foi validado, mas o offset explícito define o instante.
    return _bound(datetime.fromisoformat(text.replace("Z", "+00:00"))), False


def _event(item, calendar_zone):
    event_id = _identity(item["id"])
    status = item["status"]
    transparency = item.get("transparency", "opaque")
    if status not in {"confirmed", "tentative"} or transparency not in {"opaque", "transparent"}:
        raise ValueError()
    start, all_day = _part(item["start"], calendar_zone)
    end, end_all_day = _part(item["end"], calendar_zone)
    if all_day != end_all_day or end <= start:
        raise ValueError()
    if "recurringEventId" in item or "originalStartTime" in item:
        parent = _identity(item.get("recurringEventId"))
        if parent == event_id:
            raise ValueError()
        _, original_all_day = _part(item["originalStartTime"], calendar_zone)
        if original_all_day != all_day:
            raise ValueError()
    return AvailabilityEvent(event_id, start, end, status, transparency, all_day)


class GoogleAvailabilityCalendarGateway:
    MAX_PAGES = MAX_PAGES
    MAX_EVENTS = MAX_EVENTS
    EVENT_FIELDS = (
        "items(id,status,start,end,transparency,recurrence,recurringEventId,originalStartTime),nextPageToken"
    )

    def __init__(self, service):
        self.service = service

    def binding(self):
        try:
            calendar = resolve_calendar(self.service)
            if type(calendar) is not dict:
                raise ValueError()
            identity = _identity(calendar["id"])
            if (identity == "primary" or calendar.get("primary", False) is not False
                    or calendar.get("deleted", False) is not False or calendar.get("accessRole") != "owner"):
                raise ValueError()
            return AvailabilityBinding(identity, _zone(calendar["timeZone"]).key)
        except CalendarSetupError:
            raise AvailabilitySetupError() from None
        except Exception:
            raise AvailabilityCollectionError() from None

    def list_occupancy(self, period, *, expected_binding):
        try:
            if type(period) is not CalendarPeriod or type(expected_binding) is not AvailabilityBinding:
                raise ValueError()
            expected_id = _identity(expected_binding.calendar_id)
            _zone(expected_binding.time_zone)
            if expected_id == "primary" or period.time_zone != expected_binding.time_zone:
                raise ValueError()
            _zone(period.time_zone)
            validate_period(period)
            start, end = _bound(period.start), _bound(period.end)
            if end <= start:
                raise ValueError()
            query_start, query_end = start.replace(microsecond=0), end.replace(microsecond=0)
            if end.microsecond:
                query_end += timedelta(seconds=1)
            binding = self.binding()
            if binding != expected_binding:
                raise ValueError()
            zone = _zone(binding.time_zone)
            resource = self.service.events()
            collected, observed = {}, {}
            seen_tokens = set()
            token = None
            raw_count = 0
            for index in range(self.MAX_PAGES):
                page = resource.list(calendarId=binding.calendar_id,
                    timeMin=query_start.isoformat(), timeMax=query_end.isoformat(),
                    timeZone=binding.time_zone, singleEvents=True, showDeleted=False,
                    showHiddenInvitations=True, orderBy="startTime", maxResults=250,
                    pageToken=token, fields=self.EVENT_FIELDS).execute(num_retries=2)
                if type(page) is not dict or type(page.get("items", [])) is not list:
                    raise ValueError()
                for item in page.get("items", []):
                    raw_count += 1
                    if raw_count > self.MAX_EVENTS:
                        raise AvailabilityLimitError()
                    if type(item) is not dict:
                        raise ValueError()
                    identity = _identity(item.get("id"))
                    if identity in observed and observed[identity] != item:
                        raise ValueError()
                    observed[identity] = deepcopy(item)
                    if "recurrence" in item:
                        raise ValueError()
                    if item.get("status") == "cancelled":
                        continue
                    entry = _event(item, zone)
                    if entry.start_utc < end and entry.end_utc > start:
                        collected[identity] = entry
                next_token = page.get("nextPageToken")
                if next_token is None or next_token == "":
                    ordered = tuple(sorted(collected.values(), key=lambda entry: (entry.start_utc, entry.event_id)))
                    return AvailabilitySnapshot(binding, period, ordered, True, index + 1, raw_count)
                if (type(next_token) is not str or len(next_token) > 4096
                        or any(char.isspace() or unicodedata.category(char).startswith("C") for char in next_token)
                        or next_token in seen_tokens):
                    raise ValueError()
                next_token.encode("utf-8")
                if index + 1 == self.MAX_PAGES or raw_count == self.MAX_EVENTS:
                    raise AvailabilityLimitError()
                seen_tokens.add(next_token)
                token = next_token
            raise AvailabilityLimitError()
        except CalendarSetupError:
            raise AvailabilitySetupError() from None
        except (AvailabilitySetupError, AvailabilityLimitError):
            raise
        except Exception:
            raise AvailabilityCollectionError() from None


@contextmanager
def default_availability_gateway():
    try:
        with calendar_client(interactive=False) as client:
            yield GoogleAvailabilityCalendarGateway(client)
    except CalendarSetupError:
        raise AvailabilitySetupError() from None
    except AvailabilityCollectionError:
        raise
    except Exception:
        raise AvailabilityCollectionError() from None
