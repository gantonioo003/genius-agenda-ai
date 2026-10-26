"""Disponibilidade de uma agenda autorizada, sem reserva, IA ou persistência."""
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import re

from app.services.agenda import CalendarPeriod
from app.services.event_selection import validate_event_id
from app.services.time_rules import load_zone


MAX_PAGES = 20
MAX_EVENTS = 2000
STEP_MINUTES = 15
SLOT_LIMIT = 3
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)
_TIME = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", re.ASCII)
_FIELDS = ("date_from", "date_to", "window_start", "window_end",
           "duration_minutes", "buffer_minutes")


class _Private:
    def __repr__(self):
        return type(self).__name__ + "(<private>)"


@dataclass(frozen=True, repr=False)
class AvailabilityBinding(_Private):
    calendar_id: str
    time_zone: str


@dataclass(frozen=True, repr=False)
class AvailabilityEvent(_Private):
    event_id: str
    start_utc: datetime
    end_utc: datetime
    status: str
    transparency: str
    all_day: bool


@dataclass(frozen=True, repr=False)
class AvailabilitySnapshot(_Private):
    binding: AvailabilityBinding
    period: CalendarPeriod
    events: tuple[AvailabilityEvent, ...]
    complete: bool
    pages_read: int
    raw_events_seen: int


class AvailabilityInputError(ValueError):
    def __init__(self, fields):
        super().__init__("Informe datas, faixa diária, duração e intervalo válidos.")
        self.fields = tuple(field for field in _FIELDS if field in fields)


class AvailabilityCollectionError(RuntimeError):
    def __init__(self):
        super().__init__("Não foi possível confirmar a disponibilidade completa da agenda.")


class AvailabilitySetupError(AvailabilityCollectionError):
    pass


class AvailabilityLimitError(AvailabilityCollectionError):
    pass


@dataclass(frozen=True, repr=False)
class _Request(_Private):
    first: date
    last: date
    start_minute: int
    end_minute: int
    duration: int
    buffer: int


def validate_request(*, date_from, date_to, window_start, window_end,
                     duration_minutes, buffer_minutes=0):
    """Validar léxico e limites antes de consultar relógio ou conector."""
    fields = []
    parsed = {}
    for field, value in (("date_from", date_from), ("date_to", date_to)):
        try:
            if type(value) is not str or _DATE.fullmatch(value) is None:
                raise ValueError()
            parsed[field] = date.fromisoformat(value)
        except (ValueError, OverflowError):
            fields.append(field)
    if len(parsed) == 2 and not 0 <= (parsed["date_to"] - parsed["date_from"]).days < 31:
        fields.extend(("date_from", "date_to"))
    minutes = {}
    for field, value in (("window_start", window_start), ("window_end", window_end)):
        if field == "window_end" and type(value) is str and value == "24:00":
            minutes[field] = 1440
        elif type(value) is str and _TIME.fullmatch(value) is not None:
            minutes[field] = int(value[:2]) * 60 + int(value[3:])
        else:
            fields.append(field)
    if len(minutes) == 2 and minutes["window_start"] >= minutes["window_end"]:
        fields.extend(("window_start", "window_end"))
    if type(duration_minutes) is not int or not 1 <= duration_minutes <= 1440:
        fields.append("duration_minutes")
    if type(buffer_minutes) is not int or not 0 <= buffer_minutes <= 240:
        fields.append("buffer_minutes")
    if parsed.get("date_to") == date.max and minutes.get("window_end") == 1440:
        fields.append("date_to")
    if fields:
        raise AvailabilityInputError(fields)
    return _Request(parsed["date_from"], parsed["date_to"], minutes["window_start"],
                    minutes["window_end"], duration_minutes, buffer_minutes)


def _utc(value):
    if type(value) is not datetime or value.utcoffset() is None:
        raise ValueError()
    return value.astimezone(timezone.utc)


def _identifier(value):
    if type(value) is not str:
        raise ValueError()
    validate_event_id(value)
    value.encode("utf-8")
    return value


def _binding(value):
    if (type(value) is not AvailabilityBinding or _identifier(value.calendar_id) == "primary"
            or type(value.time_zone) is not str):
        raise ValueError()
    return load_zone(value.time_zone)


def _civil_candidates(naive, zone):
    """Instantes de uma hora civil: zero em gaps, dois em folds, um normalmente."""
    found = set()
    for fold in (0, 1):
        local = naive.replace(tzinfo=zone, fold=fold)
        instant = _utc(local)
        returned = instant.astimezone(zone)
        if (returned.replace(tzinfo=None) == naive
                and returned.utcoffset() == local.utcoffset()):
            found.add(instant)
    return sorted(found)


def _windows(request, zone):
    windows, fields = [], []
    for offset in range((request.last - request.first).days + 1):
        day = request.first + timedelta(days=offset)
        pair = []
        for minute, field in ((request.start_minute, "window_start"),
                              (request.end_minute, "window_end")):
            try:
                naive = datetime.combine(day, time.min) + timedelta(minutes=minute)
                candidates = _civil_candidates(naive, zone)
                if len(candidates) != 1:
                    raise ValueError()
                pair.append(candidates[0])
            except (ValueError, OverflowError):
                fields.append(field)
        if len(pair) == 2:
            if pair[0] >= pair[1]:
                fields.extend(("window_start", "window_end"))
            else:
                windows.append((day, pair[0], pair[1]))
    if fields:
        raise AvailabilityInputError(fields)
    return windows


def _snapshot(value, binding, selected):
    if (type(value) is not AvailabilitySnapshot or type(value.complete) is not bool
            or value.complete is not True or type(value.binding) is not AvailabilityBinding
            or value.binding != binding or type(value.period) is not CalendarPeriod
            or type(value.events) is not tuple or value.period.time_zone != selected.time_zone
            or _utc(value.period.start) != _utc(selected.start)
            or _utc(value.period.end) != _utc(selected.end)):
        raise ValueError()
    if (type(value.pages_read) is not int or value.pages_read < 1
            or type(value.raw_events_seen) is not int or value.raw_events_seen < 0):
        raise ValueError()
    if value.pages_read > MAX_PAGES or value.raw_events_seen > MAX_EVENTS or len(value.events) > MAX_EVENTS:
        raise AvailabilityLimitError()
    if value.raw_events_seen < len(value.events):
        raise ValueError()


def _busy(events, buffer):
    identities, intervals = {}, []
    for event in events:
        if type(event) is not AvailabilityEvent:
            raise ValueError()
        identity = _identifier(event.event_id)
        if (type(event.status) is not str or event.status not in {"confirmed", "tentative", "cancelled"}
                or type(event.transparency) is not str or event.transparency not in {"opaque", "transparent"}
                or type(event.all_day) is not bool):
            raise ValueError()
        start, end = _utc(event.start_utc), _utc(event.end_utc)
        if start >= end:
            raise ValueError()
        normalized = (start, end, event.status, event.transparency, event.all_day)
        if identity in identities and identities[identity] != normalized:
            raise ValueError()
        if identity in identities:
            continue
        identities[identity] = normalized
        if event.status != "cancelled" and event.transparency == "opaque":
            intervals.append((start - buffer, end + buffer))
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _slots(windows, request, zone, busy, observed):
    candidates = set()
    duration = timedelta(minutes=request.duration)
    for day, lower, upper in windows:
        first = ((request.start_minute + STEP_MINUTES - 1) // STEP_MINUTES) * STEP_MINUTES
        for minute in range(first, request.end_minute, STEP_MINUTES):
            naive = datetime.combine(day, time.min) + timedelta(minutes=minute)
            for start in _civil_candidates(naive, zone):
                if start >= observed and lower <= start < upper and duration <= upper - start:
                    candidates.add((start, start + duration))
    ends = [end for _, end in busy]
    available = []
    for start, end in sorted(candidates):
        index = bisect_right(ends, start)
        if index < len(busy) and busy[index][0] < end:
            continue
        available.append((start, end))
        if len(available) > SLOT_LIMIT:
            break
    return available


class AvailabilityService:
    def __init__(self, gateway_factory, *, clock=None):
        self._gateway_factory = gateway_factory
        self._clock = clock if clock is not None else lambda: datetime.now(timezone.utc)

    def find(self, *, date_from, date_to, window_start, window_end,
             duration_minutes, buffer_minutes=0) -> dict:
        request = validate_request(date_from=date_from, date_to=date_to, window_start=window_start,
                                   window_end=window_end, duration_minutes=duration_minutes,
                                   buffer_minutes=buffer_minutes)
        try:
            before = _utc(self._clock())
            with self._gateway_factory() as gateway:
                binding = gateway.binding()
                zone = _binding(binding)
                windows = _windows(request, zone)
                lower, upper = windows[0][1], windows[-1][2]
                buffer = timedelta(minutes=request.buffer)
                try:
                    selected = CalendarPeriod((lower - buffer).astimezone(zone),
                                              (upper + buffer).astimezone(zone), binding.time_zone)
                except (ValueError, OverflowError):
                    raise AvailabilityInputError(("buffer_minutes",)) from None
                snapshot = gateway.list_occupancy(selected, expected_binding=binding)
                _snapshot(snapshot, binding, selected)
                busy = _busy(snapshot.events, buffer)
            # O instante inclui a leitura e o encerramento do cliente; nunca sugerir
            # algo que começou enquanto o conector trabalhava.
            observed = _utc(self._clock())
            if observed < before:
                raise ValueError()
            available = _slots(windows, request, zone, busy, observed)
            slots = [{"ref": "S" + str(index + 1), "start": start.astimezone(zone).isoformat(),
                      "end": end.astimezone(zone).isoformat(), "time_zone": binding.time_zone}
                     for index, (start, end) in enumerate(available[:SLOT_LIMIT])]
            return {"schema_version": 1, "request": {"date_from": date_from, "date_to": date_to,
                    "window_start": window_start, "window_end": window_end,
                    "duration_minutes": request.duration, "buffer_minutes": request.buffer,
                    "step_minutes": STEP_MINUTES},
                    "period": {"start": lower.astimezone(zone).isoformat(),
                               "end": upper.astimezone(zone).isoformat(), "time_zone": binding.time_zone},
                    "snapshot": {"observed_at_utc": observed.isoformat(), "complete": True},
                    "slots": slots, "count": len(slots), "limit": SLOT_LIMIT,
                    "more_available": len(available) > SLOT_LIMIT,
                    "scope": "authorized_calendar_only", "reservation_created": False,
                    "execution_allowed": False}
        except AvailabilityInputError as error:
            raise AvailabilityInputError(error.fields) from None
        except AvailabilitySetupError:
            raise AvailabilitySetupError() from None
        except AvailabilityLimitError:
            raise AvailabilityLimitError() from None
        except Exception:
            raise AvailabilityCollectionError() from None
