"""Fotografia mínima e completa da agenda para composição factual de resumos.

Paginação/contadores são de páginas lógicas, não de tentativas HTTP do SDK.
Não reutiliza nem altera list_events do conector CRUD. O helper de descoberta
existente pode renovar metadados locais/OAuth; este adaptador só lê eventos.
"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import re
import unicodedata

from app.google_calendar import CalendarSetupError, calendar_client, resolve_calendar
from app.services.agenda import AgendaEvent, CalendarPeriod, validate_period
from app.services.event_selection import validate_event_id
from app.services.summary_context import (
    MAX_EVENTS, MAX_PAGES, SummaryCalendarBinding, SummaryCalendarSnapshot,
    SummaryCollectionError, SummaryLimitError, SummarySetupError,
)
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
    if (not isinstance(value, str) or not 1 <= len(value) <= 128
            or any(char.isspace() or unicodedata.category(char).startswith("C") for char in value)):
        raise ValueError()
    value.encode("utf-8")
    return load_zone(value)


def _bound(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError()
    instant = value.astimezone(timezone.utc)
    local = instant.astimezone(value.tzinfo)
    if local.replace(tzinfo=None) != value.replace(tzinfo=None) or local.utcoffset() != value.utcoffset():
        raise ValueError()
    return instant


def _part(value, calendar_zone):
    if not isinstance(value, dict) or ("date" in value) == ("dateTime" in value):
        raise ValueError()
    declared_zone = _zone(value["timeZone"]) if "timeZone" in value else None
    if "date" in value:
        day = value["date"]
        if not isinstance(day, str) or _DATE.fullmatch(day) is None:
            raise ValueError()
        if declared_zone is not None and declared_zone.key != calendar_zone.key:
            raise ValueError()
        local = datetime.combine(date.fromisoformat(day), time.min, tzinfo=calendar_zone)
        return _bound(local), True, day
    timestamp = value["dateTime"]
    if not isinstance(timestamp, str) or _TIMESTAMP.fullmatch(timestamp) is None:
        raise ValueError()
    if not timestamp.endswith("Z") and (int(timestamp[-5:-3]) > 23 or int(timestamp[-2:]) > 59):
        # fromisoformat normaliza certos offsets inválidos, como +01:60.
        # Validar os componentes originais antes de perder essa informação.
        raise ValueError()
    # O offset retornado é autoritativo. Um IANA original/de recorrência pode
    # diferir da representação da resposta; foi validado, sem refazer o instante.
    instant = _bound(datetime.fromisoformat(timestamp.replace("Z", "+00:00")))
    return instant, False, instant.astimezone(calendar_zone).isoformat()


def _event(item, calendar_zone):
    event_id = _identity(item["id"])
    if item["status"] not in {"confirmed", "tentative"} or "recurrence" in item:
        raise ValueError()
    start, all_day, display_start = _part(item["start"], calendar_zone)
    end, end_all_day, display_end = _part(item["end"], calendar_zone)
    if end_all_day != all_day or end <= start:
        raise ValueError()
    if "recurringEventId" in item or "originalStartTime" in item:
        parent = _identity(item.get("recurringEventId"))
        if parent == event_id:
            raise ValueError()
        _, original_all_day, _ = _part(item["originalStartTime"], calendar_zone)
        if original_all_day != all_day:
            raise ValueError()
    title = item.get("summary", "")
    if not isinstance(title, str) or len(title) > 10000:
        raise ValueError()
    title.encode("utf-8")
    return start, end, AgendaEvent(event_id, title, display_start, display_end,
        all_day, calendar_zone.key, item["status"], html_link=None)


class GoogleSummaryCalendarGateway:
    MAX_PAGES = MAX_PAGES
    MAX_EVENTS = MAX_EVENTS
    EVENT_FIELDS = (
        "items(id,summary,status,start,end,recurrence,recurringEventId,originalStartTime),nextPageToken"
    )

    def __init__(self, service):
        self.service = service

    def binding(self):
        try:
            calendar = resolve_calendar(self.service)
            if not isinstance(calendar, dict):
                raise ValueError()
            calendar_id = _identity(calendar["id"])
            if (calendar_id == "primary" or calendar.get("primary", False) is not False
                    or calendar.get("deleted", False) is not False
                    or "accessRole" in calendar and calendar["accessRole"] != "owner"):
                raise ValueError()
            zone = _zone(calendar["timeZone"])
            return SummaryCalendarBinding(calendar_id, zone.key)
        except CalendarSetupError:
            raise SummarySetupError() from None
        except Exception:
            raise SummaryCollectionError() from None

    def list_events(self, period, *, expected_binding):
        try:
            if type(period) is not CalendarPeriod or type(expected_binding) is not SummaryCalendarBinding:
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
            # O Google descarta frações nos filtros. Ampliar para fora e filtrar
            # os limites exatos localmente, sem excluir sobreposições legítimas.
            query_start = start.replace(microsecond=0)
            query_end = end.replace(microsecond=0)
            if end.microsecond:
                query_end += timedelta(seconds=1)
            binding = self.binding()
            if binding != expected_binding:
                raise ValueError()
            zone = _zone(binding.time_zone)
            resource = self.service.events()
            collected, observed = {}, {}
            seen_tokens = set()
            page_token = None
            raw_count = 0
            for index in range(self.MAX_PAGES):
                page = resource.list(calendarId=binding.calendar_id,
                    timeMin=query_start.isoformat(), timeMax=query_end.isoformat(),
                    timeZone=binding.time_zone, singleEvents=True, showDeleted=False,
                    orderBy="startTime", maxResults=250, pageToken=page_token,
                    fields=self.EVENT_FIELDS).execute(num_retries=2)
                if not isinstance(page, dict) or not isinstance(page.get("items", []), list):
                    raise ValueError()
                for item in page.get("items", []):
                    raw_count += 1
                    if raw_count > self.MAX_EVENTS:
                        raise SummaryLimitError()
                    if not isinstance(item, dict):
                        raise ValueError()
                    event_id = _identity(item.get("id"))
                    previous = observed.get(event_id)
                    if previous is not None and previous != item:
                        raise ValueError()
                    observed[event_id] = deepcopy(item)
                    if "recurrence" in item:
                        raise ValueError()
                    if item.get("status") == "cancelled":
                        continue
                    event_start, event_end, entry = _event(item, zone)
                    if event_start >= end or event_end <= start:
                        continue
                    collected[event_id] = (event_start, entry)
                next_token = page.get("nextPageToken")
                if next_token is None or next_token == "":
                    ordered = tuple(value[1] for value in sorted(collected.values(),
                        key=lambda value: (value[0], value[1].event_id)))
                    return SummaryCalendarSnapshot(binding, period, ordered, True, index + 1, raw_count)
                if (not isinstance(next_token, str) or len(next_token) > 4096
                        or any(char.isspace() or unicodedata.category(char).startswith("C") for char in next_token)
                        or next_token in seen_tokens):
                    raise ValueError()
                next_token.encode("utf-8")
                if index + 1 == self.MAX_PAGES or raw_count == self.MAX_EVENTS:
                    raise SummaryLimitError()
                seen_tokens.add(next_token)
                page_token = next_token
            raise SummaryLimitError()
        except CalendarSetupError:
            raise SummarySetupError() from None
        except (SummarySetupError, SummaryLimitError):
            raise
        except Exception:
            raise SummaryCollectionError() from None


@contextmanager
def default_summary_calendar_gateway():
    try:
        with calendar_client(interactive=False) as client:
            yield GoogleSummaryCalendarGateway(client)
    except CalendarSetupError:
        raise SummarySetupError() from None
    except SummaryCollectionError:
        raise
    except Exception:
        raise SummaryCollectionError() from None
